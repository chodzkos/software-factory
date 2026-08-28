#!/usr/bin/env python3
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
MANIFEST = json.loads((SKILLS / "manifest.yaml").read_text())
REPO_MAP = SKILLS / "upstream" / "repo-map"
HELPER = REPO_MAP / "scripts" / "repo_map.py"
EXPECTED_REPOSITORY = "mohitagw15856/pm-claude-skills"
EXPECTED_COMMIT = "aa71bee8d20b7febdfd49f3aa96f26f316344628"
EXPECTED_FILES = {
    "SKILL.md": "9d6923145b22099e604b2ea3888e6f59d215cf98c8c87e00f41c98f5db01c7e0",
    "scripts/repo_map.py": "bf4ccffe145eb9361f60c32aa74c13294a58890e5a2918572dacb3f9f153962d",
}


class RepoMapReferenceTests(unittest.TestCase):
    def test_reference_is_exactly_pinned_and_not_installable(self):
        ref = MANIFEST["upstream_references"]["repo-map"]
        self.assertEqual(ref["repository"], EXPECTED_REPOSITORY)
        self.assertEqual(ref["commit"], EXPECTED_COMMIT)
        self.assertEqual(ref["path"], "skills/repo-map")
        self.assertEqual(ref["local_path"], "skills/upstream/repo-map")
        self.assertFalse(ref["installable"])
        self.assertFalse(ref["vetted"])
        self.assertEqual(ref["review_status"], "replaced-by-factory-fork")
        self.assertEqual(ref["adapter"], "factory-repo-map")
        self.assertEqual(ref["files"], EXPECTED_FILES)
        self.assertNotIn("repo-map", MANIFEST["skills"])
        candidate = MANIFEST["skills"]["factory-repo-map"]
        self.assertIs(candidate["installable"], False)
        self.assertEqual(candidate["profiles"], [])
        self.assertEqual(candidate["activation_status"], "blocked-on-runtime-isolation")

    def test_reference_tree_has_only_allowlisted_regular_files(self):
        self.assertTrue(REPO_MAP.is_dir())
        self.assertFalse(REPO_MAP.is_symlink())
        actual = []
        for path in sorted(REPO_MAP.rglob("*")):
            rel = path.relative_to(REPO_MAP).as_posix()
            if path.is_dir():
                self.assertFalse(path.is_symlink(), rel)
                continue
            self.assertTrue(path.is_file(), rel)
            self.assertFalse(path.is_symlink(), rel)
            actual.append(rel)
        self.assertEqual(actual, sorted(EXPECTED_FILES))

    def test_each_vendored_file_matches_its_sha256(self):
        for rel, expected in EXPECTED_FILES.items():
            path = REPO_MAP / rel
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, expected, rel)

    def test_helper_uses_stdlib_read_only_primitives(self):
        text = HELPER.read_text()
        for required in ("import argparse, os, re, sys", "os.walk", "open(fp", "errors=\"replace\""):
            self.assertIn(required, text)
        for forbidden in ("subprocess", "socket", "urllib", "requests", "http.client", "shutil.rmtree", "os.remove", "os.unlink", "os.rename", "os.replace", "os.system", "eval(", "exec("):
            self.assertNotIn(forbidden, text)

    def test_helper_skip_defect_remains_pinned_in_raw_reference(self):
        """Raw upstream remains unsafe audit material; Factory fork is also runtime-disabled pending isolation."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src").mkdir(); (root / ".git").mkdir(); (root / "node_modules").mkdir()
            (root / "src" / "app.py").write_text("def alpha():\n    return 1\n\nclass Beta:\n    pass\n")
            (root / ".git" / "secret.py").write_text("def hidden_git(): pass\n")
            (root / "node_modules" / "hidden.js").write_text("function hidden_module() {}\n")
            run = subprocess.run([sys.executable, str(HELPER), str(root), "--max-files", "20", "--max-symbols", "8"], check=True, text=True, capture_output=True, timeout=10)
            out = run.stdout
            self.assertIn("app.py", out); self.assertIn("alpha", out); self.assertIn("Beta", out)
            self.assertIn("secret.py", out); self.assertIn("hidden_git", out); self.assertIn("node_modules", out); self.assertIn("hidden_module", out)

    def test_helper_max_files_guard_truncates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for index in range(3):
                (root / f"f{index}.py").write_text(f"def f{index}(): pass\n")
            run = subprocess.run([sys.executable, str(HELPER), str(root), "--max-files", "1"], check=True, text=True, capture_output=True, timeout=10)
            self.assertIn("truncated at 1 files", run.stdout)


if __name__ == "__main__":
    unittest.main()
