# PR23 / 0.13.0 — second independent review response

Source-only candidate. **NO_MERGE / NO_DEPLOY / NOT_FOR_INSTALL.**
This document supersedes no runtime gate by declaration. The prior manifest was
`b52d79c1aecca25bf3c2149c79cdb1d068386e2731cdcb029b15af80cf491261`.
The next source manifest, exact patch and execution logs are packet artifacts.

## N-1: MoA is explicitly unsupported for the protected coder

The full native conversation loop sets `_moa_prepared_request` only when the
live facade exposes `prepare`. That operation can run reference models before
normal request dispatch. The prepared object is consumed by the MoA facade;
it is not a harmless metadata field to whitelist or silently strip at the
capability boundary. A turn-specific `moa_config` can also run advisors even
when the main provider is not `moa`.

The candidate now refuses an explicitly selected MoA provider before its own
native initializer, checks the effective provider after initialization before
binding, and refuses a bound worker's later route drift. Explicit and decoded
turn-level MoA are checked outside the decoder's optional-error handler, before
turn state setup, and again at the aggregate call site. The persistent prepare
site rechecks the worker before facade preparation/rebase. No discovery lock
is held across model/network calls. These checks do not claim atomic last-check
versus send or cancellation of an already admitted effect.

This is a deliberate capability restriction, **not MoA support**. Configurable
provider resolution inside the initializer remains part of the trusted native
initialization chain; a post-init refusal is not a claim of pre-init detection.
General, unprotected Hermes profiles retain the native marker handling:
facades receive the marker; replaced non-facade clients have it stripped. The
positive tests use a real general-profile initializer and mock call endpoints,
not real reference models or a real MoA facade implementation.

## N-2: explicit rejection matrix, not compatibility certification

The unchanged model boundary rejects **every nonempty `extra_body`** and revokes
the protected generation. No heuristic identifies metadata as harmless and no
request is silently retried with fewer parameters. The new tests run actual
native builders/summary construction, observe the rejected payload and assert
that no client endpoint was reached and revocation persists. Covered emissions:

| Native path | Emission | Expected protected behavior |
|---|---|---|
| Nous Messages builder | portal tags/session attribution | refuse and revoke |
| Summary with reasoning enabled | `extra_body.reasoning` | refuse and revoke |
| Nous summary | `extra_body.tags` | refuse and revoke |
| OpenRouter routing summary | `extra_body.provider` | refuse and revoke |
| Pareto-code summary | `extra_body.plugins` | refuse and revoke |
| Codex backend summary | native `extra_headers` session/request IDs | refuse and revoke |

The last row was found while expanding the retry test. The generic Responses
wire mode and the ChatGPT Codex backend are **not equivalent compatibility
claims**: the latter emits SDK header extensions which this candidate does not
accept. The test does not remove those headers to manufacture a positive result.
No chosen provider is certified for live use by these tests. Native reasoning,
routing, compaction, transformed schemas and SDK-specific additions can still
cause intentional fail-closed refusal. Full provider/SDK compatibility is open.

## Responses summary retry

In generic Responses wire mode the first summary attempt already used an empty
tool list; retry built with the worker's tools and then removed only `tools`.
The remaining capability envelope could differ and be refused. Both attempts
now call the real builder with `tools_for_api=[]`. The regression requires two
endpoint invocations, validates each exact payload and checks both builder
arguments. It uses real native conversion and lifetime validation but a response
normalization fixture and a mock endpoint, not SDK serialization or network.
The original failing test log is retained; no blanket extra-field whitelist was
introduced to make the Codex-backend case pass.

## H-1: ContextVar-selected profiles must enter the start gate

Reviewing the complete native profile resolution uncovered a concrete startup
gap, not just an unproven hypothesis. Native `get_hermes_home()` honors the
`hermes_constants._HERMES_HOME_OVERRIDE` ContextVar before environment values.
The candidate selector previously inspected only the environment, so an actual
context-scoped coder/reviewer/release home with missing or forged general-profile
environment could skip the mandatory constructor checks. Seven test-first
regressions failed on the preceding sources: six constructors reached a spy
initializer without refusal and the independent selector case returned `None`.

The selector now reads the already-loaded native module's exact stdlib
`ContextVar` object, without importing or calling native profile getters before
the selected profile's pin gate. A recognized protected home routes to the
existing mandatory checks; absent or conflicting identity is refused before the
initializer rather than silently repaired. General-profile context remains
unprotected and follows native policy. The tests use real ContextVar/native
profile resolution and a controlled initializer, not a real gateway, dispatcher,
provider call, or demonstrated post-init exploit. This does not give reviewer or
release the coder lifetime policy and does not authenticate already imported
modules or arbitrary in-process tampering. Those trust boundaries stay open.

## N-3: review the consumed patch, not only its diff

The next review materials include the entire file at the exact path
`sf/hermes/native_integration/native_runtime.patch`, its manifest, and all 14
native overlay files as COMPLETE source blocks. The SHA-256 of `SOURCE_MANIFEST.json`
is the SHA-256 of that file's raw UTF-8 bytes including its final newline, not
an unspecified composite algorithm. The complete manifest is supplied.
`verify_native_overlay.py` remains unchanged; local before/apply/after checks
are not hosted GitHub Actions, a signature, or permission to install.

## N-4 / N-5 and trust boundaries

The 28 `kanban_db.py` migration calls pass literal table, column and DDL strings.
A static regression checks those exact call sites. The separate projects-table
helper iterates the literal tuple `board_slug`, `primary_path`, `icon`, `color`
and appends the literal SQL type `TEXT`. This is not a claim about arbitrary SQL
in the whole native checkout or arbitrary callers of `add_column_if_missing`.
No production SQL change was needed for the reported hypothesis.

The five read-only plugin files and their inherited manifest remain unchanged.
Their existing `reviewed-ready` / `installable=true` status does not approve the
new combined runtime, profile integration, or native overlay. The execution
plugin and native overlay remain non-installable; bootstrap refusal is tested.

`factory_worker_start.py` is not a self-authenticating root of trust. Its own
identity comes from an external, trusted artifact/installation gate; the runtime
pinset cannot prove the identity of the code performing its own verification.
CI checks its source bytes but is not a production trusted launcher. Checking
`.py` files is not checking already executed bytecode. `-B` and
`PYTHONDONTWRITEBYTECODE` prevent cache writes, **not reads of existing `.pyc`**.
The suggested flags alone therefore do not close the cache-safe-launcher gate.

## Collection and gates still open

`review_round2` is the seventh explicit, exact-count CI collection; the earlier
six collections and assertions are retained, except that the CI inventory test
now requires the additional collection. Source/plugin/transaction protections
are not relaxed. General-profile policy tests are distinct from protected tests.

This list records the state at the time of this note, during candidate 0.13
review. Current gate status is maintained outside this note, in the owner gate
status record.

Still open: independent review of these exact new bytes; full reviewer/release
lifetime and approval-COMMIT/close ordering; trusted cache-safe import chain;
real SDK serialization and full provider/retry/auxiliary/dispatcher/Claude paths;
Python 3.11 execution; clean locked installation/wheel; hosted GitHub Actions;
full native dependency checkout audit; production native-overlay installer.
The weaker legacy argv parsers in a standalone guard import remain an explicitly
unresolved residual from the review, outside this change's MoA/retry boundary.
