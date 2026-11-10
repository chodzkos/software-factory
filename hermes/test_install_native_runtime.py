"""Unit tests for the native runtime installer (G4/G5). No network, no subprocess, no installation."""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("sf_install_native_runtime", HERE / "install_native_runtime.py")
INSTALLER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALLER)
FIXED = {"pyjwt": "2.15.0", "anyio": "4.14.2", "certifi": "2026.10.18", "pillow": "13.0.0", "urllib3": "2.8.0"}


class InstallerUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def wheel(self, directory: Path, name: str, data: bytes, mode: int = 0o644) -> str:
        path = directory / name
        path.write_bytes(data)
        path.chmod(mode)
        return hashlib.sha256(data).hexdigest()

    def test_candidate_locks_pin_every_package_with_hashes_and_fixed_versions(self):
        names, hashes = INSTALLER.lock_hashes(HERE / "native_integration" / "runtime.lock.txt")
        text = (HERE / "native_integration" / "runtime.lock.txt").read_text()
        self.assertEqual(len(names), 60)
        self.assertEqual(text.count("==") , len(names))
        self.assertGreaterEqual(len(hashes), len(names))
        versions = {re.sub(r"[-_.]+", "-", n).lower(): v for n, v in names}
        for package, version in FIXED.items():
            self.assertEqual(versions[package], version, package)
        bnames, _ = INSTALLER.lock_hashes(HERE / "native_integration" / "build.lock.txt")
        self.assertEqual(bnames, [("setuptools", "83.0.0")])

    def test_empty_lock_refused(self):
        lock = self.root / "empty.lock"
        lock.write_text("# nothing\n")
        with self.assertRaisesRegex(SystemExit, "empty lock"):
            INSTALLER.lock_hashes(lock)

    def test_wheelhouse_accepts_only_locked_private_wheels(self):
        wh = self.root / "wh"
        wh.mkdir()
        good = self.wheel(wh, "a-1.0-py3-none-any.whl", b"locked")
        self.assertEqual(INSTALLER.check_wheelhouse(wh, {good}), {"a-1.0-py3-none-any.whl": good})
        self.wheel(wh, "b-1.0-py3-none-any.whl", b"not locked")
        with self.assertRaisesRegex(SystemExit, "wheel not in lock"):
            INSTALLER.check_wheelhouse(wh, {good})

    def test_wheelhouse_refuses_group_writable_and_foreign_entries(self):
        wh = self.root / "wh"
        wh.mkdir()
        digest = self.wheel(wh, "a-1.0-py3-none-any.whl", b"locked", mode=0o664)
        with self.assertRaisesRegex(SystemExit, "wheelhouse entry refused"):
            INSTALLER.check_wheelhouse(wh, {digest})
        (wh / "a-1.0-py3-none-any.whl").chmod(0o644)
        (wh / "notes.txt").write_text("x")
        with self.assertRaisesRegex(SystemExit, "wheelhouse entry refused"):
            INSTALLER.check_wheelhouse(wh, {digest})

    def test_hardlinked_runtime_file_refused(self):
        venv = self.root / "venv"
        venv.mkdir()
        (venv / "a.py").write_text("x")
        INSTALLER.no_hardlinks(venv)
        os.link(venv / "a.py", self.root / "outside.py")
        with self.assertRaisesRegex(SystemExit, "hardlinked"):
            INSTALLER.no_hardlinks(venv)

    def test_editable_mapping_must_stay_inside_runtime(self):
        runtime = self.root / "rt"
        sp = runtime / ".venv/lib/python3.11/site-packages"
        sp.mkdir(parents=True)
        finder = sp / "__editable___hermes_agent_0_20_4_finder.py"
        finder.write_text("MAPPING: dict[str, str] = %r\n" % {"hermes_cli": str(runtime / "hermes_cli")})
        self.assertEqual(INSTALLER.editable_mapping(runtime)[1], {"hermes_cli": str(runtime / "hermes_cli")})
        finder.write_text("MAPPING: dict[str, str] = %r\n" % {"hermes_cli": "/elsewhere/hermes_cli"})
        with self.assertRaisesRegex(SystemExit, "maps outside the runtime"):
            INSTALLER.editable_mapping(runtime)

    def test_existing_runtime_target_refused_before_any_command(self):
        target = self.root / "existing"
        target.mkdir()
        argv = ["install_native_runtime.py", "--pinned-native", str(self.root), "--runtime", str(target), "--python", sys.executable,
                "--uv", "/nonexistent/uv", "--wheelhouse", str(self.root), "--verify-python", sys.executable]
        with patch.object(sys, "argv", argv), patch.object(INSTALLER.subprocess, "run", side_effect=AssertionError("no command")):
            with self.assertRaisesRegex(SystemExit, "must not exist"):
                INSTALLER.main()


if __name__ == "__main__":
    unittest.main()
