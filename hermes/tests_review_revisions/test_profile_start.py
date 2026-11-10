"""D-05: rzeczywisty forwarder i loader, bez klienta providera ani instalacji."""
from __future__ import annotations
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT'])
# Import natywnego runtime poprzedzony pustym, tymczasowym home jak w start40.
_spec = importlib.util.spec_from_file_location('profile_start_fixture', ROOT/'hermes/tests_worker_start/test_native_worker_start.py')
fx = importlib.util.module_from_spec(_spec); sys.modules[_spec.name] = fx; _spec.loader.exec_module(fx)
PROFILES = ('reviewer-gpt', 'release-manager')
GUARD = 'factory-execution-guards'
READONLY = 'factory-repository-readonly'
DISABLED = {'terminal','file','code_execution','web','browser','image_gen','delegation','computer_use','cronjob','skills','vision','todo','memory','session_search','clarify','messaging','tts','moa','bfl','x_search','mcp'}

class ProfileStartTests(unittest.TestCase):
    @contextmanager
    def profile(self, name, *, real_catalog=False):
        with tempfile.TemporaryDirectory(prefix='sf-profile-gate-') as raw:
            root = Path(raw)
            home = root/'.hermes'/('profiles' if name=='reviewer-gpt' else 'factory-profiles')/name
            home.mkdir(parents=True)
            for plugin in (GUARD, READONLY):
                shutil.copytree(ROOT/'hermes/plugins'/plugin, home/'plugins'/plugin)
            env = dict(HOME=str(root), HERMES_HOME=str(home), HERMES_PROFILE=name,
                       TERMINAL_ENV='local', FACTORY_RELEASE_TASK='t_fixture',
                       FACTORY_RELEASE_WORKSPACE=str(root), PYTHONDONTWRITEBYTECODE='1')
            if name == 'reviewer-gpt':
                env.update(HERMES_KANBAN_TASK='t_fixture',HERMES_KANBAN_BOARD='fixture',
                           HERMES_KANBAN_RUN_ID='7',HERMES_KANBAN_WORKSPACE=str(root))
            config = {
                'plugins':{'enabled':[GUARD,READONLY], 'disabled':[],
                           'entries':{GUARD:{'allow_tool_override':True}, READONLY:{'allow_tool_override':False}}},
                'mcp_servers':{}, 'tools':{'tool_search':{'enabled':'off'}},
                'toolsets':[GUARD,READONLY],
                'agent':{'disabled_toolsets':sorted(DISABLED)},
                'factory':{'execution_backend':'native-openai' if name=='reviewer-gpt' else 'read-only-release-decision'},
            }
            (home/'config.yaml').write_text(json.dumps(config))
            self.home, self.config, self.called = home, config, 0
            self.transform = lambda a: None
            def initializer(instance, **kw):
                self.called += 1
                instance.tools = fx.get_tool_definitions(enabled_toolsets=kw['enabled_toolsets'], disabled_toolsets=kw['disabled_toolsets'], quiet_mode=True) if real_catalog else []
                instance.valid_tool_names = {x['function']['name'] for x in instance.tools}
                self.transform(instance)
            with patch.dict(os.environ, env), patch.object(fx.agent.agent_init, 'init_agent', initializer):
                if name == 'release-manager':
                    for key in list(os.environ):
                        if key.startswith('HERMES_KANBAN_'): del os.environ[key]
                try:
                    yield
                finally:
                    manager=fx.get_plugin_manager()
                    for plugin in list(manager._plugins): manager.unload(plugin)

    def construct(self):
        return fx.run_agent.AIAgent(enabled_toolsets=[GUARD,READONLY,'kanban'],disabled_toolsets=sorted(DISABLED))

    def refuse_early(self):
        (self.home/'config.yaml').write_text(json.dumps(self.config))
        with self.assertRaisesRegex(RuntimeError,'FACTORY_.*START_REFUSED'):
            self.construct()
        self.assertEqual(self.called,0,'native provider initializer must not run')

    def test_d05_unapproved_plugin_refused_before_initializer(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                self.config['plugins']['enabled'].append('future-unreviewed')
                self.refuse_early()

    def test_d05_mcp_refused_before_initializer(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                self.config['mcp_servers']={'untrusted':{'command':'NEVER_EXECUTE'}}
                self.refuse_early()

    def test_d05_readonly_override_permission_refused(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                self.config['plugins']['entries'][READONLY]['allow_tool_override']=True
                self.refuse_early()

    def test_d05_missing_guard_refused_before_initializer(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                shutil.rmtree(self.home/'plugins'/GUARD)
                self.refuse_early()

    def test_d05_changed_readonly_source_refused_before_import(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                marker=self.home/'executed'
                (self.home/'plugins'/READONLY/'__init__.py').write_text(f'from pathlib import Path\nPath({str(marker)!r}).touch()\n')
                self.refuse_early()
                self.assertFalse(marker.exists())

    def test_d05_missing_disabled_boundary_refused(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                self.config['agent']['disabled_toolsets'].remove('terminal')
                self.refuse_early()

    def test_d05_valid_exact_catalog_and_raw_complete_refusal(self):
        from tools import kanban_tools
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                obj=self.construct()
                from factory_profile_start import MODEL_TOOLS
                self.assertEqual(obj.valid_tool_names,MODEL_TOOLS[name])
                self.assertEqual(self.called,1)
                self.assertEqual(len(obj.tools),6 if name=='reviewer-gpt' else 3)
                with patch.object(kanban_tools,'_handle_complete',side_effect=AssertionError('NATIVE_COMPLETE_FORBIDDEN')) as complete:
                    from factory_worker_lifetime import WorkerLifetimeError
                    # With lifetime bound, raw dispatch now raises before registry
                    # normalization rather than returning a disabled-tool error.
                    with self.assertRaises(WorkerLifetimeError):
                        fx.registry.dispatch('kanban_complete',{'summary':'NO'})
                complete.assert_not_called()
                entry=fx.registry.get_entry('kanban_complete')
                self.assertIs(entry.check_fn(),False)

    def test_d05_source_drift_during_initializer_refuses(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                self.transform=lambda a:(self.home/'plugins'/READONLY/'repo_map.py').write_text('# modified\n')
                with self.assertRaisesRegex(RuntimeError,'START_REFUSED'):
                    self.construct()
                self.assertEqual(self.called,1)

    def test_d05_unload_during_initializer_refuses(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                self.transform=lambda a:fx.get_plugin_manager().unload(GUARD)
                with self.assertRaisesRegex(RuntimeError,'START_REFUSED'):
                    self.construct()
                self.assertEqual(self.called,1)

    def test_d05_added_model_tool_is_not_silently_trimmed(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                def add(a):
                    a.tools.append({'type':'function','function':{'name':'unapproved','parameters':{}}})
                    a.valid_tool_names.add('unapproved')
                self.transform=add
                with self.assertRaisesRegex(RuntimeError,'capability surface'):
                    self.construct()
                self.assertEqual(self.called,1)

    def test_d05_model_schema_drift_refused(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                self.transform=lambda a:a.tools[0]['function'].update(description='changed')
                with self.assertRaisesRegex(RuntimeError,'schema mismatch'):
                    self.construct()
                self.assertEqual(self.called,1)

    def test_d05_false_profile_selector_does_not_bypass(self):
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name):
                os.environ['HERMES_PROFILE']='general'
                self.refuse_early()

    def test_d05_complete_initializers_without_provider_or_claude(self):
        import socket
        original=__import__('agent.agent_init',fromlist=['init_agent']).init_agent
        for name in PROFILES:
            with self.subTest(profile=name), self.profile(name,real_catalog=True):
                self.config.update(model={'context_length':131072}, compression={'enabled':False})
                self.config['agent']['environment_probe']=False
                (self.home/'config.yaml').write_text(json.dumps(self.config))
                obj=None
                with patch.object(fx.agent.agent_init,'init_agent',original), patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN')) as network:
                    try:
                        obj=fx.run_agent.AIAgent(enabled_toolsets=[GUARD,READONLY,'kanban'],disabled_toolsets=sorted(DISABLED),
                            provider='bedrock',api_mode='bedrock_converse',model='local-fixture',
                            api_key='test-not-real',base_url='http://127.0.0.1:9/v1',skip_memory=True,
                            skip_context_files=True,skip_background_review=True,quiet_mode=True,platform='cli',max_iterations=1)
                        from factory_profile_start import MODEL_TOOLS
                        self.assertEqual(obj.valid_tool_names,MODEL_TOOLS[name])
                        self.assertEqual(self.called,0)
                        network.assert_not_called()
                    finally:
                        if obj is not None:obj.close()
