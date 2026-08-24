# Standard Decision Log

## 2026-08-24 — Language policy
- identifiers: English
- comments: Polish
- docstrings: Polish
- docs: Polish by default
Status: ACCEPTED / GLOBAL_REQUIRED

## 2026-08-24 — Merge policy
- squash merge default
- `--no-ff` only as justified exception
Status: ACCEPTED / GLOBAL_REQUIRED

## 2026-08-24 — Git dependency pinning
- full commit SHA + readable version comment
- no bare tag/main
Status: ACCEPTED / GLOBAL_REQUIRED / SECURITY

## 2026-08-24 — GitHub Actions pinning
- external actions pinned to full commit SHA + version comment
- no floating `@vN`
Status: ACCEPTED / GLOBAL_REQUIRED / SECURITY

## 2026-08-24 — DONE != VERIFIED
- implementation, CI and merge are not sufficient evidence
- verification depends on change class
Status: ACCEPTED / GLOBAL_REQUIRED

## 2026-08-24 — Multi-agent isolation
- one logical task / branch / current owner
- implementer != reviewer
Status: ACCEPTED / GLOBAL_REQUIRED

## 2026-08-24 — HIGH/CRITICAL handling
- blocks merge and release
- false-positive/exception requires recorded rationale
Status: ACCEPTED / GLOBAL_REQUIRED / SECURITY

## 2026-08-24 — gui-kit boundary
- canonical shared desktop GUI source
- theme/chrome palette only; domain colors stay app data
Status: ACCEPTED / GLOBAL_REQUIRED in GUI domain

## 2026-08-24 — chodzkos-detection boundary
- stdlib-only generic CLI/HTTP probes
- no Qt/torch/heavy GPU/hardware policy
Status: ACCEPTED / GLOBAL_REQUIRED in detection domain

## 2026-08-24 — LOC guidance
- <400 preferred
- 400–500 inspect
- >500 justify/split assessment
- never split solely for metric
Status: ACCEPTED / GLOBAL_RECOMMENDED

## 2026-08-24 — Known non-compliance
- pdf2md release workflow has floating Actions tags, floating uv toolchain input, and workflow-level write
Status: OPEN FINDING

## 2026-08-24 — Standard v1.0
- final consistency review: APPROVE
- transient repo violations moved to KNOWN_NON_COMPLIANCE.md
Status: ACCEPTED
