"""Repository execution launcher v3 (literal routing) for the immutable worktree projection.

Not a model tool or public CLI. No global configuration, installation, native
claim, inference, native approval, retry, or requeue. Accepted v1 admission/DATA modules remain byte-identical; this new namespace
binds v2 authority without replacing them. This explicit versioned
API adds the process path and mandatory finalization bridge for future jobs.

The caller supplies a pinned source manifest and runtime authority from its
reviewed controller, never from model JSON. The controller must have accepted
an exact source review before using this library. The supplied review helper
has no command capable of invoking this launcher on a server job.
"""
from __future__ import annotations
import contextlib
from dataclasses import dataclass
import datetime
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

import repository_task_contract as c
import repository_native_adapter_v3 as n
import repository_isolation_plan as iso
import repository_execution_admission_v3 as a
import finalization_core as f
import repository_execution_io as e
import repository_execution_transport as t
import repository_execution_service as s

VERSION = '3.0.0'
REQUEST_SCHEMA = 'sf-repository-test-service-request/v1'
CONSUMED_SCHEMA = 'sf-repository-execution-consumed/v3'
RESULT_SCHEMA = 'sf-repository-execution-collected/v3'
SOURCE_ORDER = ('repository_task_contract', 'finalization_core', 'repository_native_adapter', 'repository_isolation_plan', 'repository_execution_admission', 'linux_memfd_seals', 'repository_execution_io', 'repository_execution_transport', 'repository_execution_service', 'repository_worktree_v2', 'repository_routing_v3', 'repository_native_adapter_v3', 'repository_execution_admission_v3', 'repository_execution_launcher_v3')
KNOWN_PYTHON = Path('/home/marcin/projects/software-factory/.factory-local/runs/sf-runner/'
                    'dispatch-binding-391ac8a9/native/.venv/bin/python')


def verify_interpreter(spec_python):
    e.need(Path(spec_python).absolute() == KNOWN_PYTHON and
           Path(sys.executable).absolute() == KNOWN_PYTHON and
           tuple(sys.version_info[:3]) == (3, 11, 16), 'use_existing_candidate_python')


def utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


@dataclass(frozen=True)
class RuntimeAuthority:
    root: Path
    approval: bytes
    approval_sha256: str
    fixed_boot_archive: bytes


def sources(root: Path, expected_manifest: str) -> dict[str, bytes]:
    c.hex_value(expected_manifest)
    manifest = e.read(root / 'SOURCES.json')
    e.need(e.sha(manifest) == expected_manifest, 'launcher_source_manifest_pin')
    d = c.parse(manifest)
    e.need(set(d) == {'schema', 'files'} and d['schema'] == 'sf-repository-launcher-sources/v3' and
           set(d['files']) == {name + '.py' for name in SOURCE_ORDER}, 'launcher_source_inventory')
    iso.inventory(root, set(d['files']) | {'SOURCES.json'})
    result = {}
    for name, expected in d['files'].items():
        raw = e.read(root / name, 2 * c.MAX_JSON)
        e.need(e.sha(raw) == expected, 'launcher_source_drift:' + name)
        result[name] = raw
    result['SOURCES.json'] = manifest
    return result


def token_for(binding: dict, permit: dict) -> str:
    return e.sha(c.canonical({'parent': binding['token'], 'task_id': permit['task_id'],
                             'run_id': permit['run_id'], 'claim_hash': permit['claim_lock_sha256']}))[:32]


def paths(ctx, identity) -> tuple[Path, Path]:
    identity.validate()
    return (ctx.staged / 'launches' / ('run-' + str(identity.run_id)),
            Path(ctx.binding['public']) / 'repository-executions' / ('run-' + str(identity.run_id)))


def permit_path(ctx, run_id: int) -> Path:
    return ctx.staged / 'control' / a._permit_name(run_id)


def consumed_path(ctx, run_id: int) -> Path:
    return ctx.staged / 'control' / ('EXECUTION_CONSUMED_' + str(run_id) + '.json')


def result_path(ctx, run_id: int) -> Path:
    return ctx.staged / 'control' / ('EXECUTION_COLLECTED_' + str(run_id) + '.json')


def checked_permit(ctx, identity, expected: str) -> tuple[bytes, dict]:
    c.hex_value(expected)
    raw = e.read(permit_path(ctx, identity.run_id))
    e.need(e.sha(raw) == expected, 'persisted_permit_pin')
    p = a.parse_permit(raw)
    e.need(p['routing_sha256'] == n.routing.from_spec(ctx.spec, ctx.admission).digest(), 'persisted_permit_routing')
    e.need(p['task_id'] == identity.task_id and p['run_id'] == identity.run_id and
           p['claim_lock_sha256'] == a._claim_hash(identity) and
           p['frozen_root'] == str(ctx.staged / 'execution' / ('run-' + str(identity.run_id))),
           'persisted_permit_identity')
    return raw, p


