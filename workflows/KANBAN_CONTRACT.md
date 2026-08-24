# Kanban task contract v1

Ten dokument doprecyzowuje `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` dla Software Factory uruchamianego przez Hermes Kanban. Standard pozostaje nadrzędnym źródłem prawdy.

## 1. Tryb orkiestracji

- `kanban.auto_decompose=false` — wbudowany decomposer Hermesa jest wyłączony.
- Dekompozycję wykonuje jawnie profil `task-decomposer`.
- Każdy task ma jawnego `assignee`; nierozpoznany routing trafia do `routing-sink`.
- `kanban.auto_subscribe_on_create=true` — twórca tasku może zostać wznowiony po zdarzeniu terminalnym i ocenić dalszy krok.
- Orchestrator koordynuje, ale nie implementuje i nie wykonuje independent review.

## 2. Stany Hermesa

| Stan | Znaczenie w Software Factory |
|---|---|
| `triage` | surowy pomysł lub wymaganie; bez automatycznej dekompozycji |
| `todo` | task opisany, ale oczekuje na zależności lub świadome uruchomienie |
| `ready` | wszystkie wymagane zależności są spełnione i task może być dispatchowany |
| `running` | worker wykonuje dokładnie ten task |
| `blocked` | wymagane wejście/decyzja/provider/evidence uniemożliwia dalszy postęp |
| `review` | task lub zmiana oczekuje na wymagane review/audit/verification |
| `done` | ta konkretna karta została zakończona; nie oznacza automatycznie VERIFIED całej zmiany |
| `archived` | zamknięty historyczny task po zakończeniu lifecycle |

Hermes może przenieść pojedynczą kartę wykonawczą do `done`, gdy worker ją kończy. To jest status **karty**, nie automatyczne potwierdzenie całej zmiany. `IMPLEMENTED != VERIFIED`.

Zmiana feature/bugfix może być uznana za VERIFIED/DONE dopiero wtedy, gdy wymagane przez jej task contract karty review/audytu oraz wymagane evidence są zakończone i nie ma nierozwiązanych blockerów. Orchestrator nie może wywnioskować VERIFIED wyłącznie z `done` karty implementera.

## 3. Task body — wymagane pola

Każdy task wykonawczy powinien zawierać poniższy kontrakt Markdown:

```text
## Task Contract
TYPE: feature|bugfix|audit|docs|release|analysis|architecture|decomposition|review
RISK: low|medium|high|critical
ASSIGNEE: <profile>
REPOSITORY: <owner/repo lub path>
WORKSPACE: none|repo|worktree:<absolute-repo-path>
IMPLEMENTER: <profile|none>
REQUIRED_REVIEWERS: <comma-separated profiles|none>
OPTIONAL_REVIEWERS: <comma-separated profiles|none>
REQUIRED_EVIDENCE: <opis>
ACCEPTANCE_CRITERIA:
- ...
```

Dla tasku modyfikującego kod `WORKSPACE` musi być `worktree:<absolute-repo-path>`. Jedna logiczna zmiana = jeden branch/worktree/current owner.

Pole `WORKSPACE` jest kontraktem Software Factory. Przy tworzeniu tasku Kanban musi zostać odwzorowane na rzeczywiste pola Hermesa: `workspace_kind=worktree` oraz `workspace_path` wskazujące izolowany worktree dla tej karty, nie główny checkout repo. Sam tekst `WORKSPACE:` nie tworzy izolacji.

## 4. Routing

- analiza repozytorium → `repository-analyst` (Ox best-effort; przy jawnie niedostępnym Ox orchestrator tworzy nowy task analizy na GPT),
- architektura → `architect`,
- dekompozycja → `task-decomposer`,
- implementacja → `coder`,
- quick review → `quick-reviewer`,
- deep review → `critic`,
- audit obowiązkowy → `auditor-gpt` i `auditor-grok` zgodnie z klasą zadania,
- Audit 3 → `auditor-ox` tylko jako opcjonalny dodatkowy sygnał,
- dokumentacja → `docs`,
- release gate → `release-manager`,
- nierozpoznany/niebezpieczny routing → `routing-sink`.

## 5. Ox Alpha

Ox Alpha jest opcjonalny i best-effort.

- chwilowy rate limit/przeciążenie nie może blokować podstawowego gate GPT+Grok,
- dla `repository-analyst` orchestrator może utworzyć zastępczy task na GPT po jawnej porażce Ox,
- `auditor-ox` przy niedostępności kończy wynik `DECISION: SKIPPED_OX_UNAVAILABLE`,
- `SKIPPED_OX_UNAVAILABLE` jest dozwolone wyłącznie dla opcjonalnego Audit 3,
- żadna porażka Ox nie może zostać przedstawiona jako `APPROVE`.

## 6. Kontrakt review/audit

Wynik reviewerów musi kończyć się dokładnie jedną linią decyzji:

```text
DECISION: APPROVE
```

albo:

```text
DECISION: CHANGES_REQUIRED
```

Dla opcjonalnego `auditor-ox` dopuszczalne jest także:

```text
DECISION: SKIPPED_OX_UNAVAILABLE
```

Każdy finding zawiera co najmniej jawne pole `severity`, a także `location`, `evidence`, `impact`, `proposed fix`. Pole severity może być zapisane zwykłym Markdownem, np. `severity: HIGH`, ``- `severity`: HIGH`` albo w tabeli `| severity | HIGH |`.

- wiarygodny HIGH/CRITICAL → `CHANGES_REQUIRED`,
- brak decyzji, wiele decyzji lub nieparsowalny wynik → `REVIEW_PENDING`, nigdy APPROVE,
- implementer nie może zatwierdzić własnej zmiany jako independent reviewer.

Parser decyzji nie zgaduje severity z dowolnej prozy; gate opiera się na jawnym polu `severity` w strukturze findingu. Reviewer ma obowiązek użyć tego pola dla każdego findingu.

## 7. Minimalna ścieżka feature/bugfix

Typowy feature:

`repository-analyst? → architect → task-decomposer → coder → quick-reviewer → critic/required audits → required real evidence → docs? → release-manager? → done`

Typowy bugfix:

`reproducer/root cause → coder + regression test → tests → quick-reviewer → required independent review → targeted verification → done`

Znaki `?` oznaczają etap wymagany tylko przez zakres/ryzyko/task contract.

## 8. Reguły przejść

- worker może zakończyć własną kartę wykonawczą jako `done`, ale nie może sam nadać całej zmianie statusu VERIFIED,
- nadrzędna zmiana pozostaje nieweryfikowana, dopóki wszystkie wymagane review/audit/evidence z task contract nie są zamknięte,
- `CHANGES_REQUIRED` tworzy jawny follow-up dla implementera i nie pozwala zamknąć nadrzędnej zmiany,
- `REVIEW_PENDING` zatrzymuje przejście całej zmiany do VERIFIED/DONE,
- brak wymaganego evidence → `blocked` albo pozostanie w `review`, nie VERIFIED,
- `release-manager` odmawia release przy brakującym required review/evidence lub wiarygodnym nierozwiązanym HIGH/CRITICAL.
