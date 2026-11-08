#!/usr/bin/env python3
"""Osobne procesy dla bramek kandydata; nie instaluje pluginu ani nakładki.

JSON z pełnej kolekcji i wyników odróżnia brak testów, skip i przerwanie od PASS.
Każdy proces otrzymuje puste HOME i blokadę połączeń TCP/UDP w Pythonie. To
higiena testów offline, nie izolacja wrogiego kodu ani zamiennik kontenera.
"""
from __future__ import annotations

import argparse
import json
import os
import pwd
import re
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
SPEC = Path(__file__).with_name("gates.json")


def validate_gates(gates: list[dict]) -> None:
    """Reject empty/ambiguous collections before creating any output directory."""
    if not isinstance(gates, list) or not gates:
        raise ValueError("at least one explicitly declared gate is required")
    names = set()
    for gate in gates:
        if not isinstance(gate, dict):
            raise ValueError("gate must be an object")
        name = gate.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in names:
            raise ValueError("gate names must be safe and unique")
        names.add(name)
        count = gate.get("expected_tests")
        if type(count) is not int or not 1 <= count <= 100000:
            raise ValueError("expected_tests must be a positive integer")
        kind = gate.get("kind")
        if kind == "unittest":
            paths = [gate.get("directory")]
            if gate.get("pattern") != "test*.py":
                raise ValueError("unittest collection pattern changed")
        elif kind == "pytest":
            paths = gate.get("paths")
        else:
            raise ValueError("unknown gate kind")
        if not isinstance(paths, list) or not paths:
            raise ValueError("gate paths must be nonempty")
        for path in paths:
            if (not isinstance(path, str) or not path or path.startswith("-")
                    or Path(path).is_absolute() or ".." in Path(path).parts):
                raise ValueError("gate path must stay within its declared source root")


def gate_passed(result: dict, expected: int) -> bool:
    """Require exact nonduplicated collection, with no skips or partial result."""
    ids = result.get("ids")
    if (type(expected) is not int or expected <= 0 or not isinstance(ids, list)
            or len(ids) != expected or any(not isinstance(i, str) or not i for i in ids)
            or len(set(ids)) != expected):
        return False
    counts = ("tests", "collected", "passed", "failures", "errors", "skipped", "unexpected", "expected_failures")
    return (all(type(result.get(key)) is int for key in counts)
            and result["tests"] == expected and result["collected"] == expected
            and result["passed"] == expected
            and all(result[key] == 0 for key in counts[3:]))