def active_locked(adapter, conn, ctx, identity, p: dict) -> None:
    role = adapter.active(conn, ctx, identity)
    e.need(p['routing_sha256'] == n.routing.from_spec(ctx.spec, ctx.admission).digest(), 'launch_routing_drift')
    n.no_pending(ctx)
    current, modes = n.validate_workspace(ctx)
    e.need(role == p['profile'] and ctx.admission.job_id == p['job_id'] and
           ctx.admission.repository_id == p['repository_id'] and
           a.workspace_descriptor(ctx, current, modes) == p['workspace'],
           'launch_native_or_worktree_drift')
    iso.verify_frozen(Path(p['frozen_root']), p['frozen_seal_sha256'], ctx.admission)


def rebuild(ctx, p: dict, authority: RuntimeAuthority) -> tuple[dict, object]:
    e.need(type(authority) is RuntimeAuthority, 'runtime_authority_type')
    runtime = t.runtime_binding(Path(authority.root), authority.approval,
                                authority.approval_sha256, authority.fixed_boot_archive)
    plan = iso.build_plan(ctx.admission, Path(p['frozen_root']), p['frozen_seal_sha256'],
                          runtime.root, authority.approval, authority.approval_sha256, t.TOOLS['/usr/bin/bwrap'])
    # plan_binding expects only bwrap_sha256 from the trusted proof here; the
    # complete archive and runtime mapping were checked by runtime_binding().
    e.need(a.plan_binding(plan, {'bwrap_sha256': t.TOOLS['/usr/bin/bwrap']}) == p['plan'], 'persisted_plan_drift')
    return plan, runtime


def driver_bytes(source_manifest_sha: str) -> bytes:
    """Literal pinned module graph. No import search through a mutable worktree."""
    return ('''"""Trusted sealed repository-test service entry; no model input."""
import hashlib,json,os,pathlib,sys,types
job=pathlib.Path(__file__).absolute().parent
if len(sys.argv)!=4 or sys.argv[1]!=str(job/'task_request.json') or sys.argv[3]!='controller':
    raise SystemExit('bad sealed repository service arguments')
root=job/'src'
raw=(root/'SOURCES.json').read_bytes()
if hashlib.sha256(raw).hexdigest()!=%r:
    raise SystemExit('sealed launcher source manifest changed')
manifest=json.loads(raw)
expected=%r
if set(manifest)!= {'schema','files'} or set(manifest['files'])!=set(x+'.py' for x in expected):
    raise SystemExit('sealed launcher source inventory changed')
for name in expected:
    if name in sys.modules:
        raise SystemExit('sealed launcher namespace occupied')
    data=(root/(name+'.py')).read_bytes()
    if hashlib.sha256(data).hexdigest()!=manifest['files'][name+'.py']:
        raise SystemExit('sealed launcher source changed')
    m=types.ModuleType(name);m.__file__=str(root/(name+'.py'));sys.modules[name]=m
    exec(compile(data,m.__file__,'exec',dont_inherit=True),m.__dict__)
data=(job/'base_runner.py').read_bytes()
if hashlib.sha256(data).hexdigest()!=%r:
    raise SystemExit('sealed base runner changed')
r=types.ModuleType('_sf_repo_existing_runner');r.__file__=str(job/'base_runner.py')
sys.modules[r.__name__]=r;exec(compile(data,r.__file__,'exec',dont_inherit=True),r.__dict__)
raise SystemExit(sys.modules['repository_execution_launcher_v3'].service_main(r,pathlib.Path(sys.argv[1]),sys.argv[2]))
''' % (source_manifest_sha, SOURCE_ORDER, s.BASE_SHA)).encode()


