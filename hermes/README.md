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

`factory-execution-guards` v0.8.0 zachowuje v0.7.0 same-card targeted review dispatch oraz v0.6.0 Claude confinement/content-attestation i wzmacnia ich granice bezpieczeństwa:

- blokuje direct outer-GPT write/patch/code execution,
- terminal outer GPT pozwala wyłącznie na literalne `claude`; żaden `find`, Git, Python, grep ani inny helper binary nie jest dopuszczony,
- każda delegacja wymaga `--safe-mode`, który wyłącza project/user `CLAUDE.md`, hooks, plugins, skills i MCP bez wyłączania OAuth,
- coder wymaga `--permission-mode dontAsk` i dokładnych permission rules `Read,Glob,Grep,Edit(//<exact-resolved-worktree>/**)`; szerokie `Write`/`Edit`, Bash/Python/Git są zabronione,
- resolved workspace musi należeć do małego przenośnego alfabetu bez przecinków, nawiasów, globów ani innych separatorów składni Claude permissions; niebezpieczna ścieżka jest odrzucana przed zbudowaniem `allowedTools`,
- workspace-scoped `Edit(...)` jest mechanicznie wyliczany przez guard z resolved Kanban workspace; write poza worktree nie powinien przejść ani poprosić o rozszerzenie uprawnień,
- reviewer-claude i architect-claude-opus wymagają `--permission-mode plan` i dokładnych `Read,Glob,Grep`,
- odrzuca `./claude`, `/tmp/claude`, duplicate/unknown flags, permission bypass, settings/MCP/plugin/resume/worktree/debug/fallback,
- prompt musi zawierać dokładnie po jednej osobnej linii `TASK_ID: ...`, `RUN_ID: ...`, `WORKSPACE: ...`, a cwd Claude musi być exact resolved worktree,
- quoted multiline prompt jest dozwolony; newline/CR poza shell quotes jest blokowany przed wykonaniem jako separator poleceń,
- przed Claude runem guard tworzy losowy in-process attestation i zapisuje Git HEAD + content-state digest,
- Git dla attestation jest wybierany z platformowego domyślnego PATH i uruchamiany z minimalnym, oczyszczonym środowiskiem; odziedziczone `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE`, config injection i worker PATH nie mogą przekierować pomiaru do innego repo,
- content-state digest używa domenowo rozdzielonych, długościowo ramkowanych pól i osobnych hashy rekordów; obejmuje staged diff oraz bytes digest/mode/symlink target wszystkich tracked i untracked paths, **także Git-ignored untracked**,
- regular files są otwierane bez podążania za symlinkiem, a stat przed/po odczycie wykrywa zmianę pliku podczas pomiaru,
- evidence schema v5 wiąże task/run/profile, resolved workspace, Claude session, command hash, Claude binary path+SHA-256, Git HEAD/content-state before/after oraz attestation ID,
- sam plik evidence nie odblokowuje lifecycle: wymagany jest też completed attestation nadal obecny w pamięci tego samego worker process,
- zmiana zawartości workspace, HEAD, resolved workspace albo Claude binary po evidence unieważnia handoff/completion; rozpoczęcie kolejnego Claude command również unieważnia poprzedni attestation.

Brak Claude CLI/OAuth/evidence oznacza blocked; nie ma hidden fallbacku.

## Runtime controller

`runtime-controller` ma tylko toolset `terminal`; `pre_tool_call` przepuszcza wyłącznie pojedyncze wywołanie wrappera:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Operacje: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing-body`, `validate-routing-live`, `dispatch-review`.

Unquoted literal newline/CR i shell separators są blokowane. Quoted multiline argument pozostaje pojedynczym argv i nadal podlega walidacji konkretnej operacji. Body-independent `validate-handoff` został usunięty.

Pre-create routing może sprawdzić przekazany body przez `validate-routing-body`. Po create wszystkie security-relevant live walidacje przyjmują **task ID**, nie JSON snapshot:

```text
validate-routing-live --task-id <task-id>
validate-routed-handoff --task-id <task-id>
dispatch-review --task-id <task-id>
validate-runtime --task-id <task-id> ...
```

Validator sam wykonuje `hermes kanban show <task-id> --json` i strict-decodes wynik. Model/runtime-controller nie może sfabrykować `--actual-json` jako live evidence.

Software Factory wymusza `kanban.review_dispatch=false`. Po native `review_requested` karta pozostaje w `review`, dopóki runtime-controller nie uzyska `MODEL_ROUTING_OK` i `RUNTIME_CONTRACT_OK`. Dopiero wtedy `dispatch-review --task-id <id>` ponownie weryfikuje live handoff i wymaga wyłączonego globalnego review auto-dispatchu.

W v0.8.0 finalny odczyt task/event/run oraz natywny `claim_review_task` wykonują się pod jednym `BEGIN IMMEDIATE`. Wewnętrzna transakcja Hermesa jest bezpiecznie mapowana na savepoint, a wynik claimu jest ponownie sprawdzany przed commit. Assignee, body, branch, skills, worktree i provenance nie mogą zmienić się między walidacją i utworzeniem reviewer runu; spawn używa wyłącznie obiektu zwróconego przez atomowo zatwierdzony claim.

Wrapper wyprowadza dokładny Hermes-managed Python ze zweryfikowanego launchera, a przed uruchomieniem helpera czyści `PYTHONPATH`, `PYTHONHOME`, `PYTHONSTARTUP` i `PYTHONINSPECT` oraz używa `-E -s`. Nie istnieje chroniona operacja board-globalnego review dispatchu.

Targeted helper jest jawnie przypięty do Hermesa 0.20.4; brak oczekiwanych private primitives albo inna wersja kończy się fail-closed. Review worker zachowuje dokładnie ten sam worktree i otrzymuje `sdlc-review` tak jak natywny review lane Hermesa.

Routed handoff wiąże live body z assignee/event/run/worktree. `WORKSPACE: worktree:<base-repo>` z body musi odpowiadać istniejącemu, kanonicznemu i niesymlinkowanemu live `<base-repo>/.worktrees/<task-id>`, run IDs muszą być prawdziwymi integerami (nie JSON bool), a implementer-run metadata musi zawierać exact `task_id` i exact resolved workspace.

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
