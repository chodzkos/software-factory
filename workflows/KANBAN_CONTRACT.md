# Kanban task contract v1

Ten dokument doprecyzowuje `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` dla Software Factory uruchamianego przez Hermes Kanban. Standard pozostaje nadrzędnym źródłem prawdy. Politykę modeli i reviewerów doprecyzowuje `workflows/MODEL_ROUTING_POLICY.md`.

## 1. Tryb orkiestracji

- `kanban.auto_decompose=false`; dekompozycję wykonuje `task-decomposer`.
- Każdy task ma jawnego `assignee`; nierozpoznany routing trafia do `routing-sink`.
- `kanban.auto_subscribe_on_create=true`.
- Orchestrator koordynuje, ale nie implementuje i nie wykonuje independent review.
- Mechaniczne operacje wymagające CLI wykonuje `runtime-controller`; orchestrator nie ma terminala.

## 2. Stany i znaczenie DONE

`triage`, `todo`, `ready`, `running`, `blocked`, `review`, `done`, `archived` zachowują znaczenie Hermesa. `done` pojedynczej karty nie oznacza automatycznie VERIFIED całej zmiany. `IMPLEMENTED != VERIFIED`.

Zmiana może być uznana za VERIFIED dopiero po zamknięciu wszystkich wymaganych review/audit/evidence i bez nierozwiązanych blockerów.

## 3. Task body

Każdy task wykonawczy zawiera:

```text
## Task Contract
TYPE: feature|bugfix|audit|docs|release|analysis|architecture|decomposition|review
RISK: low|medium|high|critical
SECURITY_SENSITIVE: yes|no
ASSIGNEE: <profile>
REPOSITORY: <owner/repo lub path>
WORKSPACE: none|repo|worktree:<absolute-base-repository>
IMPLEMENTER: <profile|none>
REQUIRED_REVIEWERS: <exact reviewer profile|none for non-code tasks>
OPTIONAL_REVIEWERS: <comma-separated profiles|none>
REQUIRED_EVIDENCE: <opis>
ACCEPTANCE_CRITERIA:
- ...
```

Dla code-changing task/review `SECURITY_SENSITIVE`, `IMPLEMENTER`, `REQUIRED_REVIEWERS` i `WORKSPACE` są obowiązkowe dokładnie raz. `WORKSPACE` musi być `worktree:<absolute-base-repository>`.

## 4. Exact model routing

| Implementer | SECURITY_SENSITIVE | Exact REQUIRED_REVIEWERS |
|---|---|---|
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | forbidden |
| `coder-claude` | `yes` | `reviewer-gpt` |

`reviewer-gpt` jest przypięty do `openai-codex/gpt-5.6-sol`; nie dziedziczy security-review providera z `PRIMARY_PROFILE`.

`hermes/model_routing_policy.py` egzekwuje exact reviewer set. Malformed CSV, `none`, duplicate fields, unknown profiles, duplicate JSON keys i invalid nested `task` kończą się `MODEL_ROUTING_DRIFT`.

## 5. Fail-closed runtime gate

Body nie potwierdza pól runtime. Każda niezgodność runtime kończy się `RUNTIME_CONTRACT_DRIFT`, a model/reviewer route `MODEL_ROUTING_DRIFT`. Oba są fail-closed.

### 5.1 Runtime controller

