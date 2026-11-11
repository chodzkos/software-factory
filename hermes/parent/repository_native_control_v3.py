"""Run-bound controller integration; no new dispatcher, profile or retry loop.

Authorities come from trusted native transport and external source acceptance,
not tool JSON. Old coder/reviewer proofs and the ten accepted launcher modules
remain unchanged. This library cannot install itself or admit a live workload.
"""
from __future__ import annotations
from dataclasses import dataclass
import contextlib
import os
from pathlib import Path
import types
import repository_task_contract as c
import repository_native_adapter_v3 as n
import repository_execution_admission_v3 as a
import repository_execution_launcher_v3 as l
import repository_execution_io as e
import repository_execution_transport as t
import finalization_core as f

VERSION = '3.0.0'
RETAINED_V1_LIBRARY_MANIFEST = '7696b41e1e90c292efcd17cb37593425a03b1742fc3e9ed698818091c1195c54'
LIBRARY_REVIEW_SHA = 'f199265b93b7d3ebac5fb2bf68f97ddac8cb0c353e5bccebec0df5e8c6de523b'
ADMISSION_HELPER_SHA = '37215bff2aba2aefe3309508a607a8bc72ef421b1f7bc91fa597a2919b682e71'
MODULES = ('repository_native_control_v3','repository_native_control_tools_v3','repository_native_control_runner_v3')
ACCEPT_SCHEMA = 'sf-native-controller-source-acceptance/v3'
BIND_SCHEMA = 'sf-native-controller-run-binding/v3'
ISSUE_SCHEMA = 'sf-native-controller-test-request/v3'

class ControllerRefused(RuntimeError):
    """Bounded refusal; callers retain effects and do not restart them."""

def need(ok, code):
    if not ok:
        raise ControllerRefused(code)

def empty_arguments(raw):
    value = c.parse(raw)
    need(type(value) is dict and not value, 'test_arguments_must_be_empty')

def source_files(root, expected):
    """Controller files are separate from the immutable launcher src inventory."""
    root = Path(root); c.hex_value(expected)
    raw = e.read(root/'CONTROLLER_SOURCES.json')
    need(e.sha(raw) == expected, 'controller_manifest_changed')
    d = c.parse(raw); c.keys(d, ('schema','files'), 'controller_manifest_fields')
    need(d['schema'] == 'sf-native-controller-sources/v3' and type(d['files']) is dict and
         set(d['files']) == {x+'.py' for x in MODULES}, 'controller_inventory')
    from repository_isolation_plan import inventory
    inventory(root, set(d['files']) | {'CONTROLLER_SOURCES.json'})
    result = {}
    for name, digest in d['files'].items():
        c.hex_value(digest); data = e.read(root/name)
        need(e.sha(data) == digest, 'controller_source_changed:'+name)
        result[name] = data
    return dict(result, **{'CONTROLLER_SOURCES.json':raw})

@dataclass(frozen=True)
class Authority:
    """External trust anchors supplied by a separately reviewed control caller.

    A manufactured acceptance object and its own hash are NOT an independent
    review. Source acceptance is separate from the sealed repository approval.
    This API is not a capability against hostile code in the same interpreter.
    """
    controller_root: Path
    controller_manifest_sha256: str
    library_root: Path
    library_manifest_sha256: str
    acceptance: bytes
    acceptance_sha256: str
    admission_helper: Path
    runtime: l.RuntimeAuthority

    def verify(self):
        need(type(self.runtime) is l.RuntimeAuthority, 'controller_runtime_type')
        source_files(self.controller_root, self.controller_manifest_sha256)
        l.sources(self.library_root, self.library_manifest_sha256)
        c.hex_value(self.acceptance_sha256)
        need(type(self.acceptance) is bytes and len(self.acceptance) <= c.MAX_JSON and
             e.sha(self.acceptance) == self.acceptance_sha256, 'controller_acceptance_hash')
        d = c.parse(self.acceptance)
        c.keys(d, ('schema','controller_manifest_sha256','launcher_manifest_sha256',
            'retained_v1_launcher_review_sha256','controller_review_sha256','target_python','source_review_accepted',
            'target_tests_passed','live_execution_authorized','production_launch_ready'), 'controller_acceptance_fields')
        c.hex_value(d['controller_review_sha256'])
        need(d['schema'] == ACCEPT_SCHEMA and d['controller_manifest_sha256'] == self.controller_manifest_sha256 and
             d['launcher_manifest_sha256'] == self.library_manifest_sha256 and d['retained_v1_launcher_review_sha256'] == LIBRARY_REVIEW_SHA and
             d['target_python'] == [3,11,16] and d['source_review_accepted'] is True and
             d['target_tests_passed'] is True and d['live_execution_authorized'] is False and
             d['production_launch_ready'] is False, 'controller_source_acceptance')
        return d

