"""One-start finalization for NEW private job databases only.

No daemon, model, shell, task requeue, native migration or historical recovery.
The caller must fence service admission and supply freshly verified quiescence.
The journal is captured by SQLite triggers in the SAME commits as native task
creation and run claims. Live writes require a per-connection function installed
only by the sealed driver inside the bound service cgroup. Not a same-UID sandbox.
"""
import contextlib as fj_contextlib
import hashlib as fj_hashlib
import json as fj_jsonlib
import os as fj_os
from pathlib import Path as FjPath
import re as fj_re
import sqlite3 as fj_sqlite3
import stat as fj_stat
import time as fj_time

FJ_PROTOCOL = 'one-start-finalization-v1'
FJ_TABLE = 'sf_finalization_job'
FJ_RUNS = 'sf_finalization_runs'


class FjRefused(RuntimeError):
    pass


class FjPhaseFinished(FjRefused):
    pass


def fj_need(value, code):
    if not value:
        raise FjRefused(code)


def fj_bytes(value):
    return (fj_jsonlib.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode()


def fj_sha(raw):
    return fj_hashlib.sha256(raw).hexdigest()


def fj_pairs(items):
    out = {}
    for key, value in items:
        fj_need(key not in out, 'duplicate_finalization_key')
        out[key] = value
    return out


def fj_decode(raw):
    return fj_jsonlib.loads(raw, object_pairs_hook=fj_pairs)


def fj_path(path):
    path = FjPath(path)
    fj_need(path.is_absolute() and '..' not in path.parts, 'finalization_path_not_absolute')
    for item in (path, *path.parents):
        fj_need(not item.is_symlink(), 'finalization_symlink')
    return path


def fj_read(path, limit=1024*1024):
    path = fj_path(path)
    fd = fj_os.open(path, fj_os.O_RDONLY | fj_os.O_NOFOLLOW | fj_os.O_CLOEXEC)
    try:
        before = fj_os.fstat(fd)
        fj_need(fj_stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and before.st_uid == fj_os.geteuid() and before.st_size <= limit,
                'finalization_input_not_owned_regular_single_link')
        chunks = []; size = 0
        while True:
            data = fj_os.read(fd, min(65536, limit+1-size))
            if not data:
                break
            chunks.append(data); size += len(data)
            fj_need(size <= limit, 'finalization_input_oversize')
        after = fj_os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        fj_need(identity(before) == identity(after) and size == before.st_size,
                'finalization_input_changed_during_read')
        return b''.join(chunks)
    finally:
        fj_os.close(fd)


def fj_db_identity(path):
    st = fj_path(path).stat()
    fj_need(fj_stat.S_ISREG(st.st_mode) and st.st_nlink == 1 and st.st_uid == fj_os.geteuid()
            and fj_stat.S_IMODE(st.st_mode) in (0o600, 0o644), 'finalization_database_identity_unsafe')
    return {'device': st.st_dev, 'inode': st.st_ino, 'uid': st.st_uid}


# Explicit statements, never executescript inside a write transaction (which
# would implicitly COMMIT). The exact schema is rechecked before any finalization.
FJ_SCHEMA = (
    '''CREATE TABLE sf_finalization_job (
        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
        binding TEXT NOT NULL, binding_sha256 TEXT NOT NULL,
        task_id TEXT, last_run_id INTEGER, generation INTEGER NOT NULL DEFAULT 0,
        state TEXT NOT NULL CHECK (state IN ('open','closed')),
        receipt TEXT
    )''',
    '''CREATE TABLE sf_finalization_runs (
        run_id INTEGER PRIMARY KEY, task_id TEXT NOT NULL,
        profile TEXT, claim_lock TEXT, started_at INTEGER
    )''',
    '''CREATE TRIGGER sf_finalization_task_insert BEFORE INSERT ON tasks BEGIN
        SELECT CASE WHEN sf_one_start_scope() != 'live'
          OR (SELECT COUNT(*) FROM sf_finalization_job) != 1
          OR (SELECT state FROM sf_finalization_job) != 'open'
          OR (SELECT task_id FROM sf_finalization_job) IS NOT NULL
          THEN RAISE(ABORT, 'one_start_task_insert_refused') END;
    END''',
    '''CREATE TRIGGER sf_finalization_task_bind AFTER INSERT ON tasks BEGIN
        UPDATE sf_finalization_job SET task_id = NEW.id, generation = generation + 1;
    END''',
    '''CREATE TRIGGER sf_finalization_task_update BEFORE UPDATE ON tasks BEGIN
        SELECT CASE WHEN sf_one_start_scope() NOT IN ('live','finalize')
          OR (SELECT state FROM sf_finalization_job) != 'open'
          OR OLD.id IS NOT (SELECT task_id FROM sf_finalization_job)
          OR NEW.id IS NOT OLD.id
          OR (NEW.current_run_id IS NOT NULL AND NEW.current_run_id IS NOT
              (SELECT last_run_id FROM sf_finalization_job))
          THEN RAISE(ABORT, 'one_start_task_update_refused') END;
    END''',
    '''CREATE TRIGGER sf_finalization_task_delete BEFORE DELETE ON tasks BEGIN
        SELECT RAISE(ABORT, 'one_start_task_delete_refused');
    END''',
    '''CREATE TRIGGER sf_finalization_run_insert BEFORE INSERT ON task_runs BEGIN
        SELECT CASE WHEN sf_one_start_scope() != 'live'
          OR (SELECT state FROM sf_finalization_job) != 'open'
          OR NEW.task_id IS NOT (SELECT task_id FROM sf_finalization_job)
          OR EXISTS (SELECT 1 FROM task_runs WHERE ended_at IS NULL)
          THEN RAISE(ABORT, 'one_start_run_insert_refused') END;
    END''',
    '''CREATE TRIGGER sf_finalization_run_bind AFTER INSERT ON task_runs BEGIN
        INSERT INTO sf_finalization_runs(run_id, task_id, profile, claim_lock, started_at)
            VALUES (NEW.id, NEW.task_id, NEW.profile, NEW.claim_lock, NEW.started_at);
        UPDATE sf_finalization_job SET last_run_id = NEW.id, generation = generation + 1;
    END''',
    '''CREATE TRIGGER sf_finalization_run_update BEFORE UPDATE ON task_runs BEGIN
        SELECT CASE WHEN sf_one_start_scope() NOT IN ('live','finalize')
          OR (SELECT state FROM sf_finalization_job) != 'open'
          OR NEW.id IS NOT OLD.id OR NEW.task_id IS NOT OLD.task_id
          OR NEW.profile IS NOT OLD.profile OR NEW.started_at IS NOT OLD.started_at
          OR (OLD.ended_at IS NOT NULL AND NEW.ended_at IS NOT OLD.ended_at)
          THEN RAISE(ABORT, 'one_start_run_update_refused') END;
    END''',
    '''CREATE TRIGGER sf_finalization_run_delete BEFORE DELETE ON task_runs BEGIN
        SELECT RAISE(ABORT, 'one_start_run_delete_refused');
    END''',
)


def fj_schema_map():
    return {statement.split()[2]: ' '.join(statement.split()) for statement in FJ_SCHEMA}


def fj_check_schema(conn):
    rows = conn.execute("SELECT name,sql FROM sqlite_master WHERE name GLOB 'sf_finalization_*'").fetchall()
    actual = {row['name']: ' '.join(row['sql'].split()) for row in rows}
    fj_need(actual == fj_schema_map(), 'finalization_schema_changed')


def fj_register(conn, callback):
    # Deliberately NOT deterministic: cancellation is checked on every statement.
    conn.create_function('sf_one_start_scope', 0, callback, deterministic=False)


def fj_binding_base(spec, descriptor_raw):
    d = fj_decode(descriptor_raw)
    job = fj_path(spec['job']); public = fj_path(d['public'])
    expected_db = job/'task-home/.hermes/kanban/boards'/spec['board']/'kanban.db'
    fj_need(fj_re.fullmatch(r'sf-job-[0-9a-f]{16}', spec['board']) is not None,
            'finalization_board_not_private_job')
    fj_need(fj_re.fullmatch(r'[a-z0-9][a-z0-9-]{0,39}', spec['job_id']) is not None,
            'finalization_invalid_job_id')
    fj_need(spec['db'] == str(expected_db) and d['job'] == str(job)
            and d['job_id'] == spec['job_id'] and d['uid'] == fj_os.geteuid()
            and d['candidate_manifest'] == spec['manifest']
            and d['backend'] == 'systemd-user-v1'
            and spec['public'] == str(public/'source-job')
            and fj_re.fullmatch(r'[0-9a-f]{32}', d['token']) is not None,
            'finalization_request_descriptor_mismatch')
    fj_need(fj_sha(fj_read(job/'task_request.json', 32*1024*1024)) == d['request_sha256']
            and fj_sha(fj_read(job/'source_job_driver.py', 32*1024*1024)) == d['driver_sha256'],
            'finalization_driver_or_request_changed')
    return {'schema': 1, 'protocol': FJ_PROTOCOL, 'job_id': spec['job_id'], 'token': d['token'],
            'descriptor_sha256': fj_sha(descriptor_raw), 'request_sha256': d['request_sha256'],
            'driver_sha256': d['driver_sha256'], 'candidate_manifest': spec['manifest'],
            'board': spec['board'], 'db': str(expected_db), 'job': str(job), 'public': str(public),
            'uid': d['uid'], 'boot': d['boot'], 'max_runs': 2*spec['loop_limits']['coding_cycles']}


def fj_initialize(kb, spec, descriptor_raw):
    """One admission-only initialization, before any service can start."""
    binding = fj_binding_base(spec, descriptor_raw)
    path = fj_path(binding['db'])
    fj_need(not path.exists(), 'finalization_database_already_exists_no_readmission')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old_umask = fj_os.umask(0o077)
    try:
        with kb.connect_closing(path, board=binding['board']) as conn:
            fj_need(conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
                    and conn.execute('SELECT COUNT(*) FROM task_runs').fetchone()[0] == 0,
                    'finalization_requires_empty_new_database')
            binding['database_identity'] = fj_db_identity(path)
            with kb.write_txn(conn):
                for statement in FJ_SCHEMA:
                    conn.execute(statement)
                conn.execute('INSERT INTO sf_finalization_job(singleton,binding,binding_sha256,state) VALUES(1,?,?,?)',
                             (fj_bytes(binding).decode(), fj_sha(fj_bytes(binding)), 'open'))
            fj_check_schema(conn)
        return binding
    finally:
        fj_os.umask(old_umask)


def fj_validate_binding(spec):
    job = FjPath(spec['job'])
    descriptor_raw = fj_read(job/'managed_job.json')
    base = fj_binding_base(spec, descriptor_raw)
    raw = fj_read(job/'finalization_binding.json')
    fj_need(raw == fj_read(FjPath(base['public'])/'finalization_binding.json'),
            'finalization_binding_copies_differ')
    binding = fj_decode(raw)
    fj_need(set(binding) == set(base)|{'database_identity'}
            and all(binding[k] == v for k,v in base.items()), 'finalization_binding_identity_changed')
    fj_need(binding['database_identity'] == fj_db_identity(binding['db']), 'finalization_database_replaced')
    return binding


def fj_live_scope(binding):
    """Called by SQLite inside the service, never an authority from a report."""
    try:
        job, public = FjPath(binding['job']), FjPath(binding['public'])
        if (public/'finalization_intent.json').exists() or (public/'stop_requested.json').exists():
            return 'denied'
        fj_need(binding['uid'] == fj_os.geteuid()
                and FjPath('/proc/sys/kernel/random/boot_id').read_text().strip() == binding['boot'],
                'finalization_live_boot_or_uid')
        fj_need(fj_sha(fj_read(job/'managed_job.json')) == binding['descriptor_sha256'],
                'finalization_live_descriptor_changed')
        release = fj_decode(fj_read(public/'managed_service_release.json'))
        record = fj_decode(fj_read(public/'service_native.json'))
        unit = 'sf-job-'+binding['token']+'-native.service'
        cg = release.get('cgroup')
        fj_need(release.get('token') == binding['token'] and release.get('job_id') == binding['job_id']
                and release.get('descriptor_sha256') == binding['descriptor_sha256']
                and release.get('unit') == unit and record.get('unit') == unit
                and record.get('token') == binding['token']
                and record.get('descriptor_sha256') == binding['descriptor_sha256']
                and record.get('uid') == binding['uid'] and record.get('boot') == binding['boot']
                and record.get('invocation_id') == release.get('invocation_id')
                and type(release.get('invocation_id')) is str
                and fj_re.fullmatch(r'[0-9a-f]{32}', release['invocation_id']) is not None
                and type(cg) is str and cg.endswith('/'+unit) and record.get('cgroup') == cg,
                'finalization_live_service_binding')
        groups = FjPath('/proc/self/cgroup').read_text().splitlines()
        fj_need(groups == ['0::'+cg], 'finalization_writer_outside_bound_cgroup')
        fj_need(fj_db_identity(binding['db']) == binding['database_identity'],
                'finalization_live_database_replaced')
        return 'live'
    except Exception:
        return 'denied'


def fj_install_runtime(spec):
    """Add one connection function; retain the native connector and transactions."""
    binding = fj_validate_binding(spec)
    from hermes_cli import kanban_db as kb
    fj_need(not hasattr(kb, '_sf_finalization_binding'), 'finalization_hook_already_installed')
    native_connect = kb._sqlite_connect
    def connect(*args, **kwargs):
        conn = native_connect(*args, **kwargs)
        try:
            names = conn.execute('PRAGMA database_list').fetchall()
            main = next(row[2] for row in names if row[1] == 'main')
            if main == binding['db']:
                fj_register(conn, lambda: fj_live_scope(binding))
            return conn
        except BaseException:
            conn.close()
            raise
    kb._sf_finalization_binding = dict(binding)
    kb._sqlite_connect = connect
    return binding


@fj_contextlib.contextmanager
def fj_open(binding):
    """Open an EXISTING bound DB, never kb.connect's repair/init/quarantine path."""
    path = fj_path(binding['db'])
    fj_need(fj_db_identity(path) == binding['database_identity'], 'finalization_database_replaced')
    # Check sidecars as well; no symlink/hardlink escape through SQLite journaling.
    for suffix in ('-wal','-shm','-journal'):
        sidecar = fj_path(str(path)+suffix)
        if sidecar.exists():
            st = sidecar.stat()
            fj_need(fj_stat.S_ISREG(st.st_mode) and st.st_uid == binding['uid'] and st.st_nlink == 1,
                    'finalization_unsafe_database_sidecar')
    conn = fj_sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=0.1)
    conn.row_factory = fj_sqlite3.Row
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('PRAGMA synchronous=FULL')
        conn.execute('PRAGMA busy_timeout=100')
        fj_register(conn, lambda: 'finalize')
        fj_need(fj_db_identity(path) == binding['database_identity'], 'finalization_database_replaced_on_open')
        fj_check_schema(conn)
        yield conn
    finally:
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def fj_guard(conn, binding):
    rows = conn.execute('SELECT * FROM sf_finalization_job').fetchall()
    fj_need(len(rows) == 1 and rows[0]['singleton'] == 1, 'finalization_guard_cardinality')
    row = dict(rows[0])
    fj_need(row['binding'] == fj_bytes(binding).decode()
            and row['binding_sha256'] == fj_sha(fj_bytes(binding)), 'finalization_guard_binding_changed')
    return row


