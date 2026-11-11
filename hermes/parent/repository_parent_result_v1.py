"""Durable patch/test binding for a trusted native-v3 parent; no execution API.

This is a NEW caller, not a modification of the accepted adapter/launcher.
It neither creates Authority nor calls run_tests/request_review/complete_task.
The caller supplies the exact result digest returned by collect_tests, then
retains the returned capsule digest OUTSIDE untrusted model arguments.

Capture is one-shot. Read/export is idempotent only for the same trusted digest.
A partial capture is a terminal barrier, not permission to replay. A capsule
asserts tested bytes, never protected handoff, reviewer approval or native done.
The external bridge still owns process stop and child-aware finalization.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

import repository_task_contract as c
import repository_execution_io as e
import repository_isolation_plan as iso
import repository_native_adapter_v3 as n
import repository_execution_admission_v3 as a
import repository_execution_launcher_v3 as l
import repository_native_control_v3 as nc
import finalization_core as f

VERSION = '1.0.0'
SCHEMA = 'sf-parent-result-capsule/v1'
COMMIT = 'CAPSULE.json'
MEMBERS = ('INTENT.json', 'candidate.patch', 'patch_receipt.json',
           'execution.json', 'permit.json', 'consumption.json')
FLAGS = dict(native_approval=False, protected_handoff_verified=False,
             native_done=False, independent_test_attestation=False,
             live_execution_authorized=False, production_launch_ready=False,
             merge_authorized=False, deploy_authorized=False,
             automatic_retry=False)


def run_id(value: int) -> int:
    e.need(type(value) is int and value > 0, 'parent_run_id')
    return value


def directory(path: Path) -> None:
    if not e.present(path):
        e.mkdir(path)
    with c.directory_fd(path):
        pass


def _admission_hash(ctx) -> str:
    return e.sha(c.canonical(ctx.admission.receipt()))


@dataclass(frozen=True)
class Capsule:
    """Receipt only: the hash is NOT an execution or approval capability."""
    task_id: str
    coder_run_id: int
    capsule_sha256: str
    patch_sha256: str
    execution_sha256: str


class ParentResultStore:
    """Trusted parent-only data API, not registered on model tool surfaces.

    Same-UID hostile Python can impersonate any in-process object; this class
    does not claim to be a sandbox against it. New caller sources and the real
    Authority construction must receive independent review before deployment.
    """
    def __init__(self, control: nc.NativeRepositoryControl):
        e.need(type(control) is nc.NativeRepositoryControl, 'parent_control_type')
        self.control = control
        ctx = control.verify()
        self.binding_sha256 = e.sha(c.canonical(ctx.binding))
        self.request_sha256 = e.sha(e.read(control.job/'task_request.json'))
        self.pid = os.getpid()

    def _context(self):
        e.need(os.getpid() == self.pid, 'parent_result_forked')
        ctx = self.control.verify()
        e.need(e.sha(c.canonical(ctx.binding)) == self.binding_sha256 and
               e.sha(e.read(self.control.job/'task_request.json')) == self.request_sha256,
               'parent_result_context_changed')
        return ctx

    def _path(self, ctx, rid: int) -> Path:
        return ctx.staged/'parent-results'/('run-' + str(run_id(rid)))

    def _execution(self, ctx, ident, expected_sha256: str):
        """Bind collected result to original permit/consumption and caller pin.

        The digest must originate from the original controller's collect_tests.
        This method is deliberately NOT a replacement parser of child stdout.
        """
        c.hex_value(expected_sha256)
        raw = e.read(l.result_path(ctx, ident.run_id))
        e.need(e.sha(raw) == expected_sha256, 'parent_collected_result_pin')
        result = c.parse(raw)
        observed = self.control.public_result(result)
        e.need(observed['status'] == 'PASS' and observed['failures'] == 0 and
               observed['errors'] == 0 and result['observation']['ok'] is True,
               'parent_requires_test_pass')
        permit_raw, permit = l.checked_permit(ctx, ident, result['permit_sha256'])
        consumed_raw = e.read(l.consumed_path(ctx, ident.run_id))
        consumed = c.parse(consumed_raw)
        e.need(result['schema'] == l.RESULT_SCHEMA and
               result['task_id'] == ident.task_id and result['run_id'] == ident.run_id and
               result['claim_lock_sha256'] == a._claim_hash(ident) and
               result['source_manifest_sha256'] == self.control.authority.library_manifest_sha256 and
               result['consumption_sha256'] == e.sha(c.canonical(consumed)) and
               consumed['schema'] == l.CONSUMED_SCHEMA and consumed['launch_consumed'] is True and
               consumed['permit_sha256'] == e.sha(permit_raw) and
               result['native_approval'] is False and result['automatic_retry'] is False and
               result['production_launch_ready'] is False and
               result['test_output_is_independent_attestation'] is False and
               observed['tests_run'] == permit['workspace']['expected_count'],
               'parent_execution_binding')
        # Cross-check the original controller's request and binding receipts.
        request_raw = e.read(nc.request_path(ctx, ident.run_id))
        request = c.parse(request_raw)
        issued = e.read_json(nc.issue_path(ctx, ident.run_id))
        e.need(request['schema'] == nc.ISSUE_SCHEMA and
               request['binding_sha256'] == e.sha(e.read(nc.binding_path(ctx, ident.run_id))) and
               issued['permit_sha256'] == e.sha(permit_raw) and
               issued['request_sha256'] == e.sha(request_raw), 'parent_test_request_binding')
        return raw, permit_raw, consumed_raw, permit

    def capture(self, coder_run_id: int, *, collected_result_sha256: str) -> Capsule:
        """Save exact tested patch before native coder/reviewer completion.

        Does not start, collect or repeat tests. Requires an already collected
        PASS and a still active CODER. Duplicate/partial destinations are refused
        without fencing or altering the first capture. The caller handles errors
        via its independent outer lifecycle, not by repeating this method.
        """
        ctx = self._context(); rid = run_id(coder_run_id)
        ident = self.control.identity(rid)
        parent = self.control.launcher.parent_service(ctx)
        with parent.fence(), self.control.connection() as conn:
            with self.control.adapter.locked(conn) as fresh:
                e.need(self.control.adapter.active(conn, fresh, ident) == n.CODER,
                       'parent_capture_requires_active_coder')
                root = fresh.staged/'parent-results'
                directory(root)
                dest = self._path(fresh, rid)
                e.need(not e.present(dest), 'parent_capture_already_consumed')
                execution, permit_raw, consumed, permit = self._execution(
                    fresh, ident, collected_result_sha256)
                l.active_locked(self.control.adapter, conn, fresh, ident, permit)
                current, modes = n.validate_workspace(fresh)
                patch, patch_receipt = c.patch(fresh.admission, current, modes)
                intent = dict(schema='sf-parent-result-intent/v1',
                    binding_sha256=self.binding_sha256, request_sha256=self.request_sha256,
                    admission_sha256=_admission_hash(fresh), routing_sha256=permit['routing_sha256'],
                    task_id=ident.task_id, coder_run_id=rid,
                    claim_lock_sha256=a._claim_hash(ident),
                    execution_sha256=e.sha(execution), **FLAGS)
                data = dict(zip(MEMBERS, (c.canonical(intent), patch,
                    c.canonical(patch_receipt), execution, permit_raw, consumed)))
                # The exclusive directory is itself a one-shot barrier even
                # when creating INTENT fails. No cleanup/unlink on any failure.
                e.mkdir(dest)
                for name in MEMBERS:
                    e.new_file(dest/name, data[name])
                # Re-check native identity and tested bytes AFTER durable copies,
                # before the final commit record. Locks are never nested/replayed.
                l.active_locked(self.control.adapter, conn, fresh, ident, permit)
                e.need(e.read(l.result_path(fresh, rid)) == execution,
                       'parent_execution_changed_during_capture')
                meta = dict(schema=SCHEMA, task_id=ident.task_id, coder_run_id=rid,
                    binding_sha256=self.binding_sha256, request_sha256=self.request_sha256,
                    admission_sha256=_admission_hash(fresh), routing_sha256=permit['routing_sha256'],
                    source_manifest_sha256=self.control.authority.library_manifest_sha256,
                    controller_manifest_sha256=self.control.authority.controller_manifest_sha256,
                    members={name:dict(bytes=len(raw), sha256=e.sha(raw)) for name,raw in data.items()},
                    tested_bytes_preserved=True, **FLAGS)
                e.new_json(dest/COMMIT, meta)
                digest = e.sha(c.canonical(meta))
        # May be re-exported later, but a successful capture is never re-created.
        return self.read(rid, expected_capsule_sha256=digest)[0]

    def read(self, coder_run_id: int, *, expected_capsule_sha256: str):
        """Read only. Does not depend on a surviving worktree or active run.

        Requires the externally retained exact capsule digest. Does not infer
        protected approval from native status, file existence or test PASS.
        """
        c.hex_value(expected_capsule_sha256)
        ctx = self._context(); rid = run_id(coder_run_id); dest = self._path(ctx, rid)
        iso.inventory(dest, set(MEMBERS) | {COMMIT})
        raw = e.read(dest/COMMIT)
        e.need(e.sha(raw) == expected_capsule_sha256, 'parent_capsule_pin')
        meta = c.parse(raw)
        c.keys(meta, ('schema','task_id','coder_run_id','binding_sha256','request_sha256',
                     'admission_sha256','routing_sha256','source_manifest_sha256',
                     'controller_manifest_sha256','members','tested_bytes_preserved',*FLAGS),
               'parent_capsule_fields')
        c.keys(meta['members'], MEMBERS, 'parent_capsule_members')
        e.need(meta['schema'] == SCHEMA and meta['coder_run_id'] == rid and
               meta['task_id'] == n.w.bound(ctx)['task_id'] and
               meta['binding_sha256'] == self.binding_sha256 and
               meta['request_sha256'] == self.request_sha256 and
               meta['admission_sha256'] == _admission_hash(ctx) and
               meta['routing_sha256'] == n.routing.from_spec(ctx.spec, ctx.admission).digest() and
               meta['source_manifest_sha256'] == self.control.authority.library_manifest_sha256 and
               meta['controller_manifest_sha256'] == self.control.authority.controller_manifest_sha256 and
               meta['tested_bytes_preserved'] is True and
               all(meta[k] is False for k in FLAGS), 'parent_capsule_binding')
        data = {}
        for name in MEMBERS:
            pin = meta['members'][name]
            c.keys(pin, ('bytes','sha256'), 'parent_member_pin_fields')
            c.hex_value(pin['sha256'])
            limit = c.MAX_PATCH if name == 'candidate.patch' else c.MAX_JSON
            e.need(type(pin['bytes']) is int and 0 <= pin['bytes'] <= limit, 'parent_member_size')
            data[name] = e.read(dest/name, limit)
            e.need(len(data[name]) == pin['bytes'] and e.sha(data[name]) == pin['sha256'],
                   'parent_member_drift:' + name)
        # Closed set + externally pinned metadata protects against editing both
        # a member and its self-described checksum. No orphan partial is accepted.
        e.need(e.read(dest/COMMIT) == raw, 'parent_capsule_changed_during_read')
        pr = c.parse(data['patch_receipt.json']); ex = c.parse(data['execution.json'])
        intent = c.parse(data['INTENT.json'])
        e.need(pr['patch_sha256'] == e.sha(data['candidate.patch']) and
               pr['admission_sha256'] == meta['admission_sha256'] and
               ex['task_id'] == meta['task_id'] == intent['task_id'] and
               ex['run_id'] == rid == intent['coder_run_id'] and
               ex['status'] == 'PASS' and ex['native_approval'] is False and
               intent['execution_sha256'] == e.sha(data['execution.json']), 'parent_member_cross_binding')
        receipt = Capsule(meta['task_id'], rid, expected_capsule_sha256,
                          e.sha(data['candidate.patch']), e.sha(data['execution.json']))
        return receipt, dict(data, **{COMMIT: raw})

    def verify_reviewer_input(self, reviewer_run_id: int, coder_run_id: int,
                              *, expected_capsule_sha256: str) -> dict:
        """Prerequisite only; does not call/replace guarded_reviewer_complete.

        Requires same card, live reviewer, immediately preceding ended coder,
        unchanged tested files and Git metadata. The original protected seal
        and downstream approval must still be verified by the full parent.
        """
        cap, data = self.read(coder_run_id, expected_capsule_sha256=expected_capsule_sha256)
        ident = self.control.identity(run_id(reviewer_run_id)); ctx = self._context()
        parent = self.control.launcher.parent_service(ctx)
        with parent.fence(), self.control.connection() as conn:
            with self.control.adapter.locked(conn) as fresh:
                e.need(self.control.adapter.active(conn, fresh, ident) == n.REVIEWER,
                       'parent_requires_active_reviewer')
                snapshot, _ = f.fj_snapshot(conn, fresh.binding)
                runs = sorted(snapshot['runs'], key=lambda x:x['id'])
                e.need(len(runs) >= 2 and runs[-1]['id'] == ident.run_id and
                       runs[-2]['id'] == cap.coder_run_id and
                       runs[-2]['profile'] == n.CODER and runs[-2]['ended_at'] is not None and
                       runs[-2]['outcome'] == 'review_requested' and
                       runs[-2]['task_id'] == cap.task_id == ident.task_id,
                       'parent_reviewer_predecessor')
                n.no_pending(fresh)
                current, modes = n.validate_workspace(fresh)
                permit = a.parse_permit(data['permit.json'])
                e.need(a.workspace_descriptor(fresh,current,modes) == permit['workspace'],
                       'parent_reviewer_tested_bytes_changed')
                iso.verify_frozen(Path(permit['frozen_root']),permit['frozen_seal_sha256'],fresh.admission)
                patch, pr = c.patch(fresh.admission,current,modes)
                e.need(patch == data['candidate.patch'] and c.canonical(pr) == data['patch_receipt.json'],
                       'parent_reviewer_patch_changed')
        return dict(schema='sf-parent-reviewer-input-check/v1',task_id=cap.task_id,
                    coder_run_id=cap.coder_run_id,reviewer_run_id=ident.run_id,
                    capsule_sha256=cap.capsule_sha256,candidate_matches_tested_bytes=True, **FLAGS)

    def export(self, coder_run_id: int, *, expected_capsule_sha256: str) -> dict:
        """Export already committed bytes; no new capture or run, same bytes only."""
        cap, data = self.read(coder_run_id, expected_capsule_sha256=expected_capsule_sha256)
        ctx = self._context(); root = Path(ctx.binding['public'])/'parent-results'
        directory(root); dest = root/('run-' + str(cap.coder_run_id)); directory(dest)
        for name in (*MEMBERS, COMMIT):
            e.publish(dest/name, data[name])
        # A second verified read binds the publication commit to the same source.
        self.read(coder_run_id, expected_capsule_sha256=expected_capsule_sha256)
        receipt = dict(schema='sf-parent-result-export/v1',task_id=cap.task_id,
            coder_run_id=cap.coder_run_id,capsule_sha256=cap.capsule_sha256,
            patch_sha256=cap.patch_sha256,execution_sha256=cap.execution_sha256,
            artifact_directory=str(dest), **FLAGS)
        e.publish_json(dest/'PUBLISHED.json',receipt)
        return receipt