def boot_proof(authority):
    raw = e.read(authority.admission_helper)
    need(e.sha(raw) == ADMISSION_HELPER_SHA, 'controller_admission_helper_pin')
    m = types.ModuleType('_sf_controller_closed_admission'); m.__file__ = str(authority.admission_helper)
    exec(compile(raw,m.__file__,'exec',dont_inherit=True),m.__dict__)
    # Exact pinned archived-proof validation; no helper main/old tests/review.
    proof = m.verify_fixed_boot13(authority.runtime.fixed_boot_archive)
    return a.fixed_boot_proof(proof)

def initialize_repository(kb, job, expected_request_sha256, expected_descriptor_sha256):
    """Initialize existing runner's staged NEW private DB, then its snapshot.

    The trusted caller stages request, descriptor and repository bytes first.
    Never runs SourceJobRunner's integer task builder, creates a card, starts a
    service, changes a profile, or retries a partially initialized private DB.
    """
    job = Path(job); raw = e.read(job/'task_request.json'); descriptor = e.read(job/'managed_job.json')
    c.hex_value(expected_request_sha256); c.hex_value(expected_descriptor_sha256)
    need(e.sha(raw) == expected_request_sha256 and e.sha(descriptor) == expected_descriptor_sha256,
         'controller_initialization_pins')
    spec = c.parse(raw); d = c.parse(descriptor)
    need(spec['job'] == str(job) and d['job'] == str(job) and d['request_sha256'] == expected_request_sha256,
         'controller_initialization_paths')
    public = Path(d['public'])
    need(e.read(public/'managed_job.json') == descriptor, 'controller_descriptor_copies')
    for name in ('service_native.json','managed_service_ready.json','managed_service_release.json',
                 'managed_service_exit.json','managed_service.log','stop_requested.json','finalization_intent.json'):
        need(not e.present(public/name), 'controller_initialization_after_service')
    pins = spec.get('repository_inputs'); c.keys(pins,n.INPUT_KEYS,'repository_input_pins')
    admitted = c.admit(e.read(job/'repository/approval.json'),e.read(job/'repository/envelope.json'),
        e.read(job/'repository/snapshot.zip',c.MAX_ARCHIVE),expected_approval_sha256=pins['approval_sha256'],
        expected_envelope_sha256=pins['envelope_sha256'],expected_snapshot_sha256=pins['snapshot_sha256'])
    need(admitted.job_id == spec['job_id'], 'controller_repository_job_id')
    e.new_json(job/'NATIVE_CONTROLLER_INIT_STARTED.json',dict(schema=1,request_sha256=expected_request_sha256,
        descriptor_sha256=expected_descriptor_sha256,automatic_retry=False))
    # The accepted stage() requires an existing private parent. Create that
    # container only after one-shot consumption; never reuse staged contents.
    stage_parent = job/'repository/staged'
    with c.directory_fd(job/'repository') as parent_fd:
        if not e.present(stage_parent):
            os.mkdir('staged',mode=0o700,dir_fd=parent_fd)
        with c.directory_fd(stage_parent) as stage_fd:
            need(not os.listdir(stage_fd),'controller_initialization_stage_not_empty')
    binding = f.fj_initialize(kb,spec,descriptor)
    for root in (job,public): e.new_json(root/'finalization_binding.json',binding)
    staged = n.prepare_snapshot(kb,job)
    result = dict(schema=1,initialized=True,staged=str(staged),binding_sha256=e.sha(f.fj_bytes(binding)),
                  task_created=False,service_started=False,automatic_retry=False)
    e.new_json(job/'NATIVE_CONTROLLER_INITIALIZED.json',result)
    return result

def binding_path(ctx,rid):
    need(type(rid) is int and rid > 0, 'controller_run_id')
    return ctx.staged/'control'/('NATIVE_CONTROLLER_BIND_'+str(rid)+'.json')

def request_path(ctx,rid):
    binding_path(ctx,rid)
    return ctx.staged/'control'/('NATIVE_CONTROLLER_TEST_REQUEST_'+str(rid)+'.json')

def issue_path(ctx,rid):
    binding_path(ctx,rid)
    return ctx.staged/'control'/('NATIVE_CONTROLLER_PERMIT_'+str(rid)+'.json')