def fj_snapshot(conn, binding):
    fj_check_schema(conn)
    guard = fj_guard(conn, binding)
    tid = guard['task_id']
    tasks = conn.execute('SELECT * FROM tasks').fetchall()
    # This is a one-card database from admission. Never pick a card by status.
    fj_need(len(tasks) == (0 if tid is None else 1), 'finalization_task_inventory_changed')
    task = dict(tasks[0]) if tasks else None
    fj_need(task is None or task['id'] == tid, 'finalization_task_binding_changed')
    runs = [dict(r) for r in conn.execute('SELECT * FROM task_runs ORDER BY id')]
    journal = [dict(r) for r in conn.execute('SELECT * FROM sf_finalization_runs ORDER BY run_id')]
    fj_need(len(runs) == len(journal) <= binding['max_runs'], 'finalization_run_inventory_changed')
    fj_need(guard['last_run_id'] == (runs[-1]['id'] if runs else None)
            and guard['generation'] == len(runs)+(1 if task else 0), 'finalization_run_generation_changed')
    for run, saved in zip(runs, journal):
        fj_need(run['id'] == saved['run_id'] and run['task_id'] == saved['task_id'] == tid
                and run['profile'] == saved['profile'] and run['started_at'] == saved['started_at'],
                'finalization_run_binding_changed')
        if run['ended_at'] is None:
            fj_need(run['claim_lock'] == saved['claim_lock'], 'finalization_run_claim_changed')
    open_runs = [r for r in runs if r['ended_at'] is None]
    if open_runs:
        fj_need(len(open_runs) == 1 and task is not None and task['status'] == 'running'
                and task['current_run_id'] == open_runs[0]['id'] == guard['last_run_id']
                and task['claim_lock'] == open_runs[0]['claim_lock']
                and task['assignee'] == open_runs[0]['profile'], 'finalization_active_run_conflict')
        fj_need(task['worker_pid'] == open_runs[0]['worker_pid'], 'finalization_worker_binding_changed')
    elif task is not None:
        fj_need(task['current_run_id'] is None and task['claim_lock'] is None
                and task['claim_expires'] is None and task['worker_pid'] is None
                and task['status'] != 'running', 'finalization_unjournaled_active_claim')
    snapshot = {'generation': guard['generation'], 'task': task, 'runs': runs, 'journal': journal}
    return snapshot, fj_sha(fj_bytes(snapshot))


