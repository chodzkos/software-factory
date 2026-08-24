# Hermes Software Factory

Ta sekcja zawiera deklaratywną konfigurację profili Hermesa dla Software Factory.

## Polityka modeli

| Profil | Rola | Domyślny model |
|---|---|---|
| `orchestrator` | koordynacja i routing Kanban | główny GPT z `PRIMARY_PROFILE` |
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

Ta polityka rozdziela zadania według kosztu i niezależności: GPT jest głównym modelem reasoning/coding, Grok odpowiada za niezależną krytykę i audyt, Gemini za częste tańsze zadania, a Ox Alpha za analizę repozytorium i dodatkowy trzeci audyt.

## Ważne: profil głównego GPT

Skrypt nie zakłada, że `default` jest GPT. Jeśli wskazany `PRIMARY_PROFILE` nie wygląda na profil GPT, bootstrap przerwie działanie zamiast utworzyć mylący profil `auditor-gpt`.

Na hoście testowym główny GPT jest utrzymywany jako osobny profil, dzięki czemu `default` może pozostać na innym modelu:

```bash
PRIMARY_PROFILE=primary-gpt \
DISPATCHER_PROFILE=default \
bash hermes/bootstrap_profiles.sh
```

## Gemini

Domyślny routing częstych, tańszych ról jest przypięty do konkretnego modelu:

```text
provider: gemini
model: gemini-3.5-flash-lite
```

Profile korzystające z Gemini:

- `task-decomposer`
- `quick-reviewer`
- `docs`

Można jawnie nadpisać model na czas bootstrapu:

```bash
GEMINI_MODEL=<MODEL_ID> \
PRIMARY_PROFILE=primary-gpt \
DISPATCHER_PROFILE=default \
bash hermes/bootstrap_profiles.sh
```

Fallback z Gemini do GPT jest decyzją warstwy routingu/task contract, nie ukrytym automatycznym przełączeniem w profilu. Dzięki temu awaria lub limit Gemini jest widoczny i audytowalny.

## Ox Alpha

Domyślny routing:

```text
provider: openrouter
model: stealth/ox-alpha
```

Ox jest traktowany jako **opcjonalny** provider, ponieważ model jest obecnie stealth i jego przyszła dostępność/koszt mogą się zmienić.

Gdy Ox jest dostępny:

- `repository-analyst` używa Ox Alpha,
- tworzony i konfigurowany jest `auditor-ox` jako dodatkowy trzeci audyt.

Ox można świadomie wyłączyć bez blokowania podstawowej fabryki:

```bash
OX_MODEL="" \
PRIMARY_PROFILE=primary-gpt \
DISPATCHER_PROFILE=default \
bash hermes/bootstrap_profiles.sh
```

W takim trybie:

- `repository-analyst` przechodzi na primary GPT,
- `auditor-ox` nie jest tworzony ani wymagany,
- podstawowy niezależny gate nadal może opierać się na rozdzieleniu GPT implementer / Grok reviewer-auditor.

Jeśli `auditor-ox` istniał z wcześniejszego wdrożenia, jego obecność nie oznacza automatycznie, że ma być używany po wyłączeniu Ox; decyzję o wymaganym Audit 3 podejmuje warstwa workflow.

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
```

Bootstrap jest idempotentny względem tworzenia profili: istniejący profil jest wykrywany po katalogu `~/.hermes/profiles/<name>`. Przy każdym uruchomieniu odświeżane są role SOUL oraz jawnie zarządzane ustawienia/model routing.

## Założenia

- `PRIMARY_PROFILE` jest działającym profilem z głównym modelem GPT i poprawnym auth.
- `DISPATCHER_PROFILE` to profil, z którego faktycznie działa gateway/dispatcher; domyślnie `default`.
- `xai-oauth` jest zalogowany i Grok 4.6 jest dostępny.
- provider `gemini` jest skonfigurowany i `gemini-3.5-flash-lite` jest dostępny.
- OpenRouter jest skonfigurowany, jeśli Ox jest włączony.
- Hindsight jest już skonfigurowany na hoście i bootstrap go nie instaluje ani nie rekonfiguruje.
- Kanban worker dostaje lifecycle i narzędzia `kanban_*` automatycznie po dispatchu.
- Taski kodujące używają `workspace=worktree:<repo>` jako jedynej warstwy izolacji worktree.
- Bootstrap jawnie ustawia `coder worktree=false` i `worktree_sync=false`, aby nie odziedziczyć tych flag z `PRIMARY_PROFILE`.
- `kanban.orchestrator_profile` oraz `kanban.default_assignee` są zapisywane w `DISPATCHER_PROFILE`.

## Orchestrator

Orchestrator jest coordination-only. Nie dostaje terminala, narzędzi plikowych, code execution, web/browser, image generation, delegation, `computer_use` ani `cronjob`.

Żeby mimo tego zawsze znał nadrzędny Software Development Standard, bootstrap buduje jego runtime `~/.hermes/profiles/orchestrator/SOUL.md` z dwóch części:

1. rolowego `hermes/profiles/orchestrator/SOUL.md`,
2. aktualnej treści kanonicznego `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`.

To jest generowany kontekst runtime, nie drugie źródło prawdy. Przy każdym bootstrapie jest odtwarzany z pliku kanonicznego.

Kanban jest włączany przez top-level `toolsets=["hermes-cli","kanban"]`. `agent.disabled_toolsets` następnie usuwa z profilu orchestratora narzędzia implementacyjne i dodatkowe powierzchnie sterujące.

## Fail-closed routing

`kanban.default_assignee` wskazuje na `routing-sink`, ponieważ pusty fallback może skierować task do aktywnego/default profilu.

`routing-sink`:

- nie implementuje kodu,
- nie tworzy kolejnych kart zgodnie z SOUL,
- nie ma toolsetów implementacyjnych,
- nie ma `computer_use` ani `cronjob`,
- po dispatchu korzysta z lifecycle Kanban,
- blokuje źle skierowaną kartę i wymaga jawnego przypisania do właściwej roli.

## Bezpieczne ponowne uruchomienie

Skrypt:

- nie usuwa profili,
- nie dotyka `auth.json`,
- wymusza `coder worktree=false` i `worktree_sync=false`,
- synchronizuje role GPT z `PRIMARY_PROFILE`,
- synchronizuje role Grok z `xai-oauth/grok-4.6`,
- synchronizuje `task-decomposer`, `quick-reviewer` i `docs` z Gemini,
- synchronizuje `repository-analyst` i opcjonalny `auditor-ox` z Ox, jeśli Ox jest włączony,
- utrzymuje działający fallback analizy repozytorium do GPT po `OX_MODEL=""`,
- ustawia `tool_loop_guardrails.hard_stop_enabled=true`,
- ustawia fail-closed `routing-sink`,
- wstrzykuje kanoniczny Standard do runtime kontekstu orchestratora,
- jawnie ustawia Kanban w top-level `toolsets` orchestratora,
- wykonuje walidację krytycznych ustawień po bootstrapie.

## Verification

`hermes/verify_bootstrap.sh` jest nieinwazyjny: nie modyfikuje `~/.hermes`. Sprawdza składnię Bash oraz inwarianty profili, model policy, worktree, Kanban i SOUL przed uruchomieniem bootstrapu na hoście.
