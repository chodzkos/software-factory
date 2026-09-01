# Kanban task contract v1

Ten dokument doprecyzowuje `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` dla Software Factory uruchamianego przez Hermes Kanban. Standard pozostaje nadrzędnym źródłem prawdy. Politykę modeli i reviewerów doprecyzowuje `workflows/MODEL_ROUTING_POLICY.md`.

## 1. Tryb orkiestracji

- `kanban.auto_decompose=false`; dekompozycję wykonuje `task-decomposer`.
- Każdy task ma jawnego `assignee`; nierozpoznany routing trafia do `routing-sink`.
- `kanban.auto_subscribe_on_create=true`.
- `kanban.review_dispatch=false`; Hermes 0.20.4 nie może automatycznie claimować kart z `review`, ponieważ provenance-bound routed-handoff gate musi wykonać się przed reviewer runem. Reviewer jest uruchamiany dopiero przez targetowane `runtime-controller dispatch-review --task-id <task-id>` po zielonych walidacjach.
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

`reviewer-gpt` jest przypięty do `openai-codex/gpt-5.6-sol`; canonical bootstrap fizycznie usuwa legacy `fallback_model` i wymaga pustego `fallback_providers`, więc security reviewer nie może odziedziczyć fallbacku z `PRIMARY_PROFILE`.

`hermes/model_routing_policy.py` egzekwuje exact reviewer set. Malformed CSV, `none`, duplicate fields, unknown profiles, duplicate JSON keys i invalid nested `task` kończą się `MODEL_ROUTING_DRIFT`.

## 5. Fail-closed runtime gate

Body nie potwierdza pól runtime. Każda niezgodność runtime kończy się `RUNTIME_CONTRACT_DRIFT`, a model/reviewer route `MODEL_ROUTING_DRIFT`. Oba są fail-closed.

### 5.1 Runtime controller

