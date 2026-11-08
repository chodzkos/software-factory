"""Testy prawdziwego forwardera/loadera/rejestru; initializer providera jest atrapą.

Brak wywołań modelu. FACTORY_NATIVE_ROOT wskazuje izolowany, przypięty kandydat.
"""
from __future__ import annotations
import copy
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
PLUGIN = Path(__file__).resolve().parents[1] / 'plugins' / 'factory-execution-guards'
sys.path.insert(0, str(NATIVE))
_IMPORT_HOME = tempfile.TemporaryDirectory(prefix='sf-native-import-')
os.environ.update(HOME=_IMPORT_HOME.name, HERMES_HOME=_IMPORT_HOME.name+'/.hermes',
                  PYTHONDONTWRITEBYTECODE='1')
os.environ.pop('HERMES_PROFILE', None)
import run_agent
import factory_worker_start as boundary
import agent.agent_init
from tools.registry import registry
from hermes_cli.plugins import get_plugin_manager, discover_plugins
from model_tools import get_tool_definitions


class NativeWorkerStartTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sf-worker-start-')
        self.root = Path(self.tmp.name)
        self.home = self.root/'.hermes/profiles/coder-claude'
        self.home.mkdir(parents=True)
        self.env = patch.dict(os.environ, {
            'HOME':str(self.root), 'HERMES_HOME':str(self.home),
            'HERMES_PROFILE':'coder-claude', 'HERMES_KANBAN_TASK':'t_fixture',
            'HERMES_KANBAN_BOARD':'fixture', 'HERMES_KANBAN_RUN_ID':'7',
            'HERMES_KANBAN_WORKSPACE':str(self.root), 'TERMINAL_ENV':'local',
        })
        self.env.start()
        self.plugin = self.home/'plugins'/boundary.PLUGIN
        shutil.copytree(PLUGIN, self.plugin)
        self.config = {
            'toolsets':sorted(boundary.TOOLSETS),
            'tools':{'tool_search':{'enabled':'off'}}, 'mcp_servers':{},
            'plugins':{'enabled':[boundary.PLUGIN], 'disabled':[],
                       'entries':{boundary.PLUGIN:{'allow_tool_override':True}}},
            'factory':{'execution_backend':'claude-code', 'claude_model_class':'sonnet'},
        }
        self.save_config()
        self.called = 0
        self.transform = lambda a: None
        self.init_patch = patch.object(agent.agent_init, 'init_agent', self.initializer)
        self.init_patch.start()

    def tearDown(self):
        self.init_patch.stop()
        manager=get_plugin_manager()
        for name in list(manager._plugins):
            manager.unload(name)
        self.env.stop()
        self.tmp.cleanup()

    def save_config(self):
        (self.home/'config.yaml').write_text(json.dumps(self.config), encoding='utf-8')

    def initializer(self, instance, **kwargs):
        """Celowo nie tworzy klienta API, jedynie rzeczywiste definicje narzędzi."""
        self.called += 1
        instance.tools = get_tool_definitions(enabled_toolsets=kwargs['enabled_toolsets'],
                                             disabled_toolsets=kwargs['disabled_toolsets'],
                                             quiet_mode=True)
        instance.valid_tool_names = {x['function']['name'] for x in instance.tools}
        self.transform(instance)

    def construct(self, **kwargs):
        return run_agent.AIAgent(enabled_toolsets=kwargs.pop('enabled_toolsets',sorted(boundary.TOOLSETS)),
                                 **kwargs)

    def before(self):
        return boundary.before_agent_init(sorted(boundary.TOOLSETS),None)

    def assert_early_refusal(self, pattern='FACTORY_WORKER_START_REFUSED', **kwargs):
        with self.assertRaisesRegex(boundary.WorkerStartError, pattern):
            self.construct(**kwargs)
        self.assertEqual(self.called,0)

    def assert_late_refusal(self, pattern='FACTORY_WORKER_START_REFUSED'):
        with self.assertRaisesRegex(boundary.WorkerStartError, pattern):
            self.construct()
        self.assertEqual(self.called,1)

    def test_valid_native_forwarder_loader_and_exact_eight_tools(self):
        original=registry.snapshot_registration('kanban_request_review')
        result=self.construct()
        self.assertEqual(self.called,1)
        self.assertEqual(result.valid_tool_names,boundary.MODEL_TOOLS)
        self.assertEqual({t['function']['name'] for t in result.tools},boundary.MODEL_TOOLS)
        self.assertEqual(len(result.tools),8)
        self.assertIs(registry.snapshot_registration('kanban_request_review'),original)
        self.before().verify()

    def test_missing_plugin_fails_before_initializer(self):
        shutil.rmtree(self.plugin)
        self.assert_early_refusal()

    def test_modified_plugin_fails_before_initializer(self):
        (self.plugin/'__init__.py').write_text('raise RuntimeError("import fails")\n')
        self.assert_early_refusal('source pin mismatch')

    def test_extra_plugin_file_fails_before_initializer(self):
        (self.plugin/'injected.py').write_text('')
        self.assert_early_refusal('file set changed')

    def test_plugin_symlink_fails_before_initializer(self):
        path=self.plugin/'injected.py';path.symlink_to(self.plugin/'guard.py')
        self.assert_early_refusal('symlink')

    def test_override_disabled_fails_before_initializer(self):
        self.config['plugins']['entries'][boundary.PLUGIN]['allow_tool_override']=False
        self.save_config();self.assert_early_refusal('configuration mismatch')

    def test_override_integer_is_not_boolean_permission(self):
        self.config['plugins']['entries'][boundary.PLUGIN]['allow_tool_override']=1
        self.save_config();self.assert_early_refusal('configuration mismatch')

    def test_plugin_disabled_fails_before_initializer(self):
        self.config['plugins']['disabled']=[boundary.PLUGIN]
        self.save_config();self.assert_early_refusal()

    def test_additional_configured_plugin_fails_before_initializer(self):
        self.config['plugins']['enabled'].append('future-plugin')
        self.save_config();self.assert_early_refusal()

    def test_mcp_configuration_fails_before_initializer(self):
        self.config['mcp_servers']={'untrusted':{'command':'no-run'}}
        self.save_config();self.assert_early_refusal('discovery')

    def test_tool_search_configuration_fails_before_initializer(self):
        self.config['tools']['tool_search']['enabled']='auto'
        self.save_config();self.assert_early_refusal('discovery')

    def test_wrong_backend_fails_before_initializer(self):
        self.config['factory']['execution_backend']='direct'
        self.save_config();self.assert_early_refusal('backend')

    def test_missing_explicit_toolsets_fails_before_initializer(self):
        self.assert_early_refusal('toolset',enabled_toolsets=None)

    def test_duplicate_toolsets_fails_before_initializer(self):
        self.assert_early_refusal('toolset',enabled_toolsets=sorted(boundary.TOOLSETS)+['kanban'])

    def test_additional_skills_toolset_fails_before_initializer(self):
        self.assert_early_refusal('toolset',enabled_toolsets=sorted(boundary.TOOLSETS)+['skills'])

    def test_nonempty_disabled_toolsets_fails_before_initializer(self):
        self.assert_early_refusal('toolset',disabled_toolsets=['file'])

    def test_actual_profile_catches_missing_env_selector(self):
        os.environ.pop('HERMES_PROFILE')
        self.assert_early_refusal('profile identity')

    def test_actual_profile_catches_forged_env_selector(self):
        os.environ['HERMES_PROFILE']='general'
        self.assert_early_refusal('profile identity')

    def test_env_coder_with_other_home_fails_before_initializer(self):
        other=self.root/'.hermes/profiles/general';other.mkdir()
        os.environ['HERMES_HOME']=str(other)
        self.assert_early_refusal('profile identity')

    def test_missing_pre_hook_is_rejected_before_initializer(self):
        proof=self.before();proof.manager._hooks['pre_tool_call'].clear()
        self.assert_early_refusal('required hook')

    def test_duplicate_post_hook_is_rejected_before_initializer(self):
        proof=self.before();proof.manager._hooks['post_tool_call'].append(proof.module.on_post_tool_call)
        self.assert_early_refusal('required hook')

    def test_changed_pre_hook_is_rejected_before_initializer(self):
        proof=self.before();proof.manager._hooks['pre_tool_call'][:]=[lambda *a,**k:None]
        self.assert_early_refusal('required hook')

    def test_disposed_hook_handle_is_rejected_before_initializer(self):
        proof=self.before()
        for r in proof.manager._ownership_ledger[boundary.PLUGIN]:
            if r.kind=='hook' and r.key=='pre_tool_call':r.dispose()
        self.assert_early_refusal()

    def test_failed_loader_record_is_rejected_before_initializer(self):
        proof=self.before();proof.loaded.error='simulated loader failure'
        self.assert_early_refusal('did not load')

    def test_unload_during_initializer_blocks_return(self):
        self.transform=lambda a:get_plugin_manager().unload(boundary.PLUGIN)
        self.assert_late_refusal('unavailable or replaced')

    def test_hook_loss_during_initializer_blocks_return(self):
        self.transform=lambda a:get_plugin_manager()._hooks['pre_tool_call'].clear()
        self.assert_late_refusal('required hook')

    def test_source_change_during_initializer_blocks_return(self):
        self.transform=lambda a:(self.plugin/'guard.py').write_text('# replaced\n')
        self.assert_late_refusal('source pin mismatch')

    def test_duplicate_raw_schema_blocks_return(self):
        def transform(a):a.tools.append(copy.deepcopy(a.tools[0]))
        self.transform=transform;self.assert_late_refusal('raw native')

    def test_unknown_raw_schema_blocks_return(self):
        def transform(a):
            item=copy.deepcopy(a.tools[0]);item['function']['name']='future_unsafe'
            a.tools.append(item);a.valid_tool_names.add('future_unsafe')
        self.transform=transform;self.assert_late_refusal('raw native')

    def test_missing_raw_schema_blocks_return(self):
        def transform(a):a.valid_tool_names.remove(a.tools.pop()['function']['name'])
        self.transform=transform;self.assert_late_refusal('raw native')

    def test_schema_drift_blocks_return(self):
        def transform(a):a.tools[0]['function']['description']='mutated definition'
        self.transform=transform;self.assert_late_refusal('schema mismatch')

    def test_name_definition_mismatch_blocks_return(self):
        self.transform=lambda a:a.valid_tool_names.clear()
        self.assert_late_refusal('names/definitions')

    def test_proof_rejects_pid_drift(self):
        proof=self.before()
        with patch.object(boundary.os,'getpid',return_value=proof.pid+1):
            with self.assertRaisesRegex(boundary.WorkerStartError,'identity changed'):proof.verify()

    def test_real_unload_rejects_old_proof_and_reload_replaces_it(self):
        proof=self.before();proof.manager.unload(boundary.PLUGIN)
        with self.assertRaises(boundary.WorkerStartError):proof.verify()
        discover_plugins(force=True)
        newer=self.before();self.assertIsNot(newer.loaded,proof.loaded)
        newer.verify()
        with self.assertRaisesRegex(boundary.WorkerStartError,'replaced'):proof.verify()

    def test_real_scoped_native_override_is_rejected(self):
        proof=self.before();entry=registry.snapshot_registration('terminal')
        registry.register('terminal',entry.toolset,entry.schema,lambda *a,**k:'unsafe',
                          scope=str(self.home))
        try:
            with self.assertRaisesRegex(boundary.WorkerStartError,'native tool identity'):proof.verify()
        finally:
            registry.restore_registration('terminal',registry.snapshot_registration('terminal',scope=str(self.home)),None,scope=str(self.home))

    def test_real_loader_import_exception_fails_before_initializer(self):
        manager=get_plugin_manager()
        original=manager._load_directory_module
        def injected(manifest, **kwargs):
            if manifest.name==boundary.PLUGIN:
                raise ImportError('deliberate guard import failure')
            return original(manifest,**kwargs)
        with patch.object(manager,'_load_directory_module',side_effect=injected):
            self.assert_early_refusal('did not load')
        self.assertFalse(manager._plugins[boundary.PLUGIN].enabled)
        self.assertIn('deliberate',manager._plugins[boundary.PLUGIN].error)
        self.assertFalse(manager._hooks.get('pre_tool_call'))
        self.assertIsNone(registry.snapshot_registration('kanban_request_review',scope=str(self.home)))

    def test_real_loader_permission_failure_is_not_a_ready_plugin(self):
        self.config['plugins']['entries'][boundary.PLUGIN]['allow_tool_override']=False
        self.save_config()
        discover_plugins()
        loaded=get_plugin_manager()._plugins[boundary.PLUGIN]
        self.assertFalse(loaded.enabled)
        self.assertTrue(loaded.error)
        self.config['plugins']['entries'][boundary.PLUGIN]['allow_tool_override']=True
        self.save_config()
        self.assert_early_refusal('did not load')

    def test_plugin_bytecode_cache_is_refused(self):
        cache=self.plugin/'__pycache__';cache.mkdir()
        (cache/'guard.cpython-313.pyc').write_bytes(b'not-authorized-code')
        self.assert_early_refusal('file set changed')

    def test_complete_native_constructor_bedrock_branch_without_provider_calls(self):
        import socket
        self.config.update(model={'context_length':131072}, compression={'enabled':False},agent={'environment_probe':False})
        self.save_config()
        self.init_patch.stop()
        result=None
        try:
            with patch.object(socket.socket,'connect',side_effect=AssertionError('network forbidden')) as network:
                result=self.construct(provider='bedrock',api_mode='bedrock_converse',model='local-fixture',
                    api_key='test-not-a-real-key',base_url='http://127.0.0.1:9/v1',
                    skip_memory=True,skip_context_files=True,skip_background_review=True,
                    quiet_mode=True,platform='cli',max_iterations=1)
            self.assertEqual(result.valid_tool_names,boundary.MODEL_TOOLS)
            self.assertEqual(len(result.tools),8)
            self.assertEqual(self.called,0)
            network.assert_not_called()
        finally:
            if result is not None:result.close()
            self.init_patch.start()

    def test_other_profile_is_not_given_coder_policy(self):
        other=self.root/'.hermes/profiles/general';other.mkdir()
        with patch.dict(os.environ,{'HERMES_HOME':str(other),'HERMES_PROFILE':'general'}):
            with patch.object(agent.agent_init,'init_agent',lambda a,**k:setattr(a,'marker','unchanged')):
                obj=run_agent.AIAgent()
        self.assertEqual(obj.marker,'unchanged')


if __name__=='__main__':unittest.main(verbosity=2)
