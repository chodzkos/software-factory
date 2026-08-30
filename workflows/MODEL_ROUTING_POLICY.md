# Software Factory Model Routing Policy v1

This policy supplements `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` and `workflows/KANBAN_CONTRACT.md`. The Standard remains authoritative.

## 1. Active implementation backends

### `coder`

- native OpenAI/GPT implementation profile,
- `factory.execution_backend=native-openai`,
- normal independent reviewer: `reviewer-claude`.

### `coder-claude`

- Hermes coordination profile; actual coding is delegated through the bundled Hermes skill `claude-code` to Claude Code CLI authenticated separately (OAuth/subscription or another explicitly configured Claude Code auth),
- `factory.execution_backend=claude-code`,
- normal Claude model class: `sonnet`, configurable through `CLAUDE_NORMAL_MODEL`,
- normal independent reviewer: `reviewer-gpt`.

Ox Alpha is not an active Software Factory implementation/review backend and must not appear in bootstrap routing.

## 2. Review backends

### `reviewer-gpt`

- native OpenAI/GPT independent reviewer,
- default cross-vendor reviewer for `coder-claude`,
- the only profile allowed to perform deep `SECURITY_SENSITIVE: yes` review.

### `reviewer-claude`

- actual review delegated to Claude Code through the bundled `claude-code` skill,
- normal Claude model class: `sonnet`, configurable through `CLAUDE_NORMAL_MODEL`,
- default cross-vendor reviewer for native OpenAI `coder`,
- forbidden for `SECURITY_SENSITIVE: yes` review.

### `critic`

- Grok independent deep reviewer,
- required as an additional cross-vendor reviewer when a `SECURITY_SENSITIVE: yes` change was implemented by native OpenAI `coder`.

## 3. Complex architecture escalation

`architect-claude-opus` is optional and may be used only for difficult architecture/hard-reasoning tasks. It delegates analysis through the `claude-code` skill with model class `opus` (configurable by `CLAUDE_DEEP_MODEL`). It is not a routine coding/review profile and is never a security reviewer.

## 4. Required task-contract field

Every code-changing implementation/review contract must state:

```text
SECURITY_SENSITIVE: yes|no
```

This field is explicit; absence is routing drift, not an implicit `no`.

## 5. Mechanical routing matrix

| Implementer | SECURITY_SENSITIVE | Required reviewers |
| --- | --- | --- |
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | `reviewer-gpt`, `critic` |
| `coder-claude` | `yes` | `reviewer-gpt` |

Rules:

- normal review is cross-vendor relative to the implementer,
- Claude/Anthropic review is forbidden for `SECURITY_SENSITIVE: yes`,
- security-sensitive review always includes `reviewer-gpt`/OpenAI,
- when OpenAI implemented a security-sensitive change, `critic`/Grok provides the additional cross-vendor independent signal,
- unknown implementers/reviewers fail closed,
- hidden provider/model fallback is forbidden; backend unavailability becomes a visible blocked state.

The executable policy is `hermes/model_routing_policy.py`. `runtime-controller` must execute `kanban_runtime_cli.sh validate-routing ...` before releasing a runtime gate for implementation/review work.

## 6. Claude Code backend requirements

Profiles using `factory.execution_backend=claude-code` must:

- invoke the bundled Hermes `claude-code` skill rather than pretending Anthropic is a native Hermes provider,
- use the task's assigned worktree,
- propagate repository instructions and acceptance criteria to Claude Code,
- fail visibly if the skill/CLI/authentication is unavailable,
- never silently fall back to another implementation or review backend.

`reviewer-claude` must keep Claude Code read-only and may not modify the reviewed workspace.
