---
name: evidence-ledger
description: Maintain a compact factual evidence record tied to commands, results, SHAs, review and CI; never invent missing evidence.
---

# Evidence Ledger

Record observations, not intentions. Preserve failed evidence. Never store secrets or credential-bearing output.

Recommended fields:

```text
Task ID:
Repository:
Base SHA:
Current SHA:
PR HEAD SHA:
Reviewed SHA:
Verified SHA:
Workspace:

Reproduction:
RED:
GREEN:
Tests:
Lint/type/build:
Security/dependency checks:
CI:
Reviews:
Unverified:
Blocking items:
```

Tie every major completion claim to evidence. Label evidence by SHA when SHA-sensitive. Do not merge evidence from different SHAs without saying so.

This ledger does not replace runtime gates, independent review, verification, or release-manager policy. Missing mandatory evidence stays missing and must fail closed where the task contract requires it.
