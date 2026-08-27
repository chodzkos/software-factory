#!/usr/bin/env python3
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORK = ROOT / "skills" / "custom" / "factory-repo-map"
HELPER = FORK / "scripts" / "repo_map.py"


def run_map(workspace: Path, target=".", *args, check=True):
    cmd = [sys.executable, str(HELPER), "--workspace", str(workspace), target, *args]
    return subprocess.run(cmd, text=True, capture_output=True, check=check, timeout=10)


class FactoryRepoMapTests(unittest.TestCase):
    def test_review_only_not_manifest_or_profile_visible(self):
        import json
        manifest = json.loads((ROOT / "skills" / "manifest.yaml").read_text())
        profiles = json.loads((ROOT / "skills" / "profiles.yaml").read_text())["profiles"]
        self.assertNotIn("factory-repo-map", manifest["skills"])
        for name, policy in profiles.items():
            self.assertNotIn("factory-repo-map", policy["required"] + policy["optional"], name)

    def test_prunes_generated_and_hidden_dirs_at_all_depths(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir()
            for rel in (".git", "node_modules", ".venv", "vendor", "target", "build", "dist", "coverage", "src/.hidden", "src/node_modules"):
                (root / rel).mkdir(parents=True, exist_ok=True)
                (root / rel / "leak.py").write_text("def must_not_leak(): pass\n")
            (root / "src" / "ok.py").write_text("def visible(): pass\n")
            out = run_map(root).stdout
            self.assertIn("src/ok.py", out)
            self.assertIn("visible", out)
            self.assertNotIn("leak.py", out)
            self.assertNotIn("must_not_leak", out)
            self.assertNotIn("node_modules", out)
            self.assertNotIn(".git", out)

    def test_refuses_hidden_or_generated_target_root(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / ".git").mkdir()
            (root / "node_modules").mkdir()
            for target in (".git", "node_modules"):
                run = run_map(root, target, check=False)
                self.assertNotEqual(run.returncode, 0, target)

    def test_refuses_absolute_and_parent_escape_targets(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td:
            root = Path(td)
            outside = Path(outside_td)
            run = run_map(root, str(outside), check=False)
            self.assertNotEqual(run.returncode, 0)
            sibling = root.parent / (root.name + "-outside")
            sibling.mkdir()
            try:
                run = run_map(root, f"../{sibling.name}", check=False)
                self.assertNotEqual(run.returncode, 0)
            finally:
                sibling.rmdir()

    def test_refuses_workspace_target_file_and_directory_symlinks(self):
        with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside_td:
            root = Path(td)
            outside = Path(outside_td)
            (outside / "outside.py").write_text("def outside_secret(): pass\n")
            (root / "link.py").symlink_to(outside / "outside.py")
            (root / "linkdir").symlink_to(outside, target_is_directory=True)
            out = run_map(root).stdout
            self.assertNotIn("outside_secret", out)
            self.assertNotIn("link.py", out)
            self.assertNotIn("linkdir", out)

            target_link = root / "target-link"
            target_link.symlink_to(outside, target_is_directory=True)
            run = run_map(root, "target-link", check=False)
            self.assertNotEqual(run.returncode, 0)

            workspace_link = root.parent / (root.name + "-link")
            workspace_link.symlink_to(root, target_is_directory=True)
            try:
                run = subprocess.run(
                    [sys.executable, str(HELPER), "--workspace", str(workspace_link), "."],
                    text=True, capture_output=True, timeout=10,
                )
                self.assertNotEqual(run.returncode, 0)
            finally:
                workspace_link.unlink()

    def test_enforces_file_count_and_directory_count_limits(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for i in range(5):
                (root / f"f{i}.py").write_text(f"def f{i}(): pass\n")
            out = run_map(root, ".", "--max-files", "2").stdout
            self.assertIn("truncated by configured safety limits", out)
            self.assertLessEqual(sum(1 for line in out.splitlines() if ".py" in line), 2)

            for i in range(4):
                d = root / f"d{i}"
                d.mkdir()
                (d / "x.py").write_text("def x(): pass\n")
            out = run_map(root, ".", "--max-dirs", "1").stdout
            self.assertIn("truncated by configured safety limits", out)

    def test_enforces_per_file_and_total_byte_limits_before_read(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "big.py").write_text("x" * 5000 + "\ndef big(): pass\n")
            out = run_map(root, ".", "--max-file-bytes", "1024").stdout
            self.assertNotIn("big.py", out)
            self.assertIn("truncated by configured safety limits", out)

            (root / "a.py").write_text("def a(): pass\n" + "a" * 600)
            (root / "b.py").write_text("def b(): pass\n" + "b" * 600)
            out = run_map(root, ".", "--max-file-bytes", "2048", "--max-total-bytes", "900").stdout
            self.assertIn("truncated by configured safety limits", out)
            self.assertLessEqual(sum(1 for line in out.splitlines() if ".py" in line), 1)

    def test_filters_non_code_secret_and_binary_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            samples = {
                "credentials.json": "{\"token\":\"secret\"}",
                "service-account.json": "{\"key\":\"secret\"}",
                "private.pem": "SECRET",
                "id_rsa": "SECRET",
                "dump.sql": "select secret;",
                "notes.txt": "secret",
                "config.yaml": "secret: yes",
            }
            for name, text in samples.items():
                (root / name).write_text(text)
            (root / "blob.py").write_bytes(b"\x00\x01def fake(): pass\n")
            (root / "safe.py").write_text("def safe_symbol(): pass\n")
            out = run_map(root).stdout
            self.assertIn("safe.py", out)
            self.assertIn("safe_symbol", out)
            for name in samples:
                self.assertNotIn(name, out)
            self.assertNotIn("blob.py", out)
            self.assertNotIn("fake", out)

    def test_output_is_relative_and_sanitizes_controls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            name = "evil\nname.py"
            (root / name).write_text("def control_name(): pass\n")
            out = run_map(root).stdout
            self.assertIn("# Repo map: .", out)
            self.assertNotIn(str(root), out)
            self.assertIn("evil?name.py", out)
            self.assertNotIn("evil\nname.py", out)

    def test_deterministic_output_and_no_execution_primitives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "b.py").write_text("def b(): pass\n")
            (root / "a.py").write_text("def a(): pass\n")
            first = run_map(root).stdout
            second = run_map(root).stdout
            self.assertEqual(first, second)
            text = HELPER.read_text()
            for forbidden in ("subprocess", "socket", "urllib", "requests", "os.system", "eval(", "exec("):
                self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
