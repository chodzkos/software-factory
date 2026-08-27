#!/usr/bin/env python3
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"


def load_json_yaml(path: Path):
    return json.loads(path.read_text())


class FactorySkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_json_yaml(SKILLS / "manifest.yaml")
        cls.profiles = load_json_yaml(SKILLS / "profiles.yaml")
        cls.scenarios = json.loads((SKILLS / "tests" / "routing_scenarios.json").read_text())

    def test_manifest_paths_exist(self):
        for name, spec in self.manifest["skills"].items():
            self.assertRegex(name, r"^[a-z0-9][a-z0-9-]*$")
            self.assertEqual(spec["source"], "custom")
            self.assertEqual(spec["path"], f"skills/custom/{name}")
            skill = ROOT / spec["path"] / "SKILL.md"
            self.assertTrue(skill.is_file(), f"missing {name}: {skill}")

    def test_upstream_is_fail_closed_until_pinned(self):
        policy = self.manifest["upstream_policy"]
        self.assertFalse(policy["enabled"])
        self.assertEqual(
            policy["required_fields_when_enabled"],
            ["repository", "commit", "path", "sha256", "vetted"],
        )

    def test_profile_references_are_declared(self):
        declared = set(self.manifest["skills"])
        for profile, policy in self.profiles["profiles"].items():
            for skill in policy["required"] + policy["optional"]:
                self.assertIn(skill, declared, f"{profile}: undeclared {skill}")
                self.assertIn(profile, self.manifest["skills"][skill]["profiles"])

    def test_router_cannot_be_global_orchestrator(self):
        router_profiles = self.manifest["skills"]["task-skill-router"]["profiles"]
        self.assertEqual(router_profiles, ["task-decomposer"])
        self.assertNotIn("orchestrator", router_profiles)
        self.assertEqual(self.profiles["forbidden_global_routing"], ["task-skill-router"])

    def test_merge_gate_is_required_release_manager_only(self):
        release = self.profiles["profiles"]["release-manager"]
        self.assertIn("pr-merge-gate", release["required"])
        self.assertNotIn("pr-merge-gate", release["optional"])
        self.assertEqual(
            self.manifest["skills"]["pr-merge-gate"]["profiles"],
            ["release-manager"],
        )
        for profile, policy in self.profiles["profiles"].items():
            if profile == "release-manager":
                continue
            self.assertNotIn("pr-merge-gate", policy["required"] + policy["optional"])

    def test_workspace_skill_forbids_second_worktree(self):
        text = (SKILLS / "custom" / "workspace-integrity" / "SKILL.md").read_text()
        self.assertIn("do **not** run `git worktree add`", text)
        self.assertIn("validate-runtime", text)
        self.assertIn("validate-handoff", text)

    def test_merge_gate_pins_reviewed_and_verified_head(self):
        text = (SKILLS / "custom" / "pr-merge-gate" / "SKILL.md").read_text()
        block = text.split("## Block when", 1)[1].split("\n## ", 1)[0]
        self.assertIn("- `REVIEWED_SHA != PR_HEAD_SHA`", block)
        self.assertIn("- `VERIFIED_SHA != PR_HEAD_SHA`", block)
        self.assertIn("MERGE_GATE_BLOCKED", text)

    def test_sha_integrity_records_current_sha(self):
        text = (SKILLS / "custom" / "sha-integrity-check" / "SKILL.md").read_text()
        self.assertIn("CURRENT_SHA", text)
        self.assertIn("`REVIEWED_SHA != PR_HEAD_SHA` → block", text)
        self.assertIn("`VERIFIED_SHA != PR_HEAD_SHA` → block", text)

    def test_evidence_ledger_preserves_fail_closed_fields(self):
        text = (SKILLS / "custom" / "evidence-ledger" / "SKILL.md").read_text()
        for field in (
            "Current SHA:",
            "PR HEAD SHA:",
            "Reviewed SHA:",
            "Verified SHA:",
            "Unverified:",
            "Blocking items:",
        ):
            self.assertIn(field, text)
        self.assertIn("Never invent missing evidence", text)
        self.assertIn("Missing mandatory evidence stays missing", text)

    def test_routing_scenarios_fit_profile_policy(self):
        profile_map = self.profiles["profiles"]
        for scenario in self.scenarios:
            allowed = set(
                profile_map[scenario["profile"]]["required"]
                + profile_map[scenario["profile"]]["optional"]
            )
            must_include = set(scenario["must_include"])
            must_not = set(scenario["must_not_include"])
            self.assertTrue(must_include.issubset(allowed), scenario["id"])
            self.assertFalse(must_not & allowed, scenario["id"])
            self.assertFalse(must_include & must_not, scenario["id"])


if __name__ == "__main__":
    unittest.main()
