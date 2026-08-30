# Kanban task contract v1

Ten dokument doprecyzowuje `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` dla Software Factory uruchamianego przez Hermes Kanban. Standard pozostaje nadrzędnym źródłem prawdy. Politykę doboru backendu/modelu i reviewerów doprecyzowuje `workflows/MODEL_ROUTING_POLICY.md`.

## 1. Tryb orkiestracji

- `kanban.auto_decompose=false` — wbudowany decomposer Hermesa jest wyłączony.
- Dekompozycję wykonuje jawnie profil `task-decomposer`.
- Każdy task ma jawnego `assignee`; nierozpoznany routing trafia do `routing-sink`.
- `kanban.auto_subscribe_on_create=true` — twórca tasku może zostać wznowiony po zdarzeniu terminalnym i ocenić dalszy krok.
- Orchestrator koordynuje, ale nie implementuje i nie wykonuje independent review.
- Mechaniczne operacje runtime wymagające Hermes CLI wykonuje profil `runtime-controller`; orchestrator sam nie dostaje terminala.

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

Hermes może przenieść pojedynczą kartę wykonawczą do `done`, gdy worker ją kończy bez wymaganego handoffu albo po zakończeniu jej natywnego review lifecycle. To jest status **karty**, nie automatyczne potwierdzenie całej zmiany. `IMPLEMENTED != VERIFIED`.

Zmiana feature/bugfix może być uznana za VERIFIED/DONE dopiero wtedy, gdy wszystkie wymagane przez jej task contract review/audyty oraz wymagane evidence są zakończone i nie ma nierozwiązanych blockerów. Orchestrator nie może wywnioskować VERIFIED wyłącznie z zakończenia runu implementera.

## 3. Task body — wymagane pola

Każdy task wykonawczy powinien zawierać poniższy kontrakt Markdown:

