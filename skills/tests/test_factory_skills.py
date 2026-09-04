#!/usr/bin/env python3
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
UPSTREAM_COMMIT = "aa71bee8d20b7febdfd49f3aa96f26f316344628"
UPSTREAM_REPOSITORY = "mohitagw15856/pm-claude-skills"
UPSTREAM_DIGESTS = {
    "bug-diagnosis": "fa14879f7924234d710ed0b9890ca004caaef6cd448936a1d2f780b55f14259f",
    "tdd-workflow": "55ddcbf38feff891b811e5b7027c0b5efebc65831bc5f7d599c62cbd19561e1a",
    "ai-code-review": "2bb60f6f1ef619a6b48390b320cbf30a85fb233686d2a64c8e4b90e8d521a7ba",
}


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
            self.assertIn(source, {"custom", "custom-multifile", "upstream-vendored"})
            expected_root = "upstream" if source == "upstream-vendored" else "custom"
            self.assertEqual(spec["path"], f"skills/{expected_root}/{name}")
            skill = ROOT / spec["path"] / "SKILL.md"
            self.assertTrue(skill.is_file(), f"missing {name}: {skill}")
            self.assertFalse(skill.is_symlink(), f"symlink skill source: {name}")

    def test_upstream_policy_is_vendored_only(self):
        policy = self.manifest["upstream_policy"]
        self.assertTrue(policy["enabled"])
        self.assertEqual(policy["mode"], "vendored-only")
        self.assertFalse(policy["network_install"])
        self.assertEqual(policy["repository_allowlist"], [UPSTREAM_REPOSITORY])
        self.assertEqual(policy["required_fields_when_enabled"], ["repository", "commit", "path", "sha256", "vetted"])

    def test_upstream_bytes_are_exactly_pinned(self):
        for name, digest in UPSTREAM_DIGESTS.items():
            path = SKILLS / "upstream" / name / "SKILL.md"
            self.assertTrue(path.is_file(), name)
            self.assertFalse(path.is_symlink(), name)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, name)

    def test_only_conflict_free_upstream_is_installable(self):
        upstream = {name: spec for name, spec in self.manifest["skills"].items() if spec["source"] == "upstream-vendored"}
        self.assertEqual(set(upstream), {"bug-diagnosis"})
        spec = upstream["bug-diagnosis"]
        provenance = spec["upstream"]
        self.assertEqual(provenance["repository"], UPSTREAM_REPOSITORY)
        self.assertEqual(provenance["commit"], UPSTREAM_COMMIT)
        self.assertEqual(provenance["path"], "skills/bug-diagnosis/SKILL.md")
        self.assertEqual(provenance["sha256"], UPSTREAM_DIGESTS["bug-diagnosis"])
        self.assertIs(provenance["vetted"], True)
        entries = sorted(p.name for p in (ROOT / spec["path"]).iterdir())
        self.assertEqual(entries, ["SKILL.md"])

    def test_conflicting_upstream_is_reference_only_with_adapters(self):
        refs = self.manifest["upstream_references"]
        self.assertEqual(set(refs), {"tdd-workflow", "ai-code-review", "repo-map"})
        for name, adapter in {"tdd-workflow": "factory-tdd-workflow", "ai-code-review": "factory-ai-code-review"}.items():
            ref = refs[name]
            self.assertFalse(ref["installable"])
            self.assertEqual(ref["adapter"], adapter)
            self.assertEqual(ref["repository"], UPSTREAM_REPOSITORY)
            self.assertEqual(ref["commit"], UPSTREAM_COMMIT)
            self.assertEqual(ref["sha256"], UPSTREAM_DIGESTS[name])
            self.assertNotIn(name, self.manifest["skills"])
            self.assertIn(adapter, self.manifest["skills"])
        repo_map = refs["repo-map"]
        self.assertFalse(repo_map["installable"])
        self.assertFalse(repo_map["vetted"])
        self.assertEqual(repo_map["review_status"], "replaced-by-factory-fork")
        self.assertEqual(repo_map["adapter"], "factory-repo-map")
        self.assertNotIn("repo-map", self.manifest["skills"])
        self.assertIn("factory-repo-map", self.manifest["skills"])

    def test_profile_references_are_declared(self):
        declared = set(self.manifest["skills"])
        for profile, policy in self.profiles["profiles"].items():
            for skill in policy["required"] + policy["optional"]:
                self.assertIn(skill, declared, f"{profile}: undeclared {skill}")
                self.assertIn(profile, self.manifest["skills"][skill]["profiles"])
                self.assertIsNot(self.manifest["skills"][skill].get("installable", True), False)

    def test_first_upstream_batch_is_least_privilege(self):
        profiles = self.profiles["profiles"]
        self.assertIn("bug-diagnosis", profiles["coder"]["optional"])
        self.assertIn("factory-tdd-workflow", profiles["coder"]["optional"])
        self.assertNotIn("tdd-workflow", profiles["coder"]["required"] + profiles["coder"]["optional"])
        self.assertIn("factory-ai-code-review", profiles["quick-reviewer"]["optional"])
        self.assertIn("factory-ai-code-review", profiles["critic"]["optional"])
        self.assertNotIn("ai-code-review", profiles["quick-reviewer"]["required"] + profiles["quick-reviewer"]["optional"])
        for privileged in ("orchestrator", "runtime-controller", "release-manager", "task-decomposer"):
            granted = set(profiles[privileged]["required"] + profiles[privileged]["optional"])
            self.assertFalse({"bug-diagnosis", "factory-tdd-workflow", "factory-ai-code-review"} & granted, privileged)

    def test_factory_repo_map_activation_is_fail_closed(self):
        spec = self.manifest["skills"]["factory-repo-map"]
        self.assertEqual(spec["source"], "custom-multifile")
        self.assertEqual(spec["profiles"], [])
        self.assertIs(spec["installable"], False)
        self.assertEqual(spec["activation_status"], "blocked-on-runtime-isolation")
        for profile, policy in self.profiles["profiles"].items():
            self.assertNotIn("factory-repo-map", policy["required"] + policy["optional"], profile)
        self.assertEqual(set(spec["files"]), {"SKILL.md", "REVIEW.md", "scripts/repo_map.py", "scripts/run_repo_map.py"})
        for rel, pin in spec["files"].items():
            self.assertRegex(pin["git_blob_sha1"], r"^[0-9a-f]{40}$", rel)

    def test_tdd_adapter_overrides_commit_per_green(self):
        text = (SKILLS / "custom" / "factory-tdd-workflow" / "SKILL.md").read_text()
        self.assertIn("Do **not** commit at each green cycle", text)
        self.assertIn("Kanban-assigned workspace/worktree", text)
        self.assertIn("exact current SHA", text)
        self.assertIn("one logical task/change per branch and PR", text)

    def test_ai_review_adapter_uses_canonical_decision_contract(self):
        text = (SKILLS / "custom" / "factory-ai-code-review" / "SKILL.md").read_text()
        self.assertIn("DECISION: APPROVE", text)
        self.assertIn("DECISION: CHANGES_REQUIRED", text)
        self.assertIn("kanban_request_changes", text)
        self.assertIn("severity: LOW|MEDIUM|HIGH|CRITICAL", text)
        self.assertIn("approve with required fixes", text)
        sys.path.insert(0, str(ROOT / "hermes"))
        try:
            from review_decision import parse_review
            result = parse_review("severity: HIGH\nDECISION: APPROVE\n")
            self.assertEqual(result.status, "REVIEW_PENDING")
            self.assertEqual(result.reason, "approve_with_blocking_finding")
            result = parse_review("severity: MEDIUM\nDECISION: CHANGES_REQUIRED\n")
            self.assertEqual(result.status, "CHANGES_REQUIRED")
        finally:
            sys.path.pop(0)

    def test_router_cannot_be_global_orchestrator(self):
        router_profiles = self.manifest["skills"]["task-skill-router"]["profiles"]
        self.assertEqual(router_profiles, ["task-decomposer"])
        self.assertNotIn("orchestrator", router_profiles)
        self.assertEqual(self.profiles["forbidden_global_routing"], ["task-skill-router"])

    def test_merge_gate_is_required_release_manager_only(self):
        release = self.profiles["profiles"]["release-manager"]
        self.assertIn("pr-merge-gate", release["required"])
        self.assertNotIn("pr-merge-gate", release["optional"])
        self.assertEqual(self.manifest["skills"]["pr-merge-gate"]["profiles"], ["release-manager"])
        for profile, policy in self.profiles["profiles"].items():
            if profile == "release-manager": continue
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
        self.assertIn("verify-approval --board <BOARD_SLUG> --task-id <TASK_ID>", text)
        self.assertIn("live mutation lease", text)

    def test_sha_integrity_records_current_sha(self):
        text = (SKILLS / "custom" / "sha-integrity-check" / "SKILL.md").read_text()
        self.assertIn("CURRENT_SHA", text)
        self.assertIn("`REVIEWED_SHA != PR_HEAD_SHA` → block", text)
        self.assertIn("`VERIFIED_SHA != PR_HEAD_SHA` → block", text)

    def test_evidence_ledger_preserves_fail_closed_fields(self):
        text = (SKILLS / "custom" / "evidence-ledger" / "SKILL.md").read_text()
        for field in ("Current SHA:", "PR HEAD SHA:", "Reviewed SHA:", "Verified SHA:", "Unverified:", "Blocking items:"):
            self.assertIn(field, text)
        lowered = text.lower()
        self.assertIn("never invent missing evidence", lowered)
        self.assertIn("missing mandatory evidence stays missing", lowered)

    def test_verifier_resolves_manifest_paths_for_installed_skills(self):
        text = (ROOT / "hermes" / "verify_factory_skills.sh").read_text()
        self.assertIn("spec['source']", text)
        self.assertIn("installed profile skills", text)
        self.assertIn("installed upstream skill digest drift", text)
        self.assertIn("installed skill contains symlink", text)
        self.assertIn("installed multifile blob drift", text)

    def test_routing_scenarios_fit_profile_policy(self):
        profile_map = self.profiles["profiles"]
        for scenario in self.scenarios:
            allowed = set(profile_map[scenario["profile"]]["required"] + profile_map[scenario["profile"]]["optional"])
            must_include = set(scenario["must_include"])
            must_not = set(scenario["must_not_include"])
            may_include = set(scenario.get("may_include", []))
            self.assertTrue(must_include.issubset(allowed), scenario["id"])
            self.assertTrue(may_include.issubset(allowed), scenario["id"])
            self.assertFalse(must_not & allowed, scenario["id"])
            self.assertFalse((must_include | may_include) & must_not, scenario["id"])


if __name__ == "__main__":
    unittest.main()
