# Hermes Software Factory

Ta sekcja opisuje konfigurację profili Hermesa dla Software Factory. Kanoniczne zasady pozostają w `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`, `workflows/KANBAN_CONTRACT.md` i `workflows/MODEL_ROUTING_POLICY.md`.

## Polityka modeli

| Profil | Rola | Backend/model |
|---|---|---|
| `orchestrator` | koordynacja Kanban | primary GPT |
| `runtime-controller` | mechaniczny create/readback/runtime/model/handoff gate + targeted review dispatch | primary GPT + guarded terminal only |
| `architect` | wymagania/architektura | primary GPT |
| `architect-claude-opus` | trudna architektura/hard reasoning | `claude-code` / pinned `opus` |
| `repository-analyst` | analiza repo | primary GPT + mandatory isolated readonly tools |
| `task-decomposer` | dekompozycja | Gemini Flash-Lite |
| `coder` | non-security implementation | primary GPT / native-openai |
| `coder-claude` | Claude implementation; wymagany dla security-sensitive | `claude-code` / pinned `sonnet` |
| `reviewer-gpt` | cross-vendor review pracy Claude; jedyny security reviewer | **pinned `openai-codex/gpt-5.6-sol`** |
| `reviewer-claude` | read-only cross-vendor review non-security pracy `coder` | `claude-code` / pinned `sonnet` |
| `quick-reviewer` | tani pre-pass/CI triage | Gemini Flash-Lite |
| `critic` | deep review/audit | Grok 4.6 |
| `auditor-gpt` | audyt GPT | primary GPT |
| `auditor-grok` | audyt Grok | Grok 4.6 |
| `docs` | dokumentacja | Gemini Flash-Lite |
| `release-manager` | release gate | primary GPT |
| `routing-sink` | fail-closed fallback | primary GPT bez uprawnień implementacyjnych |

Ox Alpha nie jest aktywnym backendem Software Factory.

## Exact routing

```text
SECURITY_SENSITIVE=no:
  coder        -> reviewer-claude
  coder-claude -> reviewer-gpt

SECURITY_SENSITIVE=yes:
  coder        -> forbidden
  coder-claude -> reviewer-gpt
```

Reviewer set musi być dokładny. Security reviewer jest przypięty do OpenAI, `fallback_providers=[]`, a canonical bootstrap usuwa legacy `fallback_model`, więc reviewer nie dziedziczy fallbacku z `PRIMARY_PROFILE`.

## Claude Code

Profile Claude nie udają natywnego Anthropica w Hermesie. Outer Hermes koordynuje, ale właściwa praca musi przejść przez `claude-code`.

`factory-execution-guards` v0.11.0 zachowuje v0.10.0 schema-6 terminal/cwd attestation i wcześniejsze kontrole, a dodatkowo wprowadza nadzorowaną dzierżawę procesu, board binding, atomowe approval oraz handoff schema v2:

