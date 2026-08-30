# Software Factory Model Routing Policy v1

This policy supplements `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` and `workflows/KANBAN_CONTRACT.md`. The Standard remains authoritative.

## 1. Active implementation backends

### `coder`

- native OpenAI/GPT implementation profile,
- `factory.execution_backend=native-openai`,
- allowed only for `SECURITY_SENSITIVE: no`,
- exact normal reviewer: `reviewer-claude`.

### `coder-claude`

- Hermes coordination profile; actual coding is delegated through the bundled Hermes skill `claude-code` to Claude Code CLI,
- `factory.execution_backend=claude-code`,
- model class is pinned to `sonnet`,
- exact reviewer: `reviewer-gpt`,
- required implementer for `SECURITY_SENSITIVE: yes` so the mandatory OpenAI security review remains cross-vendor.

Ox Alpha is not an active Software Factory implementation/review backend.

## 2. Review backends

### `reviewer-gpt`

- native OpenAI/GPT independent reviewer,
- exact reviewer for `coder-claude`,
- the only same-card profile allowed to perform `SECURITY_SENSITIVE: yes` review.

### `reviewer-claude`

- actual review delegated to Claude Code through the bundled `claude-code` skill,
- model class is pinned to `sonnet`,
- exact reviewer for non-security native OpenAI `coder`,
- forbidden for `SECURITY_SENSITIVE: yes` review.

### `critic`

- Grok independent deep reviewer/auditor,
- may be required by a separate mechanically gated audit task,
- is not represented as a second REQUIRED_REVIEWER on the same Hermes 0.20.4 card because that lifecycle cannot mechanically guarantee sequential multi-review completion.

## 3. Complex architecture escalation

`architect-claude-opus` is optional and may be used only for difficult architecture/hard-reasoning tasks. It delegates analysis through the `claude-code` skill with model class pinned to `opus`. It is not a routine coding/review profile and is never a security reviewer.

## 4. Required task-contract fields

Every code-changing implementation/review contract must state exactly once:

```text
IMPLEMENTER: <profile>
REQUIRED_REVIEWERS: <exact reviewer profile>
SECURITY_SENSITIVE: yes|no
```

The actual Markdown body stored on the Kanban card is the routing source of truth. Missing/duplicate fields, malformed reviewer CSV, `none`, unknown profiles or extra reviewers fail closed.

## 5. Mechanical routing matrix

| Implementer | SECURITY_SENSITIVE | Exact REQUIRED_REVIEWERS |
| --- | --- | --- |
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | **forbidden** |
| `coder-claude` | `yes` | `reviewer-gpt` |

Rules:

- normal review is cross-vendor relative to the implementer,
- security-sensitive review is always OpenAI and cross-vendor,
- OpenAI implementation is therefore forbidden for security-sensitive cards,
- Claude review is forbidden for security-sensitive cards,
- the reviewer set must equal the canonical set exactly; additional reviewers cannot be smuggled into `REQUIRED_REVIEWERS`,
- hidden provider/model fallback is forbidden.

The executable policy is `hermes/model_routing_policy.py`.

## 6. Live routing and handoff binding

`runtime-controller` performs two distinct checks against the same live task state:

1. `validate-routing --actual-json "<live show --json>"` validates exact task-body routing;
2. `validate-routed-handoff --actual-json "<same live show --json>"` derives implementer/reviewer from that body and verifies the current assignee, `review_requested` event, implementer run and resolved worktree.

The handoff validator does not accept orchestrator-supplied implementer/reviewer identities as security inputs. A conflicting assignee/event cannot pass by supplying matching CLI arguments.

## 7. Claude Code mechanical execution boundary

Profiles using `factory.execution_backend=claude-code` must have profile-scoped `factory-execution-guards` enabled.

The guard:

- blocks direct outer-agent write/patch/code-execution capability,
- allows canonical Claude print-mode invocation only with the profile's pinned model class and JSON output,
- permits only a small read-only terminal verification surface outside Claude,
- stores durable successful Claude evidence outside the repository keyed by task/run/profile, including model class and Claude session id,
- blocks `coder-claude` review handoff until successful Claude evidence exists for the current run,
- blocks Claude-backed reviewer/architecture completion until equivalent evidence exists,
- fails closed if evidence cannot be written or validated.

`reviewer-claude` additionally refuses Claude commands that expose `Write` in its allowed-tools declaration.

## 8. Runtime-controller mechanical boundary

`runtime-controller` also has profile-scoped `factory-execution-guards` enabled. It receives only the terminal toolset, and `pre_tool_call` permits only direct execution of the installed `kanban_runtime_cli.sh` with the closed operation allowlist. Direct `hermes`, Git, Python, curl, file tools, shell operators and command substitution are blocked before execution.

## 9. Legacy Ox

A pre-existing `auditor-ox` directory may remain as historical local state, but bootstrap sets both `model.provider` and `model.default` to `disabled-legacy`, clears fallbacks/toolsets and disables tool discovery. `auditor-ox` is removed from Factory skill manifests and active routing/documentation. The custom metadata value is defense-in-depth; the invalid provider/model is the inference kill switch.
