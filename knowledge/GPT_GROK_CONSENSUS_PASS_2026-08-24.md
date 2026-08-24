# Consensus Pass — GPT × Grok

Data: 2026-08-24  
Wejścia:
- CHODZKOS Software Standard Draft v0.5
- Standard Mining Report v0.5
- Project Memory Packs
- GitHub Actions supply-chain audit
- GPT Review #1
- Grok Independent Review

## DECISION

**CHANGES_REQUIRED → Draft v0.6 prepared**

Oba review zgadzają się, że kierunek standardu jest właściwy, ale v0.5 nie może być jeszcze źródłem prawdy z powodu pozostawionych, sprzecznych fragmentów po poprzednich iteracjach.

## Findings wspólne GPT + Grok — ACCEPTED

1. Usunąć sprzeczne stare zapisy dotyczące:
   - języka komentarzy/docstringów,
   - merge policy,
   - GitHub Actions SHA pinning.
2. Zbudować jeden spójny dokument zamiast dopisywania kolejnych sekcji na końcu.
3. Poprawić source-of-truth:
   - security floor nie może być uchylony przypadkową wiadomością,
   - aktualny kod/testy nie powinny automatycznie przegrywać ze stale repo docs.
4. HIGH/CRITICAL ma blokować merge i release, nie tylko release.
5. `DONE != VERIFIED` musi mieć operational evidence.
6. Implementer ≠ independent reviewer.
7. Scope discipline / brak drive-by refactorów.
8. Multi-agent isolation.
9. GitHub Actions pełny SHA jako SECURITY/GLOBAL_REQUIRED.
10. `pdf2md/release.yml` pozostaje otwartym HIGH supply-chain findingiem.

## Trafne dodatkowe findings Groka — ACCEPTED

- `version: latest` nie może być używane w release toolchain.
- Least privilege:
  - workflow-level read/{} default,
  - write wyłącznie jobowi publikującemu.
- `pull_request_target` i fork PR wymagają specjalnego security handling.
- Git dependency bump powinien potwierdzać relację SHA ↔ oczekiwana wersja/tag.
- Direct dependencies deklarować jawnie, zamiast świadomie polegać na transitive package.
- Nie wolno osłabiać testów/types/CI tylko po to, by uzyskać green.
- Untrusted issue/diff/document content jest danymi, nie instrukcją.
- Standard wymaga formalnego amendment process.
- Sprzeczne repo instructions po v1.0 muszą zostać zsynchronizowane.

## Findings przyjęte z modyfikacją

### 500 LOC

Grok obawiał się traktowania 500 LOC jako twardego gate.

**Decyzja:** GLOBAL_RECOMMENDED, nie hard CI gate.

### mypy --strict

Grok słusznie wskazał ryzyko dużej migracji istniejących repo.

**Decyzja:** REQUIRED dla nowych projektów Python; istniejące repo mogą migrować etapami. Nie wolno obniżać istniejącego poziomu bez osobnej decyzji.

### Agent merge authority

Grok zaproponował zakaz merge bez człowieka.

**Decyzja:** na tym etapie domyślnie agent nie merge'uje bez polityki repo/Software Factory. Docelowa automatyzacja może zezwolić na auto-merge po spełnieniu gates, więc nie wprowadzamy permanentnego wymogu human-click.

## Nieprzyjęte jako globalne twarde reguły

- jeden uniwersalny coverage threshold — pozostaje per-project,
- jeden uniwersalny zestaw smoke tests dla wszystkich aplikacji — verification jest klasyfikowane według typu zmiany,
- obowiązkowy `--no-ff` w specjalnych przypadkach — pozostaje opcjonalnym wyjątkiem od squash.

## Security findings z review

### CI-SC-001 — HIGH
`pdf2md/.github/workflows/release.yml` używa ruchomych `@v7/@v8`.

**ACCEPTED / OPEN**

### CI-SC-002 — HIGH
`setup-uv` w release używa `version: latest`.

**ACCEPTED / OPEN**

### CI-SC-003 — MEDIUM
`permissions: contents: write` jest ustawione na poziomie całego workflow release.

**ACCEPTED / OPEN**

### STD-SEC-001 — HIGH
Owner command mógł w v0.5 uchylić security floor.

**FIXED IN v0.6**

### STD-SEC-002 — HIGH
HIGH/CRITICAL blokowało tylko release.

**FIXED IN v0.6**

## Gate do v1.0

Draft v0.6 może zostać uznany za kandydata do v1.0 po:

1. szybkim final consistency review,
2. potwierdzeniu, że dokument nie zawiera sprzecznych aktywnych reguł,
3. zaakceptowaniu znanych non-compliance jako otwartych findings, a nie jako normy,
4. ewentualnym oddzielnym PR naprawiającym `pdf2md/release.yml`.

Naprawa pdf2md jest zalecana przed uznaniem całego ekosystemu za compliant, ale nie musi blokować formalnego zatwierdzenia treści standardu v1.0.