- blokuje direct outer-GPT write/patch/code execution,
- terminal `coder-claude` pozwala wyłącznie na przypięty supervisor z dokładnym board/task/run/workspace i literalnym wewnętrznym `claude`; żaden `find`, Git, Python, grep ani inny helper binary nie jest dopuszczony,
- każda delegacja wymaga `--safe-mode`, który wyłącza project/user `CLAUDE.md`, hooks, plugins, skills i MCP bez wyłączania OAuth,
- coder wymaga `--permission-mode dontAsk` i dokładnych permission rules `Read,Glob,Grep,Edit(//<exact-resolved-worktree>/**)`; szerokie `Write`/`Edit`, Bash/Python/Git są zabronione,
- resolved workspace musi należeć do małego przenośnego alfabetu bez przecinków, nawiasów, globów ani innych separatorów składni Claude permissions; niebezpieczna ścieżka jest odrzucana przed zbudowaniem `allowedTools`,
- workspace-scoped `Edit(...)` jest mechanicznie wyliczany przez guard z resolved Kanban workspace; write poza worktree nie powinien przejść ani poprosić o rozszerzenie uprawnień,
- reviewer-claude i architect-claude-opus wymagają `--permission-mode plan` i dokładnych `Read,Glob,Grep`,
- odrzuca `./claude`, `/tmp/claude`, duplicate/unknown flags, permission bypass, settings/MCP/plugin/resume/worktree/debug/fallback,
- prompt musi zawierać dokładnie po jednej osobnej linii `TASK_ID: ...`, `RUN_ID: ...`, `WORKSPACE: ...`,
- modelowy terminal call musi jawnie ustawić `workdir=<exact resolved HERMES_KANBAN_WORKSPACE>`; brak pola, alias leksykalny/symlink, inny katalog, background, PTY, notification/session/task override albo dowolny nieznany argument jest blokowany przed attestation,
- pełny zaakceptowany obiekt terminal args jest kanonicznie serializowany i hashowany; opcjonalny `timeout` musi być prawdziwym integerem 1..600 i jest częścią digestu,
- quoted multiline prompt jest dozwolony; newline/CR poza shell quotes jest blokowany przed wykonaniem jako separator poleceń,
- przed Claude runem guard tworzy losowy in-process attestation i zapisuje Git HEAD + content-state digest,
- Git dla attestation jest wybierany z platformowego domyślnego PATH i uruchamiany z minimalnym, oczyszczonym środowiskiem; odziedziczone `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, config injection i worker PATH nie mogą przekierować pomiaru do innego repo,
- content-state digest używa domenowo rozdzielonych, długościowo ramkowanych pól i osobnych hashy rekordów; obejmuje staged diff oraz bytes digest/mode/symlink target wszystkich tracked i untracked paths, **także Git-ignored untracked**,
- regular files są otwierane bez podążania za symlinkiem, a stat przed/po odczycie wykrywa zmianę pliku podczas pomiaru,
- evidence schema v6 wiąże task/run/profile, resolved workspace, `execution_cwd`, `terminal_args_sha256`, Claude session, command hash, Claude binary path+SHA-256, Git HEAD/content-state before/after oraz attestation ID,
- `post_tool_call` wymaga udanego `exit_code=0`; w Hermes 0.20.4 brak pola wyniku `cwd` oznacza brak zmiany względem zwalidowanego `command_cwd`, więc efektywnym cwd pozostaje jawny canonical workdir, a obecne pole `cwd` musi być stringiem identycznym z kanonicznym workspace; malformed, alias albo inny cwd nie może utworzyć evidence,
- sam plik evidence nie odblokowuje lifecycle: wymagany jest też completed attestation nadal obecny w pamięci tego samego worker process,
- przed każdą mutation-capable operacją `coder-claude` guard czyta aktywną bazę i wymaga dokładnego task/run/board/workspace, stanu `running`, assignee/current-run oraz aktywnego runu bez outcome/end; niepewność blokuje przed attestation i procesem,
- proces Claude i potomkowie działają w identyfikowanej sesji/process-group pod wyłączną board/task/workspace lease; utrata aktywnego runu, reclaim/timeout, zmiana planszy albo śmierć workera kończy i reapuje należące drzewo przed zwolnieniem lease,
- po ścisłym sukcesie native `review_requested` oraz trwałym potwierdzeniu task/run/event powstaje dokładnie jedna atomowa, no-follow pieczęć handoff schema v2 wiążąca kanoniczny board, HEAD/content, board-scoped schema-6 evidence, attestation/command/terminal args, event i PID z Linux process-start tokenem,
- skuteczny handoff zapisuje in-process seal, usuwa wcześniejszą autoryzację i ustawia `HERMES_KANBAN_STOP_NUDGE=0` wyłącznie jako defense in depth; aktywna bramka nadal mechanicznie blokuje drugi Claude call i inne mutacje,
- routed validation oraz targeted dispatch wymagają niezmienionych sealed bytes i potwierdzonego wyjścia dokładnego implementer process; dispatch powtarza pomiar pod writer lockiem, po claimie i przed spawnem oraz wiąże reviewer run metadata z pieczęcią,
- `reviewer-gpt` dostaje wyłącznie board-bound repository-read tools, `kanban_show`, `kanban_request_changes` i `factory_review_approve`; nie ma terminala, execute_code, generic write/patch ani MCP,
- `factory_review_approve` rewaliduje board/seal/evidence/process/HEAD/content pod tą samą lease i `BEGIN IMMEDIATE`, wykonuje natywną completion przez savepoint i po niej ponownie hashuje bajty przed commit; downstream `verify-approval` powtarza kontrolę przed ready/release/merge,
- zmiana zawartości workspace, HEAD, resolved workspace, process identity, evidence albo Claude binary po evidence unieważnia handoff/completion; rozpoczęcie kolejnego Claude command również unieważnia poprzedni attestation.

Brak Claude CLI/OAuth/evidence oznacza blocked; nie ma hidden fallbacku.

## Runtime controller

`runtime-controller` ma tylko toolset `terminal`; `pre_tool_call` przepuszcza wyłącznie pojedyncze wywołanie wrappera:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Operacje: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing-body`, `validate-routing-live`, `dispatch-review`, `verify-approval`.

