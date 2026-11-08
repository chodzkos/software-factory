"""Local-only, phase-driven tests of the real process supervisor (no provider).

The subprocess is its own ptrace/waitpid domain; lease probes cannot be reaped
by its waitpid(-1). FIFOs/phase files determine order. Deadlines only bound a
broken test, never grant permission to write or indicate successful cleanup.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

TIMEOUT = 10.0


def _emit(root: Path, phase: str, value: dict) -> None:
    target = root / phase
    temporary = root / (phase + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(target)


def _wait(root: Path, phase: str, process=None) -> dict:
    deadline = time.monotonic() + TIMEOUT
    while not (root / phase).exists():
        if process is not None and process.poll() is not None:
            raise AssertionError(f'{phase}: process exited {process.returncode}; '
                                 f'log={(root / "runner.log").read_text()}')
        if time.monotonic() >= deadline:
            raise AssertionError(f'phase deadline: {phase}')
        time.sleep(0.01)  # Polling only: phase order never depends on this value.
    return json.loads((root / phase).read_text(encoding='utf-8'))


def _identity(pid: int) -> dict:
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()
        return {'pid': pid, 'start': fields[19], 'state': fields[0]}
    except FileNotFoundError:
        return {'pid': pid, 'start': None, 'state': 'absent'}


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {path}')
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _runner(root: Path, plugin: Path) -> int:
    module = _load(plugin / 'supervisor.py', 'phase_supervisor')
    module._HANDOFF._load_kanban_db = lambda: object()
    module._ambient_board = lambda _kb: 'isolated'
    module._HANDOFF.active_coder_run_matches = lambda: not (root / 'revoked').exists()
    original_kill = module._kill_traced
    original_trace = module._trace_until_quiescent
    original_register = module._register_tracee
    original_waitpid = os.waitpid
    observed: dict[int, str] = {}
    terminal_waits: dict[int, int] = {}
    held = False

    def register(pid, identities, pidfds):
        original_register(pid, identities, pidfds)
        observed[pid] = identities[pid]

    def waitpid(pid, options):
        got, status = original_waitpid(pid, options)
        if got in observed and (os.WIFEXITED(status) or os.WIFSIGNALED(status)):
            terminal_waits[got] = status
        return got, status

    def kill(identities, pidfds):
        nonlocal held
        if not held:
            held = True
            _emit(root, 'cancel_entered', {'identities': identities,
                                           'pidfds': sorted(pidfds)})
            try:
                _wait(root, 'allow_kill')
            finally:
                original_kill(identities, pidfds)
        else:
            original_kill(identities, pidfds)

    def trace(*args, **kwargs):
        rc, lost = original_trace(*args, **kwargs)
        no_waitable = {}
        for pid in observed:
            try:
                no_waitable[str(pid)] = list(original_waitpid(pid, os.WNOHANG | module._WAIT_WALL))
            except ChildProcessError:
                no_waitable[str(pid)] = 'ECHILD'
        _emit(root, 'quiescent_with_lease', {
            'rc': rc, 'authorization_lost': lost, 'observed': observed,
            'terminal_waits': terminal_waits, 'no_waitable': no_waitable,
            'process_states': {str(pid): _identity(pid) for pid in observed},
        })
        _wait(root, 'allow_lease_release')
        return rc, lost

    module._register_tracee = register
    module._kill_traced = kill
    module._trace_until_quiescent = trace
    os.waitpid = waitpid
    worker_pid, worker_start = module._HANDOFF._process_identity(os.getppid())
    return module.supervise(
        ['claude', str(root)], board='isolated', task_id='t_supervise', run_id=9,
        workspace=str(root / 'workspace'), worker_pid=worker_pid,
        worker_start=worker_start, poll_seconds=0.01,
    )


def _writer(root: Path) -> int:
    # The test owns an O_RDWR descriptor so the reader can open independently.
    with (root / 'write_gate').open('rb', buffering=0) as gate:
        _emit(root, 'writer_ready', _identity(os.getpid()))
        generation = 0
        while gate.read(1) == b'W':
            (root / 'late').write_text('late', encoding='utf-8')
            generation += 1
            _emit(root, 'write_complete', {'generation': generation})
    return 0


def _leader(root: Path) -> int:
    child = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve()),
                              'writer', str(root)])
    _emit(root, 'leader_ready', _identity(os.getpid()))
    return child.wait()


def _probe(root: Path, plugin: Path, label: str, held: bool) -> int:
    if held:
        _emit(root, label + '_entered', {})
        _wait(root, label + '_allow')
    module = _load(plugin / 'handoff.py', 'phase_lease_probe')
    try:
        with module.mutation_lease('isolated', 't_supervise',
                                   str(root / 'workspace'), blocking=False):
            pass
    except module.HandoffError as exc:
        if str(exc) != 'mutation lease already held':
            raise
        return 7
    return 0


def assert_reaped(case, evidence: dict, identities: list[dict]) -> None:
    """Do not accept zombies, PID reuse as the original process, or missing reap."""
    expected = {str(item['pid']) for item in identities}
    case.assertEqual(set(evidence['observed']), expected)
    case.assertEqual(set(evidence['terminal_waits']), expected)
    for item in identities:
        key = str(item['pid'])
        case.assertEqual(evidence['observed'][key], item['start'])
        case.assertNotEqual(evidence['process_states'][key]['start'], item['start'],
                            f'original process still present (including zombie): {item}')
        case.assertEqual(evidence['no_waitable'][key], 'ECHILD')
        status = evidence['terminal_waits'][key]
        case.assertTrue(os.WIFSIGNALED(status))
        case.assertEqual(os.WTERMSIG(status), signal.SIGKILL)


def check_reclaim(case, plugin: Path, *, stalled_probe=False, writer_control=False) -> dict:
    """Run the real kill/reap/lease path with only authorization and phase fixtures."""
    # No sitecustomize/automatic numeric-library thread pools in this two-process fixture.
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix='sf-reclaim-phases-') as temporary:
        root = Path(temporary).resolve()
        (root / 'workspace').mkdir()
        os.mkfifo(root / 'write_gate', 0o600)
        gate = os.open(root / 'write_gate', os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        fake = root / 'claude'
        fake.write_text(f'#!{sys.executable} -S\n'
                        'import runpy,sys\n'
                        f'sys.argv=[{str(script)!r}, "leader", sys.argv[1]]\n'
                        f'runpy.run_path({str(script)!r}, run_name="__main__")\n')
        fake.chmod(0o755)
        env = dict(os.environ, HOME=str(root), PYTHONDONTWRITEBYTECODE='1',
                   PATH=f'{root}:{os.environ.get("PATH", "")}',
                   HERMES_KANBAN_BOARD='isolated', HERMES_KANBAN_TASK='t_supervise',
                   HERMES_KANBAN_RUN_ID='9', HERMES_KANBAN_WORKSPACE=str(root / 'workspace'))
        probe = None
        with (root / 'runner.log').open('w') as log:
            process = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(script), 'runner',
                                         str(root), str(plugin)], env=env,
                                        start_new_session=True, stdout=log, stderr=log)
            try:
                identities = [_wait(root, 'leader_ready', process),
                              _wait(root, 'writer_ready', process)]
                for item in identities:
                    case.assertEqual(_identity(item['pid'])['start'], item['start'])
                case.assertFalse((root / 'late').exists())

                def lease(label, *, stalled=False):
                    nonlocal probe
                    probe = subprocess.Popen([sys.executable, '-I', '-S', '-B', str(script), 'probe',
                                               str(root), str(plugin), label, str(int(stalled))],
                                              env=env, stdout=log, stderr=log)
                    if stalled:
                        _wait(root, label + '_entered', probe)
                        case.assertIsNone(probe.poll())
                        case.assertFalse((root / 'revoked').exists())
                        case.assertFalse((root / 'late').exists())
                        _emit(root, label + '_allow', {})
                    result = probe.wait(timeout=TIMEOUT)
                    probe = None
                    return result

                case.assertEqual(lease('active_probe', stalled=stalled_probe), 7)
                if writer_control:
                    case.assertEqual(os.write(gate, b'W'), 1)
                    case.assertEqual(_wait(root, 'write_complete', process)['generation'], 1)
                    case.assertEqual((root / 'late').read_text(), 'late')
                    (root / 'late').unlink()  # Only the acknowledged pre-revoke control write.
                case.assertFalse((root / 'late').exists())
                _emit(root, 'revoked', {})
                cancel = _wait(root, 'cancel_entered', process)
                case.assertEqual(set(cancel['identities']), {str(x['pid']) for x in identities})
                case.assertEqual(set(cancel['pidfds']), {x['pid'] for x in identities})
                case.assertEqual(lease('cancel_probe'), 7)
                for item in identities:
                    case.assertEqual(_identity(item['pid'])['start'], item['start'])
                _emit(root, 'allow_kill', {})
                evidence = _wait(root, 'quiescent_with_lease', process)
                case.assertEqual(evidence['rc'], 125)
                case.assertIs(evidence['authorization_lost'], True)
                assert_reaped(case, evidence, identities)
                case.assertEqual(lease('reaped_probe'), 7)
                # A late write request is real, but the original reader must be gone.
                case.assertEqual(os.write(gate, b'W'), 1)
                _emit(root, 'allow_lease_release', {})
                case.assertEqual(process.wait(timeout=TIMEOUT), 125)
                case.assertEqual(lease('released_probe'), 0)
                for item in identities:
                    case.assertNotEqual(_identity(item['pid'])['start'], item['start'])
                case.assertFalse((root / 'late').exists())
                evidence.update(stalled_probe=stalled_probe, writer_control=writer_control,
                                lease_results=[7, 7, 7, 0], no_late_write=True)
                print('RECLAIM_PHASE_EVIDENCE ' + json.dumps(evidence, sort_keys=True))
                return evidence
            finally:
                _emit(root, 'allow_kill', {})
                _emit(root, 'allow_lease_release', {})
                if probe is not None:
                    probe.kill()
                    probe.wait(timeout=TIMEOUT)
                if process.poll() is None:
                    try:
                        process.wait(timeout=TIMEOUT)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=TIMEOUT)
                os.close(gate)


if __name__ == '__main__':
    mode, root_value, *rest = sys.argv[1:]
    root_path = Path(root_value)
    if mode == 'runner':
        raise SystemExit(_runner(root_path, Path(rest[0])))
    if mode == 'leader':
        raise SystemExit(_leader(root_path))
    if mode == 'writer':
        raise SystemExit(_writer(root_path))
    if mode == 'probe':
        raise SystemExit(_probe(root_path, Path(rest[0]), rest[1], bool(int(rest[2]))))
    raise SystemExit(f'unknown fixture mode: {mode}')