def fj_validate_intent(binding, intent):
    expected = fj_sha(fj_bytes({'protocol': FJ_PROTOCOL, 'binding_sha256': fj_sha(fj_bytes(binding))}))
    fj_need(set(intent) == {'schema','protocol','binding_sha256','operation_id','reason','created_utc'}
            and intent['schema'] == 1 and intent['protocol'] == FJ_PROTOCOL
            and intent['binding_sha256'] == fj_sha(fj_bytes(binding))
            and intent['operation_id'] == expected
            and intent['reason'] in ('failed','cancelled','monitor_lost','postflight_failed'),
            'finalization_intent_identity_changed')
    return expected


def fj_validate_quiescence(binding, proof):
    fj_need(proof.get('schema') == 1 and proof.get('binding_sha256') == fj_sha(fj_bytes(binding))
            and proof.get('token') == binding['token'] and proof.get('boot') == binding['boot']
            and proof.get('descriptor_sha256') == binding['descriptor_sha256']
            and proof.get('admission_fenced') is True and proof.get('start_consumed') is True,
            'finalization_quiescence_binding_changed')
    if proof.get('kind') == 'never_launched':
        fj_need(proof.get('unit_not_found') is True and proof.get('service_record_absent') is True
                and proof.get('release_artifacts_absent') is True, 'finalization_never_launched_not_proved')
    else:
        fj_need(proof.get('kind') == 'bound_service_empty'
                and type(proof.get('invocation_id')) is str
                and fj_re.fullmatch(r'[0-9a-f]{32}', proof['invocation_id']) is not None
                and proof.get('unit') == 'sf-job-'+binding['token']+'-native.service'
                and type(proof.get('cgroup')) is str and proof['cgroup'].endswith('/'+proof['unit'])
                and proof.get('cgroup_empty') is True and proof.get('observed_processes_absent') is True
                and proof.get('diagnostic_absence_only') is False
                and proof.get('previous_boot_ended') is False
                and proof.get('manager_terminal') is True,
                'finalization_service_not_verified_quiescent')


