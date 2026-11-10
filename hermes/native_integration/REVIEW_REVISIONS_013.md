# Rewizja po dwóch niezależnych recenzjach 0.13.0

**Kandydat źródłowy, NIE INSTALOWAĆ / NO_MERGE / NO_DEPLOY.** Recenzje dotyczą
poprzedniego zestawu hash. Poniższe zmiany nie dziedziczą ich akceptacji.
`installable=false`, `activation_status=candidate-unreviewed` pozostają wymagane.

## Kolejność startu i zakres zaufania

`run_agent.AIAgent` wybiera chroniony profil przez stdlibowy `selected_profile`.
Znane home codera/recenzenta/izolowanego release-managera nie pozwalają ominąć
wyboru bramki przez usunięcie albo sfałszowanie `HERMES_PROFILE`.

Coder ustala kanoniczny home i sprawdza piny przed importami wykonywanymi przez
własne `_identity` oraz `_configuration`. `hermes_cli/config.py` jest teraz
przypięty. Odrzucane są zarówno zmienna `PYTHONPYCACHEPREFIX`, jak i aktywny
`sys.pycache_prefix`; dodatkowe pliki cache wewnątrz pluginu również odmawiają.

**Ograniczenie:** nie jest to kontrola przed pierwszym importem całego Hermesa.
Kod na poziomie modułu `run_agent` i inne wcześniejsze importy nadal stanowią
zaufaną bazę. Kontrola `.py` nie udowadnia tożsamości już wykonywanego bytecode.
Cache-safe launcher/instalator i pełny łańcuch importów pozostają otwarte.

`factory_profile_start` dodaje niezależne kontrole PRZED i PO initializerze
`reviewer-gpt` oraz `release-manager`. Dopuszcza dokładnie guard z override i
niezmieniony plugin read-only BEZ override. Kontroluje piny obu pluginów,
konfigurację wyłączonych toolsetów, pusty MCP, wyłączony tool search, manager,
hooki, handlery, aktywne ograniczenia Kanban oraz dokładne 6/3 definicje modelu.
Nadmiarowego katalogu nie przycina do pozornie poprawnego wyniku.

**To wyłącznie kontrola startu tych dwóch profili.** Nie dodaje im coderowego
lifetime, nie serializuje ich approval COMMIT z `close` i nie dowodzi odporności
na unload już po starcie. Ta odrębna bramka pozostaje otwarta. Uprawnienia do
instalacji i zestaw profili z override nie zostały poszerzone.

## Model: walidacja znaczenia zamiast rejestrowania dowolnego hasha

`factory_model_boundary.capabilities` dekoduje końcowe narzędzia i porównuje
każdą funkcję z niezmiennym snapshotem definicji przy starcie codera. Obsługuje
wąski podzbiór rzeczywistych formatów Chat Completions, Responses, Anthropic
Messages i Bedrock Converse. Sprawdza duplikaty, dodatkowe nazwy, schematy,
wybrany podzbiór narzędzi oraz jawne `tool_choice` i trasę model/provider/API.
Znane adnotacje cache i mapowanie nazwy OAuth mają jawny, zamknięty format.

Builder nie może zarejestrować dziewiątego narzędzia, usuniętych ograniczeń
schematu ani narzędzia dołożonego do mniejszego żądanego podzbioru. Klient jest
wywoływany przez `checked_model_call` na odrębnej kopii końcowego słownika,
po wybranych natywnych sanitizerach i middleware. Po powrocie sprawdzany jest
stan agenta. Nie ma blokady discovery trzymanej podczas sieci.

Nieznane klucze rozszerzeń oraz **każde niepuste `extra_body` są odrzucane**.
Dotyczy to także potencjalnie nieszkodliwych ustawień providera. To celowa
odmowa niezweryfikowanej semantyki, nie pełna zgodność wszystkich providerów.
Nieznane server-side tools, konwersja osłabiająca schemat oraz dopisane
kompaktor/plugin nie uzyskują automatycznie nowego uprawnienia przez hash.