def test_ids(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from test_ids(item)
        else:
            yield item.id()


def run_child(gate: dict, output: Path, native: Path) -> int:
    """Wykonaj jedną kolekcję; nie łącz modułów o globalnym stanie między bramkami."""
    details = {}
    try:
        if gate["kind"] == "unittest":
            suite = unittest.TestLoader().discover(str(ROOT / gate["directory"]), pattern=gate["pattern"])
            ids = list(test_ids(suite))
            result = unittest.TextTestRunner(verbosity=2).run(suite)
            details = {"tests": result.testsRun, "collected": len(ids), "ids": ids,
                       "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped)
                                 - len(result.expectedFailures) - len(result.unexpectedSuccesses),
                       "failures": len(result.failures), "errors": len(result.errors),
                       "skipped": len(result.skipped), "unexpected": len(result.unexpectedSuccesses),
                       "expected_failures": len(result.expectedFailures),
                       "failed_ids": [t.id() for t, _ in result.failures + result.errors],
                       "skip_reasons": [(t.id(), why) for t, why in result.skipped]}
        else:
            import pytest
            os.chdir(native)
            xml = output.with_suffix(".xml")
            class Reports:
                def __init__(self):
                    self.ids, self.xfail, self.xpass = [], 0, 0
                def pytest_collection_finish(self, session):
                    self.ids = [item.nodeid for item in session.items]
                def pytest_runtest_logreport(self, report):
                    if hasattr(report, "wasxfail"):
                        if report.skipped:
                            self.xfail += 1
                        elif report.passed and report.when == "call":
                            self.xpass += 1
            reports = Reports()
            rc = pytest.main(["-q", "-p", "no:cacheprovider", "--basetemp", str(output.parent / (gate["name"] + "-tmp")),
                              "--junitxml", str(xml), *gate["paths"]], plugins=[reports])
            cases = list(ET.parse(xml).iter("testcase")) if xml.exists() else []
            errors = sum(len(c.findall("error")) for c in cases)
            failures = sum(len(c.findall("failure")) for c in cases)
            skipped = sum(len(c.findall("skipped")) for c in cases)
            details = {"tests": len(cases), "collected": len(reports.ids), "passed": len(cases) - errors - failures - skipped,
                       "errors": errors, "failures": failures, "skipped": skipped,
                       "unexpected": reports.xpass + int(rc != 0 and not (errors or failures)), "expected_failures": reports.xfail,
                       "ids": reports.ids, "pytest_exit": int(rc)}
    except BaseException:
        traceback.print_exc()
        details["harness_exception"] = traceback.format_exc()
    details.update(name=gate["name"], python=sys.version, executable=sys.executable, euid=os.geteuid())
    details["ok"] = gate_passed(details, gate["expected_tests"])
    output.write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
    return 0 if details["ok"] else 1


_NETWORK_GUARD = '''import socket
def _offline(*a, **kw):
    raise RuntimeError("FACTORY_TEST_NETWORK_FORBIDDEN")
socket.socket.connect = _offline
socket.socket.connect_ex = _offline
socket.socket.sendto = _offline
'''


def run_all(native: Path, python: Path, output: Path, gates: list[dict], timeout: int) -> int:
    validate_gates(gates)
    output.mkdir(parents=True, exist_ok=False)
    output.chmod(0o700)
    rows = []
    for gate in gates:
        log = output / (gate["name"] + ".log")
        result_path = output / (gate["name"] + ".json")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="sf-ci-", dir=output) as td:
            scratch = Path(td)
            home = scratch / "home"
            (home / ".hermes").mkdir(parents=True)
            (home / ".hermes/hermes-agent").symlink_to(native, target_is_directory=True)
            (scratch / "sitecustomize.py").write_text(_NETWORK_GUARD, encoding="utf-8")
            # G3: fresh private empty bytecode cache prefix per gate (the start gates require it).
            pycache = scratch / "pycache"; pycache.mkdir(mode=0o700)
            # Nie przenoś OAuth, API keys, PYTHONSTARTUP ani profilu właściciela.
            env = {"PATH": os.pathsep.join((str(python.parent), str(native), os.defpath, "/usr/local/bin")),
                   "HOME": str(home), "HERMES_HOME": str(home / ".hermes"),
                   "USER": pwd.getpwuid(os.getuid()).pw_name, "LOGNAME": pwd.getpwuid(os.getuid()).pw_name, "LANG": "C.UTF-8",
                   "TMPDIR": str(scratch), "PYTHONPATH": os.pathsep.join((str(scratch), str(ROOT), str(native))),
                   "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPYCACHEPREFIX": str(pycache), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                   "FACTORY_NATIVE_ROOT": str(native), "FACTORY_HERMES_PYTHON": str(python),
                   "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_TERMINAL_PROMPT": "0"}
            command = [str(python), "-B", str(Path(__file__).resolve()), "--child", gate["name"],
                       "--native-root", str(native), "--output", str(result_path)]
            with log.open("w", encoding="utf-8") as handle:
                proc = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                                        stderr=subprocess.STDOUT, start_new_session=True)
                try:
                    rc = proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
                    rc = 124
            details = json.loads(result_path.read_text()) if result_path.exists() else {"ok": False, "name": gate["name"], "missing_result": True}
            details.update(process_exit=rc, elapsed_seconds=time.monotonic() - started)
            details["ok"] = bool(rc == 0 and details.get("ok") is True)
            result_path.write_text(json.dumps(details, indent=2) + "\n", encoding="utf-8")
            rows.append(details)
            print(f"{gate['name']}: {'PASS' if details['ok'] else 'FAIL'} tests={details.get('tests', 0)} rc={rc}", flush=True)
    summary = {"schema": 1, "source_only": True, "installable": False,
               "native_root": str(native), "python": str(python), "gates": rows,
               "ok": all(r["ok"] for r in rows)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0 if summary["ok"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-root", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    gates = json.loads(SPEC.read_text())["gates"]
    validate_gates(gates)
    native = args.native_root.resolve(strict=True)
    if args.child:
        gate = next(g for g in gates if g["name"] == args.child)
        return run_child(gate, args.output, native)
    if args.timeout < 1 or args.timeout > 1800:
        parser.error("timeout must be between 1 and 1800 seconds")
    python = args.python.absolute()
    if not python.is_file() or not os.access(python, os.X_OK):
        parser.error("managed Python must be an executable file")
    return run_all(native, python, args.output.absolute(), gates, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
