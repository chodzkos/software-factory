# Hermes Software Factory

Ta sekcja zawiera deklaratywną konfigurację profili Hermesa dla Software Factory.

## Profile

| Profil | Rola | Domyślny model |
|---|---|---|
| `orchestrator` | dekompozycja i routing Kanban | klon `default` |
| `architect` | wymagania i architektura | klon `default` |
| `coder` | implementacja | klon `default` |
| `quick-reviewer` | tani pierwszy review | Gemini po jawnej konfiguracji |
| `critic` | niezależny deep review | `xai-oauth / grok-4.6` |
| `auditor-gpt` | końcowy audyt GPT | klon `default` |
| `auditor-grok` | końcowy audyt Grok | `xai-oauth / grok-4.6` |
| `release-manager` | release gate | klon `default` |

## Instalacja

Na serwerze:

```bash
cd ~/projects/software-factory
git switch main
git pull --ff-only
bash hermes/bootstrap_profiles.sh
```

Skrypt jest idempotentny w tym sensie, że nie usuwa istniejących profili. Jeżeli profil już istnieje, pomija jego utworzenie i odświeża tylko SOUL.md oraz wybrane bezpieczne ustawienia.

## Założenia

- `default` jest już działającym profilem z głównym modelem GPT i poprawnym auth.
- `xai-oauth` jest już zalogowany; Grok 4.6 jest dostępny.
- Hindsight/pamięć jest już skonfigurowana na hoście i nie jest instalowana przez ten skrypt.
- Kanban worker dostaje lifecycle i narzędzia `kanban_*` automatycznie po dispatchu.
- Taski kodujące powinny używać `--workspace worktree:<repo>` lub równoważnego worktree z dashboardu/CLI.

## Gemini quick-reviewer

Skrypt nie zgaduje identyfikatora modelu Gemini. Po ustaleniu modelu można uruchomić np.:

```bash
quick-reviewer config set model.provider gemini
quick-reviewer config set model.default <MODEL_ID>
```

albo ustawić przed bootstrapem:

```bash
GEMINI_MODEL=<MODEL_ID> bash hermes/bootstrap_profiles.sh
```
