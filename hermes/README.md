# Hermes Software Factory

Ta sekcja zawiera deklaratywną konfigurację profili Hermesa dla Software Factory.

## Polityka modeli

| Profil | Rola | Domyślny model |
|---|---|---|
| `orchestrator` | koordynacja i routing Kanban | główny GPT z `PRIMARY_PROFILE` |
| `runtime-controller` | mechaniczne create/readback/runtime gate Kanban | główny GPT z `PRIMARY_PROFILE`, osobny scoped bootstrap |
| `architect` | wymagania i architektura | główny GPT z `PRIMARY_PROFILE` |
| `repository-analyst` | analiza repozytorium przed planowaniem | `openrouter / stealth/ox-alpha`; fallback do primary GPT po `OX_MODEL=""` |
| `task-decomposer` | dekompozycja zaakceptowanego planu na taski | `gemini / gemini-3.5-flash-lite` |
| `coder` | implementacja | główny GPT z `PRIMARY_PROFILE` |
| `quick-reviewer` | tani pierwszy review i triage | `gemini / gemini-3.5-flash-lite` |
| `critic` | niezależny deep review | `xai-oauth / grok-4.6` |
| `auditor-gpt` | audyt GPT | główny GPT z `PRIMARY_PROFILE` |
| `auditor-grok` | niezależny audyt Grok | `xai-oauth / grok-4.6` |
| `auditor-ox` | opcjonalny trzeci audyt | `openrouter / stealth/ox-alpha` |
| `docs` | dokumentacja | `gemini / gemini-3.5-flash-lite` |
| `release-manager` | release gate | główny GPT z `PRIMARY_PROFILE` |
| `routing-sink` | fail-closed fallback dla błędnie skierowanych kart | główny GPT z `PRIMARY_PROFILE`, bez narzędzi implementacyjnych |
| pamięć | pamięć długoterminowa | Hindsight, poza routingiem profili LLM |

## Runtime controller

`orchestrator` pozostaje coordination-only i ma wyłączony terminal. Hermes 0.20.4 tool `kanban_create` nie ustawia wszystkich pól runtime wymaganych przez factory contract, dlatego operacje wymagające CLI są delegowane do osobnego profilu `runtime-controller`.

Po synchronizacji repo należy jawnie uruchomić:

```bash
PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh
```

Bootstrap instaluje do `~/.hermes/profiles/runtime-controller/`:

- `SOUL.md`,
- `kanban_runtime_cli.sh`,
- `kanban_runtime_contract.py`.

Profil ma `terminal`, ale jego SOUL ogranicza użycie do zainstalowanego wrappera. Wrapper nie używa `eval` i whitelistuje tylko `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-handoff`. Orchestrator nie otrzymuje terminala.

Runtime gate używa sticky-blocked control parent `RUNTIME_CONTRACT_PENDING`; właściwy worker task ma ten gate jako parent i nie może przejść do `ready`, dopóki validator nie zwróci `RUNTIME_CONTRACT_OK`.

## Ważne: profil głównego GPT

Skrypt głównego bootstrapu nie zakłada, że `default` jest GPT. Jeśli wskazany `PRIMARY_PROFILE` nie wygląda na profil GPT, bootstrap przerwie działanie zamiast utworzyć mylący profil `auditor-gpt`.

Na hoście testowym główny GPT jest utrzymywany jako osobny profil:

```bash
PRIMARY_PROFILE=primary-gpt \
DISPATCHER_PROFILE=default \
bash hermes/bootstrap_profiles.sh
```

## Gemini

Domyślny routing częstych, tańszych ról:

```text
provider: gemini
model: gemini-3.5-flash-lite
```

Profile: `task-decomposer`, `quick-reviewer`, `docs`. Fallback z Gemini do GPT jest decyzją workflow, nie ukrytym przełączeniem profilu.

## Ox Alpha

Domyślny routing:

```text
provider: openrouter
model: stealth/ox-alpha
```

Ox jest opcjonalny. `repository-analyst` używa Ox, a `auditor-ox` jest dodatkowym Audit 3. Przy `OX_MODEL=""` analiza repo przechodzi jawnie na primary GPT, a Audit 3 nie jest wymagany.

## Instalacja / ponowny bootstrap

Po merge i synchronizacji `main`:

```bash
cd ~/projects/software-factory
git switch main
git pull --ff-only
bash hermes/verify_bootstrap.sh
PRIMARY_PROFILE=primary-gpt \
DISPATCHER_PROFILE=default \
bash hermes/bootstrap_profiles.sh
PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

## Założenia

- `PRIMARY_PROFILE` jest działającym profilem GPT.
- `DISPATCHER_PROFILE` to profil gateway/dispatcher; domyślnie `default`.
- xAI/Gemini/OpenRouter są skonfigurowane zgodnie z używanymi rolami.
- Hindsight jest już skonfigurowany i bootstrap go nie rekonfiguruje.
- Kanban worker dostaje lifecycle automatycznie po dispatchu.
- Taski kodujące używają `workspace=worktree:<repo>`.
- Orchestrator pozostaje bez terminala; scoped CLI jest izolowane do `runtime-controller`.

## Fail-closed routing

`kanban.default_assignee` wskazuje na `routing-sink`. `routing-sink` nie implementuje kodu i blokuje źle skierowaną kartę. `runtime-controller` jest osobnym helperem technicznym, nie fallbackiem routingu.

## Verification

`hermes/verify_bootstrap.sh` i `hermes/verify_kanban.sh` są nieinwazyjne. Sprawdzają m.in. model policy, orchestrator denylist, runtime-controller bootstrap/wrapper, validator, review parser i kontrakt Kanban.