class RepositoryExecutionLauncher:
    def __init__(self, runner, adapter: n.NativeRepositoryAdapter, source_root: Path, source_manifest_sha256: str):
        e.need(type(adapter) is n.NativeRepositoryAdapter, 'launcher_native_adapter_type')
        self.runner, self.adapter = runner, adapter
        self.source_root = Path(source_root)
        self.source_manifest_sha = c.hex_value(source_manifest_sha256)
        sources(self.source_root, self.source_manifest_sha)
        self.service_type = s.service_class(runner)
        self.finalizer = s.finalization_module(runner)

    def parent_service(self, ctx):
        binding, svc = self.finalizer.fj_bind_service(ctx.spec)
        e.need(binding == ctx.binding, 'launcher_parent_binding')
        return svc

    def prepare(self, conn, identity: n.RunIdentity, permit_sha256: str, authority: RuntimeAuthority) -> dict:
        """Stage one sealed service; no consumption, manager command or spawn."""
        e.need(type(identity) is n.RunIdentity, 'launcher_run_identity')
        with self.adapter.locked(conn) as ctx:
            raw, p = checked_permit(ctx, identity, permit_sha256)
            active_locked(self.adapter, conn, ctx, identity, p)
            plan, runtime = rebuild(ctx, p, authority)
            source_files = sources(self.source_root, self.source_manifest_sha)
            for path in t.TOOLS:
                t.host_tool(path)
            verify_interpreter(ctx.spec['python'])
            parent_service = self.parent_service(ctx)
            e.need(parent_service.token == ctx.binding['token'], 'launcher_parent_token')
            # A new native run cannot launch over an uncollected earlier child.
            control = ctx.staged / 'control'
            for name in os.listdir(control):
                if name.startswith('EXECUTION_CONSUMED_'):
                    e.need(re.fullmatch(r'EXECUTION_CONSUMED_[1-9][0-9]*\.json', name), 'consumption_inventory')
                    rid = int(name.removeprefix('EXECUTION_CONSUMED_').removesuffix('.json'))
                    e.need(e.present(result_path(ctx, rid)), 'earlier_execution_uncollected')
            a._private_child(ctx.staged, 'launches')
            a._private_child(Path(ctx.binding['public']), 'repository-executions')
            job, public = paths(ctx, identity)
            e.mkdir(job)
            e.mkdir(public)
            e.mkdir(job / 'src')
            e.mkdir(job / 'task-repo')
            for name, data in source_files.items():
                e.new_file(job / 'src' / name, data)
            base = Path(self.runner.__file__).read_bytes()
            e.need(e.sha(base) == s.BASE_SHA, 'base_runner_changed_before_stage')
            e.new_file(job / 'base_runner.py', base)
            e.new_file(job / 'runtime_approval.json', authority.approval)
            e.new_file(job / 'fixed_boot13.zip', authority.fixed_boot_archive)
            driver = driver_bytes(self.source_manifest_sha)
            e.new_file(job / 'source_job_driver.py', driver)
            payload = self.runner.MANAGED_SERVICE_PAYLOAD.encode()
            e.new_file(job / 'managed_service_payload.py', payload)
            owner = parent_service.owner
            env = {'HOME': str(owner), 'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1'}
            e.new_json(job / 'task_env.json', env)
            request = {'schema': REQUEST_SCHEMA, 'task_id': identity.task_id, 'run_id': identity.run_id,
                'permit_sha256': permit_sha256, 'parent_binding': ctx.binding,
                'parent_request_sha256': ctx.binding['request_sha256'], 'job': str(job), 'public': str(public),
                'runtime_root': str(runtime.root), 'runtime_approval_sha256': authority.approval_sha256,
                'source_manifest_sha256': self.source_manifest_sha, 'plan': plan,
                'timeout_seconds': p['workspace']['timeout_seconds'], 'automatic_retry': False,
                'native_approval': False, 'production_launch_ready': False}
            e.new_json(job / 'task_request.json', request)
            descriptor = {'schema': 1, 'backend': 'systemd-user-v1', 'job_id': 'repo-tests-' + str(identity.run_id),
                'token': token_for(ctx.binding, p), 'uid': os.geteuid(), 'boot': ctx.binding['boot'],
                'public': str(public), 'job': str(job), 'python': str(KNOWN_PYTHON),
                'driver': str(job / 'source_job_driver.py'), 'request': str(job / 'task_request.json'),
                'environment': str(job / 'task_env.json'), 'cwd': str(job / 'task-repo'),
                'runtime_seconds': max(60, request['timeout_seconds'] + 10), 'candidate_manifest': c.CANDIDATE,
                'request_sha256': e.sha(c.canonical(request)), 'environment_sha256': e.sha(c.canonical(env)),
                'driver_sha256': e.sha(driver), 'payload_sha256': e.sha(payload)}
            e.new_json(job / 'managed_job.json', descriptor)
            e.new_json(public / 'managed_job.json', descriptor)
            active_locked(self.adapter, conn, ctx, identity, p)
            n.scope_live(ctx)
            prepared = {'schema': 'sf-repository-execution-prepared/v1', 'request_sha256': descriptor['request_sha256'],
                'descriptor_sha256': e.sha(c.canonical(descriptor)), 'permit_sha256': permit_sha256,
                'source_manifest_sha256': self.source_manifest_sha, 'task_id': identity.task_id,
                'run_id': identity.run_id, 'status': 'PREPARED', 'launch_consumed': False,
                'repository_code_executed': False, 'automatic_retry': False}
            e.new_json(job / 'LAUNCH_PREPARED.json', prepared)
            e.new_json(public / 'LAUNCH_PREPARED.json', prepared)
            return prepared

    def _loaded(self, ctx, identity):
        job, public = paths(ctx, identity)
        prepared = e.read_json(job / 'LAUNCH_PREPARED.json')
        e.need(prepared == e.read_json(public / 'LAUNCH_PREPARED.json') and
               prepared['source_manifest_sha256'] == self.source_manifest_sha and
               prepared['task_id'] == identity.task_id and prepared['run_id'] == identity.run_id,
               'prepared_launch_binding')
        request_raw = e.read(job / 'task_request.json')
        e.need(e.sha(request_raw) == prepared['request_sha256'], 'launch_request_drift')
        request = c.parse(request_raw)
        e.need(request['parent_binding'] == ctx.binding and request['job'] == str(job) and request['public'] == str(public),
               'launch_request_parent_or_path')
        desc_raw = e.read(job / 'managed_job.json')
        e.need(desc_raw == e.read(public / 'managed_job.json') and e.sha(desc_raw) == prepared['descriptor_sha256'],
               'launch_descriptor_drift')
        raw, permit = checked_permit(ctx, identity, prepared['permit_sha256'])
        e.need(c.parse(desc_raw)['token'] == token_for(ctx.binding, permit), 'launch_descriptor_token')
        e.need(request['permit_sha256'] == prepared['permit_sha256'] and
               request['timeout_seconds'] == permit['workspace']['timeout_seconds'], 'launch_request_permit')
        e.need(e.read(job / 'source_job_driver.py') == driver_bytes(self.source_manifest_sha), 'service_bootstrap_drift')
        sources(job / 'src', self.source_manifest_sha)
        authority = RuntimeAuthority(Path(request['runtime_root']), e.read(job / 'runtime_approval.json'),
            request['runtime_approval_sha256'], e.read(job / 'fixed_boot13.zip', 2 * c.MAX_JSON))
        return job, public, prepared, request, permit, authority

    def _guard(self, conn, identity):
        with self.adapter.locked(conn) as ctx:
            job, public, prepared, request, p, authority = self._loaded(ctx, identity)
            active_locked(self.adapter, conn, ctx, identity, p)
            plan, _ = rebuild(ctx, p, authority)
            e.need(plan == request['plan'], 'current_plan_changed')
            sources(self.source_root, self.source_manifest_sha)
            for path in t.TOOLS:
                t.host_tool(path)
            n.scope_live(ctx)
            return ctx, (job, public, prepared, request, p, authority)

    def _fence_failed(self, ctx, public, code='failed'):
        # First-reason-wins. No assertion that processes are already dead.
        intent = self.finalizer.fj_ensure_intent(ctx.spec, code, utc())
        e.publish_json(public / 'FINALIZATION_REQUIRED.json', {
            'schema': 1, 'job_id': ctx.binding['job_id'], 'reason': intent['reason'],
            'automatic_retry': False, 'native_finalization_pending': True})

    def start(self, conn, identity: n.RunIdentity) -> dict:
        """Consume before the first external service launch; never retry a slot."""
        ctx, loaded = self._guard(conn, identity)
        job, public, prepared, request, p, authority = loaded
        parent = self.parent_service(ctx)
        with parent.fence():
            # The fixed parent fence + short native transaction serialize stop,
            # claim changes and consumption. No DB write lock spans child wait.
            ctx, loaded = self._guard(conn, identity)
            with self.adapter.locked(conn) as current:
                active_locked(self.adapter, conn, current, identity, p)
                n.scope_live(current)
                consumption = {'schema': CONSUMED_SCHEMA, 'task_id': identity.task_id, 'run_id': identity.run_id,
                    'claim_lock_sha256': a._claim_hash(identity), 'permit_sha256': prepared['permit_sha256'],
                    'request_sha256': prepared['request_sha256'], 'descriptor_sha256': prepared['descriptor_sha256'],
                    'source_manifest_sha256': self.source_manifest_sha, 'parent_binding_sha256': e.sha(f.fj_bytes(ctx.binding)),
                    'created_utc': utc(), 'launch_consumed': True, 'automatic_retry': False,
                    'task_requeued': False, 'native_approval': False}
                e.new_json(consumed_path(ctx, identity.run_id), consumption)
        # Duplicate start returns at O_EXCL above; it must NOT cancel the first
        # running attempt or call the failure path below.
        service = None
        try:
            service = self.service_type(parent.owner, job, public, job / 'managed_job.json',
                                        launch_guard=lambda: self._guard(conn, identity))
            record = service.launch('native')
            return {'status': 'STARTED', 'launch_consumed': True, 'unit': record['unit'],
                    'invocation_id': record['invocation_id'], 'automatic_retry': False}
        except BaseException:
            try:
                self._fence_failed(ctx, public)
            finally:
                if service is not None:
                    service.stop_all()
            raise

    def collect(self, conn, identity: n.RunIdentity) -> dict:
        """Collect after actual child-service quiescence; no spawn path here."""
        ctx = n.context(self.adapter.job)
        job, public, prepared, request, p, authority = self._loaded(ctx, identity)
        consumed = e.read_json(consumed_path(ctx, identity.run_id))
        e.need(consumed['schema'] == CONSUMED_SCHEMA and consumed['launch_consumed'] is True and
               consumed['permit_sha256'] == prepared['permit_sha256'] and
               consumed['descriptor_sha256'] == prepared['descriptor_sha256'], 'consumption_binding')
        parent = self.parent_service(ctx)
        service = self.service_type(parent.owner, job, public, job / 'managed_job.json')
        final = s.verified_empty(service)
        if e.present(result_path(ctx, identity.run_id)):
            receipt = e.read_json(result_path(ctx, identity.run_id))
            e.need(receipt['schema'] == RESULT_SCHEMA and receipt['consumption_sha256'] == e.sha(c.canonical(consumed)) and
                   receipt['permit_sha256'] == prepared['permit_sha256'] and
                   receipt['service_invocation_id'] == service.record('native')['invocation_id'], 'collected_receipt_drift')
            # Finish required fencing even if an earlier process died after
            # the test-failure receipt commit, before exporting/fencing.
            if receipt['status'] != 'PASS':
                self._fence_failed(ctx, public)
            # Complete an interrupted export from immutable control evidence.
            e.publish_json(public / 'EXECUTION_RESULT.json', receipt)
            return receipt
        committed = False
        try:
            self._guard(conn, identity)
            process = e.read_json(public / 'PROCESS_RESULT.json')
            stdout = e.read(public / 'test.stdout.log', e.MAX_OUTPUT)
            stderr = e.read(public / 'test.stderr.log', e.MAX_OUTPUT)
            e.need(process['stdout_sha256'] == e.sha(stdout) and process['stderr_sha256'] == e.sha(stderr) and
                   process['stdout_bytes'] == len(stdout) and process['stderr_bytes'] == len(stderr) and
                   process['request_sha256'] == prepared['request_sha256'] and
                   process['permit_sha256'] == prepared['permit_sha256'], 'process_output_binding')
            obs = e.observation(stdout, stderr, process, p['plan']['input_sha256'], p['workspace']['expected_count'])
            exit_receipt = s.verify_exit(service, final, process['exit'])
            self._guard(conn, identity)
            receipt = {'schema': RESULT_SCHEMA, 'status': 'PASS' if obs['ok'] else 'TEST_FAILED',
                'task_id': identity.task_id, 'run_id': identity.run_id, 'claim_lock_sha256': a._claim_hash(identity),
                'permit_sha256': prepared['permit_sha256'], 'consumption_sha256': e.sha(c.canonical(consumed)),
                'request_sha256': prepared['request_sha256'], 'source_manifest_sha256': self.source_manifest_sha,
                'observation': obs, 'process_result_sha256': e.sha(c.canonical(process)),
                'service_invocation_id': service.record('native')['invocation_id'],
                'service_quiescence': final, 'service_exit_receipt': exit_receipt,
                'native_approval': False, 'test_output_is_independent_attestation': False,
                'production_launch_ready': False, 'automatic_retry': False, 'launch_consumed': True}
            # Serialize late claim/stop vs accepting the observation.
            with parent.fence():
                with self.adapter.locked(conn) as fresh:
                    active_locked(self.adapter, conn, fresh, identity, p)
                    e.new_json(result_path(ctx, identity.run_id), receipt)
                    committed = True
            if not obs['ok']:
                self._fence_failed(ctx, public)
            e.publish_json(public / 'EXECUTION_RESULT.json', receipt)
            return receipt
        except BaseException:
            # Export failure after an accepted PASS is recoverable by collect;
            # it is not evidence of failed tests and must not cancel the job.
            if not committed or not obs['ok']:
                self._fence_failed(ctx, public)
            raise

    def execute(self, conn, identity: n.RunIdentity) -> dict:
        """One start plus bounded wait/collection. Never writes native done."""
        self.start(conn, identity)
        ctx = n.context(self.adapter.job)
        job, public, prepared, request, p, authority = self._loaded(ctx, identity)
        parent = self.parent_service(ctx)
        service = self.service_type(parent.owner, job, public, job / 'managed_job.json')
        deadline = time.monotonic() + request['timeout_seconds'] + 15
        try:
            while time.monotonic() < deadline:
                service.cancel_check()
                with self.adapter.locked(conn) as fresh:
                    self.adapter.active(conn, fresh, identity)
                    n.no_pending(fresh)
                empty, _ = service.empty('native')
                if empty:
                    return self.collect(conn, identity)
                time.sleep(.1)
            raise e.ExecutionRefused('test_service_deadline')
        except BaseException:
            saved = result_path(ctx, identity.run_id)
            if e.present(saved) and e.read_json(saved).get('status') == 'PASS':
                raise  # committed observation; collect can finish export
            try:
                self._fence_failed(ctx, public)
            finally:
                service.stop_all()
            raise

    def cancel(self) -> dict:
        """Fence the parent and stop only its known test units. No parent kill here."""
        ctx = n.context(self.adapter.job)
        self.finalizer.fj_ensure_intent(ctx.spec, 'cancelled', utc())
        results = []
        for svc in self._children(ctx):
            if svc.record_path('native').exists():
                svc.request_stop()
                results.append(s.verified_empty(svc))
        return {'cancelled': True, 'children': results, 'native_finalization_pending': True, 'automatic_retry': False}

    def _children(self, ctx):
        parent = self.parent_service(ctx)
        root = ctx.staged / 'launches'
        if not e.present(root):
            return []
        with c.directory_fd(root) as fd:
            names = sorted(os.listdir(fd))
        result = []
        for name in names:
            e.need(re.fullmatch(r'run-[1-9][0-9]*', name), 'test_service_inventory')
            job = root / name
            prepared_path = job / 'LAUNCH_PREPARED.json'
            if not e.present(prepared_path):
                # A partial stage is not a spawn proof and is never reused.
                rid = int(name[4:])
                e.need(not e.present(consumed_path(ctx, rid)), 'partial_consumed_stage_unknown')
                continue
            prepared = e.read_json(prepared_path)
            request_raw = e.read(job / 'task_request.json')
            e.need(e.sha(request_raw) == prepared['request_sha256'], 'child_request_changed')
            request = c.parse(request_raw)
            e.need(request['parent_binding'] == ctx.binding, 'foreign_child_service')
            svc = self.service_type(parent.owner, job, Path(request['public']), job / 'managed_job.json')
            e.need(svc.descriptor_sha == prepared['descriptor_sha256'], 'child_descriptor_changed')
            result.append(svc)
        return result

    def stop_and_finalize(self, reason='cancelled') -> dict:
        """Explicit OUTER-controller stop using existing JobService and finalizer.

        No background recovery loop. Must not run inside the native parent:
        stopping one's own cgroup would abandon the control operation. Every
        failed child stop is retained and prevents claiming successful closure.
        """
        e.need(reason in ('cancelled', 'failed', 'monitor_lost', 'postflight_failed'), 'stop_reason')
        ctx = n.context(self.adapter.job)
        parent = self.parent_service(ctx)
        backend = s.backend_module(self.runner)
        if parent.record_path('native').exists():
            record = parent.record('native')
            e.need(record.get('boot') == parent.boot and record.get('cgroup') and
                   backend.proc_cgroup(os.getpid()) != record['cgroup'], 'stop_must_run_outside_native_parent')
        self.finalizer.fj_ensure_intent(ctx.spec, reason, utc())
        failures = []
        children = self._children(ctx)
        for child in children:
            if child.record_path('native').exists():
                try:
                    child.stop_all()
                    s.verified_empty(child)
                except Exception as exc:
                    failures.append({'unit': child.unit('native'), 'error_type': type(exc).__name__})
        # Fence before all stops. The parent cannot release a new child now.
        parent.stop_all()
        if failures:
            raise e.ExecutionRefused('child_stop_unverified_native_finalization_pending')
        return self.finalize_after_stop()

    def finalize_after_stop(self) -> dict:
        """Outside the native service, after explicit stop/crash: existing finalizer.

        A new parent intent fences every possible child spawn. Check all staged
        child units before delegating to fj_close_verified (fresh parent proof,
        CAS SQLite closure and idempotent DB receipt export). Unknown is pending.
        """
        ctx = n.context(self.adapter.job)
        self.finalizer.fj_ensure_intent(ctx.spec, 'failed', utc())
        for svc in self._children(ctx):
            if svc.record_path('native').exists():
                s.verified_empty(svc)
            else:
                observed = svc.show('native')
                e.need(observed['LoadState'] == 'not-found' and not any(e.present(svc.public / name) for name in
                    ('managed_service_ready.json', 'managed_service_release.json', 'managed_service_exit.json', 'managed_service.log')),
                    'unrecorded_test_service_unknown')
        return self.finalizer.fj_close_verified(self.adapter.kb, ctx.spec)


