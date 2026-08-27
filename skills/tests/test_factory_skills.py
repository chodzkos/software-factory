#!/usr/bin/env python3
import hashlib
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
UPSTREAM_COMMIT = "aa71bee8d20b7febdfd49f3aa96f26f316344628"
UPSTREAM_REPOSITORY = "mohitagw15856/pm-claude-skills"


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
            source = spec["source"]
            self.assertIn(source, {"custom", "upstream-vendored"})
            expected_root = "custom" if source == "custom" else "upstream"
            self.assertEqual(spec["path"], f"skills/{expected_root}/{name}")
            skill = ROOT / spec["path"] / "SKILL.md"
            self.assertTrue(skill.is_file(), f"missing {name}: {skill}")

    def test_upstream_policy_is_vendored_only(self):
        policy = self.manifest["upstream_policy"]
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["mode"], "vendored-only")
        self.assertFalse(policy["network_install"])
        self.assertEqual(
            policy["required_fields_when_enabled"],
            ["repository", "commit", "path", "sha256", "vetted"],
        )

    def test_upstream_batch_is_exactly_pinned_and_hashed(self):
        upstream = {
            name: spec
            for name, spec in self.manifest["skills"].items()
            if spec["source"] == "upstream-vendored"
        }
        self.assertEqual(set(upstream), {"bug-diagnosis", "tdd-workflow", "ai-code-review"})
        for name, spec in upstream.items():
            provenance = spec["upstream"]
            self.assertEqual(provenance["repository"], UPSTREAM_REPOSITORY)
            self.assertEqual(provenance["commit"], UPSTREAM_COMMIT)
            self.assertRegex(provenance["commit"], r"^[0-9a-f]{40}$")
            self.assertEqual(provenance["path"], f"skills/{name}/SKILL.md")
            self.assertIs(provenance["vetted"], True)
            self.assertRegex(provenance["sha256"], r"^[0-9a-f]{64}$")
            actual = hashlib.sha256((ROOT / spec["path"] / "SKILL.md").read_bytes()).hexdigest()
            self.assertEqual(actual, provenance["sha256"], name)

    def test_profile_references_are_declared(self):
        declared = set(self.manifest["skills"])
        for profile, policy in self.profiles["profiles"].items():
            for skill in policy["required"] + policy["optional"]:
                self.assertIn(skill, declared, f"{profile}: undeclared {skill}")
                self.assertIn(profile, self.manifest["skills"][skill]["profiles"])

    def test_first_upstream_batch_is_least_privilege(self):
        profiles = self.profiles["profiles"]
        self.assertIn("bug-diagnosis", profiles["coder"]["optional"])
        self.assertIn("tdd-workflow", profiles["coder"]["optional"])
        self.assertNotIn("ai-code-review", profiles["coder"]["required"] + profiles["coder"]["optional"])
        self.assertIn("ai-code-review", profiles["quick-reviewer"]["optional"])
        self.assertIn("ai-code-review", profiles["critic"]["optional"])
        for privileged in ("orchestrator", "runtime-controller", "release-manager", "task-decomposer"):
            granted = profiles[privileged]["required"] + profiles[privileged]["optional"]
            self.assertFalse({"bug-diagnosis", "tdd-workflow", "ai-code-review"} & set(granted), privileged)

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
        lowered = text.lower()
        self.assertIn("never invent missing evidence", lowered)
        self.assertIn("missing mandatory evidence stays missing", lowered)

    def test_verifier_resolves_manifest_paths_for_installed_skills(self):
        text = (ROOT / "hermes" / "verify_factory_skills.sh").read_text()
        self.assertIn("manifest['skills'][skill]['path']", text)
        self.assertIn("installed profile skills", text)

    def test_routing_scenarios_fit_profile_policy(self):
        profile_map = self.profiles["profiles"]
        for scenario in self.scenarios:
            allowed = set(
                profile_map[scenario["profile"]]["required"]
                + profile_map[scenario["profile"]]["optional"]
            )
            must_include = set(scenario["must_include"])
            must_not = set(scenario["must_not_include"])
            may_include = set(scenario.get("may_include", []))
            self.assertTrue(must_include.issubset(allowed), scenario["id"])
            self.assertTrue(may_include.issubset(allowed), scenario["id"])
            self.assertFalse(must_not & allowed, scenario["id"])
            self.assertFalse((must_include | may_include) & must_not, scenario["id"])


if __name__ == "__main__":
    unittest.main()
