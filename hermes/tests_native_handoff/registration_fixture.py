"""Jawna atrapa API rejestru i lifecycle, nie import zainstalowanego Hermesa.

Odwzorowuje get_entry/snapshot_registration/profilowy overlay i CAS disposal
zaobserwowane w upstream 0a0497060ad4bf1ccd7f6846b0c4d4b03f8476f1.
To test kontraktu naszego adaptera, a nie dowód zgodności całego runtime.
"""
from __future__ import annotations

import os
import sys
import types
from unittest.mock import patch


class Handle:
    def __init__(self, release=lambda: None):
        self.release = release
        self.disposed = False
        self.raise_on_dispose = False

    def dispose(self):
        if self.disposed:
            return
        self.disposed = True
        self.release()
        if self.raise_on_dispose:
            raise RuntimeError('CONTROLLED_CLEANUP_FAILURE')


class Registry:
    def __init__(self, scope, entry):
        self.scope = scope
        self.global_entries = {entry.name: entry}
        self.overlays = {}

    def current_scope_key(self):
        return self.scope

    def get_all_entries(self):
        return list((self.global_entries | self.overlays.get(self.scope, {})).values())

    def get_entry(self, name, *, scope=None):
        return (self.global_entries | self.overlays.get(scope or self.scope, {})).get(name)

    def snapshot_registration(self, name, *, scope=None):
        if scope is None:
            return self.global_entries.get(name)
        return self.overlays.get(scope, {}).get(name)


class RegistrationHost:
    def __init__(self, case, *, profile='coder-claude', authorize=True, mode='normal'):
        os.environ['HERMES_PROFILE'] = profile
        self.profile_name = profile
        path = case.home / '.hermes' / 'profiles' / profile
        path.mkdir(parents=True, exist_ok=True)
        self.scope = str(path)
        self._manager = types.SimpleNamespace(scope_key=self.scope)
        self.authorize = authorize
        self.mode = mode
        self.recorder = []
        self.handles = []
        self.unload_callbacks = []
        self.schema = {'name': 'kanban_request_review', 'description': 'UNIT schema',
                       'parameters': {'type': 'object', 'properties': {}}}
        self.kt = types.ModuleType('tools.kanban_tools')
        self.kt._handle_request_review = case.native
        self.kt._check_kanban_mode = lambda: True
        self.original = types.SimpleNamespace(
            name='kanban_request_review', toolset='kanban', schema=self.schema,
            handler=case.native, check_fn=self.kt._check_kanban_mode,
            requires_env=[], is_async=False, description='UNIT schema', emoji='K',
            max_result_size_chars=None, dynamic_schema_overrides=None)
        self.registry = Registry(self.scope, self.original)
        sys.modules['tools.registry'].registry = self.registry
        module_patch = patch.dict(sys.modules, {'tools.kanban_tools': self.kt})
        module_patch.start()
        case.addCleanup(module_patch.stop)

    def register_tool(self, **kwargs):
        if self.mode == 'permission_error':
            raise PermissionError('CONTROLLED_OVERRIDE_DENIED')
        if not self.authorize:
            return None
        self.recorder.append(kwargs)
        if self.mode == 'noop':
            return Handle()
        scope = self.scope if self.mode != 'wrong_scope' else self.scope + '-other'
        slot = self.registry.overlays.setdefault(scope, {})
        previous = slot.get(kwargs['name'])
        # Domyślne wartości należą do nowej rejestracji. Nie dziedziczymy
        # brakujących pól z poprzedniego wpisu (native register też tego nie robi).
        values = dict(name=kwargs['name'], toolset=kwargs['toolset'],
                      schema=kwargs['schema'], handler=kwargs['handler'],
                      check_fn=kwargs.get('check_fn'),
                      requires_env=kwargs.get('requires_env') or [],
                      is_async=kwargs.get('is_async', False),
                      description=kwargs.get('description') or kwargs['schema'].get('description', ''),
                      emoji=kwargs.get('emoji', ''), max_result_size_chars=None,
                      dynamic_schema_overrides=None)
        if self.mode == 'wrong_handler':
            values['handler'] = self.original.handler
        entry = types.SimpleNamespace(**values)
        slot[entry.name] = entry
        if self.mode == 'global_corruption':
            self.registry.global_entries[entry.name] = entry

        def release():
            if slot.get(entry.name) is entry:
                if previous is None:
                    slot.pop(entry.name, None)
                else:
                    slot[entry.name] = previous
        handle = Handle(release)
        self.handles.append(handle)
        if self.mode == 'missing_tool_handle':
            return None
        if self.mode == 'invalid_tool_handle':
            return object()
        return handle

    def on_unload(self, callback):
        if self.mode == 'unload_raises':
            raise RuntimeError('CONTROLLED_UNLOAD_REGISTRATION_FAILURE')
        if self.mode == 'unload_none':
            return None
        self.unload_callbacks.append(callback)
        handle = Handle(callback)
        self.handles.append(handle)
        return handle

    def unload(self):
        for handle in reversed(self.handles):
            handle.dispose()

    @property
    def handler(self):
        return self.recorder[-1]['handler']

    @property
    def entry(self):
        return self.registry.snapshot_registration('kanban_request_review', scope=self.scope)
