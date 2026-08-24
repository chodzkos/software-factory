# GitHub Actions Supply-Chain Audit — FINAL — 2026-08-24

## Zakres

Przeskanowano wszystkie aktualne pliki `.github/workflows/*` w repozytoriach:

- gui-kit — 3 workflowy
- chodzkos-detection — 2 workflowy
- epubforge — 4 workflowy
- pdf2md — 2 workflowy
- icoforge — 2 workflowy
- mediaforge — 1 workflow

Łącznie: **14 workflowów**.

## Polityka

Każde zewnętrzne `uses:` powinno mieć postać:

```yaml
uses: owner/action@<FULL_40_CHAR_COMMIT_SHA>  # vX.Y.Z
```

Ruchome referencje `@main`, `@master`, `@vN` są niezgodne z docelowym standardem supply-chain.

## Wynik

| Repo | Workflowy | Wynik |
|---|---:|---|
| gui-kit | 3 | PASS |
| chodzkos-detection | 2 | PASS |
| epubforge | 4 | PASS |
| icoforge | 2 | PASS |
| mediaforge | 1 | PASS |
| pdf2md | 2 | **FAIL — 1 workflow** |

## Finding CI-SC-001 — pdf2md/release.yml

**Severity:** HIGH  
**Status:** OPEN

`pdf2md/.github/workflows/ci.yml` jest przypięty do pełnych SHA, ale `release.yml` używa ruchomych tagów:

```yaml
actions/checkout@v7
astral-sh/setup-uv@v7
actions/upload-artifact@v7
actions/download-artifact@v8
```

To tworzy niespójność: najbardziej uprzywilejowany tor — publikacja release — ma słabszy hardening niż zwykłe CI.

### Dodatkowe ustalenie

`pdf2md/.github/dependabot.yml` ma już ekosystem:

```yaml
package-ecosystem: "github-actions"
schedule:
  interval: "weekly"
```

Czyli infrastruktura do późniejszego automatycznego podbijania SHA już istnieje.

## Zalecana poprawka

Osobny PR typu security/chore:

1. zamienić każde `uses: ...@vN` w `release.yml` na pełny 40-znakowy SHA,
2. zostawić komentarz z czytelną wersją,
3. nie zmieniać logiki workflow,
4. zweryfikować diff jako wyłącznie supply-chain pinning,
5. uruchomić CI,
6. wykonać bezpieczną walidację workflow release bez publikowania niezamierzonego wydania.

## Wniosek dla Software Standard

Reguła pełnego SHA dla GitHub Actions jest potwierdzona zarówno historycznym audytem, jak i stanem 13 z 14 aktualnych workflowów. Może mieć status:

**GLOBAL / REQUIRED / SECURITY**
