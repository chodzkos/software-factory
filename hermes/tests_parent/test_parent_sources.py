"""Source checks for the imported parent stack (hermes/parent). Source-only: no parent, worker or model runs.

The 22 modules are the bytes accepted in activation review 4, with one documented change in
repository_task_contract.py (the candidate manifest digest is bound at runtime instead of hard-coded).
"""
from __future__ import annotations
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

HERMES = Path(__file__).resolve().parents[1]
PARENT = HERMES / 'parent'
PROVENANCE = PARENT / 'PARENT_PROVENANCE.json'
CONTRACT = PARENT / 'repository_task_contract.py'
ACCEPTED_CANDIDATE = 'd426468118a2260970f473b77c879d0eca49102ee532dfd82b6b1ebd35aea879'
BINDING_API_SHA256 = '5ed0d48b2012158e7de5eca4cb81ccf256d2785e67bf150da5ee53be0df51486'
GOOD = 'a' * 64
OTHER = 'b' * 64


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def constant(path: Path, name: str) -> str:
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and [getattr(t, 'id', None) for t in node.targets] == [name]:
            return node.value.value
    raise AssertionError(name)


def load_contract(tag: str):
    spec = importlib.util.spec_from_file_location('sf_parent_contract_' + tag, CONTRACT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve the defining module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


class ProvenanceChecks(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(PROVENANCE.read_bytes())

    def test_inventory_is_exact(self):
        self.assertEqual(self.doc['schema'], 'sf-parent-provenance/v1')
        self.assertEqual(set(self.doc['files']), {p.name for p in PARENT.glob('*.py')})
        self.assertEqual(len(self.doc['files']), 22)
        self.assertEqual(sorted(p.name for p in PARENT.iterdir()),
                         sorted(list(self.doc['files']) + ['PARENT_PROVENANCE.json', 'README.md']))

    def test_repository_bytes_match_provenance(self):
        for name, row in self.doc['files'].items():
            with self.subTest(name=name):
                self.assertEqual(sha(PARENT / name), row['repository_sha256'])

    def test_only_documented_change(self):
        changed = sorted(n for n, r in self.doc['files'].items() if r['changed'])
        self.assertEqual(changed, ['repository_parent_native_spawn_v1.py', 'repository_task_contract.py'])
        self.assertEqual(sorted(self.doc['changes']), changed)
        for name, row in self.doc['files'].items():
            self.assertEqual(row['changed'], row['accepted_sha256'] != row['repository_sha256'])

    def test_contract_change_reverses_to_accepted_bytes(self):
        text = CONTRACT.read_text(encoding='utf-8')
        head, sep, tail = text.partition('\n\n\ndef bind_candidate(')
        self.assertTrue(sep)
        # The appended binding API is pinned verbatim: nothing else may follow the accepted bytes.
        self.assertEqual(hashlib.sha256((sep + tail).encode()).hexdigest(), BINDING_API_SHA256)
        head += '\n'
        start = head.index('# Repository-resident form')
        end = head.index('_CANDIDATE = None\n') + len('_CANDIDATE = None\n')
        accepted = (head[:start] + "CANDIDATE = '%s'\n" % ACCEPTED_CANDIDATE + head[end:]).replace(
            "envelope['candidate_manifest']==bound_candidate() and", "envelope['candidate_manifest']==CANDIDATE and")
        self.assertEqual(hashlib.sha256(accepted.encode()).hexdigest(),
                         self.doc['files']['repository_task_contract.py']['accepted_sha256'])

    def test_status_is_source_only(self):
        self.assertEqual(self.doc['status'], dict(parent_implemented=False, source_only=True,
                                                  installed=False, live_executed=False))

    def test_finalization_core_identical_to_native_integration(self):
        self.assertEqual(sha(PARENT / 'finalization_core.py'),
                         sha(HERMES / 'native_integration' / 'factory_finalization_core.py'))

    def test_spawner_pins_match_repository_bytes(self):
        spawn = PARENT / 'repository_parent_native_spawn_v1.py'
        self.assertEqual(constant(spawn, 'DISPATCH_SHA256'), sha(HERMES / 'kanban_review_dispatch.py'))
        self.assertEqual(constant(spawn, 'GATE_SHA256'), sha(PARENT / 'repository_parent_exec_gate_v1.py'))

    def test_native_kanban_pin_matches_runtime(self):
        native = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
        self.assertEqual(constant(PARENT / 'repository_parent_native_launch_v1.py', 'NATIVE_KANBAN_SHA'),
                         sha(native / 'hermes_cli' / 'kanban_db.py'))


class CandidateBindingChecks(unittest.TestCase):
    def test_unbound_reads_refuse(self):
        c = load_contract('unbound')
        for read in (lambda: c.CANDIDATE, c.bound_candidate):
            with self.assertRaises(c.RepositoryTaskRefused) as cm:
                read()
            self.assertEqual(str(cm.exception), 'candidate_unbound')

    def test_no_module_constant(self):
        c = load_contract('noconst')
        self.assertNotIn('CANDIDATE', vars(c))
        self.assertIsNone(c._CANDIDATE)

    def test_bind_then_read(self):
        c = load_contract('bind')
        self.assertEqual(c.bind_candidate(GOOD), GOOD)
        self.assertEqual(c.CANDIDATE, GOOD)
        self.assertEqual(c.bound_candidate(), GOOD)

    def test_rebind_same_is_noop_and_other_refused(self):
        c = load_contract('rebind')
        c.bind_candidate(GOOD)
        self.assertEqual(c.bind_candidate(GOOD), GOOD)
        with self.assertRaises(c.RepositoryTaskRefused) as cm:
            c.bind_candidate(OTHER)
        self.assertEqual(str(cm.exception), 'candidate_already_bound')
        self.assertEqual(c.CANDIDATE, GOOD)

    def test_malformed_digest_refused_and_stays_unbound(self):
        c = load_contract('malformed')
        for bad in (None, '', 'A' * 64, 'a' * 63, 'a' * 65, b'a' * 64, 'g' * 64):
            with self.subTest(bad=bad), self.assertRaises(c.RepositoryTaskRefused):
                c.bind_candidate(bad)
        with self.assertRaises(c.RepositoryTaskRefused):
            c.bound_candidate()

    def test_other_attributes_unaffected(self):
        c = load_contract('attrs')
        with self.assertRaises(AttributeError):
            c.NOT_A_NAME
        self.assertEqual(c.SCHEMA, 'software-factory-repository-task/v1')

    def test_envelope_check_uses_bound_candidate(self):
        tree = ast.parse(CONTRACT.read_text(encoding='utf-8'))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertNotIn('CANDIDATE', names)
        self.assertIn("envelope['candidate_manifest']==bound_candidate()", CONTRACT.read_text(encoding='utf-8'))


class ImportChecks(unittest.TestCase):
    def test_every_module_imports_against_native_runtime(self):
        native = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
        modules = sorted(p.stem for p in PARENT.glob('*.py'))
        code = 'import importlib,sys\nfor m in sys.argv[1:]:importlib.import_module(m)\nprint(len(sys.argv)-1)'
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env['PYTHONPATH'] = os.pathsep.join((str(PARENT), str(HERMES), str(native)))
        cp = subprocess.run([sys.executable, '-B', '-c', code, *modules], env=env, cwd=str(PARENT),
                            capture_output=True, text=True, timeout=120)
        self.assertEqual(cp.returncode, 0, cp.stderr[-2000:])
        self.assertEqual(cp.stdout.strip(), '22')


if __name__ == '__main__':
    unittest.main()