def fj_finalize(kb, conn, binding, intent, proof, expected_snapshot_sha):
    """One atomic closure; DB receipt survives a crash before external reporting."""
    operation = fj_validate_intent(binding, intent)
    fj_validate_quiescence(binding, proof)
    fj_need(fj_db_identity(binding['db']) == binding['database_identity'], 'finalization_database_replaced')
    with kb.write_txn(conn):
        fj_check_schema(conn)
        guard = fj_guard(conn, binding)
        if guard['state'] == 'closed':
            receipt = fj_decode(guard['receipt'])
            fj_need(receipt['operation_id'] == operation and receipt['intent'] == intent
                    and receipt['binding_sha256'] == fj_sha(fj_bytes(binding))
                    and receipt['quiescence_sha256'] == fj_sha(fj_bytes(receipt['quiescence'])),
                    'finalization_closed_operation_conflict')
            fj_validate_quiescence(binding, receipt['quiescence'])
            snap, unused = fj_snapshot(conn, binding)
            fj_need(fj_sha(fj_bytes(snap)) == receipt['final_snapshot_sha256'],
                    'finalization_closed_snapshot_changed')
            return receipt
        snap, actual_sha = fj_snapshot(conn, binding)
        fj_need(actual_sha == expected_snapshot_sha, 'finalization_compare_and_swap_conflict')
        task = snap['task']; tid = task['id'] if task else None
        fj_need(proof['kind'] != 'never_launched' or (task is None and not snap['runs']),
                'finalization_task_exists_without_service')
        done = task is not None and task['status'] == 'done'
        fj_need(task is None or task['status'] in ('ready','running','review','blocked','done'),
                'finalization_unexpected_native_state')
        closed_run = None
        if task is not None and not done:
            if task['current_run_id'] is not None:
                own = next(r for r in snap['runs'] if r['id'] == task['current_run_id'])
                metadata = fj_decode(own['metadata']) if own['metadata'] else {}
                fj_need(type(metadata) is dict and 'one_start_finalization' not in metadata,
                        'finalization_run_metadata_conflict')
                metadata = dict(metadata, one_start_finalization={
                    'operation_id': operation, 'reason': intent['reason'], 'no_retry': True})
                closed_run = kb._end_run(conn, tid, outcome='blocked', status='blocked',
                    error='one_start_'+intent['reason'], summary='One-start job ended; no automatic retry.',
                    metadata=metadata)
                fj_need(closed_run == task['current_run_id'], 'finalization_wrong_run_closed')
            changed = conn.execute('''UPDATE tasks SET status='blocked', block_kind='needs_input',
                current_run_id=NULL, worker_pid=NULL, claim_lock=NULL, claim_expires=NULL,
                last_failure_error=? WHERE id=?''', ('one_start_'+intent['reason'], tid))
            fj_need(changed.rowcount == 1, 'finalization_task_update_lost')
        receipt = {'schema': 1, 'protocol': FJ_PROTOCOL, 'operation_id': operation,
            'binding_sha256': fj_sha(fj_bytes(binding)), 'intent': intent,
            'job_id': binding['job_id'], 'board': binding['board'], 'task_id': tid,
            'last_run_id': guard['last_run_id'], 'closed_run_id': closed_run,
            'prior_native_status': task['status'] if task else None,
            'native_status': 'done' if done else ('blocked' if task else None),
            'preserved_done': done, 'no_task_created': task is None,
            'job_outcome': 'cancelled' if intent['reason'] == 'cancelled' else 'failed',
            'start_consumed': True, 'task_requeued': False, 'inference_retried': False,
            'quiescence_sha256': fj_sha(fj_bytes(proof)), 'quiescence': proof}
        if task is not None:
            # 'blocked' is also the native sticky-block fact, not circuit-breaker
            # gave_up (which native recompute_ready may automatically promote).
            kb._append_event(conn, tid, 'one_start_finalized' if done else 'blocked',
                {'kind': 'needs_input', 'reason': 'one_start_'+intent['reason'],
                 'operation_id': operation, 'no_retry': True, 'preserved_done': done},
                run_id=guard['last_run_id'])
        final, unused = fj_snapshot(conn, binding)
        receipt['final_snapshot_sha256'] = fj_sha(fj_bytes(final))
        conn.execute("UPDATE sf_finalization_job SET state='closed',receipt=? WHERE singleton=1 AND state='open'",
                     (fj_bytes(receipt).decode(),))
    return receipt


def fj_admit_prepared_board(kb, spec):
    """Replace only the FUTURE controller's old 'board must not exist' check.

    Admission now creates the empty bound DB before launch, so the historical
    predicate cannot be reused. The legacy and accepted managed drivers keep it.
    """
    binding=fj_validate_binding(spec)
    fj_need(fj_live_scope(binding)=='live','finalization_board_outside_bound_service')
    fj_need(str(kb.kanban_db_path(board=spec['board']))==binding['db'], 'finalization_board_path_changed')
    with kb.connect_closing(board=spec['board']) as conn:
        guard=fj_guard(conn,binding)
        snapshot,unused=fj_snapshot(conn,binding)
        fj_need(guard['state']=='open' and snapshot['task'] is None and not snapshot['runs'],
                'finalization_board_already_consumed')
    fj_need(not (FjPath(binding['db']).parent/'board.json').exists(), 'finalization_board_metadata_already_exists')
    kb.create_board(spec['board'])
    kb.set_current_board(spec['board'])
