# Hermes Software Factory

Ta sekcja zawiera deklaratywną konfigurację profili Hermesa dla Software Factory.

## Profile

| Profil | Rola | Domyślny model |
|---|---|---|
| `orchestrator` | dekompozycja i routing Kanban | główny GPT z `PRIMARY_PROFILE` |
| `architect` | wymagania i architektura | główny GPT z `PRIMARY_PROFILE` |
| `coder` | implementacja | główny GPT z `PRIMARY_PROFILE` |
| `quick-reviewer` | tani pierwszy review | Gemini po jawnej konfiguracji; do tego czasu główny GPT |
| `critic` | niezależny deep review | `xai-oauth / grok-4.6` |
| `auditor-gpt` | końcowy audyt GPT | główny GPT z `PRIMARY_PROFILE` |
| `auditor-grok` | końcowy audyt Grok | `xai-oauth / grok-4.6` |
| `release-manager` | release gate | główny GPT z `PRIMARY_PROFILE` |
| `routing-sink` | fail-closed fallback dla błędnie skierowanych kart | główny GPT z `PRIMARY_PROFILE`, bez narzędzi implementacyjnych |

## Ważne: profil głównego GPT

Skrypt nie zakłada, że `default` jest GPT. Jeśli wskazany `PRIMARY_PROFILE` nie wygląda na profil GPT, bootstrap przerwie działanie zamiast utworzyć mylący profil `auditor-gpt`.

Najpierw wskaż działający profil GPT:

```bash
PRIMARY_PROFILE=<profil-gpt> bash hermes/bootstrap_profiles.sh
```

Jeśli gateway/dispatcher działa z innego profilu niż `default`, wskaż go osobno:

```bash
PRIMARY_PROFILE=<profil-gpt> \
DISPATCHER_PROFILE=<profil-gateway> \
bash hermes/bootstrap_profiles.sh
```

## Instalacja

Na serwerze, po merge PR i synchronizacji `main`:

```bash
cd ~/projects/software-factory
git switch main
git pull --ff-only
bash hermes/verify_bootstrap.sh
PRIMARY_PROFILE=<profil-gpt> bash hermes/bootstrap_profiles.sh
```

Skrypt jest idempotentny względem tworzenia profili: istniejący profil jest wykrywany po katalogu `~/.hermes/profiles/<name>`, więc sposób oznaczania aktywnego profilu przez `hermes profile list` nie wpływa na wynik. Przy każdym uruchomieniu odświeżane są `SOUL.md` oraz jawnie zarządzane ustawienia/model routing.

## Założenia

- `PRIMARY_PROFILE` jest działającym profilem z głównym modelem GPT i poprawnym auth.
- `DISPATCHER_PROFILE` to profil, z którego faktycznie działa gateway/dispatcher; domyślnie `default`.
- `xai-oauth` jest już zalogowany; Grok 4.6 jest dostępny.
- Hindsight/pamięć jest już skonfigurowana na hoście i nie jest instalowana przez ten skrypt.
- Kanban worker dostaje lifecycle i narzędzia `kanban_*` automatycznie po dispatchu.
- Taski kodujące używają `workspace=worktree:<repo>` jako jedynej warstwy izolacji worktree.
- Profil `coder` nie ma globalnego `worktree: true`, żeby nie tworzyć worktree wewnątrz worktree Kanban.
- `kanban.orchestrator_profile` oraz `kanban.default_assignee` są zapisywane w `DISPATCHER_PROFILE`, nie w przypadkowo aktywnym profilu CLI.

## Orchestrator

Orchestrator ma jawnie włączony toolset `kanban` dla CLI, ale globalnie wyłączone toolsety implementacyjne: terminal, file, code execution, web/browser i image generation. Dispatcher-spawned Kanban worker również dostaje `kanban` przez lifecycle Hermesa.

Orchestrator ma tworzyć nowe karty wyłącznie z jawnym `assignee`.

## Fail-closed routing

Hermes traktuje pusty `kanban.default_assignee` jako fallback do aktywnego profilu, dlatego Software Factory nie używa pustej wartości.

`kanban.default_assignee` wskazuje na `routing-sink`. Ten profil:

- nie implementuje kodu,
- nie tworzy kolejnych kart,
- nie ma toolsetów implementacyjnych,
- po dispatchu korzysta wyłącznie z lifecycle Kanban,
- blokuje źle skierowaną kartę i wymaga jawnego przypisania do właściwego specjalisty.

Dzięki temu nieznany profil z decomposera nie trafia przypadkowo do `default` ani z powrotem do orchestratora.

## Gemini quick-reviewer

Skrypt nie zgaduje identyfikatora modelu Gemini. Po ustaleniu modelu można uruchomić bootstrap z:

```bash
PRIMARY_PROFILE=<profil-gpt> \
GEMINI_MODEL=<MODEL_ID> \
bash hermes/bootstrap_profiles.sh
```

lub ustawić model później:

```bash
hermes -p quick-reviewer config set model.provider gemini
hermes -p quick-reviewer config set model.default <MODEL_ID>
```

## Bezpieczne ponowne uruchomienie

Skrypt:

- nie usuwa profili,
- nie dotyka `auth.json`,
- nie tworzy nested worktree,
- synchronizuje role GPT z `PRIMARY_PROFILE`,
- synchronizuje role Grok z `xai-oauth/grok-4.6`,
- ustawia `tool_loop_guardrails.hard_stop_enabled=true`,
- ustawia fail-closed `routing-sink`,
- wykonuje walidację krytycznych ustawień po bootstrapie.

## Verification

`hermes/verify_bootstrap.sh` jest nieinwazyjny: nie modyfikuje `~/.hermes`. Sprawdza składnię Bash i najważniejsze inwarianty bootstrapu przed uruchomieniem go na hoście.
