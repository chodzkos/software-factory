---
name: task-skill-router
description: Task-decomposer-only helper that selects the smallest allowed skill set for one task without replacing Hermes Kanban orchestration.
---

# Task Skill Router

Use only inside `task-decomposer` work. It is not a global orchestrator.

Inputs: task type, risk, task contract, profile, workspace requirements, required evidence and relevant repository facts.

## Procedure

1. Classify the task (`bug_fix`, `feature`, `security_fix`, `ci_failure`, `audit`, `prompt_model`, `pre_merge`, or explicit project-specific type).
2. Select the implementing/review profile using the existing Kanban/profile policy.
3. From `skills/profiles.yaml`, select only skills allowed for that profile.
4. Add optional skills only when task evidence/risk justifies them.
5. Emit required evidence, forbidden actions and expected gate/state transition.
6. Unknown mandatory routing information fails closed; do not guess a privilege or bypass.

Never create cards, assign reviewers, create worktrees, transition Kanban state, merge PRs, or emulate runtime-controller decisions. Those responsibilities remain with the existing Software Factory contracts.