```text
## Task Contract
TYPE: feature|bugfix|audit|docs|release|analysis|architecture|decomposition|review
RISK: low|medium|high|critical
SECURITY_SENSITIVE: yes|no
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

Dla tasku modyfikującego kod lub review tej zmiany `SECURITY_SENSITIVE` jest obowiązkowe. Brak pola oznacza routing drift; nie wolno domyślnie przyjąć `no`.

Dla tasku modyfikującego kod `WORKSPACE` musi być `worktree:<absolute-repo-path>`. Jedna logiczna zmiana = jeden branch/worktree/current owner.

Pole `WORKSPACE` jest kontraktem Software Factory. Przy tworzeniu tasku Kanban `workspace_kind=worktree` oraz create-time `workspace_path` wskazują repo bazowe, z którego Hermes utworzy izolowany worktree. Po claimie Hermes zapisuje materializowany worktree bezpośrednio w `workspace_path`; ten post-claim path jest źródłem prawdy dla późniejszego review. Sam tekst `WORKSPACE:` nie tworzy izolacji.

### 3.1. Fail-closed runtime gate

Pola zapisane wyłącznie w `body` nie są dowodem konfiguracji runtime. Przed dopuszczeniem taska do `ready` oczekiwany kontrakt jest porównywany z **faktycznymi polami taska**, a model/reviewer routing jest walidowany przez wykonywalną politykę.

#### Runtime controller

Orchestrator pozostaje coordination-only i nie ma terminala. Hermes 0.20.4 LLM tool `kanban_create` nie potrafi ustawić wszystkich pól wymaganych przez factory contract, w szczególności jawnego `branch_name` i `max_retries`.

Dlatego dla kart wymagających tych pól orchestrator deleguje mechaniczne utworzenie i runtime gate do profilu `runtime-controller`.

`runtime-controller`:

- nie implementuje kodu i nie wykonuje independent review,
- ma osobny minimalny profil runtime,
- używa terminala wyłącznie do repozytoryjnego wrappera `hermes/kanban_runtime_cli.sh`, walidatora `hermes/kanban_runtime_contract.py` i wykonywalnej polityki `hermes/model_routing_policy.py`,
- wrapper whitelistuje tylko operacje `create`, `show`, `block`, `complete`, `validate-runtime`, `validate-handoff`, `validate-routing` i przekazuje argumenty bez `eval`,
- przed create może sprawdzić proponowane body przez `validate-routing --task-body <exact-task-body>`,
- po create/readback i przed zwolnieniem gate albo dispatch review obowiązkowo uruchamia `validate-routing --actual-json <live-show-json>`; rzeczywiste body karty jest source of truth dla `IMPLEMENTER`, `REQUIRED_REVIEWERS` i `SECURITY_SENSITIVE`,
- `MODEL_ROUTING_DRIFT` pozostawia gate zablokowany tak samo jak `RUNTIME_CONTRACT_DRIFT`,
- nie wykonuje Git, curl, package managerów ani dowolnych poleceń projektu.

Profil instaluje osobny, obowiązkowy bootstrap:

```bash
PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh
```

Brak działającego `runtime-controller` oznacza fail-closed: orchestrator nie może zastąpić wymaganych pól ani model routing policy opisem Markdown ani samodzielnie poszerzyć swoich uprawnień o terminal.

#### Kwarantanna

W Hermes 0.20.4 samo `initial_status=blocked` **nie jest sticky quarantine**: create zapisuje status `blocked`, ale nie emituje sticky `blocked` event, więc dispatcher może auto-promote taki task po spełnieniu zależności. Dlatego Software Factory nie używa samego create-time `blocked` jako pre-dispatch gate.

Dla taska wymagającego runtime validation `runtime-controller` stosuje kontrolny parent gate:

1. tworzy osobną kartę kontrolną przypisaną do `routing-sink`,
2. natychmiast zapisuje na niej sticky `kanban block --kind needs_input` z powodem `RUNTIME_CONTRACT_PENDING`,
3. dopiero potem tworzy właściwy task z parentem wskazującym tę kartę kontrolną,
4. właściwy task pozostaje zależny od niedokończonego parenta i nie może zostać promowany do `ready`,
5. `runtime-controller` waliduje actual runtime fields właściwego taska oraz model/reviewer routing,
6. drift => gate parent pozostaje blocked, task nie jest dispatchowany, zapisywane jest `RUNTIME_CONTRACT_DRIFT` lub `MODEL_ROUTING_DRIFT`,
7. zgodność => `runtime-controller` kończy wyłącznie techniczną kartę kontrolną, co pozwala zależnemu taskowi przejść dalej.

Karta kontrolna nie jest kartą implementacyjną ani review i nie stanowi evidence wykonania zmiany. Jej jedyną rolą jest zatrzymanie zależnego taska do czasu walidacji. Utworzenie gate i sticky block jest sekwencyjne, nie atomowe; `routing-sink` jest fail-closed backstopem, jeśli gate zostanie claimed w tym krótkim oknie.

#### Powierzchnie read/write Hermesa 0.20.4

Software Factory nie zakłada jednego syntetycznego snapshotu. Używa dwóch rzeczywistych powierzchni:

- przez `runtime-controller`: `hermes/kanban_runtime_cli.sh create ... --json`, który wywołuje `hermes kanban create ... --json` i daje create receipt dla pól dostępnych w CLI JSON, w szczególności `assignee`, `workspace_kind`, create-time `workspace_path`, `branch_name`, `max_retries`,
- `kanban_show` / `hermes/kanban_runtime_cli.sh show ... --json` do ponownego odczytu taska oraz parent dependency; `parents` mogą być zwrócone obok obiektu `task`, więc `hermes/kanban_runtime_contract.py` normalizuje ten kształt.

Jeżeli task wymaga jawnego `branch_name` albo `max_retries`, wartości muszą zostać ustawione przez `runtime-controller` za pomocą wrappera i flag `--branch ... --max-retries ... --json`, a następnie sprawdzone z create receipt. Nie wolno deklarować tych wartości wyłącznie w Markdown body.

Mechaniczny gate waliduje co najmniej:

- `assignee`,
- `workspace_kind`,
- create-time `workspace_path`,
- `branch_name`, jeżeli jest wymagany,
- `max_retries`, jeżeli jest wymagany,
- `parents` przez znormalizowany odczyt taska,
- model/reviewer route przez `hermes/model_routing_policy.py` na actual task body z live readback JSON.

`max_runtime` musi być jawnie ustawiony przy create, ale Hermes 0.20.4 nie wystawia go w stabilnym JSON readback używanym przez ten validator. Software Factory **nie twierdzi**, że `max_runtime` jest obecnie mechanicznie potwierdzony przez ten gate. To jawne ograniczenie pozostaje fail-visible do czasu dodania stabilnego readbacku w Hermesie.

Referencyjna logika normalizacji i walidacji znajduje się w `hermes/kanban_runtime_contract.py`. Każda niezgodność oznacza `RUNTIME_CONTRACT_DRIFT` i nie może zostać przykryta poprawnym tekstem w body lub summary workera.

### 3.2. Handoff implementer → independent reviewer

Dla zmiany wykonywanej w worktree reviewer musi czytać dokładnie artefakt implementera. Software Factory używa natywnego **same-card review flow** Hermesa 0.20.4, zweryfikowanego live w Pilocie 7B.

- task implementera jest tworzony jako `workspace_kind=worktree` z create-time `workspace_path` wskazującym repo bazowe,
- po claimie Hermes materializuje worktree i zapisuje jego path bezpośrednio w `task.workspace_path`,
- resolved path jest akceptowany tylko wtedy, gdy jest absolutny i dla taska `t_X` wskazuje `/.worktrees/t_X`; repo root nie jest resolved worktree,
- implementer kończy swój run przez natywne `review_requested`, nie przez utworzenie osobnej reviewer card,
- ta sama karta przechodzi do `status=review`, a `assignee` zmienia się na wymagany profil independent reviewera,
- `workspace_kind` pozostaje `worktree`, a `workspace_path` pozostaje dokładnie tym samym resolved worktree implementera,
- historia implementacji pozostaje w `runs`: bieżący/najnowszy implementer run ma `outcome=review_requested`,
- najnowszy event `review_requested` musi wskazywać oczekiwane różne profile `implementer` i `reviewer`,
- jeśli event zawiera `run_id`, musi on wskazywać dokładnie bieżący run implementera,
- `metadata.workspace_path` w runie jest dodatkowym corroboration: jeśli istnieje, musi zgadzać się z live `task.workspace_path`; jego brak nie blokuje poprawnego natywnego handoffu,
- dispatcher uruchamia reviewera na tej samej karcie i w tym samym worktree; nie wolno tworzyć drugiego worktree ani osobnej karty tylko po to, aby przekazać workspace.

Przed dispatch review należy obowiązkowo zlecić `runtime-controller validate-handoff` na live JSON tej samej karty oraz `validate-routing --actual-json <live-task-json>`. Brak obu pozytywnych wyników `RUNTIME_CONTRACT_OK` i `MODEL_ROUTING_OK` oznacza fail-closed i reviewer nie może zostać dispatchowany.

Validator handoff wymaga co najmniej:

- resolved `workspace_kind=worktree` i `workspace_path=.../.worktrees/<task-id>`,
- `assignee` równego oczekiwanemu reviewerowi,
- `status=review`,
- najnowszego zgodnego eventu `review_requested` z różnymi profilami implementera i reviewera,
- bieżącego/najnowszego implementer runu z `outcome=review_requested`,
- spójności `event.run_id` z tym runem, gdy `run_id` jest dostępny,
- zgodności `metadata.workspace_path` z live resolved worktree tylko wtedy, gdy metadata to pole zawiera.

Brak któregokolwiek z tych dowodów powoduje fail-closed i zatrzymanie dispatch review. Body, summary ani parent result nie zastępują live task/event/run evidence.

Osobny reviewer task pozostaje dopuszczalny wyłącznie wtedy, gdy workflow rzeczywiście wymaga odrębnej jednostki pracy niezależnej od natywnego handoffu tej karty. Nie może służyć jako emulacja same-card worktree review.

## 4. Routing

- analiza repozytorium → `repository-analyst` na primary GPT,
- architektura → `architect`; trudna architektura/hard reasoning może jawnie eskalować do `architect-claude-opus`,
- dekompozycja → `task-decomposer`,
- mechaniczna kontrola/create runtime/model routing → `runtime-controller`,
- implementacja → `coder` (native OpenAI) albo `coder-claude` (Claude Code skill),
- quick review/CI triage → `quick-reviewer` jako niewystarczający samodzielnie pre-pass,
- normalny independent review `coder` → `reviewer-claude`,
- normalny independent review `coder-claude` → `reviewer-gpt`,
- `SECURITY_SENSITIVE: yes` → `reviewer-gpt`; gdy implementer=`coder`, dodatkowo `critic`,
- deep general review → `critic`,
- audit obowiązkowy → `auditor-gpt` i `auditor-grok` zgodnie z klasą zadania,
- dokumentacja → `docs`,
- release gate → `release-manager`,
- nierozpoznany/niebezpieczny routing → `routing-sink`.

Ox Alpha nie jest aktywnym backendem Software Factory i nie może być używany jako implementer/reviewer/auditor w nowych task contracts.

## 5. Model routing i Claude Code

Wykonywalna macierz znajduje się w `hermes/model_routing_policy.py`, a pełny opis w `workflows/MODEL_ROUTING_POLICY.md`.

- `coder` i `reviewer-gpt` używają `factory.execution_backend=native-openai`,
- `coder-claude`, `reviewer-claude`, `architect-claude-opus` używają `factory.execution_backend=claude-code`,
- profile Claude są koordynatorami Hermesa; właściwa praca musi pochodzić z bundlowanego skilla `claude-code` i Claude Code CLI,
- zwykły Claude backend używa klasy modelu `sonnet`; trudna architektura może użyć `opus`,
- `reviewer-claude` jest zabroniony dla `SECURITY_SENSITIVE: yes`,
- niedostępny backend/CLI/OAuth oznacza blocked; nie ma ukrytego fallbacku.

## 6. Kontrakt review/audit

Wynik reviewerów musi kończyć się dokładnie jedną linią decyzji:

```text
DECISION: APPROVE
```

albo:

```text
DECISION: CHANGES_REQUIRED
```

Każdy finding zawiera co najmniej jawne pole `severity`, a także `location`, `evidence`, `impact`, `proposed fix`. Pole severity może być zapisane zwykłym Markdownem, np. `severity: HIGH`, ``- `severity`: HIGH`` albo w tabeli `| severity | HIGH |`.

- wiarygodny HIGH/CRITICAL → `CHANGES_REQUIRED`,
- brak decyzji, wiele decyzji, dodatkowy nieobsługiwany marker `DECISION:` lub nieparsowalny wynik → `REVIEW_PENDING`, nigdy APPROVE,
- implementer nie może zatwierdzić własnej zmiany jako independent reviewer.

Parser decyzji nie zgaduje severity z dowolnej prozy; gate opiera się na jawnym polu `severity` w strukturze findingu. Reviewer ma obowiązek użyć tego pola dla każdego findingu.

## 7. Minimalna ścieżka feature/bugfix

Typowy feature:

`repository-analyst? → architect/architect-claude-opus? → task-decomposer → runtime-controller(model+runtime gate) → coder|coder-claude → required cross-vendor reviewer → critic/required audits → required real evidence → docs? → release-manager? → done`

Typowy bugfix:

`reproducer/root cause → runtime-controller(model+runtime gate) → coder|coder-claude + regression test → tests → required cross-vendor reviewer → targeted verification → done`

Znaki `?` oznaczają etap wymagany tylko przez zakres/ryzyko/task contract.

## 8. Reguły przejść

- orchestrator nie dispatchuje taska z `RUNTIME_CONTRACT_DRIFT` ani `MODEL_ROUTING_DRIFT`; jego gate parent pozostaje blocked,
- brak `runtime-controller` przy tasku wymagającym branch/retry/model routing oznacza blocked, nie degradację do LLM-only create,
- implementer wymagający independent review nie kończy tej karty jako VERIFIED; używa natywnego `review_requested`, po którym ta sama karta przechodzi do `review` i innego assignee,
- karta może przejść do `done` dopiero po zakończeniu wymaganego review lifecycle albo gdy task contract nie wymaga review,
- nadrzędna zmiana pozostaje nieweryfikowana, dopóki wszystkie wymagane review/audit/evidence z task contract nie są zamknięte,
- przy `CHANGES_REQUIRED` aktywny independent reviewer przed zakończeniem swojego review runu wywołuje natywne same-card `kanban_request_changes`; ta sama karta wraca wtedy do implementera i zachowuje ten sam worktree/history; orchestrator nie emuluje tego post-hoc nową kartą, a nową kartę tworzy się tylko dla rzeczywiście nowej, odrębnej pracy,
- `REVIEW_PENDING` zatrzymuje przejście całej zmiany do VERIFIED/DONE,
- brak wymaganego evidence → `blocked` albo pozostanie w `review`, nie VERIFIED,
- native reviewer worktree handoff nie może być zastąpiony drugim worktree, osobną kartą emulującą handoff ani samym parent summary,
- `release-manager` odmawia release przy brakującym required review/evidence lub wiarygodnym nierozwiązanym HIGH/CRITICAL.

## 9. Obowiązkowy krok wdrożenia Kanbana

Po merge zmian kontraktu Kanban i po zwykłym bootstrapie profili należy wykonać **oba** kroki runtime:

```bash
PRIMARY_PROFILE=primary-gpt bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

Pierwszy instaluje profil `runtime-controller`, scoped runtime-control surface oraz `model_routing_policy.py`. Drugi stosuje konfigurację dispatchera Kanban. Oba są wymagane przed utworzeniem pierwszego tasku Software Factory wymagającego runtime gate.

Po wykonaniu należy potwierdzić co najmniej:

```bash
hermes -p runtime-controller config get toolsets
hermes -p runtime-controller config get fallback_providers
hermes -p default config get kanban.auto_decompose
hermes -p default config get kanban.auto_subscribe_on_create
hermes -p default config get kanban.orchestrator_profile
hermes -p default config get kanban.default_assignee
```

Oczekiwane wartości obejmują:

```text
["hermes-cli", "terminal"]
[]
false
true
orchestrator
routing-sink
```

Dopóki te kroki nie zakończą się poprawnie, Software Factory nie jest gotowy do uruchamiania tasków wymagających runtime gate.
