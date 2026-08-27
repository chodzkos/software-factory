---
name: workspace-integrity
description: Verify the live Kanban-assigned workspace/worktree before code changes; never create a second worktree for an already assigned task.
---

# Workspace Integrity

Use for code-changing Kanban tasks.

## Procedure

1. Read the live task state.
2. Require `workspace_kind=worktree` when the task contract requires isolation.
3. Resolve and record the live `workspace_path`.
4. Confirm the path exists, is a Git worktree, belongs to the expected repository, and has the expected branch/HEAD.
5. Preserve pre-existing dirty content; do not reset, clean, stash, move, or overwrite unknown changes.
6. Work only in the assigned workspace.

## Hard rules

- If Kanban already assigned a worktree, do **not** run `git worktree add` and do not create a second workspace.
- Workspace/path/branch/SHA drift is a contract failure; stop and route through the existing runtime-controller/Kanban policy.
- This skill does not bypass `validate-runtime` or `validate-handoff`.

Output `WORKSPACE_INTEGRITY_OK` only when the live assignment and Git state match; otherwise return a concrete blocking reason.
