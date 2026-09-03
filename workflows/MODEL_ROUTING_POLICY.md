# Software Factory Model Routing Policy v1

This policy supplements `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` and `workflows/KANBAN_CONTRACT.md`. The Standard remains authoritative.

## 1. Active implementation backends

### `coder`
- native OpenAI/GPT implementation profile,
- `factory.execution_backend=native-openai`,
- allowed only for `SECURITY_SENSITIVE: no`,
- exact normal reviewer: `reviewer-claude`.

### `coder-claude`
- Hermes coordination profile; actual coding is delegated through `claude-code` to Claude Code CLI,
- `factory.execution_backend=claude-code`, model class pinned to `sonnet`,
- exact reviewer: `reviewer-gpt`,
- required implementer for `SECURITY_SENSITIVE: yes` so mandatory OpenAI security review remains cross-vendor.

Ox Alpha is not an active implementation/review backend.

## 2. Review backends

### `reviewer-gpt`
- native OpenAI independent reviewer,
- provider pinned to `openai-codex`, model pinned to `gpt-5.6-sol`, independent of `PRIMARY_PROFILE` and `ALLOW_NON_GPT_PRIMARY`,
- `fallback_providers=[]` and inherited legacy `fallback_model` is physically removed by bootstrap,
- exact reviewer for `coder-claude`,
- only same-card profile allowed for `SECURITY_SENSITIVE: yes` review.

### `reviewer-claude`
- actual review delegated through `claude-code`, model class pinned to `sonnet`,
- exact reviewer for non-security `coder`,
- mechanically read-only and forbidden for security-sensitive review.

### `critic`
- Grok independent deep reviewer/auditor,
- may be required by a separate mechanically gated audit task,
- is not modeled as a second same-card REQUIRED_REVIEWER because Hermes 0.20.4 cannot mechanically guarantee that sequence.

## 3. Complex architecture escalation

`architect-claude-opus` is optional for hard architecture/reasoning. It delegates read-only analysis through `claude-code` with model class pinned to `opus`. It is never a security reviewer or implementation profile.

## 4. Required task-contract fields

Every code-changing implementation/review contract must state exactly once:

```text
IMPLEMENTER: <profile>
REQUIRED_REVIEWERS: <exact reviewer profile>
SECURITY_SENSITIVE: yes|no
WORKSPACE: worktree:<absolute-base-repository>
```

Live Markdown body is the routing/workspace source of truth. Missing/duplicate fields, malformed reviewer CSV, `none`, unknown profiles or extra reviewers fail closed.

## 5. Mechanical routing matrix

| Implementer | SECURITY_SENSITIVE | Exact REQUIRED_REVIEWERS |
| --- | --- | --- |
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | **forbidden** |
| `coder-claude` | `yes` | `reviewer-gpt` |

Normal review is cross-vendor. Security review is always pinned OpenAI and cross-vendor. Hidden provider/model fallback is forbidden.

## 6. Strict live routing and handoff binding

Pre-create body validation uses:

```text
validate-routing-body --task-body <exact-task-body>
```

Post-create/live validation never trusts caller-supplied JSON. Runtime-controller uses:

```text
validate-routing-live --task-id <task-id>
validate-routed-handoff --task-id <task-id>
dispatch-review --task-id <task-id>
```

`kanban.review_dispatch=false` is mandatory for the Software Factory dispatcher and runtime-controller. Hermes 0.20.4 must not auto-claim the review lane before provenance validation. The first two operations above must succeed while the card is still `status=review`; only then may the exact task be started through `dispatch-review`.

The runtime validator itself executes `hermes kanban show <task-id> --json` and strict-decodes the result. There is no production runtime-controller operation accepting `--actual-json` and no body-independent `validate-handoff` operation.

Routed handoff requires:

- exact `status=review` and reviewer assignee,
- exactly one `WORKSPACE: worktree:<base-repo>` field,
- existing live workspace path exactly `<declared-base>/.worktrees/<task-id>`, canonical absolute, lexical-exact and with no symlink component,
- latest `review_requested` with matching profiles and mandatory true integer `run_id` (JSON booleans rejected),
- latest implementer run with `outcome=review_requested` and identical true integer run ID,
- mandatory run metadata containing exact `task_id` and exact resolved workspace.
- for `coder-claude`, one strict handoff schema v1 record binding the exact HEAD/content, execution evidence schema v6, native event, and PID/start identity, with the implementer process proven exited.

