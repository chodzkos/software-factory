# Software Factory Model Routing Policy v1

This policy supplements `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` and `workflows/KANBAN_CONTRACT.md`. The Standard remains authoritative.

## 1. Active implementation backends

### `coder`
- native OpenAI/GPT implementation profile,
- `factory.execution_backend=native-openai`,
- allowed only for `SECURITY_SENSITIVE: no`,
- exact normal reviewer: `reviewer-claude`.

### `coder-claude`
- Hermes coordination profile; actual coding is delegated through bundled `claude-code` to Claude Code CLI,
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
```

The runtime validator itself executes `hermes kanban show <task-id> --json` and strict-decodes the result. There is no production runtime-controller operation accepting `--actual-json` and no body-independent `validate-handoff` operation.

Routed handoff requires:

- exact `status=review` and reviewer assignee,
- exactly one `WORKSPACE: worktree:<base-repo>` field,
- existing live workspace path exactly `<declared-base>/.worktrees/<task-id>`, canonical absolute, lexical-exact and with no symlink component,
- latest `review_requested` with matching profiles and mandatory true integer `run_id` (JSON booleans rejected),
- latest implementer run with `outcome=review_requested` and identical true integer run ID,
- mandatory run metadata containing exact `task_id` and exact resolved workspace.

## 7. Claude Code mechanical execution boundary

Claude-backed profiles use profile-scoped `factory-execution-guards` v0.4.0.

Outer GPT terminal access is **Claude-only**: no `find`, Git, Python, grep or other helper executable is permitted. Direct file/code mutation tools are blocked.

Only literal argv0 `claude` is accepted. `./claude`, `/tmp/claude` and alternate paths are refused. Guard resolves the PATH-selected Claude binary itself and binds its resolved path + SHA-256 to the attestation.

Every invocation requires `--safe-mode`, disabling project/user `CLAUDE.md`, hooks, plugins, skills and MCP. Coder uses `--permission-mode acceptEdits`; reviewer/architect use `--permission-mode plan`.

Claude argv uses a closed schema:

- exactly one `-p` or `--print`,
- exact profile model (`sonnet` or `opus`),
- `--output-format json`,
- mandatory `--safe-mode`,
- exact permission mode,
- exact profile-specific `--allowedTools`,
- optional bounded `--max-turns` and low/medium/high `--effort`,
- duplicate/unknown flags, permission bypass, settings, MCP, plugins, resume/worktree/debug and fallback flags are refused.

Prompt binding is structured, not substring-based. The prompt must contain exactly one line each:

```text
TASK_ID: <exact-task-id>
RUN_ID: <exact-run-id>
WORKSPACE: <exact-resolved-worktree>
```

The Claude process cwd must equal the resolved worktree.

`coder-claude` exact tools:

```text
Read,Write,Edit,Glob,Grep
```

No general Bash/Python/Git capability is granted to coder Claude.

`reviewer-claude` and `architect-claude-opus` exact read-only tools:

```text
Read,Glob,Grep
```

Reviewer/architect additionally run in permission `plan` mode and receive no Bash/Write/Edit.

### In-process attestation and durable evidence

Before canonical Claude execution, `pre_tool_call` creates a random nonce held only in the trusted Hermes worker process and captures task/run/profile, resolved workspace, command SHA-256, Claude binary path+SHA-256, Git HEAD, and a content-state digest.

The content-state digest covers staged diff plus raw bytes/mode/symlink targets for **all cached tracked paths and all untracked paths**. It does not depend on `git status` visibility; `assume-unchanged` and `skip-worktree` therefore cannot hide a tracked path from attestation.

`post_tool_call` may emit evidence only for the matching pending in-memory attestation and a successful Claude JSON result. Evidence schema v5 records before/after Git HEAD and content-state digests plus an attestation ID derived from the in-memory nonce.

A durable JSON file alone cannot unlock lifecycle. `kanban_request_review` / Claude-backed completion requires both matching schema-v5 evidence and matching completed attestation still held in the same worker process. Current Claude binary identity, resolved workspace, Git HEAD and content-state digest are revalidated immediately before lifecycle transition. Any subsequent workspace-content change or another Claude invocation invalidates the prior attestation.

## 8. Runtime-controller mechanical boundary

`runtime-controller` has profile-scoped execution guards, only the terminal toolset, and can execute only installed `kanban_runtime_cli.sh` operations: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing-body`, `validate-routing-live`.

Literal newline/CR, direct `hermes`, Git, Python, curl, file tools, shell operators and command substitution are blocked. Live validators accept task IDs, not caller-supplied snapshot bytes.

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
