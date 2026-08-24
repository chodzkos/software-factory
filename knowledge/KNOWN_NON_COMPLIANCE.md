# Known Non-Compliance

Data przeglądu: 2026-08-24

Ten plik rejestruje bieżące odstępstwa repozytoriów od `CHODZKOS_SOFTWARE_STANDARD_v1.0.md`.
Nie jest częścią trwałej polityki standardu i może być aktualizowany niezależnie.

## NC-001 — pdf2md release workflow

**Repo:** `chodzkos/pdf2md`  
**Plik:** `.github/workflows/release.yml`  
**Status:** OPEN  
**Severity:** HIGH / MEDIUM (zależnie od podproblemu)

### CI-SC-001 — HIGH

Release workflow używa ruchomych referencji GitHub Actions, m.in.:

- `actions/checkout@v7`
- `astral-sh/setup-uv@v7`
- `actions/upload-artifact@v7`
- `actions/download-artifact@v8`

Wymaganie standardu: pełny commit SHA + komentarz wersji.

### CI-SC-002 — HIGH

`astral-sh/setup-uv` w release używa `version: "latest"`.

Wymaganie standardu: reprodukowalny, przypięty toolchain na ścieżce release.

### CI-SC-003 — MEDIUM

`permissions: contents: write` jest nadane na poziomie całego workflow.

Wymaganie standardu: write permissions tylko dla joba publikującego; build/test powinny mieć read/{}.

### Zalecana naprawa

Osobny mały PR typu `chore:` / `security:` bez zmian zachowania aplikacji:

1. zamiana wszystkich zewnętrznych `uses:` na pełne SHA,
2. przypięcie konkretnej wersji `uv`,
3. przeniesienie `contents: write` do joba tworzącego release,
4. brak innych refactorów,
5. CI + bezpieczna walidacja ścieżki release bez przypadkowej publikacji.
