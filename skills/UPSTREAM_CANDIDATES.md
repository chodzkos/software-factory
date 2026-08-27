# Upstream skill candidates

Source project: `mohitagw15856/pm-claude-skills`.

This file preserves the candidate set from the earlier skill-pipeline analysis. It is **not** an installation manifest and grants no runtime authority. Upstream installation remains disabled in `manifest.yaml` until each selected skill is pinned and vetted.

## Core development

- `repo-map`
- `bug-diagnosis`
- `tdd-workflow`
- `verification-before-completion`
- `ai-code-review`

## Security

- `security-review`
- `threat-model`
- `dependency-audit`
- `skill-vetting`

## Agent / evaluation

- `session-handoff`
- `ai-eval-plan`
- `prompt-regression-suite`

## Later candidates

- `agent-design-review`
- `ai-agent-reliability`
- `agent-observability-spec`
- `llm-guardrails-spec`
- `code-review-checklist`
- `vuln-triage`
- `architecture-decision-record`

## Admission rule

Before enabling any upstream skill, add a manifest record containing at least:

- repository
- exact full commit SHA
- exact path
- content SHA-256 (or deterministic tree digest)
- `vetted: true`
- allowed profiles

Review the skill for instructions that conflict with the Software Development Standard, Kanban contract, workspace assignment, independent-review rules, or fail-closed gates. A moving upstream branch is never an install source.
