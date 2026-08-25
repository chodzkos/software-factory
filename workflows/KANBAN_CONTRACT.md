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

Pole `WORKSPACE` jest kontraktem Software Factory. Przy tworzeniu tasku Kanban `workspace_kind=worktree` oraz create-time `workspace_path` wskazują repo bazowe, z którego Hermes utworzy izolowany worktree. Po claimie Hermes zapisuje materializowany worktree bezpośrednio w `workspace_path`; ten post-claim path jest źródłem prawdy dla późniejszego review. Sam tekst `WORKSPACE:` nie tworzy izolacji.

### 3.1. Fail-closed runtime gate

Pola zapisane wyłącznie w `body` nie są dowodem konfiguracji runtime. Przed dopuszczeniem taska do `ready` orchestrator porównuje oczekiwany kontrakt z **faktycznymi polami taska**.

#### Kwarantanna

W Hermes 0.20.4 samo `initial_status=blocked` **nie jest sticky quarantine**: create zapisuje status `blocked`, ale nie emituje sticky `blocked` event, więc dispatcher może auto-promote taki task po spełnieniu zależności. Dlatego Software Factory nie używa samego create-time `blocked` jako pre-dispatch gate.

Dla taska wymagającego runtime validation orchestrator stosuje kontrolny parent gate:

1. tworzy osobną kartę kontrolną przypisaną do `routing-sink`,
2. natychmiast zapisuje na niej sticky `kanban block --kind needs_input` z powodem `RUNTIME_CONTRACT_PENDING`,
3. dopiero potem tworzy właściwy task z parentem wskazującym tę kartę kontrolną,
4. właściwy task pozostaje zależny od niedokończonego parenta i nie może zostać promowany do `ready`,
5. orchestrator waliduje actual runtime fields właściwego taska,
6. drift => gate parent pozostaje blocked, task nie jest dispatchowany, zapisywane jest `RUNTIME_CONTRACT_DRIFT`,
7. zgodność => orchestrator kończy wyłącznie techniczną kartę kontrolną, co pozwala zależnemu taskowi przejść dalej.

Karta kontrolna nie jest kartą implementacyjną ani review i nie stanowi evidence wykonania zmiany. Jej jedyną rolą jest atomowe z perspektywy zależnego taska zatrzymanie dispatchu do czasu walidacji.

#### Powierzchnie read/write Hermesa 0.20.4

Software Factory nie zakłada jednego syntetycznego snapshotu. Używa dwóch rzeczywistych powierzchni:

- `hermes kanban create ... --json` jako create receipt dla pól dostępnych w CLI JSON, w szczególności `assignee`, `workspace_kind`, create-time `workspace_path`, `branch_name`, `max_retries`,
- `kanban_show` / `hermes kanban show` do ponownego odczytu taska oraz parent dependency; `parents` mogą być zwrócone obok obiektu `task`, więc `hermes/kanban_runtime_contract.py` normalizuje ten kształt.

Tool `kanban_create` nie ustawia wszystkich pól wymaganych przez factory contract. Jeżeli task wymaga jawnego `branch_name` albo `max_retries`, orchestrator musi użyć jawnej powierzchni Hermes CLI `hermes kanban create --branch ... --max-retries ... --json`, a następnie sprawdzić wartości z create receipt. Nie wolno deklarować tych wartości wyłącznie w Markdown body.

Mechaniczny gate waliduje co najmniej:

- `assignee`,
- `workspace_kind`,
- create-time `workspace_path`,
- `branch_name`, jeżeli jest wymagany,
- `max_retries`, jeżeli jest wymagany,
- `parents` przez znormalizowany odczyt taska.

`max_runtime` musi być jawnie ustawiony przy create, ale Hermes 0.20.4 nie wystawia go w stabilnym JSON readback używanym przez ten validator. Software Factory **nie twierdzi**, że `max_runtime` jest obecnie mechanicznie potwierdzony przez ten gate. To jawne ograniczenie pozostaje fail-visible do czasu dodania stabilnego readbacku w Hermesie.

Referencyjna logika normalizacji i walidacji znajduje się w `hermes/kanban_runtime_contract.py`. Każda niezgodność oznacza `RUNTIME_CONTRACT_DRIFT` i nie może zostać przykryta poprawnym tekstem w body lub summary workera.

### 3.2. Handoff implementer → independent reviewer

Dla zmiany wykonywanej w worktree reviewer musi czytać dokładnie artefakt implementera.

- task implementera używa `workspace_kind=worktree`,
- reviewer **nie jest tworzony z wyprzedzeniem**, jeśli jego workspace zależy od worktree implementera,
- po terminalnym zakończeniu implementera orchestrator ponownie odczytuje jego live task,
- w Hermes 0.20.4 resolved worktree po claimie jest zapisany w `implementation.workspace_path`,
- resolved path jest akceptowany tylko wtedy, gdy jest absolutny i dla taska `t_X` wskazuje `/.worktrees/t_X`; repo root nie jest resolved worktree,
- reviewer jest tworzony jako `workspace_kind=dir` z `workspace_path` równym dokładnie temu post-claim `implementation.workspace_path`,
- reviewer ma parent wskazujący task implementera,
- implementer i independent reviewer muszą być różnymi profilami,
- tworzenie reviewer taska jako `worktree:<repo-root>` jest zabronione, ponieważ Hermes utworzy wtedy drugi, niezależny worktree.

Brak post-claim worktree path, inny path, inny workspace kind, brak parenta lub ten sam profil implementera i reviewera powoduje fail-closed i zatrzymanie review.

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

- orchestrator nie dispatchuje taska z `RUNTIME_CONTRACT_DRIFT`; jego gate parent pozostaje blocked,
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
