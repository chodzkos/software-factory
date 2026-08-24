# Project Memory Pack — chodzkos-detection (DRAFT)

## Rola projektu

`chodzkos-detection` jest lekkim, wspólnym pakietem sond środowiska.

## Aktualny kontrakt

Pakiet:
- jest **stdlib-only** dla własnej logiki,
- nie zależy od Qt,
- nie zależy od torcha,
- dostarcza uniwersalne sondy narzędzi CLI i usług HTTP,
- może być używany przez GUI, CLI i serwery bez ciężkiego stosu aplikacji.

Aktualne API obejmuje m.in. `probe_tool`, `find_tool`, sondy Tesseract/Poppler/Pandoc, `probe_http_service` i `check_ollama`.

## Granica odpowiedzialności

### Należy do pakietu
- uniwersalna mechanika odnajdywania narzędzia,
- uruchomienie binarki i odczyt wersji,
- lekkie sondy usług HTTP,
- kontrakty danych możliwe do współdzielenia między aplikacjami.

### Nie należy obecnie do pakietu
- Qt,
- torch,
- ciężka detekcja GPU/sprzętu,
- progi sprzętowe konkretnego silnika,
- prezentacja GUI/CLI raportu,
- domenowa lista „co sprawdzać” dla konkretnej aplikacji.

## Ewolucja decyzji

W historii Claude rozważano szerszy `chodzkos-detection`, obejmujący wspólny `hardware.py` i część detekcji GPU wywodzącej się z pdf2md.

**Status tej koncepcji: SUPERSEDED.**

Aktualny repo świadomie ogranicza pakiet do lekkiej warstwy narzędzi/usług. Detekcja GPU/torcha pozostała po stronie aplikacji.

## Decyzje nadal aktualne z historii

1. Nie duplikować `shutil.which + subprocess + parse version` w wielu aplikacjach.
2. GUI i CLI tej samej aplikacji powinny korzystać z jednego źródła danych diagnostycznych; różnić się ma prezentacja.
3. Przy refaktorze/extrakcji zachowywać kontrakt `{available, version, ...}` tam, gdzie konsumenci już go używają.
4. Najpierw realny kod i co najmniej drugi konsument, potem ekstrakcja.
5. Zmianę wspólnej biblioteki konsumenci podciągają świadomie przez zmianę przypiętego SHA/locka.

## Ważna lekcja z historycznego audytu

Podczas ekstrakcji/refaktoru nie wystarcza testowanie tylko ścieżki „narzędzia brak”.
Sondy wersji powinny mieć testy/mocki również dla `available=True`, parsowania wersji, timeoutów i błędów subprocess.

## Supply chain

- zależność konsumowana po pełnym SHA,
- workflowy GitHub Actions przypięte do pełnych SHA,
- `uv.lock` wersjonowany dla reprodukowalnego dev/CI.

## Źródła historyczne

- `AUDIT_detection_reuse_epubforge.md`
- `REVIEW_chodzkos_detection.md`
- `HANDOFF_detection_pdf2md_to_mediaforge.md`
- aktualny README `chodzkos-detection`
- aktualne workflowy repo