def read_only_native_guard(request: dict, permit: dict, conn) -> object:
    """Trusted child reads the real DB claim, then compares the consumed hash.

    No model-provided identity and no untrusted SQL. No scope_live() substitution
    in production: the child belongs to a distinct bound test unit, so it uses a
    read-only snapshot plus the separately observed native parent service.
    """
    ctx = n.context(Path(request['parent_binding']['job']))
    e.need(ctx.binding == request['parent_binding'], 'child_parent_binding_changed')
    n.connection_identity(conn, ctx)
    snap, _ = f.fj_snapshot(conn, ctx.binding)
    task = snap['task']
    e.need(task is not None and task['id'] == permit['task_id'] and
           type(task.get('claim_lock')) is str and e.sha(task['claim_lock'].encode()) == permit['claim_lock_sha256'],
           'child_native_claim_drift')
    identity = n.RunIdentity(permit['task_id'], permit['run_id'], task['claim_lock'])
    adapter = n.NativeRepositoryAdapter(None, Path(ctx.binding['job']))
    active_locked(adapter, conn, ctx, identity, permit)
    for name in ('stop_requested.json', 'finalization_intent.json'):
        e.need(not e.present(Path(ctx.binding['public']) / name), 'child_parent_fenced')
    return ctx


def current_child_service(service, backend, request: dict) -> dict:
    service.verify_inputs()
    record, observed = service.bound_show('native')
    release = e.read_json(service.public / 'managed_service_release.json')
    me = backend.process_identity(os.getpid())
    e.need(me is not None and release == dict(service.release_common(record), invocation_id=record['invocation_id']) and
           release['identity'] == me and observed['ActiveState'] == 'active' and
           backend.proc_cgroup(os.getpid()) == record['cgroup'] and service.boot == request['parent_binding']['boot'],
           'child_current_service_binding')
    group = backend.safe_cgroup(record['cgroup'], service.unit('native'), os.geteuid())
    root = Path('/sys/fs/cgroup') / group.lstrip('/')
    limits = {k: (root / k).read_text().strip() for k in ('memory.max', 'memory.swap.max', 'pids.max', 'cpu.max')}
    e.need(limits['memory.max'] == '536870912' and limits['memory.swap.max'] == '0' and limits['pids.max'] == '16',
           'child_kernel_limits')
    cpu = limits['cpu.max'].split()
    e.need(len(cpu) == 2 and all(x.isdecimal() for x in cpu) and 0 < int(cpu[0]) == int(cpu[1]) <= 1000000,
           'child_kernel_cpu_limit')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    e.need(status.get('NoNewPrivs', '').strip() == '1', 'child_no_new_privileges')
    return {'unit': service.unit('native'), 'invocation_id': record['invocation_id'],
            'cgroup': group, 'main_identity': me, 'limits': limits}