`runtime-controller` ma tylko toolset `terminal` i profile-scoped `factory-execution-guards`. `pre_tool_call` przepuszcza wyłącznie pojedyncze wywołanie wrappera; line break poza quoted argumentem jest odrzucany:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Allowlist: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing-body`, `validate-routing-live`, `dispatch-review`.

`dispatch-review` ma wyłącznie postać `dispatch-review --task-id <task-id>`. Nie istnieje board-globalny dispatch review w chronionym runtime surface.

Unquoted literal newline/CR, body-independent `validate-handoff`, bezpośrednie `hermes`, Git, Python, curl, file/code tools, shell operators, pipe/chaining i command substitution są mechanicznie blokowane. Quoted wieloliniowy argument jest dopuszczalny wyłącznie jako pojedynczy argument i nadal podlega dokładnej walidacji argv właściwej operacji.

Pre-create routing używa tylko `validate-routing-body --task-body <exact-body>`. Wszystkie post-create/live walidacje oraz targeted dispatch przyjmują `--task-id`; live state jest pobierany autorytatywnie. Runtime-controller nie może podać, przepisać ani sfabrykować `--actual-json` jako live evidence.

Targeted helper jest uruchamiany przez dokładnie wyprowadzony Hermes-managed Python. Wrapper najpierw sprawdza `python -I -c 'import hermes_cli'`, następnie czyści `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` i `PYTHONINSPECT` oraz uruchamia helper z `-E -s`, zachowując tylko wymagany katalog skryptu i standardowe biblioteki.

### 5.2 Sticky parent quarantine

Create-time `blocked` nie jest sticky quarantine w Hermes 0.20.4. Runtime-controller tworzy techniczny parent gate przypisany do `routing-sink`, natychmiast zapisuje sticky `kanban block --kind needs_input` z powodem `RUNTIME_CONTRACT_PENDING`, a worker zależy od tego parenta.

`RUNTIME_CONTRACT_DRIFT` lub `MODEL_ROUTING_DRIFT` pozostawia gate blocked. Zgodność pozwala zakończyć tylko techniczny gate.

### 5.3 Runtime fields

Gate waliduje co najmniej `assignee`, `workspace_kind`, create-time `workspace_path`, wymagany `branch_name`, `max_retries`, `parents` i exact model routing z autorytatywnego live body. `max_runtime` pozostaje create-time fail-visible ograniczeniem Hermesa 0.20.4 bez stabilnego readbacku.

## 6. Same-card implementer → reviewer handoff

Po claimie Hermes materializuje worktree. Implementer kończy run przez native `review_requested`; karta przechodzi do `status=review`, reviewer assignee i zachowuje ten sam resolved worktree. Hermes 0.20.4 zamyka wtedy implementer run, dlatego kanoniczny pre-review-claim ma `current_run_id=None`; każdy nie-`None` active run przed targetowanym claimem jest drift i kończy się fail-closed.

Software Factory utrzymuje `kanban.review_dispatch=false`, aby gateway Hermesa 0.20.4 nie przeszedł `review -> running` przed wykonaniem gate. Nie wolno chwilowo włączać auto-dispatchu ani używać board-globalnego `hermes kanban dispatch` do uruchomienia reviewera.

Przed dispatch review runtime-controller wykonuje kolejno:

```text
validate-routing-live --task-id <task-id>
validate-routed-handoff --task-id <task-id>
dispatch-review --task-id <task-id>
```

Pierwsze dwa kroki muszą zwrócić PASS przed trzecim. `dispatch-review` nie ufa wcześniejszemu wynikowi tekstowemu: ponownie pobiera i strict-decodes live task oraz ponownie wykonuje routed-handoff validation.

Finalny claim v0.8.0 jest provenance-bound i atomowy względem innych writerów. Helper otwiera `BEGIN IMMEDIATE`, pod tym samym writer lockiem ponownie sprawdza task `id/status/assignee/body/branch/skills/workspace/current_run_id`, najnowszy `review_requested` event i najnowszy implementer run wraz z exact task/workspace metadata. Następnie uruchamia natywny `claim_review_task` poprzez kontrolowany savepoint, nadal w tej samej zewnętrznej transakcji, i ponownie sprawdza obiekt zwrócony przez claim. Jakakolwiek różnica powoduje rollback całego claimu.

Dopiero atomowo zatwierdzony obiekt claimu może zostać użyty do same-worktree reviewer spawn. Późniejsza zmiana mutable task row nie może podmienić profilu/body/workspace przekazanych do `_default_spawn`. Helper jest jawnie przypięty do Hermesa 0.20.4 i ma fail-closed przy brakujących/zmienionych private primitives.

Live walidacje pobierają snapshot samodzielnie przez `hermes kanban show <task-id> --json` i używają strict duplicate-key decodera. Caller-supplied JSON nie jest security inputem.

`validate-routed-handoff` wyprowadza implementera/reviewera wyłącznie z live body i wymaga: dokładnie jednego reviewera, `status=review`, właściwego `task.assignee`, dokładnie jednego `WORKSPACE: worktree:<base-repo>`, istniejący kanoniczny live workspace dokładnie `<base-repo>/.worktrees/<task-id>` bez `.`/`..`/duplicate separator/symlink escape, najnowszy `review_requested` z mandatory prawdziwym integer `run_id` (JSON boolean jest odrzucany), latest implementer run `outcome=review_requested` z tym samym ID oraz run metadata zawierającą exact `task_id` i exact resolved workspace.

Summary ani profile names przekazane osobno nie są security inputem. Przy `CHANGES_REQUIRED` reviewer używa native same-card `kanban_request_changes`.

## 7. Claude Code execution boundary

`coder-claude`, `reviewer-claude`, `architect-claude-opus` mają profile-scoped `factory-execution-guards` v0.8.0. Wersja 0.8.0 zachowuje v0.7.0 targeted review dispatch i v0.6.0 Claude confinement/content-attestation, dodając atomic claim, bezpieczną składnię permissions, kanoniczne ramkowanie content-state oraz oczyszczone środowisko Git/Pythona.

Outer GPT nie może używać terminala do `find`, Git, Python, grep ani innych helperów. Terminal przyjmuje wyłącznie literalne argv0 `claude`; `./claude`, `/tmp/claude` i alternatywne ścieżki są blokowane.

Każdy Claude invocation musi zawierać `--safe-mode`, aby wyłączyć project/user `CLAUDE.md`, hooks, plugins, skills i MCP. Coder wymaga `--permission-mode dontAsk`; reviewer/architect wymagają `--permission-mode plan`.

Prompt musi zawierać dokładnie po jednej osobnej linii:

```text
TASK_ID: <exact-task-id>
RUN_ID: <exact-run-id>
WORKSPACE: <exact-resolved-worktree>
```

Quoted wieloliniowy prompt jest dozwolony, ale newline/CR poza quoted argumentem nadal jest mechanicznie blokowany jako separator shella. Substringi/prefiksy/sufiksy i duplikaty markerów nie są akceptowane. Cwd procesu Claude musi być dokładnie resolved worktree.

Coder permissions są wyliczane z resolved worktree i muszą mieć dokładnie postać:

```text
Read,Glob,Grep,Edit(//<exact-resolved-worktree>/**)
```

Resolved workspace jest dodatkowo akceptowany tylko wtedy, gdy używa bezpiecznego alfabetu bez przecinka, nawiasów, gwiazdek i innych znaków strukturalnych składni Claude permissions. Nie istnieje ad-hoc escaping; niebezpieczna ścieżka jest odrzucana przed zbudowaniem lub zaakceptowaniem `allowedTools`.

Nie ma szerokiego `Write` ani szerokiego `Edit`. `dontAsk` powoduje, że modyfikacja niepasująca do workspace-scoped `Edit(...)` jest odrzucana zamiast pytania o rozszerzenie uprawnień. Coder nie otrzymuje ogólnego `Bash`, Python ani Git. Shell-based testy/static gates są wykonywane jako osobny kontrolowany etap po implementacji, a nie przez Claude Code implementera.

Reviewer/architect exact read-only tools:

```text
Read,Glob,Grep
```

Reviewer/architect mają dodatkowo `--permission-mode plan`; nie otrzymują `Bash`, `Write` ani `Edit`.

### 7.1 In-process attestation i evidence schema v5

Przed canonical Claude run `pre_tool_call` tworzy losowy nonce wyłącznie w pamięci worker process i wiąże go z task/run/profile, resolved workspace, command hash, Claude binary path+SHA-256, Git HEAD oraz content-state digest przed wykonaniem.

Git jest uruchamiany przez zaufany absolute binary wybrany z platformowego `os.defpath`, z minimalnym środowiskiem tworzonym od zera. Dziedziczone `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, object/config injection i worker-controlled PATH nie są przekazywane. `rev-parse --show-toplevel` musi rozwiązać się dokładnie do deklarowanego workspace.

Content-state digest obejmuje staged diff oraz wszystkie tracked i untracked paths, także Git-ignored untracked paths. Każda wartość jest długościowo ramkowana, każdy typed path record ma osobny SHA-256, a wynikowy ordered record list jest domenowo rozdzielony (`software-factory-content-state-v2`). Raw file bytes nie mogą udawać separatorów/metadanych następnego rekordu. Regular files są czytane przez no-follow descriptor, a stat przed/po odczycie wykrywa replacement lub zmianę w czasie pomiaru. Symlink target i deletion state są jawnie reprezentowane.

Tracked paths są enumerowane z Git index niezależnie od status hints, więc `assume-unchanged` i `skip-worktree` nie ukrywają ich przed digestem, a `.gitignore` nie wyłącza lokalnej zawartości workspace z attestation.

`post_tool_call` wystawia evidence tylko dla matching pending attestation i successful Claude JSON result. Evidence schema v5 zapisuje także Git HEAD/content-state po wykonaniu oraz attestation ID wyprowadzony z in-memory nonce.

Sam durable JSON nie odblokowuje lifecycle. `kanban_request_review` albo Claude-backed completion wymaga jednocześnie matching schema-v5 file oraz matching completed attestation nadal obecnego w pamięci tego samego worker process.

Przed transition guard ponownie sprawdza current Claude binary identity, resolved workspace, Git HEAD i content-state digest. Każda późniejsza zmiana zawartości workspace albo rozpoczęcie kolejnego Claude command unieważnia poprzedni attestation.

## 8. Plugin supply chain

Installer tworzy jeden immutable snapshot current pin set oraz jawnie zatwierdzonych `replace_from` predecessor pin sets po początkowej walidacji manifestu. Późniejsza transakcja nie otwiera ponownie mutable manifestu.

Destination path i wszystkie istniejące komponenty parent są odrzucane, jeśli są symlinkami. Publikacja jest serializowana przez `flock` na zweryfikowanym katalogu destination, bez osobnego symlinkowalnego lock file. `--replace-reviewed` może zastąpić tylko exact current/reviewed predecessor bytes; jedynym tolerowanym runtime noise jest `__pycache__/*.pyc`. Unknown/drifted target jest fail-closed.

Stage jest rehashowany z frozen snapshot, rollback jest uzbrojony przed ruszeniem starego targetu, a post-publish verification failure obowiązkowo przywraca backup. Po udanym commit nowy target nie jest usuwany tylko dlatego, że cleanup starego backupu zawiedzie — cleanup failure jest raportowany osobno.

## 9. Repository analyst fresh deployment

Canonical `bootstrap_profiles.sh` po konfiguracji profili wykonuje `bootstrap_repository_analyst_isolation.sh` oraz `verify_repository_analyst_isolation.sh --live`. Re-run analyst bootstrap używa controlled reviewed replacement: current reviewed bytes plus wyłącznie runtime `__pycache__` mogą zostać odtworzone; unknown target nie może być nadpisany jako „reviewed”.

Przed włączeniem narzędzi bootstrap atomowo usuwa odziedziczone lub stale `plugins.enabled`, `plugins.disabled` i `plugins.entries`, ustawia puste struktury, a następnie włącza wyłącznie `factory-repository-readonly` z `allow_tool_override=false`. Effective config i fizyczny YAML muszą zawierać dokładnie ten jeden enabled plugin, pusty disabled set i dokładnie jeden plugin entry. Sama obecność reviewed pluginu nie wystarcza.

## 10. Integrity skills

`coder-claude`, `reviewer-gpt`, `reviewer-claude` i `architect-claude-opus` są jawnie zadeklarowane w `skills/profiles.yaml`; SHA/workspace/evidence contracts nie kończą się dla nich `unknown profile`.

## 11. Legacy Ox

Ox Alpha nie jest aktywnym backendem. Existing `auditor-ox` dostaje `model.provider=model.default=disabled-legacy`, `factory.execution_backend=disabled-legacy`, empty `fallback_providers`/toolsets, disabled tool search i broad denylist. Bootstrap fizycznie usuwa legacy `fallback_model`, inherited `mcp_servers` oraz stare API-server override keys z profilu.

## 12. Review/audit decision contract

Reviewer kończy dokładnie jedną linią `DECISION: APPROVE` albo `DECISION: CHANGES_REQUIRED`. HIGH/CRITICAL zawsze blokuje merge/release. Brak jednej parsowalnej decyzji oznacza `REVIEW_PENDING`.

## 13. Minimal lifecycle

Normal feature:

`repository-analyst? → architect? → task-decomposer → runtime-controller create/runtime gate → coder|coder-claude → review_requested → runtime-controller routed-handoff gate → targeted exact reviewer dispatch → required audits/evidence → release-manager? → done`

Security-sensitive feature:

`repository-analyst → architect? → task-decomposer → runtime-controller create/runtime gate → coder-claude → review_requested → runtime-controller routed-handoff gate → targeted reviewer-gpt dispatch → required security evidence/audits → release-manager → done`

## 14. Deployment

Canonical profile bootstrap zawiera repository-analyst isolation i ustawia `kanban.review_dispatch=false` na dispatcher profile. Po merge:

```bash
PRIMARY_PROFILE=default DISPATCHER_PROFILE=default bash hermes/bootstrap_profiles.sh
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

Przed workerem wymagane są zielone static/adversarial verifiers i live negative capability probes.
