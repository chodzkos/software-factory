# Hermes Software Factory

Ta sekcja opisuje deklaratywną konfigurację profili Hermesa dla Software Factory. Kanoniczne zasady bezpieczeństwa i procesu pozostają w `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`, `workflows/KANBAN_CONTRACT.md` i `workflows/MODEL_ROUTING_POLICY.md`.

## Polityka modeli

| Profil | Rola | Backend/model |
|---|---|---|
| `orchestrator` | koordynacja Kanban | primary GPT |
| `runtime-controller` | mechaniczny create/readback/runtime/model gate | primary GPT + guarded terminal only |
| `architect` | wymagania/architektura | primary GPT |
| `architect-claude-opus` | trudna architektura/hard reasoning | `claude-code` / pinned `opus` |
| `repository-analyst` | analiza repo | primary GPT, isolated readonly tools |
| `task-decomposer` | dekompozycja | Gemini Flash-Lite |
| `coder` | non-security implementation | primary GPT / native-openai |
| `coder-claude` | Claude implementation; wymagany dla security-sensitive | `claude-code` / pinned `sonnet` |
| `reviewer-gpt` | cross-vendor review pracy Claude; jedyny security reviewer | primary GPT / native-openai |
| `reviewer-claude` | cross-vendor review non-security pracy `coder` | `claude-code` / pinned `sonnet` |
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

Reviewer set musi być dokładny. Security review zawsze wykonuje OpenAI, a implementation security-sensitive musi być Claude, aby review pozostał cross-vendor. Dodatkowy `critic` jest modelowany osobnym mechanically gated audit taskiem, nie drugim reviewerem na tej samej Hermes 0.20.4 card.

## Claude Code

Profile Claude nie udają natywnego Anthropica w Hermesie. Outer Hermes używa primary GPT jako koordynatora, ale właściwa praca musi być wykonana przez bundlowany skill `claude-code` i Claude Code CLI.

Profile `coder-claude`, `reviewer-claude`, `architect-claude-opus` mają profile-scoped plugin `factory-execution-guards`:

- blokuje direct outer-GPT write/patch/code execution,
- ogranicza terminal do canonical Claude invocation i małego read-only verification surface,
- przypina `sonnet`/`opus`,
- zapisuje durable Claude success evidence per task/run/profile poza repo,
- blokuje lifecycle handoff/completion bez evidence.

Brak Claude CLI/OAuth/skilla/evidence oznacza blocked; nie ma hidden fallbacku.

## Runtime controller

`runtime-controller` jest mechanicznie ograniczony, nie tylko instrukcją w SOUL. Bootstrap instaluje profile-scoped `factory-execution-guards`, a profil ma tylko toolset `terminal`. `pre_tool_call` przepuszcza wyłącznie dokładny:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Direct `hermes`, Git, Python, curl, file/code tools, shell chaining, pipe i command substitution są blokowane.

Wrapper udostępnia zamknięte operacje: `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-handoff` (legacy compatibility), `validate-routed-handoff`, `validate-routing`.

Dla security-sensitive review źródłem prawdy jest `validate-routed-handoff`: sam parsuje live task body i wiąże routing z assignee/event/run/worktree. Osobne nazwy implementera/reviewera przekazane przez orchestratora nie są trusted security input.

Bootstrap:

```bash
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
```

## Repository analyst

`repository-analyst` pozostaje profilem izolowanym przez reviewed-ready `factory-repository-readonly`. Worker surface jest ograniczony do readonly repository tools + task-local guarded Kanban; brak generic terminal/file/code/MCP capability pozostaje osobnym, zweryfikowanym kontraktem z PR #22.

## Legacy Ox

Stary katalog profilu `auditor-ox` może pozostać lokalnie po wcześniejszych wdrożeniach, ale nie jest aktywną rolą Factory. Bootstrap ustawia mu:

```text
model.provider=disabled-legacy
model.default=disabled-legacy
factory.execution_backend=disabled-legacy
fallback_providers=[]
toolsets=[]
tool_search=off
```

oraz szeroki denylist. `auditor-ox` został usunięty z skill manifests i aktywnej polityki. Invalid provider/model jest inference kill switch.

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

Po bootstrapie wymagane są live negative/positive probes execution guarda oraz routing/handoffu przed uznaniem wdrożenia za VERIFIED.

## Założenia procesu

- `PRIMARY_PROFILE` jest działającym profilem GPT.
- task kodowy używa `workspace=worktree:<repo>`;
- jedna logiczna zmiana = jedna branch/worktree/card lifecycle;
- implementer nie zatwierdza własnej pracy;
- exact-SHA review i wymagane evidence poprzedzają merge/release;
- HIGH/CRITICAL blokuje merge.
