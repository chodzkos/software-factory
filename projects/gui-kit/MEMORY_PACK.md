# Project Memory Pack — gui-kit (DRAFT)

## Rola projektu

`gui-kit` jest kanoniczną biblioteką wspólnych komponentów GUI oraz implementacją `GUI_STANDARD.md`.
Kod i standard mają być rozwijane razem.

## Aktualne zasady

### Source of truth
- `GUI_STANDARD.md` mieszka w repo `gui-kit`.
- zmiana reguły GUI wymaga spójnej zmiany kodu i standardu,
- aplikacje-konsumenci nie powinny utrzymywać własnych kopii wspólnego standardu.

### Ekstrakcja zamiast projektowania na zapas
Historia powstania kitu pokazuje świadomą zasadę:
- najpierw rozwiązanie dojrzewa w realnej aplikacji,
- następnie jest ekstrahowane do kitu,
- wspólny komponent powinien mieć realnych konsumentów,
- nie tworzymy hurtowo abstrakcji „na przyszłość”.

### Reguła konsumentów
Historycznie nazywana „regułą trzech”: widget/komponent trafia do kitu dopiero, gdy jego API jest potwierdzone w realnych zastosowaniach. W praktyce istotne jest istnienie co najmniej dwóch realnych konsumentów lub bardzo konkretnej drugiej potrzeby.

### GUI architecture
- kolory wspólnego GUI mają jedno źródło w `palette.py`,
- QSS jest generowany z palety,
- `winutil/` pozostaje bez zależności od Qt/tk,
- standardowe theme/titlebar/dialogs/widgety mają pochodzić z kitu.

## Decyzje historyczne nadal aktualne

1. Porzucenie `pyqtdarktheme` jako fundamentu standardu.
2. Własny ThemeManager/paleta jako wspólny mechanizm.
3. `GUI_STANDARD.md` przeniesiony do repo kitu, by wersjonować implementację ze standardem.
4. Ekstrakcja kodu ze sprawdzonych aplikacji zamiast budowania biblioteki komponentów z góry.
5. Shared dependency pinning do commit SHA z komentarzem wersji.

## Decyzje historyczne zastąpione

- Historyczne instrukcje „pinuj do taga” → **SUPERSEDED** przez pełny SHA.
- Lokalne `theme.py` w aplikacji → **SUPERSEDED**, jeśli odpowiadający mechanizm istnieje już w gui-kit.

## Pułapki

- Nie usuwać heksów domenowych będących danymi aplikacji tylko dlatego, że GUI ma centralną paletę (np. paleta rysowania w edytorze ikon nie jest paletą motywu).
- Nie wyciągać kilku widgetów hurtem bez audytu zbieżności API.
- Nie rozszerzać kitu o funkcję, która nie ma realnego konsumenta.

## Źródła historyczne

- rozmowa Claude: „Zastąpienie qdarktheme własnym theme.py w standardzie GUI” (2026-06-12…2026-07-07)
- aktualny `gui-kit/CLAUDE.md`
- aktualny `gui-kit/GUI_STANDARD.md`
- aktualne workflowy `.github/workflows/*`
