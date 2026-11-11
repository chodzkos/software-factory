# Parent stack (source only)

Status: **source-only, not installed, not executed.** `parent_implemented=False`.

These 22 modules are the native-parent work in progress (process owner, gated native spawn and
launch, exec gate, launch bootstrap) together with the libraries it imports (repository task
contract, execution admission/launcher/transport/service, native adapter and controller,
finalization core, memfd seals, workers, parent result).

## Provenance

The bytes were accepted in activation review 4 (`activation-review-01-d4264681`): the review's
`SOURCE_UNION.json` plus `SOURCE_DELTA.json`. There the WIP suite passed 162/162 on CPython
3.11.16. `PARENT_PROVENANCE.json` records, per file, the accepted path and SHA-256 and the
repository SHA-256.

Changes after import (`PARENT_PROVENANCE.json` lists every changed file):
- `repository_parent_native_spawn_v1.py` (PR 2):
  - `_fire_worker_spawned_hook` runs only after the exec gate has released the worker, so
    observers see the worker PID. An observer exception is recorded and does not fault a live run.
  - The constructor and every operation refuse with `spawn_global_reaper_in_process` while a native
    module that reaps every child (`waitpid(-1)` via `dispatch_once`, `run_daemon` or the gateway
    watcher) is loaded. This detects the condition; it does not prevent it, and native code is not
    modified. A direct `os.waitpid(-1)` from other in-process code is still possible. The process
    owner reports it as terminal evidence loss, never as success.
  - After release, a caller error (for example an interrupt) is recorded in
    `POST_RELEASE_ERROR.json`. It does not mark the attempt failed or request finalization of
    the live, owned worker.

The first change was made at import: `repository_task_contract.py`. A file inside the candidate cannot
contain the digest of the manifest that covers it, so the hard-coded `CANDIDATE` was replaced:
- `bind_candidate(digest)` binds the accepted manifest digest once per process. Only the trusted
  factory bootstrap calls it. Rebinding the same digest is a no-op. A different digest is refused.
- `bound_candidate()` returns the bound digest.
- Reading `CANDIDATE` resolves through a PEP 562 module `__getattr__`. Until the digest is bound,
  every read refuses with `candidate_unbound` (fail closed).

`hermes/tests_parent` checks that this change, reversed, reproduces the accepted bytes.

`finalization_core.py` is byte-identical to `native_integration/factory_finalization_core.py`. A
test keeps the two copies equal.

## Not covered yet (later stages)

- B6 r3: a launch-bootstrap record for the current runtime.
- The sealed service child (`repository_execution_launcher_v3.driver_bytes`) re-executes
  `repository_task_contract` in a fresh process where nothing binds the candidate. It refuses with
  `candidate_unbound` (fail closed) until the composition passes the verified digest into the child
  and binds it there.
- `bind_candidate` is first-caller-wins. The trusted bootstrap must bind before any other code runs
  in the process.
- Composition with the controller, `RepositoryExecutionLauncher`, custody/anchor and joint
  finalization.
- The controller `Authority` pins (`LIBRARY_REVIEW_SHA`, `ADMISSION_HELPER_SHA`) and the
  `repository_execution_service` base pins refer to the earlier review chain. They have to be
  re-anchored when the stack is composed.

The old WIP test suite stays outside the repository as historical evidence. It is bound to the
whole review-4 source union (about 30 MB of old artifacts).