Unquoted literal newline/CR i shell separators są blokowane. Quoted multiline argument pozostaje pojedynczym argv i nadal podlega walidacji konkretnej operacji. Body-independent `validate-handoff` został usunięty.

Pre-create routing może sprawdzić przekazany body przez `validate-routing-body`. Po create wszystkie security-relevant live walidacje przyjmują **task ID**, nie JSON snapshot:

```text
validate-routing-live --board <slug> --task-id <task-id>
validate-routed-handoff --board <slug> --task-id <task-id>
dispatch-review --board <slug> --task-id <task-id>
verify-approval --board <slug> --task-id <task-id>
validate-runtime --board <slug> --task-id <task-id> ...
```

Validator sam wykonuje `hermes kanban show <task-id> --json` i strict-decodes wynik. Model/runtime-controller nie może sfabrykować `--actual-json` jako live evidence.

Software Factory wymusza `kanban.review_dispatch=false`. Każda operacja live wymaga jawnego kanonicznego boardu; ambient `kanban/current` nie wybiera bazy. Po native `review_requested` karta pozostaje w `review`, dopóki runtime-controller nie uzyska `MODEL_ROUTING_OK` i `RUNTIME_CONTRACT_OK`. Dopiero wtedy `dispatch-review --board <slug> --task-id <id>` ponownie weryfikuje live handoff i wymaga wyłączonego globalnego review auto-dispatchu.

W v0.11.0 finalny odczyt task/event/run oraz natywny `claim_review_task` wykonują się pod jednym `BEGIN IMMEDIATE`, z ponowną walidacją board-bound handoff schema v2, content-state i implementer-process exit przed i po claimie. Wewnętrzna transakcja Hermesa jest bezpiecznie mapowana na savepoint, wynik claimu jest ponownie sprawdzany, a reviewer run metadata dostaje exact board/seal/HEAD/content/implementer-run binding przed commit. Drift po commit, ale przed spawnem, zapisuje fail-closed spawn failure i nie uruchamia reviewera.

Wrapper wyprowadza dokładny Hermes-managed Python ze zweryfikowanego launchera, a przed uruchomieniem helpera czyści `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` i `PYTHONINSPECT` oraz używa `-E -s`. Nie istnieje chroniona operacja board-globalnego review dispatchu.

Targeted helper jest jawnie przypięty do Hermesa 0.20.4; brak oczekiwanych private primitives albo inna wersja kończy się fail-closed. Review worker zachowuje dokładnie ten sam worktree i otrzymuje `sdlc-review` tak jak natywny review lane Hermesa.

Routed handoff wiąże jawny board oraz live body z assignee/event/run/worktree i handoff schema v2. `WORKSPACE: worktree:<base-repo>` z body musi odpowiadać istniejącemu, kanonicznemu i niesymlinkowanemu live `<base-repo>/.worktrees/<task-id>`, run IDs muszą być prawdziwymi integerami (nie JSON bool), implementer-run metadata musi zawierać exact `task_id` i exact resolved workspace, sealed content/evidence musi nadal pasować, a exact PID/process-start implementera musi już nie być aktywny.

