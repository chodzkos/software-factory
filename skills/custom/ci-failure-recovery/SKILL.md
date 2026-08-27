---
name: ci-failure-recovery
description: Diagnose the earliest causal CI failure, classify it, and change application code only when evidence justifies it.
---

# CI Failure Recovery

Classify the first causal failure as one of:

`CODE_FAILURE`, `TEST_FAILURE`, `LINT_FAILURE`, `TYPE_FAILURE`, `DEPENDENCY_FAILURE`, `ENVIRONMENT_FAILURE`, `FLAKY_TEST`, `CI_INFRA_FAILURE`, `SECURITY_GATE_FAILURE`.

## Rules

1. Identify required failed jobs and the earliest causal error, not a cascade symptom.
2. Reproduce locally when feasible.
3. For code/test defects, use the repository's debugging/TDD procedure and add regression evidence where appropriate.
4. For environment/registry/runner failures, do not modify application code unless evidence proves application code caused the failure.
5. For flaky tests, establish repeatability before weakening assertions/retries.
6. For security gate failures, never disable or lower the gate merely to make CI green.
7. Record local evidence, CI run/job, SHA and re-run result in the evidence ledger.

Return classification, causal evidence, justified next action, and whether code modification is allowed.
