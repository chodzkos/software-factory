# pdf2md — Project Memory Pack (Draft)

Status: pierwszy szkielet z archiwum Claude + bieżącego repo. Nie zastępuje aktualnego kodu, docs ani testów.

## 1. Tożsamość architektoniczna

- Konwerter PDF → Markdown będący orkiestratorem wielu silników.
- GUI + CLI; część silników importowana w procesie, ciężkie/konfliktowe mogą być izolowane jako narzędzia/subprocess.
- Wspólne GUI przez `chodzkos-gui-kit`, ogólne sondy przez `chodzkos-detection`.

## 2. Aktualne reguły infrastrukturalne

- Python >=3.11,<3.14 w bieżącym pyproject.
- ruff + mypy strict + pytest.
- Wewnętrzne zależności gui-kit i chodzkos-detection przypięte do pełnych SHA w `[tool.uv.sources]`.
- Bezpieczeństwo zależności jest aktywną częścią projektu (security floors, audyty, override tylko po empirycznej weryfikacji).
- Otwarte odstępstwa release/supply-chain są śledzone w `knowledge/KNOWN_NON_COMPLIANCE.md` jako **NC-001**; Memory Pack nie oznacza projektu jako w pełni zgodnego, dopóki finding pozostaje OPEN.

## 3. Ważne doświadczenia historyczne

- Zielone CI nie gwarantuje realnej poprawności silników/artefaktu; smoke test realnej konwersji ujawniał problemy zależności i outputu.
- Nie polegać na zależnościach tranzytywnych, jeśli kod bezpośrednio importuje/wykorzystuje pakiet — deklarować zależność jawnie.
- Izolować ciężkie stosy zależności, gdy ich wersje kolidują z głównym środowiskiem.
- Ograniczenia sprzętowe/platformowe mają być prezentowane zgodnie z rzeczywistym runtime; nie hardcodować mylących instrukcji instalacji.

## 4. Znane klasy pułapek

- Windows paths/backslashes i serializacja configu.
- GUI worker musi zawsze przywrócić stan UI również po nieoczekiwanym wyjątku.
- Output path/temporary directory musi być spójny z relatywnymi assetami (obrazy inline).
- ML dependencies i ich wersje wymagają realnych smoke testów GPU przy nietypowych override.

## 5. Reguły historyczne/konflikty

- Merge strategy i język komentarzy/docstringów nie powinny być wyprowadzane z dawnych pojedynczych rozmów; obowiązuje aktualna polityka repo/global standard.
- Historyczne instrukcje silników/dependency matrix są wersjozależne i przed użyciem trzeba je skonfrontować z bieżącym pyproject/lockiem.

## 6. Źródła do pogłębienia

- bieżący `pyproject.toml`, docs/PROJEKT.md, ROADMAP, CI,
- archiwum `pdf2md/DECISIONS.md`, `PROJECT_CONTEXT.md`, rozmowy o supply-chain i realnej weryfikacji,
- gui-kit i chodzkos-detection jako bieżące canonical shared libraries.
