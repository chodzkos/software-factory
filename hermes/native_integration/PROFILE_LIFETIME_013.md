# PR23 / 0.13.0 — ROUND3: reviewer/release lifetime and approval ordering

SOURCE ONLY. NO_MERGE / NO_DEPLOY / NOT_FOR_INSTALL.
This design note does not certify tests, a provider, installed bytes or release.
The incoming reviewed manifest is dcce2181b308c66d90383100dfbcc4805e8e4cfdf3c12307f520e7e27aaeac7d.

## New contract

After both startup checks, reviewer-gpt and release-manager bind the same external
agent-state lifetime mechanism as coder-claude, using their own profile proof.
The proof retains registry, both module generations, canonical home and authority
related environment bindings. Runtime verification checks both plugin filesets,
configuration, entries, callback identities, tool definitions and captured scope.
Changes detected at a boundary revoke the existing object; restoring a file,
entry or environment variable does not revive it. Only the startup path may bind.

Both pre-hooks (guard then readonly) run as mandatory callbacks at final registry
dispatch. The guard's post-hook runs once. Optional manager delivery is suppressed
for bound scopes, without dropping readonly's required callback. Unknown/interactive
hook directives cannot grant authority. General/unprotected agents keep native
behavior. This is not protection against arbitrary Python memory manipulation.

The newly bound profiles inherit the model capability boundary and abandonment
semantics. Explicit/resolved/per-turn MoA is unsupported; nonempty extra_body and
unreviewed header extensions are refused. Tests of a Bedrock-format local builder
and a mock callback are NOT proof of real Bedrock/OpenAI/Codex compatibility.
Actual selected providers, SDKs and transport paths remain open gates.

## Approval transaction

A valid seal alone no longer grants write authority to a raw helper. The current
operation must be used/admitted/not finished, belong to reviewer-gpt, reference the
actual loaded handoff function, and execute on its admitted thread. Copying a
ContextVar does not authorize another thread. Release has no approval capability.

The helper owns BEGIN IMMEDIATE. It checks current task/run/workspace/seal/receipt,
current PID on BOTH rows, a matching nonempty claim lock, and explicit environment
claim lock when present. Heartbeat/expiry refresh is not frozen. It preserves the
native savepoint, complete_task and final approved-byte validation. Finally,
commit_approval checks the same operation and holds decision_lock over COMMIT.

Revoke winning before the decision section refuses COMMIT and rolls back the open
transaction. COMMIT winning first may remain durable despite a subsequent close.
COMMIT failure/lost acknowledgement revokes with outcome unknown. No automatic
retry or fabricated rollback is allowed. Failed rollback also revokes. Standalone
read-only receipt/approval consumers remain callable without a live agent.

Lock order: discovery -> existing mutation lease / DB preparation -> decision.
Revocation uses ONLY decision. No network, arbitrary handler, file preparation or
application hooks are added inside decision. Close can wait for a winning COMMIT;
there is no guaranteed SQLite/disk response time, universal thread cancellation,
or proof for arbitrary reentrant SQLite callbacks, process/disk crashes or every
native auxiliary side effect. Full-source/bytecode trust remains outside this gate.

## Evidence scope

The new collection is hermes/tests_profile_lifetime (40 methods). It covers both
real native constructors, both plugin unloads, close/revoke, scope restoration,
raw calls, exact hook counts, final-call model checks, explicit/resolved MoA,
approval success and durable downstream read, close/interrupt before and after
COMMIT, sequential/concurrent timeout, SQL COMMIT rejection, lost acknowledgement,
same-thread unload after real SQL, replay/thread mismatch, and stale PID/claim.
It also isolates the ROUND2 H-D final-call test and file-without-nonce rejection.
Expected discovery counts live in ci/gates.json; actual runs are separate logs.

NativeReviewWorker.attest writes synthetic evidence and directly injects a nonce
unless register_nonce=False. This bypasses production of CLI provenance. The new
negative proves a well-formed file alone is insufficient, NOT that a production
nonce can only be earned by Claude. No dispatcher/Claude/provider is executed.
Claims and implementer PID reuse are explicit fixtures. Database schema, native
SQL, loaders, registry, constructors and final COMMIT are real. The connection
factory selects a controlled temporary WAL database; not production policy.

Timeout ordering tests disable ONLY the agent's _touch_activity telemetry because
it writes independent heartbeats, outside the approval transaction. They still
execute the real approval SQL and compare all rows of three tables. These tests
do not claim all auxiliary writes after abandonment are prevented. Every started
controller/future is drained before fixture cleanup. Phase events determine
ordering; watchdogs merely bound a broken test. SDK endpoints are mocks.

The retained main atomic-approval tests now use an actual reviewer agent and
registered tool rather than raw helper access. Both original mutation phases must
still occur, and full-table rollback assertions remain. The isolated 97-case suite
uses explicit synthetic reviewer admission only in its setup; its consumer test
bodies are unchanged. The earlier startup test now expects mandatory raw-dispatch
refusal before normalization, retaining native-complete-not-called checks.

## Still open

Independent review of these exact new bytes; full native dependency audit;
cache-safe launcher and executed-bytecode identity; native auxiliary / retry /
fallback / SDK paths and selected provider; actual dispatcher and Claude;
Python 3.11 execution, clean locked dependency install and wheel; hosted Actions;
production overlay installer. The status of an unchanged readonly plugin does not
approve the combined runtime. Do not install or publish this candidate.
