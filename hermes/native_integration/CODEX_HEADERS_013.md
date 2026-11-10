# Codex session-header compatibility — source candidate, not release approval

NO_MERGE / NO_DEPLOY / NOT_FOR_INSTALL. Plugin sources and permissions are unchanged.
This source increment follows f09a30bf. Old proof applies only to old source bytes.

## Narrow product fix
The native ResponsesApiTransport emits `extra_headers` for ChatGPT Codex. The
old factory boundary rejected the native builder output before any SDK call.
Only api_mode `codex_responses`, provider `openai-codex`, and the exact HTTPS
endpoint `https://chatgpt.com/backend-api/codex` (optional trailing slash) may now
carry exactly `session_id` and `x-client-request-id`. Values must equal the live
session ID and body `prompt_cache_key`, respectively; values are bounded printable
ASCII without spaces/controls. Both fields enter the registered capability hash,
so coordinated mutation after registration also refuses before the callback.

No arbitrary `extra_headers`, Authorization, Host, beta headers, case aliases,
extra_body, custom endpoints or new tools are allowed. Native automatic compaction
(`context_management`) and nonempty extra_body remain unsupported. This is not
proof of all retry/fallback routes, actual credentials, a live provider or E2E.

## Evidence expectations
40 new tests: 38 native builder/lifetime cases including all three profiles,
plus 2 real OpenAI SDK HTTP-serialization/stream tests using httpx.MockTransport.
All use synthetic task/route data; no model or real network calls. The two SDK
cases require the real dependency; no stub or skip replaces them in CI.

The retained 25-case round2 suite keeps the prior rejection scenario on its
unapproved localhost endpoint, with a corrected test name and rejection message.
Assertions that the endpoint was not reached and generation was revoked remain.
The genuine production-endpoint positives are now independent tests.

The canonical collector requires all 9 collections: 670 tests, zero skips.
No full 670-test result or independent acceptance is implied by this document.
