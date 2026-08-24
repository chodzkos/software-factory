# EpubForge — Project Memory Pack (Draft)

Status: pierwszy szkielet z archiwum Claude + bieżącego repo. Nie zastępuje aktualnego `CLAUDE.md`, kodu ani testów.

## 1. Tożsamość architektoniczna

- Modułowy toolkit/aplikacja EPUB z core + GUI + CLI.
- Core nie powinien zależeć od GUI.
- Obecne GUI jest Qt/PySide6; wspólne elementy GUI pochodzą z `chodzkos-gui-kit`.
- Ogólne prymitywy detekcji pochodzą z `chodzkos-detection`; EpubForge zachowuje kompozycję specyficzną dla swoich narzędzi/platform.

## 2. Ważne decyzje nadal aktualne lub wymagające respektowania

- Wewnętrzne zależności gui-kit/detection po pełnym commit SHA.
- `uv.lock` wersjonowany; CI/build powinny być reprodukowalne.
- tinycss2 zamiast cssutils dla nowoczesnego CSS.
- Nie zakładać lokalizacji OPF; brać ją z `container.xml`.
- Przy dużych EPUB nie ładować bez potrzeby całego archiwum do RAM; niezmienione wpisy mogą być kopiowane strumieniowo.
- Operacje Qt wykonywać z poszanowaniem wątku GUI; worker nie manipuluje widgetami bezpośrednio.

## 3. Reguły historyczne zastąpione

- Własny system motywu `theme.py` jako wspólny wzorzec → zastąpiony przez `chodzkos-gui-kit` dla wspólnych mechanizmów.
- Pinowanie wspólnych bibliotek tylko do taga → zastąpione przez pełny commit SHA.
- xvfb jako uniwersalny mechanizm testowania Qt → nowszy standard korzysta z `QT_QPA_PLATFORM=offscreen`, z osobnymi realnymi testami platformowymi.

## 4. Znane klasy pułapek

- Windows: narzędzia mogą być poza PATH (np. App Paths/rejestr); nie redukować detekcji do samego `shutil.which`.
- Build/PyInstaller + Qt/WebEngine wymaga testu gotowego artefaktu; zielone testy źródłowe nie wystarczą.
- EPUB/ZIP/XML/HTML/regex to granice wymagające szczególnej uwagi bezpieczeństwa i testów odporności.
- Konfiguracja/persistencja: zmiany schema/config muszą uwzględniać istniejące dane, nie tylko świeżą instalację.

## 5. Zasada procesu z historii projektu

`DONE != VERIFIED`: po merge/CI wykonywać realną weryfikację funkcji/artefaktu, jeśli zmiana tego wymaga.

## 6. Źródła do pogłębienia

- bieżący `CLAUDE.md`, `pyproject.toml`, `SECURITY.md`, `ROADMAP*`, testy,
- archiwum Claude: EpubForge `DECISIONS.md`, `PROJECT_CONTEXT.md`, historyczne `GUI_STANDARD`, plany extraction detection i audyty,
- bieżące `gui-kit/GUI_STANDARD.md` i `gui-kit/CLAUDE.md`,
- bieżące `chodzkos-detection`.