`runtime-controller` ma tylko toolset `terminal` i profile-scoped `factory-execution-guards`. `pre_tool_call` przepuszcza wyłącznie:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Allowlist: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing`.

Body-independent `validate-handoff` nie istnieje. Bezpośrednie `hermes`, Git, Python, curl, file/code tools, shell operators, pipe/chaining i command substitution są mechanicznie blokowane.

### 5.2 Sticky parent quarantine

Create-time `blocked` nie jest sticky quarantine w Hermes 0.20.4. Runtime-controller tworzy techniczny parent gate przypisany do `routing-sink`, natychmiast zapisuje sticky `kanban block --kind needs_input` z powodem `RUNTIME_CONTRACT_PENDING`, a worker zależy od tego parenta.

`RUNTIME_CONTRACT_DRIFT` lub `MODEL_ROUTING_DRIFT` pozostawia gate blocked. Zgodność pozwala zakończyć tylko techniczny gate.

### 5.3 Runtime fields

Gate waliduje co najmniej `assignee`, `workspace_kind`, create-time `workspace_path`, wymagany `branch_name`, `max_retries`, `parents` i exact model routing z live body. `max_runtime` pozostaje create-time fail-visible ograniczeniem Hermesa 0.20.4 bez stabilnego readbacku.

## 6. Same-card implementer → reviewer handoff

Po claimie Hermes materializuje worktree. Implementer kończy run przez native `review_requested`; karta przechodzi do `status=review`, reviewer assignee i zachowuje ten sam resolved worktree.

Przed dispatch review runtime-controller wykonuje na tym samym live `show --json`:

```text
validate-routing --actual-json <live-json>
validate-routed-handoff --actual-json <live-json>
```

Oba wejścia używają wspólnego strict JSON decodera odrzucającego duplicate keys na każdym poziomie.

`validate-routed-handoff` wyprowadza implementera/reviewera wyłącznie z live body i wymaga: dokładnie jednego reviewera, `status=review`, właściwego `task.assignee`, dokładnie jednego `WORKSPACE: worktree:<base-repo>`, live workspace dokładnie `<base-repo>/.worktrees/<task-id>` bez `.`/`..`/symlink escape, najnowszego `review_requested` z mandatory integer `run_id`, latest implementer run `outcome=review_requested` z tym samym ID oraz run metadata zawierającej exact `task_id` i exact resolved workspace.

Summary ani profile names przekazane osobno nie są security inputem. Przy `CHANGES_REQUIRED` reviewer używa native same-card `kanban_request_changes`.

## 7. Claude Code execution boundary

`coder-claude`, `reviewer-claude`, `architect-claude-opus` mają profile-scoped `factory-execution-guards` v0.4.0.

Outer GPT nie może używać terminala do `find`, Git, Python, grep ani innych helperów. Terminal przyjmuje wyłącznie literalne argv0 `claude`; `./claude`, `/tmp/claude` i alternatywne ścieżki są blokowane. Guard wiąże attestation z resolved Claude binary path + SHA-256.

Claude command schema jest zamknięty: dokładnie jedno print prompt, exact model, JSON output, exact profile-specific `--allowedTools`, opcjonalny bounded `--max-turns`/`--effort`. Duplicate/unknown flags, permission bypass, settings/MCP/plugin/resume/worktree/debug/fallback są odrzucane. Prompt musi zawierać exact bieżący task ID, run ID i resolved worktree.

Coder tools:

```text
Read,Write,Edit,Glob,Grep,Bash(git status *),Bash(git diff *),Bash(git rev-parse *),Bash(python3 *)
```

Reviewer/architect exact read-only tools:

```text
Read,Glob,Grep
```

Reviewer/architect Claude nie otrzymuje żadnego `Bash`.

### 7.1 In-process attestation i evidence schema v5

Przed canonical Claude run `pre_tool_call` tworzy losowy nonce wyłącznie w pamięci worker process i wiąże go z task/run/profile, resolved workspace, command hash, Claude binary path+SHA-256, Git HEAD oraz content-state digest przed wykonaniem.

Content-state digest obejmuje staged diff oraz raw bytes/mode/symlink target wszystkich modified/deleted/untracked paths. Zmiana treści pliku jest więc wykrywana nawet jeśli `git status` nadal pokazuje ten sam status class.

`post_tool_call` wystawia evidence tylko dla matching pending attestation i successful Claude JSON result. Evidence schema v5 zapisuje także Git HEAD/content-state po wykonaniu oraz attestation ID wyprowadzony z in-memory nonce.

Sam durable JSON nie odblokowuje lifecycle. `kanban_request_review` albo Claude-backed completion wymaga jednocześnie matching schema-v5 file oraz matching completed attestation nadal obecnego w pamięci tego samego worker process.

Przed transition guard ponownie sprawdza current Claude binary identity, resolved workspace, Git HEAD i content-state digest. Każda późniejsza zmiana zawartości workspace albo rozpoczęcie kolejnego Claude command unieważnia poprzedni attestation.

## 8. Plugin supply chain

Installer tworzy jeden immutable snapshot source/pin set po początkowej walidacji manifestu. Późniejsza transakcja nie otwiera ponownie mutable manifestu. `--replace-reviewed` jest explicit opt-in, publikacja jest serializowana `flock`, stage jest rehashowany z frozen snapshot, rollback jest uzbrojony przed ruszeniem starego targetu, a failure po publikacji usuwa nowy target i obowiązkowo przywraca backup. Adversarial test wymusza post-publish corruption i sprawdza exact restore starego targetu.

## 9. Repository analyst fresh deployment

Canonical `bootstrap_profiles.sh` po konfiguracji profili wykonuje `bootstrap_repository_analyst_isolation.sh` oraz `verify_repository_analyst_isolation.sh --live`. Re-run analyst bootstrap używa controlled reviewed replacement, więc runtime `__pycache__` albo starsze reviewed bytes nie blokują odtworzenia exact pinned tree.

## 10. Integrity skills

`coder-claude`, `reviewer-gpt`, `reviewer-claude` i `architect-claude-opus` są jawnie zadeklarowane w `skills/profiles.yaml`; SHA/workspace/evidence contracts nie kończą się dla nich `unknown profile`.

## 11. Legacy Ox

Ox Alpha nie jest aktywnym backendem. Existing `auditor-ox` dostaje `model.provider=model.default=disabled-legacy`, `factory.execution_backend=disabled-legacy`, empty fallback/toolsets, disabled tool search i broad denylist.

## 12. Review/audit decision contract

Reviewer kończy dokładnie jedną linią `DECISION: APPROVE` albo `DECISION: CHANGES_REQUIRED`. HIGH/CRITICAL zawsze blokuje merge/release. Brak jednej parsowalnej decyzji oznacza `REVIEW_PENDING`.

## 13. Minimal lifecycle

Normal feature:

`repository-analyst? → architect? → task-decomposer → runtime-controller gate → coder|coder-claude → exact cross-vendor reviewer → required audits/evidence → release-manager? → done`

Security-sensitive feature:

`repository-analyst → architect? → task-decomposer → runtime-controller gate → coder-claude → pinned reviewer-gpt → required security evidence/audits → release-manager → done`

## 14. Deployment

Canonical profile bootstrap zawiera repository-analyst isolation. Po merge:

```bash
PRIMARY_PROFILE=default DISPATCHER_PROFILE=default bash hermes/bootstrap_profiles.sh
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

Przed workerem wymagane są zielone static/adversarial verifiers i live negative capability probes.