The targeted dispatcher does not trust a previous text result. It re-fetches live state, re-runs routed-handoff validation, requires `review_dispatch=false`, and rechecks task/provenance plus sealed content/process identity under the final writer lock, after the native savepoint claim, and immediately before spawn. The reviewer run metadata binds the handoff schema, seal ID, content digest, and implementer run. Pre-commit drift rolls back; post-commit drift records a spawn failure and launches no reviewer. The helper is pinned to Hermes 0.20.4 and fails closed if primitives drift or disappear. Board-global review dispatch is not exposed.

## 7. Claude Code mechanical execution boundary

Software Factory profiles use profile-scoped `factory-execution-guards` v0.10.0. Version 0.10.0 preserves the reviewed v0.9.0 execution evidence schema v6 and all older predecessor controls while adding exact active-run authorization, handoff schema v1, implementer process-exit proof, and reviewer approval byte binding.

Outer GPT terminal access is **Claude-only**: no `find`, Git, Python, grep or other helper executable is permitted. Direct file/code mutation tools are blocked.

Only literal argv0 `claude` is accepted. `./claude`, `/tmp/claude` and alternate paths are refused. Guard resolves the PATH-selected Claude binary itself and binds its resolved path + SHA-256 to the attestation.

Every invocation requires `--safe-mode`, disabling project/user `CLAUDE.md`, hooks, plugins, skills and MCP. Coder uses `--permission-mode dontAsk`; reviewer/architect use `--permission-mode plan`.

Claude argv uses a closed schema:

- exactly one `-p` or `--print`,
- exact profile model (`sonnet` or `opus`),
- `--output-format json`,
- mandatory `--safe-mode`,
- exact permission mode,
- exact profile-specific `--allowedTools`,
- optional bounded `--max-turns` and low/medium/high `--effort`,
- duplicate/unknown flags, permission bypass, settings, MCP, plugins, resume/worktree/debug and fallback flags are refused.

The model-visible terminal argument object is independently closed. `command` and `workdir` are mandatory; `workdir` must be the byte-for-byte canonical resolved `HERMES_KANBAN_WORKSPACE` string and must resolve strictly to that same path. An optional `timeout` is accepted only as a non-boolean integer from 1 through 600. `background`, PTY, notifications/watchers, caller session/task IDs, environment/host/cwd aliases, and every future unknown key are refused. The hook receives raw model arguments before Hermes materializes handler defaults, so omitted execution-affecting keys cannot be smuggled through a default-expanded object.

Prompt binding is structured, not substring-based. The prompt must contain exactly one line each:

```text
TASK_ID: <exact-task-id>
RUN_ID: <exact-run-id>
WORKSPACE: <exact-resolved-worktree>
```

Quoted multiline prompt arguments are allowed so those exact markers can remain separate lines; newline/CR outside shell quotes is rejected before execution as a command separator. Every Claude terminal call must explicitly set `workdir=<exact resolved HERMES_KANBAN_WORKSPACE>` and must not request background, PTY, notification, session, task, environment, host, or cwd overrides.

`coder-claude` receives read tools plus exactly one workspace-derived edit permission rule:

```text
Read,Glob,Grep,Edit(//<exact-resolved-worktree>/**)
```

The edit rule is computed by the execution guard from the resolved Kanban workspace, not supplied freely by the model. `dontAsk` means file modifications that do not match this exact scoped rule are denied rather than prompting for broader permission. Broad `Write`, broad `Edit`, Bash, Python and Git are not allowed.

`reviewer-claude` and `architect-claude-opus` exact read-only tools:

```text
Read,Glob,Grep
```

Reviewer/architect additionally run in permission `plan` mode and receive no Bash/Write/Edit.

### In-process attestation and durable evidence

Before canonical Claude execution, `pre_tool_call` creates a random nonce held only in the trusted Hermes worker process and captures task/run/profile, resolved workspace, exact execution cwd, command SHA-256, canonical `terminal_args_sha256`, Claude binary path+SHA-256, Git HEAD, and a content-state digest. The terminal digest uses UTF-8 canonical JSON with sorted keys, fixed separators and the `software-factory-claude-terminal-args-v1` domain; it includes every accepted argument plus the exact safe foreground defaults.

The content-state digest covers staged diff plus raw bytes/mode/symlink targets for **all cached tracked paths and all untracked paths, including Git-ignored untracked paths**. It does not depend on `git status` visibility; `assume-unchanged`, `skip-worktree`, and `.gitignore` therefore cannot hide workspace content from attestation.