class NativeRepositoryControl:
    def __init__(self,runner,kb,job,authority):
        need(type(authority) is Authority,'controller_authority_type'); authority.verify()
        self.authority,self.runner,self.kb,self.job = authority,runner,kb,Path(job)
        self.adapter = n.NativeRepositoryAdapter(kb,self.job)
        self.launcher = l.RepositoryExecutionLauncher(runner,self.adapter,authority.library_root,authority.library_manifest_sha256)
        self._binding = n.context(self.job).binding
        self.pid = os.getpid(); self._revoked = False

    def verify(self):
        need(not self._revoked and os.getpid() == self.pid,'controller_revoked_or_forked')
        self.authority.verify(); ctx = n.context(self.job)
        need(ctx.binding == self._binding,'controller_parent_binding_changed')
        return ctx

    def revoke(self): self._revoked = True

    @contextlib.contextmanager
    def connection(self):
        ctx = self.verify()
        with f.fj_open(ctx.binding) as conn:
            f.fj_register(conn,lambda:f.fj_live_scope(ctx.binding))
            yield conn

    def bind_run(self,identity):
        """Once per actual native claim returned by trusted transport, not JSON."""
        need(type(identity) is n.RunIdentity,'controller_identity_type'); identity.validate()
        ctx = self.verify(); parent = self.launcher.parent_service(ctx)
        with parent.fence(),self.connection() as conn,self.adapter.locked(conn) as current:
            role = self.adapter.active(conn,current,identity)
            n.no_pending(current); n.validate_workspace(current)
            record = dict(schema=BIND_SCHEMA,task_id=identity.task_id,run_id=identity.run_id,
                claim_lock_sha256=a._claim_hash(identity),claim_lock=identity.claim_lock,profile=role,
                parent_binding_sha256=e.sha(f.fj_bytes(ctx.binding)),
                controller_manifest_sha256=self.authority.controller_manifest_sha256,
                source_acceptance_sha256=self.authority.acceptance_sha256,
                runtime_approval_sha256=self.authority.runtime.approval_sha256,
                runtime_root=str(self.authority.runtime.root),fixed_boot13_sha256=a.FIXED_BOOT13_ARCHIVE_SHA256,
                automatic_retry=False,native_approval=False,production_launch_ready=False)
            e.new_json(binding_path(ctx,identity.run_id),record)
            # The private journal retains the original transport claim. It is
            # never returned through the model-facing tools or public receipts.
            return {key:value for key,value in record.items() if key != 'claim_lock'}

    def identity(self,rid):
        """Reconstruct the original journaled run, not a replacement claim.

        An ended run's claim is usable only to collect an already committed
        result. Original active() still gates every new effect.
        """
        ctx = self.verify(); record = e.read_json(binding_path(ctx,rid))
        c.keys(record,('schema','task_id','run_id','claim_lock_sha256','claim_lock','profile','parent_binding_sha256',
            'controller_manifest_sha256','source_acceptance_sha256','runtime_approval_sha256','runtime_root',
            'fixed_boot13_sha256','automatic_retry','native_approval','production_launch_ready'),'controller_binding_fields')
        need(record['schema'] == BIND_SCHEMA and type(record['run_id']) is int and record['run_id'] == rid and
             record['parent_binding_sha256'] == e.sha(f.fj_bytes(ctx.binding)) and
             record['controller_manifest_sha256'] == self.authority.controller_manifest_sha256 and
             record['source_acceptance_sha256'] == self.authority.acceptance_sha256 and
             record['runtime_approval_sha256'] == self.authority.runtime.approval_sha256 and
             record['runtime_root'] == str(self.authority.runtime.root) and
             record['fixed_boot13_sha256'] == a.FIXED_BOOT13_ARCHIVE_SHA256 and record['automatic_retry'] is False and
             record['native_approval'] is False and record['production_launch_ready'] is False,'controller_binding_changed')
        with f.fj_open(ctx.binding) as conn: snapshot,_ = f.fj_snapshot(conn,ctx.binding)
        runs = [x for x in snapshot['runs'] if x['id'] == rid]; need(len(runs)==1,'controller_bound_run_missing')
        run = runs[0]
        identity = n.RunIdentity(record['task_id'],record['run_id'],record['claim_lock']); identity.validate()
        # Native _end_run clears claim_lock. Preserve the original private
        # transport identity for collect; active() still rejects ended runs.
        need(run['task_id'] == identity.task_id and
             (run['claim_lock'] == identity.claim_lock or
              (run['ended_at'] is not None and run['claim_lock'] in (None,''))),
             'controller_native_claim_changed')
        need(identity.task_id == record['task_id'] and a._claim_hash(identity) == record['claim_lock_sha256'] and
             run['profile'] == record['profile'],'controller_claim_changed')
        return identity

    def _failure(self,ctx): self.launcher._fence_failed(ctx,Path(ctx.binding['public']))

    def run_tests(self,rid,arguments,*,admission_guard=None):
        empty_arguments(arguments); ident = self.identity(rid); ctx = self.verify()
        proof = boot_proof(self.authority)
        t.runtime_binding(self.authority.runtime.root,self.authority.runtime.approval,
                          self.authority.runtime.approval_sha256,self.authority.runtime.fixed_boot_archive)
        parent = self.launcher.parent_service(ctx)
        # Duplicate refusal precedes try: it must not fence the first attempt.
        admission = contextlib.nullcontext() if admission_guard is None else admission_guard()
        with parent.fence(),admission,self.connection() as conn,self.adapter.locked(conn) as current:
            self.adapter.active(conn,current,ident); n.no_pending(current); n.validate_workspace(current)
            need(not e.present(l.permit_path(ctx,rid)) and not e.present(l.consumed_path(ctx,rid)),
                 'controller_execution_already_present')
            e.new_json(request_path(ctx,rid),dict(schema=ISSUE_SCHEMA,
                binding_sha256=e.sha(e.read(binding_path(ctx,rid))),automatic_retry=False,task_requeued=False))
        try:
            with self.connection() as conn:
                permit = a.issue(self.adapter,conn,ident,runtime_root=self.authority.runtime.root,
                    runtime_approval=self.authority.runtime.approval,runtime_sha256=self.authority.runtime.approval_sha256,
                    fixed_boot=proof)
                e.new_json(issue_path(ctx,rid),dict(schema=1,permit_sha256=permit.sha256,
                    request_sha256=e.sha(e.read(request_path(ctx,rid))),automatic_retry=False))
                self.launcher.prepare(conn,ident,permit.sha256,self.authority.runtime)
                result = self.launcher.execute(conn,ident)
            return self.public_result(result)
        except BaseException:
            saved = l.result_path(ctx,rid)
            committed_pass = False
            try:
                committed_pass = e.present(saved) and e.read_json(saved).get('status') == 'PASS'
            except Exception:
                pass
            if not committed_pass: self._failure(ctx)
            raise

    def collect_tests(self,rid,arguments):
        empty_arguments(arguments); ident = self.identity(rid); ctx = self.verify()
        issued = e.read_json(issue_path(ctx,rid)); request = e.read_json(request_path(ctx,rid))
        c.keys(request,('schema','binding_sha256','automatic_retry','task_requeued'),'controller_request_fields')
        c.keys(issued,('schema','permit_sha256','request_sha256','automatic_retry'),'controller_issue_fields')
        need(request['schema'] == ISSUE_SCHEMA and request['binding_sha256'] == e.sha(e.read(binding_path(ctx,rid))) and
             issued['schema'] == 1 and issued['request_sha256'] == e.sha(c.canonical(request)) and
             issued['automatic_retry'] is False and request['automatic_retry'] is False and request['task_requeued'] is False and
             issued['permit_sha256'] == e.sha(e.read(l.permit_path(ctx,rid))),'controller_issue_binding')
        with self.connection() as conn: return self.public_result(self.launcher.collect(conn,ident))

    @staticmethod
    def public_result(result):
        need(result.get('status') in ('PASS','TEST_FAILED') and result.get('native_approval') is False and
             result.get('launch_consumed') is True,'controller_result_schema')
        obs = result['observation']
        return dict(status=result['status'],tests_run=obs['tests_run'],failures=obs['failures'],errors=obs['errors'],
            result_sha256=e.sha(c.canonical(result)),native_approval=False,automatic_retry=False,production_launch_ready=False)

    def data(self,rid,arguments):
        """Unchanged DATA adapter callable, not an override of protected tools."""
        ident = self.identity(rid); req = c.parse(arguments); ctx = self.verify()
        need(type(req) is dict,'controller_data_arguments_object')
        parent = self.launcher.parent_service(ctx)
        with parent.fence(),self.connection() as conn:
            if req.get('action') in ('write','delete'):
                need(not e.present(request_path(ctx,rid)),'controller_mutation_after_test_request')
            return self.adapter.tool(conn,ident,arguments)