def service_main(runner, request_path: Path, expected_sha: str) -> int:
    """Trusted broker entry; one Popen path; never called by the review action."""
    raw = e.read(request_path)
    e.need(e.sha(raw) == expected_sha, 'service_request_pin')
    req = c.parse(raw)
    job = Path(req['job']); public = Path(req['public'])
    e.need(req['schema'] == REQUEST_SCHEMA and request_path == job / 'task_request.json', 'service_request_path')
    verify_interpreter(str(KNOWN_PYTHON))
    sources(job / 'src', req['source_manifest_sha256'])
    finalizer = s.finalization_module(runner)
    parent_spec = c.parse(e.read(Path(req['parent_binding']['job']) / 'task_request.json'))
    binding, parent = finalizer.fj_bind_service(parent_spec)
    e.need(binding == req['parent_binding'], 'service_parent_binding')
    service = s.service_class(runner)(parent.owner, job, public, job / 'managed_job.json')
    backend = s.backend_module(runner)
    ctx = n.context(Path(binding['job']))
    permit_raw = e.read(permit_path(ctx, req['run_id']))
    e.need(e.sha(permit_raw) == req['permit_sha256'], 'service_persisted_permit')
    p = a.parse_permit(permit_raw)
    e.need(p['task_id'] == req['task_id'] and p['run_id'] == req['run_id'] and
           paths(ctx, n.RunIdentity(p['task_id'], p['run_id'], 'path-only')) == (job, public), 'service_run_paths')
    consumed = e.read_json(consumed_path(ctx, p['run_id']))
    e.need(consumed['schema'] == CONSUMED_SCHEMA and consumed['permit_sha256'] == req['permit_sha256'] and
           consumed['request_sha256'] == expected_sha and consumed['descriptor_sha256'] == service.descriptor_sha and
           consumed['launch_consumed'] is True and consumed['claim_lock_sha256'] == p['claim_lock_sha256'],
           'service_consumption_binding')
    authority = RuntimeAuthority(Path(req['runtime_root']), e.read(job / 'runtime_approval.json'),
        req['runtime_approval_sha256'], e.read(job / 'fixed_boot13.zip', 2 * c.MAX_JSON))
    plan, runtime = rebuild(ctx, p, authority)
    e.need(plan == req['plan'], 'service_plan_drift')
    frozen = iso.verify_frozen(Path(p['frozen_root']), p['frozen_seal_sha256'], ctx.admission)
    current_child_service(service, backend, req)

    @contextlib.contextmanager
    def before_spawn():
        with parent.fence():
            parent_record, parent_status = parent.bound_show('native')
            e.need(parent_status['ActiveState'] == 'active' and parent_record.get('released') is True and
                   backend.same_process(parent_record['main_identity']), 'native_parent_not_live')
            current_child_service(service, backend, req)
            with f.fj_open(binding) as conn:
                conn.execute('BEGIN')
                try:
                    read_only_native_guard(req, p, conn)
                    e.need(e.read(permit_path(ctx, p['run_id'])) == permit_raw, 'permit_changed_before_exec')
                    t.host_tool('/usr/bin/bwrap')
                    e.new_json(public / 'BROKER_START_CONSUMED.json', {
                        'schema': 1, 'permit_sha256': req['permit_sha256'], 'request_sha256': expected_sha,
                        'created_utc': utc(), 'automatic_retry': False})
                    yield
                finally:
                    conn.rollback()

    def after_spawn(child):
        identity = backend.process_identity(child.pid)
        group = backend.proc_cgroup(child.pid) if identity else None
        already_reaped = child.poll() is not None
        e.need((identity is not None and group == service.record('native')['cgroup']) or already_reaped,
               'bwrap_process_not_in_bound_service')
        e.new_json(public / 'BWRAP_PROCESS.json', {
            'schema': 1, 'identity': identity, 'already_reaped': already_reaped,
            'permit_sha256': req['permit_sha256'], 'request_sha256': expected_sha})

    try:
        with t.Inputs() as transport:
            transport.capture(runtime, Path(p['frozen_root']), frozen)
            argv = transport.argv(plan, runtime, Path(p['frozen_root']))
            e.new_json(public / 'SEALED_EXECUTION_INPUTS.json', {
                'schema': 1, 'files': transport.rows, 'argv': argv, 'plan_sha256': e.sha(c.canonical(plan)),
                'permit_sha256': req['permit_sha256'], 'request_sha256': expected_sha})
            result = e.run_bounded(argv, transport.pass_fds(), timeout=req['timeout_seconds'],
                                   before_spawn=before_spawn, after_spawn=after_spawn)
            transport.verify()
        stdout = result.pop('stdout'); stderr = result.pop('stderr')
        e.new_file(public / 'test.stdout.log', stdout)
        e.new_file(public / 'test.stderr.log', stderr)
        result.update(stdout_bytes=len(stdout), stderr_bytes=len(stderr), stdout_sha256=e.sha(stdout),
                      stderr_sha256=e.sha(stderr), permit_sha256=req['permit_sha256'], request_sha256=expected_sha)
        e.new_json(public / 'PROCESS_RESULT.json', result)
        obs = e.observation(stdout, stderr, result, p['plan']['input_sha256'], p['workspace']['expected_count'])
        with f.fj_open(binding) as conn:
            conn.execute('BEGIN')
            try:
                read_only_native_guard(req, p, conn)
            finally:
                conn.rollback()
        return 0 if obs['ok'] else 1
    except BaseException as exc:
        # Bound type only; tested output remains in bounded separate files.
        e.publish_json(public / 'BROKER_FAILURE.json', {'schema': 1, 'error_type': type(exc).__name__,
            'automatic_retry': False, 'permit_sha256': req['permit_sha256'], 'request_sha256': expected_sha})
        return 86