`post_tool_call` may emit evidence only for byte-identical pre/post terminal arguments, the matching pending in-memory attestation, successful terminal `exit_code=0`, and a successful Claude JSON result. In Hermes 0.20.4 an omitted result `cwd` signals that the observed cwd stayed equal to the validated `command_cwd`, so the effective cwd remains the explicit canonical workdir. If result `cwd` is present, it must be a string byte-for-byte and canonically equal to the assigned workspace; malformed, aliased, symlinked, or different values create no evidence. Evidence schema v6 records `execution_cwd`, `terminal_args_sha256`, before/after Git HEAD and content-state digests, plus an attestation ID derived from the in-memory nonce.

A durable JSON file alone cannot unlock lifecycle. `kanban_request_review` / Claude-backed completion requires both matching schema-6 evidence and matching completed attestation still held in the same worker process, including exact execution cwd and terminal-args digest. Current Claude binary identity, resolved workspace, Git HEAD and content-state digest are revalidated immediately before lifecycle transition. Any subsequent workspace-content change or any attempted Claude invocation, including a malformed/rejected call, invalidates the prior attestation.

Before every mutation-capable `coder-claude` tool call, the guard independently reads the board selected by the worker environment and requires the exact task/run/workspace to remain active (`running`, assignee/current run exact, no end/outcome, matching metadata). A second terminal call after review handoff is therefore blocked before process launch, new attestation, or evidence replacement.

Only a strict successful native request-review result plus matching durable task/run/event and unchanged schema-6 evidence creates the separate handoff schema v1 record. It is published atomically without following symlinks and binds task/run/profiles/workspace, HEAD/content, evidence file hash, attestation/command/terminal identities, native event, PID, and Linux process-start token. The in-process seal clears prior authorization; `HERMES_KANBAN_STOP_NUDGE=0` suppresses generic Hermes 0.20.4 nudges only as defense in depth.

Routed validation and dispatch require that exact implementer process to have exited and all sealed bytes to remain unchanged. Final `reviewer-gpt` approval/completion is refused unless its exact reviewer run metadata and current workspace still match the same seal. Requesting changes remains available without falsely approving drifted bytes.

## 8. Runtime-controller mechanical boundary

`runtime-controller` has profile-scoped execution guards, only the terminal toolset, and can execute only installed `kanban_runtime_cli.sh` operations: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing-body`, `validate-routing-live`, `dispatch-review`.

`dispatch-review` accepts exactly `--task-id <task-id>` and launches only the already-routed card after revalidation. It is not a general shell/Python escape and does not expose board-global `hermes kanban dispatch`.

Unquoted literal newline/CR, direct `hermes`, Git, Python, curl, file tools, shell operators and command substitution are blocked. Quoted multiline values remain single argv items and are still validated by the per-operation schema. Live validators/dispatcher accept task IDs, not caller-supplied snapshot bytes.

## 9. Plugin supply-chain transaction

Reviewed profile-scoped plugin installation freezes the current source/pin set plus explicit immutable `replace_from` predecessor pin sets. Publication never reopens the mutable manifest.

Replacement is explicit (`--replace-reviewed`) and is allowed only when the existing target matches current or an approved predecessor reviewed pin set; only `__pycache__/*.pyc` runtime noise is tolerated. Unknown/drifted targets fail closed.

The installer rejects symlinked destination components and target symlinks. Serialization locks the verified destination directory itself, eliminating a symlinkable lock pathname. Stage is re-hashed against the frozen snapshot. Rollback is armed before moving the old target and restores it after post-publication verification failure. A post-commit backup cleanup failure reports an error without deleting the already-verified new target.

## 10. Repository analyst isolation

Canonical `bootstrap_profiles.sh` ends by running `bootstrap_repository_analyst_isolation.sh` and `verify_repository_analyst_isolation.sh --live`. Re-running analyst bootstrap uses controlled reviewed replacement; unknown target state cannot be silently erased.

## 11. Integrity skills

`coder-claude`, `reviewer-gpt`, `reviewer-claude` and `architect-claude-opus` are declared in `skills/profiles.yaml`. SHA/evidence integrity contracts therefore apply to the new model-routing roles instead of failing as unknown profiles.

## 12. Legacy Ox

A pre-existing `auditor-ox` directory may remain as historical local state, but bootstrap sets both `model.provider` and `model.default` to `disabled-legacy`, sets backend `disabled-legacy`, clears `fallback_providers`/toolsets and disables tool discovery. It also physically removes legacy `fallback_model`, inherited MCP servers and old API-server override keys. `auditor-ox` is absent from active skill manifests/routing.
