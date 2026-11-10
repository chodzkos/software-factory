# software-factory

Repozytorium źródłowe dla standardów, wiedzy, workflowów i kontraktów agentów rozwijających projekty `chodzkos/*`.

## Source of truth

Najważniejszym dokumentem jest:

- `standards/SOFTWARE_DEVELOPMENT_STANDARD.md`

Pozostałe materiały wspierają standard, ale go nie nadpisują.

## Zakres

Repo ma docelowo zawierać:

- globalny Software Development Standard,
- decision log i rejestr non-compliance,
- Project Memory Packs,
- workflowy dla feature/bugfix/audit/release,
- kontrakty ról agentów,
- szablony specyfikacji, review i findingów.

## Kanoniczne repo współdzielone

- `chodzkos/gui-kit` — wspólne desktop GUI,
- `chodzkos/chodzkos-detection` — lekkie sondy CLI/HTTP.

## Stan

Bootstrap v1.0 standardu po niezależnym review GPT × Grok.
Profile Hermesa i automatyzacja Kanban będą dodawane w kolejnych PR-ach.

### Druga recenzja kandydata 0.13.0

Aktualne rozstrzygnięcia MoA, `extra_body`, retry Responses i ograniczeń
tożsamości źródeł: [REVIEW_ROUND2_013](hermes/native_integration/REVIEW_ROUND2_013.md).
Kandydat pozostaje nieinstalowalny; żadna poprawka źródeł nie zatwierdza providera.
