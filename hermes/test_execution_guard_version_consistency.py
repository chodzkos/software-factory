from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hermes.verify_execution_guard_version import collect_consistency_errors


ROOT = Path(__file__).resolve().parent.parent


class ExecutionGuardVersionConsistencyTests(unittest.TestCase):
    def test_authoritative_documents_match_plugin_version_and_schema(self):
        self.assertEqual(collect_consistency_errors(ROOT), [])

    def test_former_stale_v070_active_policy_fails_semantic_verifier(self):
        policy = ROOT / "workflows" / "MODEL_ROUTING_POLICY.md"
        stale = policy.read_text(encoding="utf-8").replace(
            "`factory-execution-guards` v0.10.0",
            "`factory-execution-guards` v0.7.0",
            1,
        )
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "MODEL_ROUTING_POLICY.md"
            override.write_text(stale, encoding="utf-8")
            errors = collect_consistency_errors(
                ROOT,
                document_overrides={"workflows/MODEL_ROUTING_POLICY.md": override},
            )
        self.assertTrue(any("MODEL_ROUTING_POLICY.md" in error for error in errors), errors)
        self.assertTrue(any("current guard version" in error for error in errors), errors)

    def test_stale_handoff_schema_fails_semantic_verifier(self):
        policy = ROOT / "workflows" / "MODEL_ROUTING_POLICY.md"
        stale = policy.read_text(encoding="utf-8").replace(
            "adding exact active-run authorization, handoff schema v1,",
            "adding exact active-run authorization, handoff schema v2,",
            1,
        )
        with tempfile.TemporaryDirectory() as td:
            override = Path(td) / "MODEL_ROUTING_POLICY.md"
            override.write_text(stale, encoding="utf-8")
            errors = collect_consistency_errors(
                ROOT,
                document_overrides={"workflows/MODEL_ROUTING_POLICY.md": override},
            )
        self.assertTrue(any("handoff schema" in error for error in errors), errors)


if __name__ == "__main__":
    unittest.main()
