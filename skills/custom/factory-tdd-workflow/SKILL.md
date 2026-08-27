---
name: factory-tdd-workflow
description: "Factory-safe TDD adapter for the vendored upstream tdd-workflow. Preserves red-green-refactor while enforcing the Kanban-assigned workspace, exact-SHA handoff, one-logical-change PR policy, and no commit-per-cycle behavior."
---

# Factory TDD Workflow Adapter

Use the red → green → refactor method from the pinned upstream `tdd-workflow` skill, but the Factory authority chain always wins:

`SOFTWARE_DEVELOPMENT_STANDARD -> KANBAN_CONTRACT -> profile/task contract -> skill`.

## Required Factory constraints

- Work only in the Kanban-assigned workspace/worktree.
- Do not create another branch or worktree.
- Run one small RED → GREEN → REFACTOR cycle at a time.
- A RED test must genuinely fail before implementation.
- GREEN adds only enough implementation to pass the current behavior.
- Refactor only while tests are green.
- Do **not** commit at each green cycle.
- Commit/push only according to the current task contract and coder SOUL.
- Preserve one logical task/change per branch and PR.
- Before handoff, record the exact current SHA and run the required verification.
- After review is requested, do not move HEAD unless the same-card review flow explicitly returns the card for rework; any new HEAD requires fresh verification/review.

## Output

For each cycle record:

1. RED — test and observed failure.
2. GREEN — minimal implementation and observed pass.
3. REFACTOR — change, if any, with tests still green.
4. Evidence — commands/results relevant to the evidence ledger.

The upstream phrase “commit at each green” is intentionally overridden by Factory policy.
