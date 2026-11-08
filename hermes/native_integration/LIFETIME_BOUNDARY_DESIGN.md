# Obowiązkowa kontrola życia workera — kandydat, NIE INSTALOWAĆ

## Zakres i granica zaufania

Dotyczy `coder-claude`, `reviewer-gpt` i izolowanego `release-manager`, po sprawdzeniu rzeczywistego loadera i rejestru przez właściwą bramkę startu. Pozostałe profile zachowują natywny dispatcher. ROUND3 wiąże dowód startowy recenzenta i release-managera z lifetime i porządkuje końcowy COMMIT zatwierdzania względem close/interrupt/timeout. Zakres dowodu i fixture: `PROFILE_LIFETIME_013.md`. Rozwiązanie wymaga zmian źródeł Hermesa, nie samego pluginu. Nie jest piaskownicą dla złośliwego kodu Pythona z prawem dowolnej zmiany pamięci procesu ani ochroną przed administratorem modyfikującym interpreter. Nie uruchomiono wdrożenia ani providera. Dwie częściowe recenzje poprzednich źródeł nie zatwierdzają nowych hashy; szczegóły rewizji i ograniczenia: `REVIEW_REVISIONS_013.md`.

Stan wiąże konkretny obiekt agenta z procesem, profilem, home, generacją managera/pluginu, aktywnymi uchwytami hooków, rejestrem, handlerami i definicjami narzędzi. Jest przechowywany poza atrybutami agenta, z referencją słabą do agenta. Wykryte naruszenie dowodu lub obowiązkowej sekwencji odwołuje uprawnienie starego obiektu nieodwracalnie; zwykła blokada narzędzia przez poprawny guard nie jest takim naruszeniem. Przywrócenie konfiguracji, wpisu lub przeładowanie pluginu nie odnawia tego obiektu. Zamknięcie również odwołuje stan. Zapamiętany scope nie wraca do surowego/opcjonalnego dispatchu po utracie weak-map wpisu.

## Sekwencja chronionego narzędzia

1. Natywne wejścia invoke oraz executory sekwencyjny/współbieżny/mieszany ponownie weryfikują dowód. Sprawdzenie istnieje również poza cienkim forwarderem.
2. `tool_entry` wiąże nazwę narzędzia i task/session/call/turn/request metadata w jednorazowym `ContextVar`. Natywne propagowanie kontekstu do wątków przenosi tę samą operację. Nie daje nowego kredytu dispatchu.
3. Finalny `registry.dispatch` wymaga operacji, prawidłowego scope/registry/task i zamkniętego snapshotu końcowych argumentów. `skip_*` oraz `pre_tool_block_checked` nie omijają tej warstwy.
4. Obowiązkowe pre-hooki (guard, a w dwóch profilach również plugin read-only) są wywoływane bezpośrednio, a nie przez pętlę opcjonalnych obserwatorów ignorującą wyjątki. Błąd oznacza brak wywołania handlera. Obsługiwane dyrektywy to brak blokady lub blokada; nie ma interaktywnych zatwierdzeń.
5. Po pre-hooku krótka blokada `decision_lock` porządkuje ostateczne dopuszczenie operacji z unieważnieniem. Operacja zapisuje `admitted` i identyfikator wątku; dopiero potem wywoływany jest przechwycony handler. Blokada nie obejmuje handlera. Operacja już dopuszczona może nadal wykonać efekt po późniejszym timeoutcie — nie udajemy anulowania wątku. Oryginalne natywne normalizowanie rezultatów/błędów pozostaje w `_dispatch_handler`.
6. Obowiązkowy post-hook i ponowna weryfikacja kończą operację. Błąd po efekcie nie jest przedstawiany jako cofnięcie efektu. Należy analizować stan, nie ponawiać automatycznie.

Dla związanych scope opcjonalne wywołania `PluginManager.invoke_hook(pre_tool_call/post_tool_call)` są pomijane. Właściwe callbacki są wykonywane raz przy finalnym handlerze. Zapobiega to również podwójnemu unieważnianiu zamiaru i tworzeniu dowodów przez same opcjonalne obserwatory. Testy sprawdzają liczniki callbacków i negatywne próby pominięcia tej granicy.

## Blokady i wyładowanie

