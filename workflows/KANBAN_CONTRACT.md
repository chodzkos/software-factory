# Kanban task contract v1

Ten dokument doprecyzowuje `standards/SOFTWARE_DEVELOPMENT_STANDARD.md` dla Software Factory uruchamianego przez Hermes Kanban. Standard pozostaje nadrzędnym źródłem prawdy. Politykę modeli i reviewerów doprecyzowuje `workflows/MODEL_ROUTING_POLICY.md`.

## 1. Tryb orkiestracji

- `kanban.auto_decompose=false`; dekompozycję wykonuje `task-decomposer`.
- Każdy task ma jawnego `assignee`; nierozpoznany routing trafia do `routing-sink`.
- `kanban.auto_subscribe_on_create=true`.
- Orchestrator koordynuje, ale nie implementuje i nie wykonuje independent review.
- Mechaniczne operacje wymagające CLI wykonuje `runtime-controller`; orchestrator nie ma terminala.

## 2. Stany i znaczenie DONE

`triage`, `todo`, `ready`, `running`, `blocked`, `review`, `done`, `archived` zachowują znaczenie Hermesa. `done` pojedynczej karty nie oznacza automatycznie VERIFIED całej zmiany. `IMPLEMENTED != VERIFIED`.

Zmiana feature/bugfix może być uznana za VERIFIED dopiero po zamknięciu wszystkich wymaganych review/audit/evidence i bez nierozwiązanych blockerów.

## 3. Task body

Każdy task wykonawczy zawiera:

```text
## Task Contract
TYPE: feature|bugfix|audit|docs|release|analysis|architecture|decomposition|review
RISK: low|medium|high|critical
SECURITY_SENSITIVE: yes|no
ASSIGNEE: <profile>
REPOSITORY: <owner/repo lub path>
WORKSPACE: none|repo|worktree:<absolute-repo-path>
IMPLEMENTER: <profile|none>
REQUIRED_REVIEWERS: <exact reviewer profile|none for non-code tasks>
OPTIONAL_REVIEWERS: <comma-separated profiles|none>
REQUIRED_EVIDENCE: <opis>
ACCEPTANCE_CRITERIA:
- ...
```

Dla tasku zmieniającego kod lub review tej zmiany `SECURITY_SENSITIVE`, `IMPLEMENTER` i `REQUIRED_REVIEWERS` są obowiązkowe dokładnie raz. Dla kodu `WORKSPACE` musi być `worktree:<absolute-repo-path>`.

## 4. Exact model routing

Mechaniczna macierz dla code-changing same-card lifecycle:

| Implementer | SECURITY_SENSITIVE | Exact REQUIRED_REVIEWERS |
|---|---|---|
| `coder` | `no` | `reviewer-claude` |
| `coder-claude` | `no` | `reviewer-gpt` |
| `coder` | `yes` | forbidden |
| `coder-claude` | `yes` | `reviewer-gpt` |

Powody:

- normal review musi być cross-vendor;
- security review zawsze wykonuje OpenAI `reviewer-gpt`;
- aby security review pozostał cross-vendor, security implementation musi wykonać `coder-claude`;
- Hermes 0.20.4 ma jeden same-card reviewer lifecycle, więc Software Factory nie deklaruje drugiego obowiązkowego reviewera bez osobnego mechanicznego audit gate.

`critic` może być dodatkowym/deep audytorem na osobnej mechanically gated karcie. Nie dodaje się go do `REQUIRED_REVIEWERS` bieżącej same-card implementation card.

`hermes/model_routing_policy.py` egzekwuje dokładny zestaw reviewerów. Dodatkowe profile, malformed CSV, `none`, duplikaty pól, nieznane profile, duplicate JSON keys i nieprawidłowy nested `task` kończą się `MODEL_ROUTING_DRIFT`.

## 5. Fail-closed runtime gate

Body samo w sobie nie potwierdza pól runtime. Przed `ready` `runtime-controller` porównuje kontrakt z faktycznym create/show JSON.

### 5.1 Runtime controller

Profil instaluje:

```bash
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
```

`runtime-controller` ma tylko toolset `terminal` oraz profile-scoped plugin `factory-execution-guards`. `pre_tool_call` przepuszcza wyłącznie bezpośrednie uruchomienie:

```text
~/.hermes/profiles/runtime-controller/kanban_runtime_cli.sh <allowlisted-op> ...
```

Allowlist operacji:

- `create`
- `show`
- `block`
- `complete`
- `validate-runtime`
- `validate-handoff` (legacy compatibility, nie security source of truth)
- `validate-routed-handoff`
- `validate-routing`

Bezpośrednie `hermes`, Git, Python, curl, file/code tools, shell operators, pipe/chaining i command substitution są mechanicznie blokowane. Ograniczenie nie opiera się wyłącznie na SOUL.

### 5.2 Sticky parent quarantine

Create-time `blocked` nie jest sticky quarantine w Hermes 0.20.4. Dla taska wymagającego runtime validation `runtime-controller` tworzy techniczny parent gate przypisany do `routing-sink`, natychmiast zapisuje sticky `kanban block --kind needs_input` z powodem `RUNTIME_CONTRACT_PENDING`, a worker task zależy od tego parenta.

Drift pozostawia gate blocked. Zgodność pozwala zakończyć tylko techniczny gate i dopiero wtedy worker może przejść dalej.

### 5.3 Runtime fields

