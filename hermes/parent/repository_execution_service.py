"""Bounded recipe service layered on the pinned 1.19.0 JobService (1.16.0 code unchanged).

The parent native service already exists. Each permit owns one sibling test
service, BindsTo/After that exact parent; there is no new daemon or unit file.
The unchanged backend owns acknowledgement, release, stop and process identity.
"""
from __future__ import annotations
import contextlib
from pathlib import Path
import re
import types

import repository_task_contract as c
from repository_execution_io import need, sha, read_json, publish_json, present
from repository_execution_transport import host_tool

BASE_SHA = '95c98b4189dda9e21e73e61f10486e40f77bfbc068782cd4bb2c5575476439c9'
LIFECYCLE_SHA = '08097ac84393f830469db5788de48bd591bb238ba0c01413f5b926c13cb9593c'
MANAGED_SHA = '3fc74f87717247f33c97845c5b8c518c4b70c2012bb65c5cdb6063fd56886157'
PAYLOAD_SHA = '53beca2902f845a498a8628d00bb20e551d22ee7145b3317a5f1350f0990422f'
EXTRA_PROPERTIES = ('Id', 'InvocationID', 'MemoryMax', 'MemorySwapMax', 'TasksMax',
                    'CPUQuotaPerSecUSec', 'LimitNOFILE', 'LimitCORE', 'BindsTo', 'After')


def backend_module(runner):
    """Private namespace: never trust a cached global module of the same name."""
    for attr, expected in (('SERVICE_LIFECYCLE_CODE', LIFECYCLE_SHA),
                           ('MANAGED_SERVICE_CODE', MANAGED_SHA),
                           ('MANAGED_SERVICE_PAYLOAD', PAYLOAD_SHA)):
        need(sha(getattr(runner, attr).encode()) == expected, 'existing_service_source_pin')
    need(sha(Path(runner.__file__).read_bytes()) == BASE_SHA, 'existing_runner_pin')
    m = types.ModuleType('sf_repository_execution_service_v1_private')
    text = runner.SERVICE_LIFECYCLE_CODE + '\n' + runner.MANAGED_SERVICE_CODE + '\n' + runner.FINALIZATION_SERVICE_ADAPTER
    exec(compile(text, '<pinned-existing-service>', 'exec'), m.__dict__)
    return m


def finalization_module(runner):
    # Reuse the exact finalizer, not an unconditional native reclaim handler.
    backend_module(runner)
    m = types.ModuleType('sf_repository_execution_finalizer_v1_private')
    m.FJ_SERVICE_MODULE_CODE = runner.finalization_service_code()
    exec(compile(runner.FINALIZATION_CORE_CODE + '\n' + runner.FINALIZATION_CONTROL_CODE,
                 '<pinned-existing-finalizer>', 'exec'), m.__dict__)
    return m


def resource_argv(argv: list[str], properties: dict, parent_unit: str, descriptor_seconds: int) -> list[str]:
    need(re.fullmatch(r'sf-job-[0-9a-f]{32}-native\.service', parent_unit), 'parent_unit_shape')
    need(type(argv) is list and argv[0] == '/usr/bin/systemd-run' and argv.count('--') == 1,
         'service_start_shape')
    pos = argv.index('--')
    flags = argv[:pos]
    existing = {}
    for flag in flags:
        if flag.startswith('--property='):
            key, value = flag[11:].split('=', 1)
            need(key not in existing, 'service_property_duplicate')
            existing[key] = value
    for key in ('Restart', 'KillMode', 'SendSIGKILL', 'NoNewPrivileges', 'Delegate', 'UMask', 'TimeoutStopSec'):
        need(existing.get(key) == properties[key], 'service_base_property:' + key)
    need(existing.get('RuntimeMaxSec') == str(descriptor_seconds) + 's', 'service_base_runtime')
    # Legacy JobService descriptors require >=60 seconds. For shorter recipes
    # reduce only the effective unit limit; never widen the admitted plan.
    flags[flags.index('--property=RuntimeMaxSec=' + str(descriptor_seconds) + 's')] = '--property=RuntimeMaxSec=' + properties['RuntimeMaxSec']
    additions = {k: properties[k] for k in ('MemoryMax', 'MemorySwapMax', 'TasksMax', 'CPUQuota')}
    additions.update(LimitNOFILE='2048', LimitCORE='0', BindsTo=parent_unit, After=parent_unit)
    need(not set(additions) & set(existing), 'resource_property_preexisting')
    return flags + ['--property=' + k + '=' + v for k, v in additions.items()] + argv[pos:]