Wykorzystano istniejący `manager._discovery_lock` (RLock) wyłącznie w synchronicznym odcinku finalnego handlera z pre/post. Nie utrzymuje się go nad executorem czekającym na swój wątek ani przez całe żądanie do modelu. Kolejność: discovery, krótkie odczyty registry, dotychczasowe blokady zamiaru/transakcji pluginu. Unload z innego wątku czeka na zakończenie handlera i obowiązkowego post-hooka. Reentrant unload z tego samego wątku jest wykrywany ponowną weryfikacją; przed efektem blokuje efekt, po efekcie oznacza niepewny wynik. Test obejmuje także rzeczywisty SQL przed COMMIT i wycofanie wszystkich trzech tabel.

**Koszt/polityka:** chronione odcinki handlerów są serializowane nawet przy użyciu współbieżnego executora. Długi handler nadal opóźnia unload. Nie jest to mechanizm zabijania wątków ani pełny dowód braku deadlocków.

## Timeout, przerwanie i kolejność decyzji

`abandon_worker` unieważnia wyłącznie już związany obiekt agenta. Nie opiera się na aktualnych zmiennych profilu, nie tworzy nowego związania i jest no-op dla niezwiązanych agentów. Używają go rzeczywiste `close()`, `interrupt()` oraz ścieżki porzucenia pracy w executorze sekwencyjnym i współbieżnym. Zachowuje pierwszą przyczynę odmowy. Kolejna kontrola agenta sprawdza znacznik unieważnienia przed próbą wejścia w discovery, więc nie oczekuje na porzucony długi handler.

Nowa blokada decyzji ma porządek **discovery → review-intent → decision**; odwołanie bierze tylko decision, nigdy discovery ani review-intent. Obejmuje dwa krótkie miejsca: dopuszczenie handlera oraz końcowy COMMIT SQLite. Nie obejmuje oczekiwania executora, przygotowania SQL, tworzenia receipt/pieczęci, obowiązkowych hooków, arbitrary handlera ani połączenia sieciowego. `close()`/timeout nie czekają na handler, ale mogą poczekać na COMMIT, który już zdobył blokadę. To nie jest gwarancja maksymalnego czasu oczekiwania na SQLite, dysk lub system operacyjny.

Unieważnienie, które wygrało przed dopuszczeniem, blokuje oczekującą operację. Już dopuszczona operacja nie jest ogólnie odwracalna. W przypadku przerwania bieżące narzędzie ma wynik `effect_disposition=unknown`, a narzędzie sekwencyjne, którego nigdy nie rozpoczęto, pozostaje `none`. We współbieżnej porzuconej partii zachowujemy konserwatywne `unknown`. Nie zastępujemy tych wyników późnym sukcesem wątku i nie uruchamiamy automatycznej powtórki. `BaseException` przy porzuceniu również unieważnia generację; `KeyboardInterrupt`/`SystemExit` nie są zmieniane w rzekomy poprawny sukces.

## Transakcja i świadectwo przekazania

Warstwa transakcji znajduje się w `review_transaction.py`. Integracja źródłowa 0.13.0 aktualizuje manifest i piny, nie zmienia opisanej semantyki transakcji. Pre-hook tworzy tylko jednorazowy zamiar. Adapter pod `BEGIN IMMEDIATE` ponownie sprawdza właściciela, wywołuje oryginalny natywny handler i SQL przez prywatną kopię globals/savepoint, publikuje pieczęć schematu 3 i zapisuje obowiązkowe `factory_handoff_committed` w tej samej transakcji co zakończenie implementacji. Każdy odczyt pieczęci wymaga zatwierdzonego receipt z osobnego połączenia. Koniec runu zachowuje `worker_pid=NULL`. Sam plik nie autoryzuje.

Adapter wymaga teraz rzeczywistej, aktualnie dopuszczonej operacji `kanban_request_review` tego samego wątku i guarda, także przed pobraniem jednorazowego zamiaru. Wywołanie surowego adaptera poza obowiązkową granicą registry nie dostaje prawa do SQL. Po pełnej rewalidacji w oknie review-intent i zapisaniu niezatwierdzonego receipt wywoływany jest `commit_review`. Ostatnie sprawdzenie unieważnienia i `conn.execute('COMMIT')` są w jednej sekcji decision, zamiast dwóch rozdzielnych czynności.

