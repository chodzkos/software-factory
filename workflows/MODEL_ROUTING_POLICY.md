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

Both live validators use the same duplicate-key-rejecting JSON decoder.

1. `validate-routing --actual-json "<live show --json>"` validates exact task-body routing.
2. `validate-routed-handoff --actual-json "<same live show --json>"` is the **only** production handoff validator.

There is no body-independent `validate-handoff` operation. Routed handoff derives implementer/reviewer from live body and requires:

- exact `status=review` and reviewer assignee,
- exactly one `WORKSPACE: worktree:<base-repo>` field,
- live workspace path exactly `<declared-base>/.worktrees/<task-id>`, canonical absolute, no `.`/`..`, and no symlink escape when paths exist,
- latest `review_requested` with matching profiles and mandatory integer `run_id`,
- latest implementer run with `outcome=review_requested` and identical run ID,
- mandatory run metadata containing exact `task_id` and exact resolved workspace.

## 7. Claude Code mechanical execution boundary

Claude-backed profiles use profile-scoped `factory-execution-guards` v0.4.0.

Outer GPT terminal access is **Claude-only**: no `find`, Git, Python, grep or other helper executable is permitted. Direct file/code mutation tools are blocked.

Only literal argv0 `claude` is accepted. `./claude`, `/tmp/claude` and alternate paths are refused. Guard resolves the PATH-selected Claude binary itself and binds its resolved path + SHA-256 to the attestation.

Claude argv uses a closed schema:

- exactly one `-p` or `--print`,
- exact profile model (`sonnet` or `opus`),
- `--output-format json`,
- exact profile-specific `--allowedTools`,
- optional bounded `--max-turns` and low/medium/high `--effort`,
- duplicate/unknown flags, permission bypass, settings, MCP, plugins, resume/worktree/debug and fallback flags are refused,
- prompt must contain the exact current Kanban task ID, run ID and resolved workspace path.

`coder-claude` exact tools:

```text
Read,Write,Edit,Glob,Grep,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(python3 *)
```

`reviewer-claude` and `architect-claude-opus` exact read-only tools:

```text
Read,Glob,Grep
```

Reviewer/architect Claude receives **no Bash capability at all**.

### In-process attestation and durable evidence

Before canonical Claude execution, `pre_tool_call` creates a random nonce held only in the trusted Hermes worker process and captures task/run/profile, resolved workspace, command SHA-256, Claude binary path+SHA-256, Git HEAD, and a content-state digest.

The content-state digest covers staged diff plus raw bytes/mode/symlink targets for all modified, deleted and untracked paths. It therefore changes when file contents change even if `git status` would still show the same status class.

`post_tool_call` may emit evidence only for the matching pending in-memory attestation and a successful Claude JSON result. Evidence schema v5 records before/after Git HEAD and content-state digests plus an attestation ID derived from the in-memory nonce.

A durable JSON file alone cannot unlock lifecycle. `kanban_request_review` / Claude-backed completion requires both matching schema-v5 evidence and matching completed attestation still held in the same worker process. Current Claude binary identity, resolved workspace, Git HEAD and content-state digest are revalidated immediately before lifecycle transition. Any subsequent workspace-content change or another Claude invocation invalidates the prior attestation.

## 8. Runtime-controller mechanical boundary

`runtime-controller` has profile-scoped execution guards, only the terminal toolset, and can execute only installed `kanban_runtime_cli.sh` operations: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing`. Direct `hermes`, Git, Python, curl, file tools, shell operators and command substitution are blocked.

## 9. Plugin supply-chain transaction

Reviewed profile-scoped plugin installation freezes a single source/pin snapshot after initial manifest validation. Publication never reopens the mutable manifest. Replacement is explicit (`--replace-reviewed`), serialized with `flock`, staged and re-hashed against the frozen snapshot. Rollback is armed before moving the old target; any post-publication failure removes the new target and restores the backup. Restoration failures are not suppressed. Adversarial tests inject post-publication corruption and require exact restoration of the previous target.

## 10. Repository analyst isolation

Canonical `bootstrap_profiles.sh` ends by running `bootstrap_repository_analyst_isolation.sh` and `verify_repository_analyst_isolation.sh --live`. Re-running the analyst bootstrap uses controlled reviewed replacement, so runtime `__pycache__` or older reviewed bytes cannot prevent restoration of the exact pinned tree.

## 11. Integrity skills

`coder-claude`, `reviewer-gpt`, `reviewer-claude` and `architect-claude-opus` are declared in `skills/profiles.yaml`. SHA/evidence integrity contracts therefore apply to the new model-routing roles instead of failing as unknown profiles.

## 12. Legacy Ox

A pre-existing `auditor-ox` directory may remain as historical local state, but bootstrap sets both `model.provider` and `model.default` to `disabled-legacy`, clears fallbacks/toolsets and disables tool discovery. `auditor-ox` is absent from active skill manifests/routing. Invalid provider/model is the inference kill switch.