Gate waliduje co najmniej:

- `assignee`,
- `workspace_kind`,
- create-time `workspace_path`,
- wymagany `branch_name`,
- wymagany `max_retries`,
- `parents`,
- exact model routing z live task body.

`max_runtime` musi być ustawiony przy create, ale Hermes 0.20.4 nie daje stabilnego JSON readbacku używanego przez validator; to ograniczenie pozostaje jawne i fail-visible.

## 6. Same-card implementer → reviewer handoff

Po claimie Hermes materializuje worktree i zapisuje resolved `workspace_path=.../.worktrees/<task-id>`.

Implementer wymagający review kończy run przez native `review_requested`. Ta sama karta przechodzi do `status=review`, assignee zmienia się na reviewera, a resolved worktree pozostaje ten sam.

Przed dispatch review runtime-controller musi wykonać na **tym samym live `show --json`**:

```text
validate-routing --actual-json <live-json>
validate-routed-handoff --actual-json <live-json>
```

`validate-routed-handoff` sam:

1. parsuje `IMPLEMENTER`, `REQUIRED_REVIEWERS`, `SECURITY_SENSITIVE` z live `task.body`,
2. sprawdza exact model-routing matrix,
3. wymaga dokładnie jednego same-card reviewera,
4. porównuje reviewera z live `task.assignee`,
5. porównuje implementera/reviewera z najnowszym `review_requested` eventem,
6. wymaga bieżącego/najnowszego implementer runu zakończonego `review_requested`,
7. sprawdza `run_id`, gdy jest dostępny,
8. sprawdza resolved worktree i opcjonalne `metadata.workspace_path`.

Nazwy implementera/reviewera przekazane osobno przez orchestratora nie są security inputem dla `validate-routed-handoff`. Summary ani parent result nie zastępują live structured evidence.

Przy `CHANGES_REQUIRED` aktywny reviewer używa native same-card `kanban_request_changes`; zwykły rework nie tworzy nowej karty.

## 7. Claude Code execution boundary

`coder-claude`, `reviewer-claude` i `architect-claude-opus` mają profile-scoped `factory-execution-guards`.

Guard mechanicznie:

- blokuje bezpośredni outer-GPT write/patch/code execution;
- ogranicza terminal do canonical Claude print-mode invocation z JSON output oraz małego read-only verification surface;
- przypina `sonnet` dla `coder-claude`/`reviewer-claude` i `opus` dla `architect-claude-opus`;
- reviewerowi Claude blokuje command exposing `Write`;
- po poprawnym Claude JSON result (`type=result`, `subtype=success`, `session_id`) zapisuje trwałe evidence poza repo, powiązane z `task_id`, `run_id`, profilem i model class;
- `coder-claude` nie może wykonać `kanban_request_review` bez evidence dla bieżącego runu;
- Claude reviewer/architect nie może zakończyć taska bez analogicznego evidence.

Brak CLI/OAuth/skilla/evidence oznacza blocked, nie fallback do outer GPT.

## 8. Legacy Ox

Ox Alpha nie jest aktywnym backendem Software Factory. `auditor-ox` został usunięty z aktywnych skill manifests i dokumentowanego routingu.

Jeśli stary lokalny profil nadal istnieje po wcześniejszej wersji, bootstrap ustawia:

```text
model.provider=disabled-legacy
model.default=disabled-legacy
factory.execution_backend=disabled-legacy
fallback_providers=[]
toolsets=[]
tool_search=off
```

oraz szeroki disabled-toolset denylist. Invalid provider/model jest inference kill switch; metadata jest tylko defense-in-depth.

## 9. Review/audit decision contract

Reviewer kończy dokładnie jedną linią:

```text
DECISION: APPROVE
```

albo:

```text
DECISION: CHANGES_REQUIRED
```

Każdy finding ma jawne `severity`, `location`, `evidence`, `impact`, `proposed fix`. Wiarygodny HIGH/CRITICAL wymusza `CHANGES_REQUIRED`. Brak/nieparsowalna/wielokrotna decyzja oznacza `REVIEW_PENDING`, nigdy APPROVE.

## 10. Routing ról

- analiza repo → `repository-analyst`
- architektura → `architect`; opcjonalna trudna eskalacja → `architect-claude-opus`
- dekompozycja → `task-decomposer`
- runtime/model gate → `runtime-controller`
- non-security implementation → `coder` albo `coder-claude` zgodnie z kontraktem
- security implementation → `coder-claude`
- normal `coder` review → `reviewer-claude`
- `coder-claude` review, w tym security → `reviewer-gpt`
- deep/audit → `critic`, `auditor-gpt`, `auditor-grok` według osobnego task contract
- docs → `docs`
- release → `release-manager`
- niebezpieczny/nieznany routing → `routing-sink`

## 11. Obowiązkowy deployment

Po merge/synchronizacji:

```bash
PRIMARY_PROFILE=default DISPATCHER_PROFILE=default bash hermes/bootstrap_profiles.sh
PRIMARY_PROFILE=default bash hermes/bootstrap_runtime_controller.sh
DISPATCHER_PROFILE=default bash hermes/configure_kanban.sh
```

Przed realnymi taskami wymagane są pozytywne static/regression/live guard probes. Software Factory nie jest gotowy do tasków wymagających runtime gate, dopóki guard plugin, routing validator i runtime-controller bootstrap nie są zweryfikowane.
