"""Worktree-native binding and guarded repository DATA operations, routed-card increment 3.0.0.

No models, worker spawning, shell, tests, approval, retry, merge or installation.
Card initialization invokes one pinned read-tree to build the new local index.
Reuses the unchanged repository contract and 1.16.0 finalization journal. The
controller must pin these modules and native kanban_db; not a hostile-caller or
same-UID sandbox. RunIdentity belongs to trusted transport, never model JSON.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import finalization_core as f
import repository_task_contract as c
import repository_worktree_v2 as w
import repository_execution_io as e
import repository_routing_v3 as routing

VERSION = '3.0.0'
INPUT_KEYS = ('approval_sha256', 'envelope_sha256', 'snapshot_sha256')
CODER, REVIEWER = 'coder-claude', 'reviewer-gpt'

class NativeRepositoryRefused(ValueError):
    """Machine-readable refusal without repository contents or credentials."""

def need(ok: object, code: str) -> None:
    if not ok:
        raise NativeRepositoryRefused(code)

@dataclass(frozen=True)
class RunIdentity:
    task_id: str
    run_id: int
    claim_lock: str

    def validate(self) -> None:
        need(type(self.task_id) is str and re.fullmatch(r't_[0-9a-f]{8}', self.task_id), 'task_identity_format')
        need(type(self.run_id) is int and self.run_id > 0, 'run_identity_format')
        need(type(self.claim_lock) is str and 0 < len(self.claim_lock) <= 256, 'claim_identity_format')

@dataclass(frozen=True)
class Context:
    spec: dict
    binding: dict
    admission: c.Admission
    staged: Path

    @property
    def work(self) -> Path:
        return Path(w.bound(self)['workspace'])

    def task_fields(self, task_id=None) -> dict:
        task = c.parse(self.admission.task)
        if task_id is not None: w.task_id(task_id)
        p = w.projection(self)
        route = routing.from_spec(self.spec, self.admission)
        body = c.canonical({'schema': 'software-factory-native-repository-card/v3',
            'routing': route.fields(), 'routing_sha256': route.digest(),
            'task': task, 'task_id': task_id, 'admission_sha256': c.sha(c.canonical(self.admission.receipt())),
            'projection_head': p['projection_head'], 'source_commit': self.admission.commit,
            'source_tree': self.admission.tree, 'policy': w.POLICY, 'execution_permitted': False}).decode()
        body = body.replace('\u0085', '\\u0085').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
        body += 'WORKSPACE: worktree:' + str(w.repository(self)) + '\n'
        body += route.declarations()
        return dict(title=task['title'].strip(), body=body, assignee=CODER,
            created_by='software-factory-repository-v3', workspace_kind='worktree',
            workspace_path=str(w.repository(self) if task_id is None else w.repository(self)/'.worktrees'/task_id),
            max_retries=0, project_id=None, branch_name=None if task_id is None else 'wt/'+task_id,
            model_override=None, provider_override=None, max_runtime_seconds=None, skills=None)

def context(job: Path, *, staged: bool = True) -> Context:
    """Reload authority from the sealed controller request, never card text."""
    job = Path(job)
    with c.directory_fd(job):
        pass
    spec = c.parse(f.fj_read(job / 'task_request.json', c.MAX_JSON))
    need(spec.get('job') == str(job), 'job_path_changed')
    binding = f.fj_validate_binding(spec)
    need(binding['candidate_manifest'] == c.CANDIDATE, 'candidate_changed')
    pins = spec.get('repository_inputs')
    c.keys(pins, INPUT_KEYS, 'repository_input_pins')
    for digest in pins.values():
        c.hex_value(digest)
    source = job / 'repository'
    with c.directory_fd(source):
        pass
    admission = c.admit(f.fj_read(source / 'approval.json', c.MAX_JSON),
        f.fj_read(source / 'envelope.json', c.MAX_JSON),
        f.fj_read(source / 'snapshot.zip', c.MAX_ARCHIVE),
        expected_approval_sha256=pins['approval_sha256'],
        expected_envelope_sha256=pins['envelope_sha256'],
        expected_snapshot_sha256=pins['snapshot_sha256'])
    need(admission.job_id == binding['job_id'], 'repository_job_mismatch')
    result = Context(spec, binding, admission, source / 'staged' / admission.job_id)
    if staged:
        control = result.staged / 'control'
        with c.directory_fd(control):
            pass
        need(f.fj_read(control / 'admission.json') == c.canonical(admission.receipt()), 'staged_admission_changed')
        need(f.fj_read(control / 'task.json') == admission.task, 'staged_task_changed')
        need(f.fj_read(control / 'STAGED.json') == c.canonical({'complete': True,
            'execution_permitted': False, 'admission_sha256': c.sha(c.canonical(admission.receipt()))}),
            'staged_receipt_changed')
    w.git_authority(result)
    routing.from_spec(result.spec, result.admission)
    return result

def connection_identity(conn: sqlite3.Connection, ctx: Context) -> None:
    need(type(conn) is sqlite3.Connection, 'native_connection_type')
    databases = conn.execute('PRAGMA database_list').fetchall()
    need(len(databases) == 1 and databases[0]['name'] == 'main' and
         databases[0]['file'] == ctx.binding['db'], 'native_connection_database')
    need(f.fj_db_identity(ctx.binding['db']) == ctx.binding['database_identity'], 'native_database_changed')
    f.fj_check_schema(conn)
    need(f.fj_guard(conn, ctx.binding)['state'] == 'open', 'job_already_finalized')

def native_paths(kb, ctx: Context) -> None:
    expected_home = str(Path(ctx.binding['job']) / 'task-home/.hermes')
    need(str(kb.kanban_home()) == expected_home and
         str(kb.kanban_db_path(board=ctx.binding['board'])) == ctx.binding['db'],
         'native_board_environment_changed')

def scope_live(ctx: Context) -> None:
    need(f.fj_live_scope(ctx.binding) == 'live', 'outside_bound_live_service')

def prepare_snapshot(kb, job: Path) -> Path:
    """Stage once before admission in an already initialized private job DB.

    No card or run creation. Partial snapshots remain consumed, never removed.
    This API does not initialize/migrate the DB or resume a historical task.
    """
    ctx = context(job, staged=False)
    with f.fj_open(ctx.binding) as conn:
        with kb.write_txn(conn):
            connection_identity(conn, ctx)
            snap, _ = f.fj_snapshot(conn, ctx.binding)
            need(snap['task'] is None and not snap['runs'], 'snapshot_after_native_admission')
            need(not (Path(ctx.binding['public']) / 'finalization_intent.json').exists() and
                 not (Path(ctx.binding['public']) / 'stop_requested.json').exists(), 'snapshot_after_stop')
            for name in ('service_native.json', 'managed_service_ready.json',
                         'managed_service_release.json', 'managed_service_exit.json', 'managed_service.log'):
                path = Path(ctx.binding['public']) / name
                need(not path.exists() and not path.is_symlink(), 'snapshot_after_service_admission')
            destination = c.stage(ctx.admission, Path(job) / 'repository' / 'staged')
            w.prepare_projection(context(job))
            return destination

def validate_workspace(ctx: Context) -> tuple[dict[str, bytes], dict[str, str]]:
    current, modes = w.read_sources(ctx)
    if current != dict(ctx.admission.files) or modes != dict(ctx.admission.modes):
        c.patch(ctx.admission, current, modes)
    return current, modes

def no_pending(ctx: Context) -> None:
    need(not e.present(ctx.staged/'control'/w.CONSUMED) or e.present(ctx.staged/'control'/w.BOUND), 'worktree_initialization_unresolved_no_retry')
    with c.directory_fd(ctx.staged / 'control') as fd:
        need('MUTATION_PENDING.json' not in os.listdir(fd), 'mutation_outcome_unresolved_no_retry')

def mutation(ctx: Context, identity: RunIdentity, path: str, before: bytes | None,
             after: bytes | None, mode: str) -> dict:
    """Write-ahead marker + atomic file replacement; no automatic repair/retry.

    A surviving marker blocks future tool calls, including reads/patch export.
    The finalizer can still close the task. SQLite is NOT a filesystem rollback.
    Native state is serialized by the caller's transaction; same-UID hostile
    actors and filesystem/driver replacement are outside this API's boundary.
    """
    operation = secrets.token_hex(16)
    receipt = dict(schema='software-factory-repository-mutation/v1', operation_id=operation,
        job_id=ctx.admission.job_id, task_id=identity.task_id, run_id=identity.run_id,
        path=path, before_sha256=None if before is None else c.sha(before),
        after_sha256=None if after is None else c.sha(after), execution_permitted=False)
    control = ctx.staged / 'control'
    with c.directory_fd(control) as ctl:
        c._write_new(ctl, 'MUTATION_PENDING.json', c.canonical(receipt))
        # No finally that removes the pending marker on failure.
        scope_live(ctx)
        with c.directory_fd(ctx.work) as root:
            parts = path.split('/')
            parent = c._mkdirs(root, parts[:-1])
            temp = 'replacement-' + operation
            try:
                if after is None:
                    os.unlink(parts[-1], dir_fd=parent)
                else:
                    c._write_new(ctl, temp, after, 0o700 if mode == '100755' else 0o600)
                    os.replace(temp, parts[-1], src_dir_fd=ctl, dst_dir_fd=parent)
                os.fsync(parent)
                os.fsync(ctl)
            finally:
                os.close(parent)
        current, modes = validate_workspace(ctx)
        need(current.get(path) == after and (after is None or modes[path] == mode), 'mutation_postcondition_failed')
        scope_live(ctx)
        c._write_new(ctl, 'mutations/' + operation + '.json', c.canonical(receipt))
        os.unlink('MUTATION_PENDING.json', dir_fd=ctl)
        os.fsync(ctl)
    return receipt

class NativeRepositoryAdapter:
    def __init__(self, kb, job: Path):
        self.kb, self.job = kb, Path(job)

    @contextmanager
    def locked(self, conn: sqlite3.Connection):
        need(type(conn) is sqlite3.Connection and not conn.in_transaction, 'caller_connection_or_transaction')
        with self.kb.write_txn(conn):
            ctx = context(self.job)
            connection_identity(conn, ctx)
            native_paths(self.kb, ctx)
            scope_live(ctx)
            yield ctx

    def create_pending_card(self, conn: sqlite3.Connection) -> dict:
        """New blocked card + genuine linked worktree, one native transaction.

        Native create_task allocates the real ID. Only while still uncommitted
        and blocked are its final path/body/branch bound to that ID. The durable
        external marker remains on rollback; no second card, reuse or retry.
        """
        with self.locked(conn) as ctx:
            snap, _ = f.fj_snapshot(conn, ctx.binding)
            need(snap['task'] is None and not snap['runs'], 'native_card_already_consumed')
            no_pending(ctx); w.projection(ctx)
            need(not e.present(ctx.staged/'control'/w.BOUND), 'worktree_card_already_bound')
            e.new_json(ctx.staged/'control'/w.CONSUMED, dict(schema='sf-worktree-card-consumed/v2',
                job_id=ctx.admission.job_id, admission_sha256=c.sha(c.canonical(ctx.admission.receipt())),
                routing_sha256=routing.from_spec(ctx.spec, ctx.admission).digest(), automatic_retry=False))
            # Pinned create_task treats None as 'inherit board project'. Empty
            # string explicitly opts out, then normalizes to SQL NULL. The
            # final card contract still requires None. Do not read/clear board
            # metadata or open the ambient per-profile project catalogue.
            creation = ctx.task_fields()
            creation['project_id'] = ''
            tid = self.kb.create_task(conn, **creation, initial_status='blocked', board=ctx.binding['board'])
            w.create_linked(ctx, tid); fields = ctx.task_fields(tid)
            # The pinned public setters own separate transactions. This one
            # initialization-only SQL is inside the same native outer commit,
            # before any claim, and still covered by the unchanged DB triggers.
            cursor = conn.execute("UPDATE tasks SET workspace_path=?, branch_name=?, body=? WHERE id=? AND status='blocked' AND current_run_id IS NULL",
                (fields['workspace_path'], fields['branch_name'], fields['body'], tid))
            need(cursor.rowcount == 1, 'worktree_initial_card_binding_failed')
            # Explicit pending admission, independent of the retry budget.
            # block_task opens its own transaction and cannot be nested here;
            # the pinned event writer is designed for an existing transaction.
            self.kb._append_event(conn, tid, 'blocked', {
                'schema': 'sf-worktree-card-admission/v3',
                'routing_sha256': routing.from_spec(ctx.spec, ctx.admission).digest(),
                'reason': 'awaiting_explicit_repository_admission',
                'admission_sha256': c.sha(c.canonical(ctx.admission.receipt()))})
            need(self.kb._has_sticky_block(conn, tid), 'worktree_initial_block_not_sticky')
            snap, _ = f.fj_snapshot(conn, ctx.binding)
            need(snap['task']['id'] == tid and snap['task']['status'] == 'blocked' and not snap['runs'], 'native_card_creation_conflict')
            self.check_task(ctx, snap['task']); current, modes = validate_workspace(ctx)
            need(current == dict(ctx.admission.files) and modes == dict(ctx.admission.modes), 'worktree_changed_before_card')
            scope_live(ctx)
            return dict(task_id=tid, board=ctx.binding['board'], status='blocked',
                execution_permitted=False, scheduled=False, model_executed=False,
                workspace_kind='worktree', projection_head=w.bound(ctx)['projection_head'])

    @staticmethod
    def check_task(ctx: Context, task: dict) -> None:
        need(task is not None, 'native_card_missing')
        record = w.bound(ctx)
        need(task['id'] == record['task_id'], 'worktree_foreign_card')
        for key, value in ctx.task_fields(record['task_id']).items():
            if key != 'assignee':
                need(task[key] == value, 'native_card_metadata_changed')
        need(task['assignee'] in (CODER, REVIEWER), 'native_assignee_changed')
        need(task['idempotency_key'] is None and task['goal_mode'] == 0 and
             task['goal_max_turns'] is None and task['reasoning_effort'] is None, 'native_task_execution_override')

    def active(self, conn: sqlite3.Connection, ctx: Context, identity: RunIdentity) -> str:
        need(type(identity) is RunIdentity, 'run_identity_type')
        identity.validate()
        snap, _ = f.fj_snapshot(conn, ctx.binding)
        task = snap['task']
        self.check_task(ctx, task)
        need(task['id'] == identity.task_id and task['status'] == 'running' and
             task['current_run_id'] == identity.run_id and task['claim_lock'] == identity.claim_lock,
             'stale_or_foreign_run')
        run = next((row for row in snap['runs'] if row['id'] == identity.run_id), None)
        need(run is not None and run['ended_at'] is None and run['task_id'] == identity.task_id and
             run['claim_lock'] == identity.claim_lock and run['profile'] == task['assignee'], 'native_run_not_active')
        need(type(task['claim_expires']) is int and task['claim_expires'] > int(time.time()), 'native_claim_expired')
        return run['profile']

    def tool(self, conn: sqlite3.Connection, identity: RunIdentity, arguments: bytes) -> dict:
        """Strict data JSON. Authority is NEVER derived from model arguments."""
        request = c.parse(arguments)
        action = request.get('action')
        schemas = {'read': ('action', 'path'), 'write': ('action', 'path', 'expected_sha256', 'content'),
                   'delete': ('action', 'path', 'expected_sha256'), 'patch': ('action',)}
        need(type(action) is str and action in schemas, 'unknown_data_action')
        c.keys(request, schemas[action], 'data_action_fields')
        with self.locked(conn) as ctx:
            role = self.active(conn, ctx, identity)
            no_pending(ctx)
            current, modes = validate_workspace(ctx)
            if action == 'patch':
                raw, receipt = c.patch(ctx.admission, current, modes)
                scope_live(ctx)
                return dict(patch=raw.decode(), receipt=receipt, quiescence_verified=False)
            path = c.path_name(request['path'])
            need(path in set(ctx.admission.read) | set(ctx.admission.add), 'path_not_admitted')
            before = current.get(path)
            if action == 'read':
                need(before is not None, 'file_not_present')
                scope_live(ctx)
                return dict(path=path, content=before.decode(), sha256=c.sha(before), mode=modes[path])
            need(role == CODER, 'reviewer_is_read_only')
            expected = request['expected_sha256']
            if expected is not None:
                c.hex_value(expected)
            need(expected == (None if before is None else c.sha(before)), 'content_compare_and_swap_conflict')
            proposed, new_modes = dict(current), dict(modes)
            if action == 'write':
                need(path in (ctx.admission.modify if path in ctx.admission.files else ctx.admission.add), 'write_not_admitted')
                need(type(request['content']) is str, 'content_not_text')
                try:
                    after = request['content'].encode('utf-8')
                except UnicodeError as exc:
                    raise NativeRepositoryRefused('content_encoding') from exc
                c.source_text(after)
                need(before != after, 'no_change')
                proposed[path] = after
                new_modes[path] = ctx.admission.modes.get(path, '100644')
            else:
                need(path in ctx.admission.delete and before is not None, 'delete_not_admitted')
                after = None
                del proposed[path], new_modes[path]
            # Returning all files to baseline is allowed; empty exported patches
            # remain disallowed by contract.patch(). Mutations still require CAS.
            if proposed != dict(ctx.admission.files) or new_modes != dict(ctx.admission.modes):
                c.patch(ctx.admission, proposed, new_modes)
            scope_live(ctx)
            receipt = mutation(ctx, identity, path, before, after, ctx.admission.modes.get(path, '100644'))
            return dict(ok=True, receipt=receipt, automatic_retry=False)

    def execute(self, *args, **kwargs):
        """No flag, receipt, tool JSON or native status enables repository code."""
        c.execution_request(*args, **kwargs)
