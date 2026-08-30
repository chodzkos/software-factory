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
- `factory.execution_backend=claude-code`,
- model class pinned to `sonnet`,
- exact reviewer: `reviewer-gpt`,
- required implementer for `SECURITY_SENSITIVE: yes` so mandatory OpenAI security review remains cross-vendor.

Ox Alpha is not an active Software Factory implementation/review backend.

## 2. Review backends

### `reviewer-gpt`

- native OpenAI independent reviewer,
- provider is pinned to `openai-codex`, model to `gpt-5.6-sol`; this pin does not inherit `PRIMARY_PROFILE` and is not affected by `ALLOW_NON_GPT_PRIMARY`,
- exact reviewer for `coder-claude`,
- the only same-card profile allowed to perform `SECURITY_SENSITIVE: yes` review.

### `reviewer-claude`

- actual review delegated to Claude Code through bundled `claude-code`,
- model class pinned to `sonnet`,
- exact reviewer for non-security native OpenAI `coder`,
- mechanically read-only and forbidden for `SECURITY_SENSITIVE: yes` review.

### `critic`

- Grok independent deep reviewer/auditor,
- may be required by a separate mechanically gated audit task,
- is not represented as a second REQUIRED_REVIEWER on the same Hermes 0.20.4 card because that lifecycle cannot mechanically guarantee sequential multi-review completion.

## 3. Complex architecture escalation

`architect-claude-opus` is optional for difficult architecture/hard reasoning. It delegates read-only analysis through `claude-code` with model class pinned to `opus`. It is never a security reviewer or implementation profile.

## 4. Required task-contract fields

Every code-changing implementation/review contract must state exactly once:

```text
IMPLEMENTER: <profile>
REQUIRED_REVIEWERS: <exact reviewer profile>
SECURITY_SENSITIVE: yes|no
```

Live Markdown body is the routing source of truth. Missing/duplicate fields, malformed reviewer CSV, `none`, unknown profiles or extra reviewers fail closed.

## 5. Mechanical routing matrix

| Implementer | SECURITY_SENSITIVE | Exact REQUIRED_REVIEWERS |
| --- | --- | --- |
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | **forbidden** |
| `coder-claude` | `yes` | `reviewer-gpt` |

Rules:

- normal review is cross-vendor relative to the implementer,
- security-sensitive review is always pinned OpenAI and cross-vendor,
- OpenAI implementation is forbidden for security-sensitive cards,
- Claude review is forbidden for security-sensitive cards,
- reviewer set must equal the canonical set exactly,
- hidden provider/model fallback is forbidden.

## 6. Strict live routing and handoff binding

Both live validators use the same strict JSON decoder and reject duplicate keys at any object depth.

1. `validate-routing --actual-json "<live show --json>"` validates exact task-body routing;
2. `validate-routed-handoff --actual-json "<same live show --json>"` is the **only** production handoff validator.

There is no body-independent `validate-handoff` operation. Routed handoff derives implementer/reviewer from live body and requires:

- exact `status=review` and reviewer assignee,
- canonical absolute `.../.worktrees/<task-id>` with no `.`/`..` escape and no symlink escape when the path exists,
- latest `review_requested` with matching profiles and mandatory integer `run_id`,
- latest implementer run with `outcome=review_requested` and identical run ID,
- mandatory run metadata containing the same resolved worktree.

## 7. Claude Code mechanical execution boundary

Profiles using `factory.execution_backend=claude-code` have profile-scoped `factory-execution-guards` v0.2.0.

Outer GPT terminal access is **Claude-only**: no `find`, Git, Python, grep or other helper executable is permitted. File/code mutation tools are also blocked directly.

Only literal argv0 `claude` is accepted; `./claude`, `/tmp/claude` and alternate paths are refused. Guard resolves the process PATH-selected Claude binary itself and records its resolved path plus SHA-256 in evidence.

Claude argv uses a closed schema:

- exactly one `-p` or `--print`,
- exact profile model (`sonnet` or `opus`),
- `--output-format json`,
- exact profile-specific `--allowedTools`,
- optional bounded `--max-turns` and low/medium/high `--effort`,
- duplicate flags, unknown flags, permission bypass, settings, MCP, plugins, resume/worktree/debug and fallback flags are refused.

`coder-claude` exact tools:

```text
Read,Write,Edit,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(python3 *)
```

`reviewer-claude` and `architect-claude-opus` exact read-only tools:

```text
Read,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(git show *),Bash(git log *)
```

Evidence schema v2 is stored outside the repository and binds task ID, run ID, profile, model class, resolved workspace, Claude session ID, command hash, resolved Claude binary path and binary SHA-256. Before handoff/completion the current binary identity and workspace are revalidated against the record.

## 8. Runtime-controller mechanical boundary

`runtime-controller` has profile-scoped `factory-execution-guards`, only the terminal toolset, and can execute only installed `kanban_runtime_cli.sh` operations: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing`. Direct `hermes`, Git, Python, curl, file tools, shell operators and command substitution are blocked.

## 9. Plugin supply-chain transaction

Reviewed profile-scoped plugin installation freezes a single immutable source/pin snapshot after initial manifest validation. Publication never reopens the mutable manifest. Replacement is explicit (`--replace-reviewed`), serialized with `flock`, staged and re-hashed against the frozen snapshot. Rollback is armed before moving the old target; any post-publication failure removes the new target and atomically restores the backup. Restoration failures are not suppressed.

## 10. Repository analyst isolation

Canonical `bootstrap_profiles.sh` ends by running `bootstrap_repository_analyst_isolation.sh` and `verify_repository_analyst_isolation.sh --live`. A fresh deployment therefore cannot leave `repository-analyst` on inherited primary-profile capabilities.

## 11. Legacy Ox

A pre-existing `auditor-ox` directory may remain as historical local state, but bootstrap sets both `model.provider` and `model.default` to `disabled-legacy`, clears fallbacks/toolsets and disables tool discovery. `auditor-ox` is removed from Factory skill manifests and active routing. The invalid provider/model is the inference kill switch.