Dwie rozróżnione sytuacje: **unieważnienie przed zdobyciem decision** powoduje odmowę COMMIT i wycofanie otwartej transakcji; **COMMIT, który już zdobył decision**, może zakończyć się trwale, a późniejsze zamknięcie unieważnia dalszą pracę, nie cofa zatwierdzonych danych. Końcowa odmowa lifetime nie jest dowodem rollbacku. Wyjątek COMMIT może oznaczać także utratę potwierdzenia po rzeczywistym zatwierdzeniu; unieważniamy generację i wymagamy inspekcji bazy, bez ponowienia. Dotychczasowe sprzątanie może usunąć własną pieczęć przy niepotwierdzonym COMMIT nawet przy trwałym receipt — pozostaje wówczas jawna konieczność uzgodnienia stanu, a nie automatyczna naprawa.

Sekcja COMMIT używa przypiętej ścieżki natywnego połączenia. Nie dodano w niej callbacków aplikacji. Testy obejmują zdarzeniowo sterowany close/interrupt/timeout przed sekcją, rzeczywisty COMMIT wygrywający z unieważnieniem w drugim wątku oraz błędy SQL/potwierdzenia. Nie stanowią dowodu dowolnych reentrant callbacków SQLite, awarii procesu/dysku ani wszystkich sygnałów systemowych.


Nowe testy składają tę ścieżkę z rzeczywistym rejestrem, loaderem, pełnym modułem handlera/kanban_db i pełnym natywnym schematem SQLite, również przez executory. Claim i schema-6 attestation są jawnie syntetyczne; to nie wykonanie Claude'a. Po utworzeniu tymczasowej bazy test modeluje zastany WAL i podmienia wyłącznie fabrykę połączeń na połączenia do tej samej bazy. Nie zmienia zasad bezpieczeństwa SQLite produktu i nie jest testem natywnego otwierania produkcyjnej bazy. Usunięcie receipt po udanym COMMIT powoduje odmowę odczytu pieczęci.

## Model i odświeżanie definicji — zakres wejściowego przyrostu

Poniższy opis dotyczy wcześniejszego inkrementu. Aktualna rewizja nie rejestruje dowolnego envelope: semantycznie waliduje cztery formaty, odrzuca niepuste rozszerzenia i kontroluje wybrane końcowe callbacki oraz summary. Pełny zakres i granice dowodu: `REVIEW_REVISIONS_013.md`.

Definicje agenta i jawnie przekazany podzbiór narzędzi muszą odpowiadać sprawdzonym definicjom. Natywny builder zapisuje jedynie skrót pól opisujących możliwości po transformacji transportu, bez treści rozmowy. Forwardery oraz natywne wejścia request/direct/nonstreaming/streaming sprawdzają rozpoznaną powierzchnię i stan, pracują na osobnej kopii payloadu oraz sprawdzają stan po powrocie. Wynik po utracie generacji nie wraca jako normalny sukces.

**Nie jest to dowód wszystkich ścieżek transportu:** nie wykonano requestu do providera; nie udowodniono semantycznej poprawności wszystkich przekształceń schematów, request_overrides, server-side tools, stream-request middleware, retry/fallback i pomocniczych połączeń podsumowań. Nie zablokowano modelu blokadą discovery podczas sieci. Nie wolno twierdzić, że wcześniejszej utraty ochrony nie może rozdzielać od wysłania już przygotowanego requestu żaden interleaving albo że da się cofnąć wysłane żądanie. Ochrona finalnego wykonywania lokalnych narzędzi ma odrębną obowiązkową granicę.

## Testy wejściowego kandydata i nadal otwarte bramki

Liczby 190/40/73/97/103 poniżej są wynikami wejściowego kandydata sprzed napraw recenzyjnych. Aktualne oczekiwane kolekcje określa `ci/gates.json`, a wyniki nowego przebiegu odrębny raport; nie przenosi się starego PASS na zmienione źródła.

