"""Oddzielne kolekcje i piny CI nie są dowodem uruchomienia GitHub Actions."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("sf_candidate_ci", ROOT / "hermes/ci/run_gates.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class CandidateCIContractTests(unittest.TestCase):
    def test_only_complete_skip_free_results_pass(self):
        self.assertEqual(runner.ROOT, ROOT)
        result = {"ids": [f"test_{i}" for i in range(7)], "tests": 7, "collected": 7, "passed": 7, "failures": 0, "errors": 0,
                  "skipped": 0, "unexpected": 0, "expected_failures": 0}
        self.assertTrue(runner.gate_passed(result, 7))
        for key, value in (("tests", 0), ("collected", 6), ("passed", 6), ("failures", 1),
                           ("errors", 1), ("skipped", 1), ("unexpected", 1), ("expected_failures", 1)):
            with self.subTest(key=key):
                self.assertFalse(runner.gate_passed(dict(result, **{key: value}), 7))
        self.assertFalse(runner.gate_passed({}, 7))

    def test_empty_duplicate_or_unsafe_gate_spec_is_refused_before_output(self):
        import copy
        import tempfile
        gates = json.loads(runner.SPEC.read_text())["gates"]
        runner.validate_gates(gates)
        bad = [[], [gates[0], gates[0]]]
        for field, value in (("name", "../escape"), ("kind", "shell"),
                             ("expected_tests", True), ("expected_tests", 0),
                             ("directory", "../outside"), ("pattern", "*.py")):
            changed = copy.deepcopy(gates[0]); changed[field] = value; bad.append([changed])
        for spec in bad:
            with self.subTest(spec=spec), tempfile.TemporaryDirectory() as td:
                output = Path(td) / "must-not-exist"
                with self.assertRaises(ValueError):
                    runner.run_all(ROOT, Path("/not-executed"), output, spec, 1)
                self.assertFalse(output.exists())

    def test_duplicate_ids_noninteger_counts_and_incomplete_results_never_pass(self):
        base = {"ids": ["one", "two"], "tests": 2, "collected": 2, "passed": 2,
                "failures": 0, "errors": 0, "skipped": 0, "unexpected": 0, "expected_failures": 0}
        self.assertTrue(runner.gate_passed(base, 2))
        for field, value in (("ids", ["one", "one"]), ("ids", []), ("ids", [None, "two"]),
                             ("tests", 2.0), ("failures", False)):
            with self.subTest(field=field, value=value):
                self.assertFalse(runner.gate_passed(dict(base, **{field: value}), 2))
        self.assertFalse(runner.gate_passed(base, True))

    def test_every_retained_separate_collection_is_explicit(self):
        config = json.loads(runner.SPEC.read_text())
        self.assertEqual(config["native_head"], "0a0497060ad4bf1ccd7f6846b0c4d4b03f8476f1")
        gates = {g["name"]: g for g in config["gates"]}
        self.assertEqual(len(gates), len(config["gates"]))
        self.assertEqual(set(gates), {"main", "start", "lifetime", "transaction_registration", "native_registry_plugins", "review_revisions", "review_round2", "profile_lifetime", "codex_transport", "dispatch_binding"})
        for name, directory in (("main", "hermes"), ("start", "hermes/tests_worker_start"),
                                ("lifetime", "hermes/tests_worker_lifetime"), ("transaction_registration", "hermes/tests_native_handoff"), ("review_revisions", "hermes/tests_review_revisions"), ("review_round2", "hermes/tests_review_round2"), ("profile_lifetime", "hermes/tests_profile_lifetime"), ("codex_transport", "hermes/tests_codex_transport"), ("dispatch_binding", "hermes/tests_dispatch_binding")):
            self.assertEqual(gates[name]["directory"], directory)
            self.assertEqual(gates[name]["pattern"], "test*.py")
            self.assertGreater(gates[name]["expected_tests"], 0)
        self.assertEqual(gates["native_registry_plugins"]["paths"], ["tests/tools/test_registry.py", "tests/hermes_cli/test_plugins.py"])
        self.assertEqual(gates["lifetime"]["expected_tests"], 73)

    def test_native_distribution_declares_mandatory_boundary_modules(self):
        import os
        import tomllib
        native = Path(os.environ["FACTORY_NATIVE_ROOT"])
        config = tomllib.loads((native / "pyproject.toml").read_text())
        declared = config["tool"]["setuptools"]["py-modules"]
        for module in ("factory_worker_start", "factory_worker_lifetime", "factory_model_boundary", "factory_profile_start"):
            self.assertIn(module, declared)
            self.assertEqual(declared.count(module), 1)
            self.assertTrue((native / (module + ".py")).is_file())

    def test_workflow_uses_immutable_actions_and_exact_native_checkout(self):
        import yaml
        file = ROOT / ".github/workflows/candidate-native-gates.yml"
        self.assertTrue(file.is_file(), "new native suites have no CI entrypoint")
        data = yaml.load(file.read_text(), Loader=yaml.BaseLoader)
        self.assertEqual(data["permissions"], {"contents": "read"})
        self.assertIn("pull_request", data["on"])
        job = data["jobs"]["native-gates"]
        self.assertEqual(set(job["strategy"]["matrix"]["python"]), {"3.11"})
        steps = job["steps"]
        for step in steps:
            if "uses" in step:
                self.assertRegex(step["uses"], r"^[\w./-]+@[0-9a-f]{40}$")
        native = next(s for s in steps if s.get("with", {}).get("repository") == "NousResearch/hermes-agent")
        self.assertEqual(native["with"]["ref"], "0a0497060ad4bf1ccd7f6846b0c4d4b03f8476f1")
        self.assertEqual(native["with"]["persist-credentials"], "false")
        source = file.read_text()
        self.assertIn("hermes/ci/run_gates.py", source)
        self.assertRegex(source, r"git(?: -C [^\s]+)? apply --check ")
        # G5/G6: dependencies come only from the reviewed hashed locks; the native uv.lock does not match the overlay.
        self.assertNotIn("uv sync", source)
        self.assertIn("uv pip install --python native/.venv/bin/python --require-hashes --no-deps -r sf/hermes/native_integration/runtime.lock.txt -r sf/hermes/native_integration/test.lock.txt", source)
        self.assertIn("--build-constraints sf/hermes/native_integration/build.lock.txt -e native", source)
        import re as _re
        lock = (ROOT / "hermes/native_integration/test.lock.txt").read_text()
        pins = _re.findall(r"^([A-Za-z0-9_.\-]+)==([^ \\\n]+)", lock, _re.M)
        self.assertEqual(sorted(n for n, _ in pins), ["iniconfig", "pluggy", "pytest", "pytest-asyncio"])
        self.assertEqual(len(_re.findall(r"--hash=sha256:[0-9a-f]{64}", lock)), len(pins))
        apply_step = next(s for s in steps if "git -C native apply --check" in s.get("run", ""))
        command = apply_step["run"]
        self.assertEqual(command.count("hermes/ci/verify_native_overlay.py"), 2)
        self.assertLess(command.index("--phase before"), command.index("git -C native apply --check"))
        self.assertLess(command.index("git -C native apply ../"), command.index("--phase after"))
        self.assertEqual(command.count('--manifest "$PWD/sf/hermes/native_integration/native_overlay.json"'), 2)
        self.assertEqual(command.count('--patch "$PWD/sf/hermes/native_integration/native_runtime.patch"'), 2)
        for name in ("factory_worker_start", "factory_worker_lifetime", "factory_model_boundary", "factory_profile_start"):
            self.assertIn(f"cmp sf/hermes/native_integration/{name}.py native/{name}.py", command)
        self.assertNotIn("pull_request_target", source)
        self.assertNotIn("secrets.", source)


if __name__ == "__main__":
    unittest.main()
