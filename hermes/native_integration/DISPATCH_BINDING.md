# Factory coder claim/worktree binding

Source-only candidate. NO_MERGE / NO_DEPLOY / NOT_FOR_INSTALL.

Ready dispatch for `coder-claude` now commits `task_id` and the resolved
`workspace_path` into the current native run before invoking the spawn function.
A `BEGIN IMMEDIATE` transaction verifies the exact claimed run and claim lock,
active coder status, not-yet-published PID and canonical persisted worktree.
Missing metadata is initialized; conflicting, duplicate-key, malformed or
non-object metadata is refused, not repaired. Other metadata is preserved.
An already-consistent pre-spawn binding is idempotent. Non-factory profiles
retain native behavior. Review dispatch is unchanged.

Refusal aborts the dispatch call before spawning. It deliberately does not use
the generic task-id-only spawn-failure handler, so it cannot reset a successor
claim. The retained native workspace/branch setters run before this boundary;
this increment is not a full CAS redesign of those setters, PID publication or
all queue operations. Normal native TTL/recovery handles an unspawned retained
claim; there is no automatic retry introduced by this change.

The binding is NOT Claude execution evidence, a handoff receipt or permission
to approve a review. The guard still requires board/task/worktree identity,
PID ancestry and the usual evidence/transaction checks. It has not been loosened.

Tests use real native schema, claim_task, dispatch_once, worktree creation and
_default_spawn with inert/guard-only children; one child proves its own PID
identity through the unchanged guard. They do NOT execute an AIAgent conversation,
Claude, a provider or a production board. The waiting loop observes the native
PID publication with a bounded deadline rather than assuming a fixed sleep.

The previous 670 tests remain collected; a separate 25-case collection is added.
All previous evidence remains scoped to its exact source bytes. New source
requires full regression and independent increment review before live use.