Trzy wejściowe regresje timeout/close są naprawione. Test procesowy był już zsynchronizowany; jego dawnej diagnostyki nie powtarzamy. Migracja starych fixture do rzeczywistego natywnego SQL, schema 3 i zatwierdzonego receipt usunęła 20 dawnych niepowodzeń. Główny zestaw liczy 190 testów. Oddzielne kolekcje: start 40, lifetime 73, transakcje/rejestracja 97 i oryginalne natywne registry/pluginy 103. W lokalnym pełnym przebiegu Python 3.13.5 wszystkie przechodzą bez pominięć. To nie dowód działania hosted CI ani Python 3.11.

`hermes/ci/gates.json` jawnie deklaruje każdą kolekcję i dokładną oczekiwaną liczbę; `run_gates.py` odrzuca niepełną kolekcję, skip/xfail/xpass i niezerowe zakończenie procesu. Każdy zestaw ma odrębny proces i pusty HOME bez danych uwierzytelniających. Sieć jest blokowana w fixture i lokalnym sitecustomize; nie jest to izolacja przed dowolnym wrogim kodem Python. Workflow jest kandydatem źródłowym, z przypiętymi akcjami i bazą Hermesa, macierzą 3.11/3.13 i nakładką natywną. Nie uruchomiono go na GitHub Actions.

Izolowane 97 testów transakcji/rejestracji korzysta z jawnie dopuszczonej operacji: wykonuje rzeczywiste klasy lifetime/commit, ale zastępuje dowód konstruktora. Nie jest to 97 pełnych startów. Odrębne testy składają rzeczywisty init, registry, adapter i SQL. Claim, schema-6 attestation oraz wybór fabryki połączenia do tymczasowej bazy pozostają syntetyczne. Nie wykonano modelu ani Claude'a.

Bootstrap zapisuje dokładną powierzchnię CLI codera, `no_mcp`, wyłączenie automatycznego BFL, pustą listę MCP i właściwe uprawnienie do podmiany narzędzia. Test używa prawdziwego CLI config oraz resolvera dispatchera, nie pełnego uruchomienia zadania. Oba bootstrapy odmawiają NIEZRECENZOWANEGO kandydata przed zmianą profilu. Natywne moduły factory są zadeklarowane w pyproject; sam wpis nie jest testem zbudowanego i zainstalowanego wheel.

Otwarte pozostają granice transportu (semantyka konwersji, overrides, middleware, retry/fallback i podsumowania), cache-safe import i produkcyjny instalator natywnej nakładki, docelowy interpreter 3.11, pełny CLI/worker i wybrany provider oraz niezależna recenzja dokładnych źródeł. `plugin.yaml` ma 0.13.0, ale manifest ma `candidate-unreviewed`, `installable=false`. Zgodne piny nie są akceptacją wydania. **NIE INSTALOWAĆ / NO_MERGE / NO_DEPLOY.**

## Second-review restriction (extended to newly bound profiles in ROUND3)

MoA fan-out is explicitly unsupported for all three bound profiles, including per-turn MoA.
See `REVIEW_ROUND2_013.md` for early gates and the tested rejection matrix.
General-profile MoA semantics are not replaced. Nonempty `extra_body` and SDK
header extensions remain refused, not certified as compatible.

## Approval recenzenta — ROUND3

`guarded_reviewer_complete` wymaga dopuszczonej, jednorazowej operacji `factory_review_approve`, właściwego profilu, modułu i wątku. Surowe wywołanie nie otwiera bazy. Pod własnym `BEGIN IMMEDIATE` zachowuje wszystkie wcześniejsze rewalidacje pieczęci/receipt/bajtów oraz wymaga bieżącego PID w zadaniu i runie, niepustego wspólnego claim lock i zgodności jawnego locka środowiska (gdy jest podany). Heartbeat/claim expiry nie są zamrażane, analogicznie do codera. Natywne `complete_task` działa wewnątrz savepointu. `commit_approval` kontroluje tę samą dopuszczoną operację, a następnie serializuje prawo do COMMIT z odwołaniem. Otwarta transakcja podlega rollbackowi przy odmowie; błąd COMMIT albo rollbacku unieważnia workera i oznacza niepewny wynik.

COMMIT, który wygrał, pozostaje trwały także po utracie odpowiedzi. Czytnik downstream pozostaje samodzielną kontrolą już zatwierdzonych danych, bez wymogu aktywnego agenta. Release-manager nie otrzymał uprawnienia zatwierdzania. Te gwarancje nie obejmują cofania plików ani wszystkich pomocniczych zapisów natywnych.
