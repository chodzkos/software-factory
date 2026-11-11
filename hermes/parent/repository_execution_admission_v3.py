"""Run-bound repository execution admission, routed-card version 3.0.0.

This module issues a sealed DATA permit only. It does not spawn bubblewrap,
systemd, models, repository workers, shells, or tests. The existing repository
contract execution_request() and isolation-plan launch() remain hard refusals.

Trusted controller obligations:
- load the exact reviewed contract/native-adapter/isolation-plan sources;
- supply RunIdentity from trusted native transport, never model JSON;
- normalize the exact fixed-boot13 PASS archive into FixedBootProof;
- keep the private staged job and runtime capsule protected from concurrent
  same-UID writers; hostile same-UID processes and kernel compromise remain out
  of scope until the separately reviewed launcher rechecks all pins at start.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
from types import MappingProxyType
from typing import Mapping

import repository_task_contract as c
import repository_native_adapter_v3 as n
import repository_worktree_v2 as w
import repository_isolation_plan as iso

VERSION = '3.0.0'
SCHEMA = 'software-factory-repository-execution-permit/v3'
POLICY = 'native-run-bound-python-unittest-worktree-routing-admission/v3'
FIXED_BOOT13_ARCHIVE_SHA256 = '2cd2af37cdb8c62f128f9b80939917113185a38e680db502f4cfeac1c41dc1c3'
FIXED_BOOT13_HELPER_SHA256 = '798e9bd9a8346cb00e0ebc9c6659dbeacfed025ddcbd35c5e3674c8cf6ad091f'
FIXED_BOOT13_RUNTIME_SNAPSHOT_SHA256 = '37eb3da386190173f141149dbb4450ee75c042ff3983a862a842fa963faac9dd'
FIXED_BOOT13_BWRAP_SHA256 = 'e318903862396f96de3df57264e0158682b952fd3fb53ac23d876413e7b30f71'
FIXED_BOOT13_PROBE_MANIFEST_SHA256 = '89e93ac4e6071e6939bc40948510d169e7461bb259abe3f626db847d08c09da8'
FIXED_BOOT13_PROBE_SHA256 = '367bcf8fb8a60bbe1230b988f502095712696522977193364ba99bb7870a2d0c'
MAX_PERMIT = 1024 * 1024


class RepositoryExecutionAdmissionRefused(ValueError):
    """Bounded refusal code only; no source contents, claim secret or credentials."""


def need(ok: object, code: str) -> None:
    if not ok:
        raise RepositoryExecutionAdmissionRefused(code)


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def exact_keys(value: object, names: tuple[str, ...], code: str) -> dict:
    need(type(value) is dict and set(value) == set(names), code)
    return value


def fixed_boot_proof(value: object) -> dict:
    """Validate a trusted controller's normalized view of exact fixed-boot13 PASS.

    The controller must derive this from the archive whose complete SHA is pinned
    here. This function deliberately does not parse ZIP input supplied by a model.
    """
    names = (
        'schema','archive_sha256','helper_sha256','python','runtime_snapshot_sha256',
        'bwrap_sha256','probe_sha256','probe_manifest_sha256','sealed_runtime_files',
        'fixed_probe_passed','sandbox_executed','service_started','service_result',
        'service_exit_status','service_restarts','network_socket_denied','fork_denied',
        'seccomp_observed','secret_paths_unmounted','predecessor_preserved',
        'application_code_executed','models_executed','native_card_bound',
        'production_launch_ready','runtime_approved'
    )
    d = exact_keys(value, names, 'fixed_boot_proof_fields')
    need(d['schema'] == 'software-factory-fixed-boot13-proof/v1', 'fixed_boot_proof_schema')
    need(d['archive_sha256'] == FIXED_BOOT13_ARCHIVE_SHA256 and
         d['helper_sha256'] == FIXED_BOOT13_HELPER_SHA256 and
         d['runtime_snapshot_sha256'] == FIXED_BOOT13_RUNTIME_SNAPSHOT_SHA256 and
         d['bwrap_sha256'] == FIXED_BOOT13_BWRAP_SHA256 and
         d['probe_sha256'] == FIXED_BOOT13_PROBE_SHA256 and
         d['probe_manifest_sha256'] == FIXED_BOOT13_PROBE_MANIFEST_SHA256,
         'fixed_boot_proof_identity')
    need(d['python'] == [3,11,16] and d['sealed_runtime_files'] == 593,
         'fixed_boot_runtime_identity')
    for key in ('fixed_probe_passed','sandbox_executed','service_started','network_socket_denied',
                'fork_denied','seccomp_observed','secret_paths_unmounted','predecessor_preserved'):
        need(d[key] is True, 'fixed_boot_positive:'+key)
    need(d['service_result'] == 'success' and d['service_exit_status'] == 0 and
         d['service_restarts'] == 0, 'fixed_boot_service_result')
    for key in ('application_code_executed','models_executed','native_card_bound',
                'production_launch_ready','runtime_approved'):
        need(d[key] is False, 'fixed_boot_scope:'+key)
    return dict(d)


def workspace_descriptor(ctx: n.Context, files: Mapping[str, bytes], modes: Mapping[str, str]) -> dict:
    """Content-address current admitted workspace without trusting filenames twice."""
    admission = ctx.admission
    git = w.descriptor(ctx)
    frozen = iso.source_state(admission, dict(files), dict(modes))
    return {
        'schema':'software-factory-repository-workspace-binding/v2',
        'git_worktree':git,
        'job_id':admission.job_id,
        'admission_sha256':sha(c.canonical(admission.receipt())),
        'input_sha256':sha(c.canonical(frozen)),
        'files_sha256':sha(c.canonical(frozen['files'])),
        'tests_sha256':sha(c.canonical(frozen['tests'])),
        'expected_count':frozen['expected_count'],
        'timeout_seconds':frozen['timeout_seconds'],
    }


def plan_binding(plan: object, proof: dict) -> dict:
    """Accept only the unchanged inert isolation-plan v1 shape and exact booted bwrap."""
    need(type(plan) is dict, 'isolation_plan_type')
    required = {
        'schema','policy','input_sha256','seal_sha256','runtime_approval_sha256','bwrap_sha256',
        'argv','host_environment','close_fds','stdin','stdout','stderr','max_combined_output_bytes',
        'fd3','service_required_properties','required_open_gates','shell_allowed','network_allowed',
        'host_secrets_allowed','host_writable_mounts','process_creation_allowed','threads_allowed',
        'execution_permitted','os_isolation_verified','runtime_boot_verified','native_binding_verified',
        'production_launch_ready'
    }
    need(set(plan) == required and plan['schema'] == 'software-factory-isolation-plan/v1' and
         plan['policy'] == iso.POLICY, 'isolation_plan_schema')
    need(plan['bwrap_sha256'] == proof['bwrap_sha256'], 'isolation_plan_bwrap_not_booted')
    need(plan['argv'][-6:] == ['--','/runtime/bin/python3.11','-I','-B','-S','/control/unittest_entry.py'],
         'isolation_plan_entrypoint')
    for flag in ('--unshare-user','--unshare-pid','--unshare-net','--unshare-ipc','--unshare-uts',
                 '--unshare-cgroup','--disable-userns','--assert-userns-disabled','--cap-drop',
                 '--new-session','--die-with-parent','--clearenv','--seccomp'):
        need(flag in plan['argv'], 'isolation_plan_flag:'+flag)
    need('--share-net' not in plan['argv'] and '--bind' not in plan['argv'] and '--dev-bind' not in plan['argv'],
         'isolation_plan_writable_or_shared_mount')
    need(plan['host_environment'] == {'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','LC_ALL':'C.UTF-8'} and
         plan['close_fds'] is True and plan['stdin'] == 'devnull' and plan['stdout'] == 'bounded-pipe' and
         plan['stderr'] == 'bounded-pipe' and plan['max_combined_output_bytes'] == 1024*1024,
         'isolation_plan_io_or_environment')
    need(plan['fd3'] == {'kind':'sealed-readonly-seccomp-bpf','sha256':sha(iso.seccomp_program()),
                         'bytes':len(iso.seccomp_program())}, 'isolation_plan_seccomp')
    need(plan['service_required_properties'].get('Restart') == 'no' and
         plan['service_required_properties'].get('KillMode') == 'control-group' and
         plan['service_required_properties'].get('NoNewPrivileges') == 'yes' and
         plan['service_required_properties'].get('MemoryMax') == '512M' and
         plan['service_required_properties'].get('MemorySwapMax') == '0' and
         plan['service_required_properties'].get('TasksMax') == '16' and
         plan['service_required_properties'].get('CPUQuota') == '100%', 'isolation_plan_service_policy')
    for key in ('shell_allowed','network_allowed','host_secrets_allowed','process_creation_allowed','threads_allowed',
                'execution_permitted','os_isolation_verified','runtime_boot_verified','native_binding_verified',
                'production_launch_ready'):
        need(plan[key] is False, 'isolation_plan_scope:'+key)
    need(plan['host_writable_mounts'] == [] and set(plan['required_open_gates']) == set(iso.OPEN_GATES),
         'isolation_plan_open_gates')
    return {
        'plan_sha256':sha(c.canonical(plan)),
        'argv_sha256':sha(c.canonical(plan['argv'])),
        'service_properties_sha256':sha(c.canonical(plan['service_required_properties'])),
        'input_sha256':plan['input_sha256'],
        'seal_sha256':plan['seal_sha256'],
        'runtime_approval_sha256':plan['runtime_approval_sha256'],
        'bwrap_sha256':plan['bwrap_sha256'],
        'seccomp_sha256':plan['fd3']['sha256'],
    }


def _private_child(parent: Path, name: str) -> Path:
    """Create/validate the shared execution parent; run directories remain exclusive."""
    c.path_name(name)
    with c.directory_fd(parent) as fd:
        try:
            os.mkdir(name, 0o700, dir_fd=fd)
            os.fsync(fd)
        except FileExistsError:
            pass
        child = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC, dir_fd=fd)
        try:
            st = os.fstat(child)
            need(st.st_uid == os.geteuid() and (st.st_mode & 0o777) == 0o700,
                 'execution_parent_metadata')
        finally:
            os.close(child)
    return parent/name


def _permit_name(run_id: int) -> str:
    need(type(run_id) is int and run_id > 0, 'permit_run_id')
    return 'EXECUTION_PERMIT_'+str(run_id)+'.json'


def _claim_hash(identity: n.RunIdentity) -> str:
    identity.validate()
    return sha(identity.claim_lock.encode('utf-8'))


@dataclass(frozen=True)
class ExecutionPermit:
    raw: bytes
    data: Mapping[str, object]

    @property
    def sha256(self) -> str:
        return sha(self.raw)


def issue(adapter: n.NativeRepositoryAdapter, conn, identity: n.RunIdentity, *,
          runtime_root: Path, runtime_approval: bytes, runtime_sha256: str,
          fixed_boot: dict) -> ExecutionPermit:
    """Freeze current active native run and issue exactly one future-launch permit.

    No subprocess, shell, model, systemd, bwrap or repository test is executed.
    Repeated issue for the same run fails because both frozen directory and permit
    filename are exclusive and never cleaned up automatically.
    """
    need(type(adapter) is n.NativeRepositoryAdapter, 'native_adapter_type')
    need(type(identity) is n.RunIdentity, 'native_identity_type')
    identity.validate(); proof = fixed_boot_proof(fixed_boot)
    c.hex_value(runtime_sha256)
    with adapter.locked(conn) as ctx:
        profile = adapter.active(conn, ctx, identity)
        n.no_pending(ctx)
        current, modes = n.validate_workspace(ctx)
        workspace = workspace_descriptor(ctx, current, modes)
        execution_parent = _private_child(ctx.staged, 'execution')
        frozen_root = execution_parent / ('run-'+str(identity.run_id))
        frozen = iso.freeze(ctx.admission, current, modes, frozen_root)
        plan = iso.build_plan(ctx.admission, frozen_root, frozen['seal_sha256'],
                              Path(runtime_root), runtime_approval, runtime_sha256,
                              proof['bwrap_sha256'])
        binding = plan_binding(plan, proof)
        need(binding['input_sha256'] == workspace['input_sha256'] and
             binding['seal_sha256'] == frozen['seal_sha256'], 'workspace_plan_binding')
        permit = {
            'schema':SCHEMA,'policy':POLICY,'candidate_manifest':c.CANDIDATE,
            'job_id':ctx.admission.job_id,'repository_id':ctx.admission.repository_id,
            'task_id':identity.task_id,'run_id':identity.run_id,
            'claim_lock_sha256':_claim_hash(identity),'profile':profile,
            'admission_sha256':workspace['admission_sha256'],
            'routing_sha256':n.routing.from_spec(ctx.spec,ctx.admission).digest(),
            'workspace':workspace,'frozen_root':str(frozen_root),
            'frozen_seal_sha256':frozen['seal_sha256'],'plan':binding,
            'fixed_boot13_archive_sha256':proof['archive_sha256'],
            'fixed_boot13_runtime_snapshot_sha256':proof['runtime_snapshot_sha256'],
            'automatic_retry':False,'launch_consumed':False,'launcher_implemented':False,
            'shell_allowed':False,'network_allowed':False,'host_secrets_allowed':False,
            'model_can_supply_authority':False,'merge_authorized':False,'deploy_authorized':False,
            'production_launch_ready':False,'execution_permitted':True,
        }
        raw = c.canonical(permit)
        need(len(raw) <= MAX_PERMIT, 'permit_size')
        with c.directory_fd(ctx.staged/'control') as fd:
            c._write_new(fd, _permit_name(identity.run_id), raw)
        return ExecutionPermit(raw, MappingProxyType(permit))


def parse_permit(raw: bytes) -> dict:
    d = c.parse(raw)
    required = ('schema','policy','candidate_manifest','job_id','repository_id','task_id','run_id',
        'claim_lock_sha256','profile','admission_sha256','workspace','frozen_root','frozen_seal_sha256',
        'plan','fixed_boot13_archive_sha256','fixed_boot13_runtime_snapshot_sha256','automatic_retry',
        'launch_consumed','launcher_implemented','shell_allowed','network_allowed','host_secrets_allowed',
        'model_can_supply_authority','merge_authorized','deploy_authorized','production_launch_ready',
        'execution_permitted','routing_sha256')
    exact_keys(d, required, 'permit_fields')
    need(d['schema']==SCHEMA and d['policy']==POLICY and d['candidate_manifest']==c.CANDIDATE,
         'permit_identity')
    c.identifier(d['job_id']); c.identifier(d['repository_id'])
    need(type(d['task_id']) is str and re.fullmatch(r't_[0-9a-f]{8}',d['task_id']) and
         type(d['run_id']) is int and d['run_id']>0, 'permit_native_identity')
    c.hex_value(d['claim_lock_sha256']); c.hex_value(d['admission_sha256']); c.hex_value(d['routing_sha256'])
    c.hex_value(d['frozen_seal_sha256']); c.hex_value(d['fixed_boot13_archive_sha256'])
    c.hex_value(d['fixed_boot13_runtime_snapshot_sha256'])
    need(d['fixed_boot13_archive_sha256']==FIXED_BOOT13_ARCHIVE_SHA256 and
         d['fixed_boot13_runtime_snapshot_sha256']==FIXED_BOOT13_RUNTIME_SNAPSHOT_SHA256,
         'permit_boot_binding')
    need(d['profile'] in (n.CODER,n.REVIEWER),'permit_profile')
    need(type(d['workspace']) is dict and type(d['plan']) is dict and
         d['plan'].get('seal_sha256')==d['frozen_seal_sha256'] and
         d['plan'].get('input_sha256')==d['workspace'].get('input_sha256'), 'permit_plan_binding')
    need(Path(d['frozen_root']).is_absolute() and '..' not in Path(d['frozen_root']).parts,
         'permit_frozen_path')
    need(d['automatic_retry'] is False and d['launch_consumed'] is False and
         d['launcher_implemented'] is False and d['shell_allowed'] is False and
         d['network_allowed'] is False and d['host_secrets_allowed'] is False and
         d['model_can_supply_authority'] is False and d['merge_authorized'] is False and
         d['deploy_authorized'] is False and d['production_launch_ready'] is False and
         d['execution_permitted'] is True, 'permit_scope')
    ws = d['workspace']
    exact_keys(ws, ('schema','git_worktree','job_id','admission_sha256','input_sha256','files_sha256',
        'tests_sha256','expected_count','timeout_seconds'), 'worktree_permit_workspace_fields')
    pin = ws['git_worktree']
    exact_keys(pin, ('schema','policy','binding_sha256','task_id','workspace','projection_head',
        'metadata_sha256','source_commit','source_tree','git_metadata_excluded_from_frozen_input'), 'worktree_permit_git_fields')
    need(ws['schema'] == 'software-factory-repository-workspace-binding/v2' and
        ws['job_id'] == d['job_id'] and ws['admission_sha256'] == d['admission_sha256'] and
        pin['schema'] == 'sf-immutable-worktree-pin/v2' and pin['policy'] == w.POLICY and
        pin['task_id'] == d['task_id'] and pin['git_metadata_excluded_from_frozen_input'] is True, 'worktree_permit_binding')
    for key in ('binding_sha256','metadata_sha256'): c.hex_value(pin[key])
    for key in ('projection_head','source_commit','source_tree'): c.hex_value(pin[key],40)
    need(type(pin['workspace']) is str and Path(pin['workspace']).is_absolute() and
        '..' not in Path(pin['workspace']).parts and Path(pin['workspace']).name == d['task_id'], 'worktree_permit_path')
    return d


def revalidate(adapter: n.NativeRepositoryAdapter, conn, identity: n.RunIdentity, permit_raw: bytes) -> dict:
    """Future launcher precondition: native run and live workspace are still exact.

    This does not consume the permit and still does not execute anything.
    """
    d = parse_permit(permit_raw)
    need(type(adapter) is n.NativeRepositoryAdapter and type(identity) is n.RunIdentity,
         'revalidate_types')
    identity.validate()
    need(d['task_id']==identity.task_id and d['run_id']==identity.run_id and
         d['claim_lock_sha256']==_claim_hash(identity), 'permit_foreign_run')
    with adapter.locked(conn) as ctx:
        profile = adapter.active(conn, ctx, identity)
        need(d['routing_sha256']==n.routing.from_spec(ctx.spec,ctx.admission).digest(),'permit_routing_changed')
        need(profile==d['profile'] and ctx.admission.job_id==d['job_id'] and
             ctx.admission.repository_id==d['repository_id'], 'permit_native_binding')
        n.no_pending(ctx)
        current,modes=n.validate_workspace(ctx)
        now=workspace_descriptor(ctx,current,modes)
        need(now==d['workspace'],'workspace_changed_after_permit')
        frozen=iso.verify_frozen(Path(d['frozen_root']),d['frozen_seal_sha256'],ctx.admission)
        need(sha(c.canonical(frozen))==d['workspace']['input_sha256'], 'frozen_changed_after_permit')
        return d


def launch(*args: object, **kwargs: object) -> None:
    """Admission v1 has no process launcher; flags cannot turn this into one."""
    raise RepositoryExecutionAdmissionRefused('repository_execution_launcher_not_implemented')