def service_class(runner):
    backend = backend_module(runner)

    class RepositoryTestService(backend.FinalizationJobService):
        def __init__(self, owner, job, public, descriptor, *, launch_guard=None):
            super().__init__(owner, job, public, descriptor)
            self.request = read_json(Path(job) / 'task_request.json')
            need(self.request.get('schema') == 'sf-repository-test-service-request/v1', 'test_service_schema')
            need(self.descriptor['job_id'] == 'repo-tests-' + str(self.request['run_id']), 'test_service_id')
            self.parent_binding = self.request['parent_binding']
            self.parent_unit = 'sf-job-' + self.parent_binding['token'] + '-native.service'
            self.properties = self.request['plan']['service_required_properties']
            self.effective_seconds = self.request['timeout_seconds'] + 10
            need(self.properties['RuntimeMaxSec'] == str(self.effective_seconds) + 's' and
                 self.descriptor['runtime_seconds'] == max(60, self.effective_seconds), 'test_service_deadline')
            self.launch_guard = launch_guard
            self._launching = False

        def launch(self, case='native'):
            need(callable(self.launch_guard), 'read_only_service_handle_cannot_launch')
            self._launching = True
            try:
                return super().launch(case)
            finally:
                self._launching = False

        @contextlib.contextmanager
        def fence(self):
            with super().fence():
                if self._launching:
                    self.launch_guard()
                yield

        def cancel_check(self):
            super().cancel_check()
            parent = Path(self.parent_binding['public'])
            if present(parent / 'stop_requested.json') or present(parent / 'finalization_intent.json'):
                raise backend.LifecycleCancelled('native_parent_fenced')

        def command(self, argv, timeout=12):
            host_tool(argv[0])
            actual = argv
            if argv[0] == '/usr/bin/systemd-run':
                need(self._launching and callable(self.launch_guard), 'test_start_not_admitted')
                self.launch_guard()
                actual = resource_argv(list(argv), self.properties, self.parent_unit, self.descriptor['runtime_seconds'])
                publish_json(self.public / 'EXECUTION_SERVICE_ARGV.json', {
                    'schema': 1, 'argv': actual, 'parent_unit': self.parent_unit,
                    'permit_sha256': self.request['permit_sha256'], 'automatic_retry': False})
            return super().command(actual, timeout=timeout)

        def show(self, case):
            row = super().show(case)
            if row['LoadState'] == 'not-found':
                return row
            cp = self.command(['/usr/bin/systemctl', '--user', '--no-ask-password', '--no-pager',
                               'show', '--property=' + ','.join(EXTRA_PROPERTIES), '--', self.unit(case)])
            need(cp.returncode == 0 and len(cp.stdout) <= 16384, 'test_resource_query_unknown')
            extra = {}
            for line in cp.stdout.decode('ascii').splitlines():
                k, v = line.split('=', 1)
                need(k in EXTRA_PROPERTIES and k not in extra, 'test_resource_property')
                extra[k] = v
            need(set(extra) == set(EXTRA_PROPERTIES) and extra['Id'] == row['Id'] and
                 extra['InvocationID'] == row['InvocationID'], 'test_resource_identity_race')
            for k, v in (('MemoryMax', '536870912'), ('MemorySwapMax', '0'), ('TasksMax', '16'),
                         ('LimitNOFILE', '2048'), ('LimitCORE', '0')):
                need(extra[k] == v, 'test_resource_not_applied:' + k)
            need(backend.duration_seconds(extra['CPUQuotaPerSecUSec']) == 1, 'test_cpu_quota')
            need(extra['BindsTo'].split() == [self.parent_unit] and
                 self.parent_unit in extra['After'].split(), 'test_parent_lifetime_not_applied')
            return dict(row, **extra)

        def validate_properties(self, case, row, runtime_seconds):
            need(runtime_seconds == self.descriptor['runtime_seconds'], 'test_record_runtime_changed')
            return super().validate_properties(case, row, self.effective_seconds)

    return RepositoryTestService


def verified_empty(service) -> dict:
    """Never elevate JobService's diagnostic absence to positive quiescence."""
    empty, proof = service.empty('native')
    record = service.record('native')
    need(empty and proof.get('cgroup_empty') is True and proof.get('observed_processes_absent') is True and
         proof.get('diagnostic_absence_only') is False and proof.get('previous_boot_ended') is False and
         record.get('boot') == service.boot and record.get('descriptor_sha256') == service.descriptor_sha and
         type(record.get('invocation_id')) is str and re.fullmatch(r'[0-9a-f]{32}', record['invocation_id']),
         'test_service_quiescence_unknown')
    return proof


def verify_exit(service, final: dict, expected_exit: int) -> dict:
    need(type(expected_exit) is int and expected_exit in (0, 1), 'test_exit_not_ordinary')
    if expected_exit == 0:
        return service.successful_exit(final)
    record = service.record('native')
    receipt = read_json(service.public / 'managed_service_exit.json')
    common = service.release_common(record)
    need(all(receipt.get(k) == v for k, v in common.items() if k != 'identity') and
         receipt.get('receipt_only') is True and receipt.get('identity', {}).get('boot') == service.boot and
         record.get('released') is True, 'test_exit_receipt_binding')
    need(receipt.get('systemd') == {'SERVICE_RESULT': 'exit-code', 'EXIT_CODE': 'exited',
         'EXIT_STATUS': '1', 'INVOCATION_ID': record['invocation_id']}, 'test_failed_exit_receipt')
    row = final['service']
    if row['LoadState'] != 'not-found':
        need(row.get('Result') == 'exit-code' and row.get('ExecMainCode') == '1' and
             row.get('ExecMainStatus') == '1' and row.get('NRestarts') == '0', 'test_exit_contradiction')
    return receipt
