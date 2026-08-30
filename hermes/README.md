# Hermes Software Factory

Ta sekcja opisuje konfigurację profili Hermesa dla Software Factory. Kanoniczne zasady pozostają w `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`, `workflows/KANBAN_CONTRACT.md` i `workflows/MODEL_ROUTING_POLICY.md`.

## Polityka modeli

| Profil | Rola | Backend/model |
|---|---|---|
| `orchestrator` | koordynacja Kanban | primary GPT |
| `runtime-controller` | mechaniczny create/readback/runtime/model gate | primary GPT + guarded terminal only |
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

Reviewer set musi być dokładny. Security reviewer jest przypięty do OpenAI i nie dziedziczy providera/modelu z `PRIMARY_PROFILE`.

## Claude Code

Profile Claude nie udają natywnego Anthropica w Hermesie. Outer Hermes koordynuje, ale właściwa praca musi przejść przez `claude-code`.

`factory-execution-guards` v0.2.0:

- blokuje direct outer-GPT write/patch/code execution,
- terminal pozwala wyłącznie na literalne `claude`; żaden `find`, Git, Python, grep ani inny helper binary nie jest dopuszczony,
- odrzuca `./claude`, `/tmp/claude`, duplicate/unknown flags, permission bypass, settings/MCP/plugin/resume/worktree/debug/fallback,
- wymaga exact model + JSON + exact profile-specific `--allowedTools`,
- dla reviewer/architect exact tools są read-only,
- evidence schema v2 wiąże task/run/profile, resolved workspace, Claude session, command hash oraz resolved Claude binary path+SHA-256,
- lifecycle transition ponownie sprawdza workspace i binary identity.

Brak Claude CLI/OAuth/skilla/evidence oznacza blocked; nie ma hidden fallbacku.

## Runtime controller

`runtime-controller` ma tylko toolset `terminal`; `pre_tool_call` przepuszcza wyłącznie:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Operacje: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-routed-handoff`, `validate-routing`.

Body-independent `validate-handoff` został usunięty. Routed handoff jest jedynym production handoff gate, używa strict duplicate-key JSON i wiąże live body z assignee/event/run/worktree.

## Repository analyst

Canonical `bootstrap_profiles.sh` nie kończy się już na utworzeniu/clonowaniu profilu. Na końcu obowiązkowo wykonuje:

```text
bootstrap_repository_analyst_isolation.sh
verify_repository_analyst_isolation.sh --live
```

Fresh deployment nie może pozostawić `repository-analyst` z szerokim surface odziedziczonym z primary profile.

## Plugin supply chain

Reviewed plugin installer zamraża source+pin set w jednym immutable transaction snapshot. Transakcja później nie czyta ponownie mutable manifestu. `--replace-reviewed` jest explicit opt-in; staging/final verification używa tego samego snapshotu, publikacja jest pod `flock`, rollback jest uzbrojony przed ruszeniem starego targetu, a failure usuwa nowy target i obowiązkowo przywraca backup.

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

`bootstrap_profiles.sh` zawiera już live repository-analyst isolation gate. Po bootstrapie nadal wymagane są live negative/positive probes execution guarda i routed handoffu przed VERIFIED.

## Założenia procesu

- `PRIMARY_PROFILE` może sterować zwykłymi primary-GPT rolami, ale nie security reviewerem;
- task kodowy używa `workspace=worktree:<repo>`;
- jedna logiczna zmiana = jedna branch/worktree/card lifecycle;
- implementer nie zatwierdza własnej pracy;
- exact-SHA review i wymagane evidence poprzedzają merge/release;
- HIGH/CRITICAL blokuje merge.
