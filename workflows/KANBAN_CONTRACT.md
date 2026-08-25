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

Pole `WORKSPACE` jest kontraktem Software Factory. Przy tworzeniu tasku Kanban musi zostać odwzorowane na rzeczywiste pola Hermesa: `workspace_kind=worktree` oraz `workspace_path` wskazujące repo bazowe dla worktree tej karty. Po claimie źródłem prawdy o faktycznym izolowanym worktree jest resolved workspace zwracany przez Hermesa. Sam tekst `WORKSPACE:` nie tworzy izolacji.

### 3.1. Fail-closed runtime gate

Pola zapisane wyłącznie w `body` nie są dowodem konfiguracji runtime. Przed dispatch orchestrator porównuje oczekiwany kontrakt z **faktycznymi polami taska** zwróconymi przez Kanban.

Każdy task tworzony przez orchestratora powinien wejść najpierw do kwarantanny `blocked` (`initial_status=blocked`). Dopiero po odczycie taska i zgodności runtime fields orchestrator może wykonać `unblock`/`promote`. Drift pozostaje zablokowany i jest kierowany do `routing-sink` lub wymaga jawnej korekty operatora.

Walidowane są co najmniej:

- `assignee`,
- `workspace_kind`,
- `workspace_path`,
- `branch_name`,
- `max_retries`,
- `max_runtime`,
- `parents`.

Referencyjna logika walidacji znajduje się w `hermes/kanban_runtime_contract.py`. Każda niezgodność oznacza `RUNTIME_CONTRACT_DRIFT` i **nie może** zostać zinterpretowana jako zgodność na podstawie poprawnego tekstu w `body` lub summary workera.

Jeżeli wymagane pole runtime nie może zostać ustawione lub odczytane przez aktualne narzędzie Hermesa, orchestrator blokuje kartę zamiast zastępować to pole opisem Markdown.

### 3.2. Handoff implementer → independent reviewer

Dla zmiany wykonywanej w worktree reviewer musi czytać dokładnie artefakt implementera.

- task implementera używa `workspace_kind=worktree`,
- reviewer **nie jest tworzony z wyprzedzeniem**, jeśli jego workspace zależy od resolved worktree implementera,
- po terminalnym zakończeniu implementera orchestrator ponownie odczytuje jego live task i pobiera resolved worktree,
- reviewer jest tworzony jako `workspace_kind=dir` z `workspace_path` równym dokładnie resolved worktree implementera,
- reviewer ma parent wskazujący task implementera,
- implementer i independent reviewer muszą być różnymi profilami,
- tworzenie reviewer taska jako `worktree:<repo-root>` jest zabronione, ponieważ Hermes utworzy wtedy drugi, niezależny worktree.

Brak resolved worktree, inny path, inny workspace kind, brak parenta lub ten sam profil implementera i reviewera powoduje fail-closed i zatrzymanie review.

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
- brak decyzji, wiele decyzji, dodatkowy nieobsługiwany marker `DECISION:` lub nieparsowalny wynik → `REVIEW_PENDING`, nigdy APPROVE,
- implementer nie może zatwierdzić własnej zmiany jako independent reviewer.

Parser decyzji nie zgaduje severity z dowolnej prozy; gate opiera się na jawnym polu `severity` w strukturze findingu. Reviewer ma obowiązek użyć tego pola dla każdego findingu.

## 7. Minimalna ścieżka feature/bugfix

Typowy feature:

`repository-analyst? → architect → task-decomposer → coder → quick-reviewer → critic/required audits → required real evidence → docs? → release-manager? → done`

Typowy bugfix:

`reproducer/root cause → coder + regression test → tests → quick-reviewer → required independent review → targeted verification → done`

Znaki `?` oznaczają etap wymagany tylko przez zakres/ryzyko/task contract.

## 8. Reguły przejść

- orchestrator nie dispatchuje taska z `RUNTIME_CONTRACT_DRIFT`; task pozostaje `blocked`,
- worker może zakończyć własną kartę wykonawczą jako `done`, ale nie może sam nadać całej zmianie statusu VERIFIED,
- nadrzędna zmiana pozostaje nieweryfikowana, dopóki wszystkie wymagane review/audit/evidence z task contract nie są zamknięte,
- `CHANGES_REQUIRED` tworzy jawny follow-up dla implementera i nie pozwala zamknąć nadrzędnej zmiany,
- `REVIEW_PENDING` zatrzymuje przejście całej zmiany do VERIFIED/DONE,
- brak wymaganego evidence → `blocked` albo pozostanie w `review`, nie VERIFIED,
- reviewer worktree handoff nie może być zastąpiony drugim worktree ani samym parent summary,
- `release-manager` odmawia release przy brakującym required review/evidence lub wiarygodnym nierozwiązanym HIGH/CRITICAL.

## 9. Obowiązkowy krok wdrożenia Kanbana

Po merge zmian kontraktu Kanban i po zwykłym bootstrapie profili konfiguracja runtime Kanbana **musi** zostać jawnie zastosowana do profilu dispatchera:

```bash
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

Ten krok jest wymagany przed utworzeniem pierwszego tasku Software Factory. Sam `bootstrap_profiles.sh` nie zastępuje konfiguracji Kanbana i nie wyłącza wbudowanego decomposera Hermesa.

Po wykonaniu należy potwierdzić co najmniej:

```bash
hermes -p default config get kanban.auto_decompose
hermes -p default config get kanban.auto_subscribe_on_create
hermes -p default config get kanban.orchestrator_profile
hermes -p default config get kanban.default_assignee
```

Oczekiwane wartości:

```text
false
true
orchestrator
routing-sink
```

Dopóki ten krok nie zakończy się poprawnie, Software Factory nie jest gotowy do uruchamiania tasków Kanban.