`handle_max_iterations` sprawdza stan na wejściu i wyjściu. Własne żądania
podsumowania są rejestrowane ze świadomie pustym zestawem narzędzi i również
przechodzą końcową kontrolę. Odwołany worker nie dochodzi do klienta.

**Ograniczenia:** testy klientów to mocki, nie połączenia z usługami. Część testów
końcowych callbacków stosuje jawną, tymczasową atrapę wyłącznie typów wyjątków
SDK OpenAI, gdy pakiet nie jest zainstalowany. Nie zastępuje to testu prawdziwego
SDK ani jego serializacji. Sprawdzane są rzeczywiste funkcje natywnych konwerterów
i końcowych callbacków; Relay/klient są kontrolowanym fixture. Nie zbadano
każdego modułu pomocniczego, wszystkich retry/fallback, SDK retries ani
wszystkich przeplotów. Kontrola nie cofa wysłanego żądania i nie dowodzi
atomowości pomiędzy ostatnim sprawdzeniem a systemowym wysłaniem.

## Handoff i poprawność dowodu testowego

Jeden serializer `handoff.workspace_content_state` jest używany także przez
samodzielny moduł guard, bez zależności od monkeypatchingu entrypointu. Usunięto
martwą kopię hashera i słabszą implementację fallback. `skill` nie jest dozwolony
przez sam guard codera; pozostałe profile Claude nie zmieniły uprawnień.

W produkcyjnym `handoff.py` nie ma już `_TEST_APPROVAL_HOOK`. Testy approval
wstrzykują zmiany przez własne opakowania rzeczywistych funkcji walidacji i SQL.
Zachowują obie rewalidacje, asercję dotarcia do wstrzykniętej mutacji oraz
porównanie wszystkich wierszy trzech tabel.

Pięć wadliwych payloadów nie jest testowanych przez bezwarunkowo odmowny
`create_handoff_seal`. Test przechodzi przez prawdziwy `_invoke_tool`, natywny
handler i SQL, podmienia tylko wynik serializerem `_ok`, a następnie wymaga
rzeczywistej odmowy `_build_handoff_seal`, rollbacku pełnych tabel oraz braku
pieczęci i receipt. Osobny test dokumentuje wycofany interfejs post-hooka.

Nie zmieniono semantyki coderowego COMMIT/revoke ani zakończonej kontroli
drzewa procesów. Izolowane 97 nadal nie jest 97 pełnymi inicjalizacjami.

## Tożsamość nakładki CI

`ci/verify_native_overlay.py` jest konsumentem `native_overlay.json`. Workflow
uruchamia go przed `git apply --check` i po rzeczywistym zastosowaniu patcha.
Sprawdza bazowy HEAD/tree, hash patcha, dokładny inwentarz ścieżek, bazowe blob
SHA-1/tryby przed zastosowaniem i docelowe SHA-256/tryby wszystkich plików po nim.
Nie akceptuje symlinków, niekanonicznych ścieżek, duplikatów JSON/inwentarza,
brakujących plików ani manifestu z prawem instalacji. To kontrola spójności,
nie podpis, audyt bezpieczeństwa ani produkcyjny instalator.

Szósta kolekcja `review_revisions` jest obowiązkowa w `gates.json`. Liczniki,
pełna kolekcja i brak skip/xfail/xpass pozostają warunkiem PASS. Uruchomienie
lokalne nie jest hosted GitHub Actions ani testem interpretera 3.11.

## Otwarte bramki

Nowe dokładne źródła wymagają niezależnej recenzji, ze szczególnym uwzględnieniem
ścisłej walidacji transportu i jawnego kosztu kompatybilności, kontroli startu
recenzenta/release-managera oraz pełnego konsumenta manifestu CI. Otwarte są też:
pełny lifetime tych dwóch profili, cache-safe import/launcher, rzeczywiste SDK,
pełna ścieżka providera/dispatchera/Claude, Python 3.11, czysty lock/wheel,
hostowane CI oraz produkcyjny instalator. Niczego z tej listy nie zamyka opis
projektu, zgodność hashy ani sama liczba zielonych testów.
