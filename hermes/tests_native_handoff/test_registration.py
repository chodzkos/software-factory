"""Negatywne testy bindingu; registry/lifecycle to jawne atrapy API.

Wywołanie poprawnie zarejestrowanego adaptera używa istniejącego fixture z
rzeczywistą bazą SQLite i niezmienionymi wybranymi ciałami natywnego kodu.
"""
from __future__ import annotations

import copy
import json
import sys
import types

from registration_fixture import RegistrationHost
from test_transaction import TransactionFixture


class TestRegistration(TransactionFixture):
    def install(self, **kwargs):
        host = RegistrationHost(self, **kwargs)
        self.rt.register(host, self.g)
        return host

    def assert_stale_refused(self, host, *, pre_blocked=False):
        if pre_blocked:
            self.assertEqual(self.before()['action'], 'block')
        else:
            self.assertIsNone(self.before())
        before = self.rows()
        result = json.loads(host.handler(self.args))
        self.assertIn('error', result)
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.connections, [])
        self.assertFalse(self.seal_path.exists())
        self.assertNotIn(self.key, self.g._COMPLETED_ATTESTATIONS)
        self.assertIsNone(self.ri._PENDING)

    def test_silent_registration_noop_is_refused(self):
        host = RegistrationHost(self, mode='noop')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertIs(host.registry.get_entry(host.original.name), host.original)

    def test_other_scope_registration_is_refused_and_removed(self):
        host = RegistrationHost(self, mode='wrong_scope')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertFalse(host.registry.overlays[host.scope + '-other'])

    def test_wrong_handler_registration_is_refused_and_removed(self):
        host = RegistrationHost(self, mode='wrong_handler')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertIsNone(host.entry)

    def test_preexisting_same_handler_overlay_is_refused(self):
        host = RegistrationHost(self)
        host.registry.overlays[host.scope] = {host.original.name: copy.copy(host.original)}
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertEqual(host.recorder, [])

    def test_valid_binding_preserves_all_supported_metadata(self):
        host = RegistrationHost(self)
        host.original.requires_env = ['UNIT_REQUIRED_ENV']
        host.original.description = 'Independent entry description'
        self.rt.register(host, self.g)
        self.assertEqual(host.entry.requires_env, ['UNIT_REQUIRED_ENV'])
        self.assertEqual(host.entry.description, host.original.description)
        self.assertIsNot(host.entry.schema, host.original.schema)
        self.assertEqual(host.entry.schema, host.original.schema)
        self.assertIsNot(host.entry.requires_env, host.original.requires_env)
        self.assertIs(host.entry.check_fn, host.original.check_fn)
        self.assertIs(host.registry.snapshot_registration(host.original.name), host.original)
        self.assertIsNone(self.plugin.review_registration.verify_active_registration())
        self.assertIsNone(self.before())
        result = json.loads(host.handler(self.args))
        self.assertTrue(result['ok'])
        self.assertEqual(self.h.load_handoff_seal(self.board, self.tid, 1)['schema'], 3)
        self.assertIsNone(self.rows()['task_runs'][0]['worker_pid'])

    def test_other_profile_overlay_and_global_entries_are_unchanged(self):
        host = RegistrationHost(self)
        other = types.SimpleNamespace(name='other', handler=object())
        host.registry.overlays['other-profile'] = {'other': other}
        global_before = host.registry.global_entries.copy()
        self.rt.register(host, self.g)
        self.assertEqual(host.registry.global_entries, global_before)
        self.assertIs(host.registry.overlays['other-profile']['other'], other)
        self.assertEqual(set(host.registry.overlays[host.scope]), {'kanban_request_review'})

    def test_retained_handler_after_entry_replacement_is_refused(self):
        host = self.install()
        host.registry.overlays[host.scope][host.original.name] = copy.copy(host.original)
        self.assert_stale_refused(host)

    def test_retained_handler_after_overlay_removal_is_refused(self):
        host = self.install()
        host.registry.overlays[host.scope].clear()
        self.assert_stale_refused(host)

    def test_retained_handler_after_unload_is_refused(self):
        host = self.install()
        host.unload()
        self.assert_stale_refused(host, pre_blocked=True)

    def test_changed_active_scope_is_refused(self):
        host = self.install()
        host.registry.scope = host.scope + '-other'
        self.assert_stale_refused(host)

    def test_changed_profile_name_is_refused(self):
        host = self.install()
        host.profile_name = 'reviewer-gpt'
        self.assert_stale_refused(host)

    def test_changed_manager_scope_is_refused(self):
        host = self.install()
        host._manager.scope_key = host.scope + '-other'
        self.assert_stale_refused(host)

    def test_replaced_registry_object_is_refused(self):
        host = self.install()
        sys.modules['tools.registry'].registry = copy.copy(host.registry)
        self.assert_stale_refused(host)

    def test_changed_nested_schema_is_refused(self):
        host = self.install()
        host.entry.schema['parameters']['properties']['injected'] = {'type': 'string'}
        self.assertNotIn('injected', host.original.schema['parameters']['properties'])
        self.assert_stale_refused(host)

    def test_changed_check_function_is_refused(self):
        host = self.install()
        host.entry.check_fn = lambda: True
        self.assert_stale_refused(host)

    def test_changed_native_global_handler_is_refused(self):
        host = self.install()
        host.original.handler = lambda *a, **kw: 'unexpected'
        self.assert_stale_refused(host)

    def test_changed_native_global_schema_is_refused(self):
        host = self.install()
        host.original.schema['description'] = 'changed'
        self.assert_stale_refused(host)

    def test_invalid_metadata_matrix_is_refused(self):
        mutations = [('is_async', True), ('is_async', 0), ('requires_env', ()),
                     ('requires_env', [1]), ('description', None), ('emoji', None),
                     ('max_result_size_chars', 4096), ('dynamic_schema_overrides', lambda: {}),
                     ('schema', {'name': 'different'})]
        for name, value in mutations:
            with self.subTest(field=name, value=repr(value)):
                host = RegistrationHost(self)
                setattr(host.original, name, value)
                with self.assertRaises(self.h.HandoffError):
                    self.rt.register(host, self.g)
                self.assertEqual(host.recorder, [])

    def test_missing_metadata_field_is_refused(self):
        host = RegistrationHost(self)
        del host.original.max_result_size_chars
        with self.assertRaises((self.h.HandoffError, AttributeError)):
            self.rt.register(host, self.g)
        self.assertEqual(host.recorder, [])

    def test_unload_registration_exception_rolls_back_overlay(self):
        host = RegistrationHost(self, mode='unload_raises')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertIsNone(host.entry)
        self.assertEqual(self.connections, [])

    def test_missing_unload_handle_rolls_back_overlay(self):
        host = RegistrationHost(self, mode='unload_none')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertIsNone(host.entry)

    def test_silent_global_corruption_is_detected_not_overwritten(self):
        host = RegistrationHost(self, mode='global_corruption')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        # Host jest uszkodzony; adapter nie zgaduje globalnego rollbacku.
        self.assertIsNot(host.registry.snapshot_registration(host.original.name), host.original)
        self.assertIsNone(host.entry)
        with self.assertRaises(self.h.HandoffError):
            self.plugin.review_registration.verify_active_registration()

    def test_unload_after_native_sql_rolls_back_all_three_tables(self):
        host = self.install()
        self.assertIsNone(self.before())
        before = self.rows()
        native_request = self.kb.request_review
        transient = []
        def unload_after_sql(conn, *args, **kwargs):
            result = native_request(conn, *args, **kwargs)
            # Oglądamy rzeczywiste połączenie pod kontrolą fixture, nie otwieramy
            # dodatkowego SQL w zamkniętym już natywnym widoku savepointu.
            transient.append(conn.conn.execute('SELECT status FROM tasks').fetchone()[0])
            self.assertEqual(self.rows(), before)
            host.unload()
            return result
        self.kb.request_review = unload_after_sql
        result = json.loads(host.handler(self.args))
        self.assertEqual(transient, ['review'])
        self.assertIn('error', result)
        self.assertEqual(self.rows(), before)
        self.assertFalse(self.seal_path.exists())
        self.assertNotIn(self.key, self.g._COMPLETED_ATTESTATIONS)

    def test_unload_does_not_remove_newer_overlay_entry(self):
        host = self.install()
        replacement = copy.copy(host.entry)
        host.registry.overlays[host.scope][host.original.name] = replacement
        host.unload()
        self.assertIs(host.entry, replacement)
        self.assert_stale_refused(host, pre_blocked=True)

    def test_revoked_generation_cannot_be_resurrected(self):
        host = self.install()
        entry = host.entry
        host.unload()
        host.registry.overlays[host.scope][host.original.name] = entry
        self.assert_stale_refused(host, pre_blocked=True)

    def test_probe_requires_successful_registration(self):
        host = RegistrationHost(self, mode='noop')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        with self.assertRaises(self.h.HandoffError):
            self.plugin.review_registration.verify_active_registration()

    def test_permission_exception_refuses_without_registry_write(self):
        host = RegistrationHost(self, mode='permission_error')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertEqual(host.recorder, [])
        self.assertIsNone(host.entry)
        self.assertEqual(self.connections, [])

    def test_missing_tool_handle_leaves_only_inactive_adapter(self):
        host = RegistrationHost(self, mode='missing_tool_handle')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        # Uszkodzony host nie dostarczył uchwytu do cofnięcia wpisu. Nie wolno
        # zastąpić tego globalnym deregister; pozostały handler musi odmawiać.
        self.assertIsNotNone(host.entry)
        self.assert_stale_refused(host, pre_blocked=True)

    def test_invalid_tool_handle_leaves_only_inactive_adapter(self):
        host = RegistrationHost(self, mode='invalid_tool_handle')
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertIsNotNone(host.entry)
        self.assert_stale_refused(host, pre_blocked=True)

    def test_active_view_disagreement_is_refused(self):
        host = self.install()
        host.registry.get_all_entries = lambda: [host.original]
        self.assert_stale_refused(host)

    def test_duplicate_active_view_is_refused(self):
        host = self.install()
        host.registry.get_all_entries = lambda: [host.entry, host.entry]
        self.assert_stale_refused(host)

    def test_noncanonical_scope_is_refused_before_registration(self):
        host = RegistrationHost(self)
        host.registry.scope = host.scope + '/.'
        host._manager.scope_key = host.registry.scope
        with self.assertRaises(self.h.HandoffError):
            self.rt.register(host, self.g)
        self.assertEqual(host.recorder, [])

    def test_close_exception_does_not_prevent_binding_revocation(self):
        host = self.install()
        host.handles[0].raise_on_dispose = True
        with self.assertRaisesRegex(RuntimeError, 'CONTROLLED_CLEANUP_FAILURE'):
            host.unload()
        self.assertIsNone(host.entry)
        self.assert_stale_refused(host, pre_blocked=True)
