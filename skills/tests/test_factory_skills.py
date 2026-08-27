#!/usr/bin/env python3
import json
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
            self.assertEqual(spec["source"], "custom")
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

    def test_workspace_skill_forbids_second_worktree(self):
        text = (SKILLS / "custom" / "workspace-integrity" / "SKILL.md").read_text()
        self.assertIn("do **not** run `git worktree add`", text)
        self.assertIn("validate-runtime", text)
        self.assertIn("validate-handoff", text)

    def test_merge_gate_pins_reviewed_and_verified_head(self):
        text = (SKILLS / "custom" / "pr-merge-gate" / "SKILL.md").read_text()
        self.assertIn("`REVIEWED_SHA != PR_HEAD_SHA`", text)
        self.assertIn("`VERIFIED_SHA != PR_HEAD_SHA`", text)
        self.assertIn("MERGE_GATE_BLOCKED", text)

    def test_routing_scenarios_fit_profile_policy(self):
        profile_map = self.profiles["profiles"]
        for scenario in self.scenarios:
            allowed = set(profile_map[scenario["profile"]]["required"] + profile_map[scenario["profile"]]["optional"])
            self.assertTrue(set(scenario["must_include"]).issubset(allowed), scenario["id"])
            self.assertFalse(set(scenario["must_include"]) & set(scenario["must_not_include"]), scenario["id"])


if __name__ == "__main__":
    unittest.main()