## Repository analyst

Canonical `bootstrap_profiles.sh` na końcu obowiązkowo wykonuje:

```text
bootstrap_repository_analyst_isolation.sh
verify_repository_analyst_isolation.sh --live
```

Fresh deployment nie może pozostawić `repository-analyst` z szerokim surface odziedziczonym z primary profile. Bootstrap usuwa odziedziczone `plugins.enabled`, `plugins.disabled` i `plugins.entries`, po czym ustawia dokładny allowlist jednego pluginu `factory-repository-readonly`, pusty disabled set i dokładnie jeden fail-closed entry z `allow_tool_override=false`. Zarówno effective config, jak i fizyczny YAML są sprawdzane dokładnie.

Re-run bootstrapu może zastąpić tylko exact current lub jawnie zatwierdzone reviewed predecessor bytes; runtime `__pycache__/*.pyc` jest jedynym tolerowanym noise.

## Integrity skills

Nowe profile `coder-claude`, `reviewer-gpt`, `reviewer-claude` i `architect-claude-opus` są jawnie zadeklarowane w `skills/profiles.yaml`. SHA/workspace/evidence contracts są więc dostępne dla model-routing roles zamiast kończyć się `unknown profile`.

## Plugin supply chain

Reviewed plugin installer zamraża current source+pin set oraz jawnie zatwierdzone `replace_from` predecessor pin sets w jednym immutable transaction snapshot. Transakcja później nie czyta ponownie mutable manifestu.

`--replace-reviewed` nie oznacza „nadpisz cokolwiek”: istniejący target musi odpowiadać current/reviewed predecessor pins (plus opcjonalny runtime `__pycache__/*.pyc`). Unknown/drifted target jest odrzucany.

Destination i jego istniejące komponenty parent nie mogą być symlinkami. Publikacja używa `flock` bezpośrednio na zweryfikowanym katalogu destination, więc nie istnieje osobny symlinkowalny lock file. Stage jest rehashowany, rollback przywraca poprzedni reviewed target po post-publish verification failure, a cleanup failure po udanym commit nie usuwa poprawnego nowego targetu.

## Legacy Ox

Existing `auditor-ox` jest inference-disabled:

```text
model.provider=disabled-legacy
model.default=disabled-legacy
factory.execution_backend=disabled-legacy
fallback_providers=[]
toolsets=[]
tool_search=off
```

Bootstrap dodatkowo fizycznie usuwa legacy `fallback_model`, odziedziczone `mcp_servers` i stare API-server override keys.

## Instalacja / ponowny bootstrap

Po merge i synchronizacji `main`:

```bash
cd ~/projects/software-factory
git switch main
git pull --ff-only
bash hermes/verify_bootstrap.sh
PYTHONDONTWRITEBYTECODE=1 bash hermes/verify_kanban.sh
PRIMARY_PROFILE=default DISPATCHER_PROFILE=default bash hermes/bootstrap_profiles.sh
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

`bootstrap_profiles.sh` zawiera live repository-analyst isolation gate i już podczas bootstrapu wyłącza globalny review auto-dispatch na dispatcher profile. `bootstrap_runtime_controller.sh` instaluje targetowany review dispatcher i również wymaga `review_dispatch=false` w runtime-controller profile. Po bootstrapie nadal wymagane są live negative/positive probes execution guarda i provenance-bound routed handoffu przed VERIFIED.

## Założenia procesu

- `PRIMARY_PROFILE` może sterować zwykłymi primary-GPT rolami, ale nie security reviewerem;
- task kodowy używa `workspace=worktree:<repo>`;
- jedna logiczna zmiana = jedna branch/worktree/card lifecycle;
- implementer nie zatwierdza własnej pracy;
- exact-SHA review i wymagane evidence poprzedzają merge/release;
- HIGH/CRITICAL blokuje merge.
