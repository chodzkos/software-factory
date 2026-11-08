"""Regresje recenzji 0.13.0: bez providera, na kopiach źródeł i lokalnym workerze."""
from __future__ import annotations
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
spec = importlib.util.spec_from_file_location('review_native_lifetime_fixture', ROOT/'hermes/tests_worker_lifetime/test_worker_lifetime.py')
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
spec.loader.exec_module(fixture)

class ReviewRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixture.NativeLifecycleTests('test_actual_read_and_required_hooks_exactly_once')
        self.fx.setUp()
        self.agent, self.life, self.state = self.fx.agent, self.fx.life, self.fx.state

    def tearDown(self):
        self.fx.tearDown()

    def _chat(self):
        self.agent.api_mode = 'chat_completions'
        self.agent.provider = 'openai'
        return {'model': self.agent.model, 'messages': [], 'tools': copy.deepcopy(self.agent.tools)}

    def test_d02_builder_cannot_authorize_ninth_tool(self):
        payload = self._chat()
        payload['tools'].append({'type':'function','function':{'name':'unapproved_ninth','description':'not authorized','parameters':{'type':'object'}}})
        before = set(self.state.payloads)
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent, payload)
        self.assertEqual(self.state.payloads, before)
        self.assertTrue(self.state.revoked.is_set())

    def test_d02_builder_cannot_authorize_changed_schema(self):
        payload = self._chat()
        payload['tools'][0]['function']['parameters'] = {'type':'object'}
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent, payload)

    def test_d02_extra_body_cannot_hide_tools(self):
        payload = self._chat()
        payload['extra_body'] = {'tools':[{'type':'web_search'}]}
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent, payload)

    def test_d02_registered_payload_cannot_gain_extension_tools(self):
        payload = self._chat()
        self.life.record_model_payload(self.agent, payload)
        payload['extra_body'] = {'tools':[{'type':'web_search'}]}
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.check_model_payload(self.agent, payload)

    def test_d02_unknown_provider_extension_refused(self):
        payload = self._chat()
        payload['extra_body'] = {'future_execution_backend':{'enabled':True}}
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent, payload)

    def test_d02_original_chat_surface_is_accepted(self):
        payload = self._chat()
        self.assertIs(self.life.record_model_payload(self.agent,payload),payload)
        self.life.check_model_payload(self.agent,payload)

    def test_d02_real_bedrock_builder_is_accepted(self):
        payload = self.agent._build_api_kwargs([])
        self.assertIn('toolConfig', payload)
        self.life.check_model_payload(self.agent,payload)

    def test_d03_revoked_summary_never_calls_client(self):
        import agent.chat_completion_helpers as helpers
        self._chat()
        client = Mock()
        transport = Mock()
        transport.normalize_response.return_value = types.SimpleNamespace(content='UNAUTHORIZED SUMMARY')
        self.life.abandon_worker(self.agent,'review fixture abandonment')
        with patch.object(self.agent,'_ensure_primary_openai_client',return_value=client), patch.object(self.agent,'_get_transport',return_value=transport):
            with self.assertRaises(self.life.WorkerLifetimeError):
                helpers.handle_max_iterations(self.agent, [], 1)
        client.chat.completions.create.assert_not_called()

    def test_l06_module_hook_uses_same_protected_scope(self):
        from hermes_cli.plugins import invoke_hook, _delivery_manager
        self.assertIs(_delivery_manager(),self.state.proof.manager)
        with patch.object(self.fx.guard,'on_pre_tool_call') as pre, patch.object(self.fx.guard,'on_post_tool_call') as post:
            self.assertEqual(invoke_hook('pre_tool_call',tool_name='read_file',args={}),[])
            self.assertEqual(invoke_hook('post_tool_call',tool_name='read_file',args={},result='FORGED'),[])
            pre.assert_not_called();post.assert_not_called()

    def test_c02_plugin_alone_refuses_skill(self):
        result = self.fx.guard.on_pre_tool_call(tool_name='skill',args={'name':'not-authorized'})
        self.assertIsInstance(result,dict)
        self.assertEqual(result.get('action'),'block')

    def test_c02_skill_skip_flags_never_reach_dispatch(self):
        with patch.object(fixture.start.run_agent,'handle_function_call') as handler:
            with self.assertRaises(self.life.WorkerLifetimeError):
                self.agent._invoke_tool('skill', {'name':'not-authorized'}, 't_fixture',
                    pre_tool_block_checked=True,skip_tool_request_middleware=True,skip_tool_execution_middleware=True)
            handler.assert_not_called()

    def _prefix(self, name, mode=0o700):
        path = self.fx.fixture.root / name; path.mkdir(mode=mode); path.chmod(mode); return str(path)

    def _first_check(self):
        # The emptiness rule applies to the first check of a process; reset that state for this case only.
        return patch.object(fixture.start.boundary, '_BYTECODE_PREFIX', None)

    def test_l07_later_compiled_cache_entries_do_not_refuse_same_prefix(self):
        own = self._prefix('own-cache')
        with self._first_check(),patch.object(sys,'pycache_prefix',own),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':own}):
            self.fx.fixture.before()
            (Path(own)/'linted.cpython-311.pyc').write_bytes(b'compiled-from-source-by-this-process')
            self.fx.fixture.before()

    def test_l07_prefix_swap_after_start_refused(self):
        first = self._prefix('first-cache'); second = self._prefix('second-cache')
        with self._first_check(),patch.object(sys,'pycache_prefix',first),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':first}):
            self.fx.fixture.before()
            with patch.object(sys,'pycache_prefix',second),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':second}):
                with self.assertRaisesRegex(fixture.start.boundary.WorkerStartError,'bytecode cache prefix changed'):
                    self.fx.fixture.before()

    def test_l07_missing_cache_prefix_refused(self):
        with patch.object(sys,'pycache_prefix',None),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':''}):
            with self.assertRaisesRegex(fixture.start.boundary.WorkerStartError,'private bytecode cache prefix required'):
                self.fx.fixture.before()

    def test_l07_mismatched_cache_prefix_refused(self):
        with patch.object(sys,'pycache_prefix',self._prefix('runtime-cache')),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':self._prefix('external-cache')}):
            with self.assertRaisesRegex(fixture.start.boundary.WorkerStartError,'private bytecode cache prefix required'):
                self.fx.fixture.before()

    def test_l07_nonempty_shared_cache_prefix_refused(self):
        shared = self._prefix('shared-cache'); (Path(shared)/'planted.cpython-311.pyc').write_bytes(b'stale')
        with self._first_check(),patch.object(sys,'pycache_prefix',shared),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':shared}):
            with self.assertRaisesRegex(fixture.start.boundary.WorkerStartError,'bytecode cache prefix not empty'):
                self.fx.fixture.before()

    def test_l07_group_accessible_cache_prefix_refused(self):
        loose = self._prefix('loose-cache', 0o750)
        with patch.object(sys,'pycache_prefix',loose),patch.dict(os.environ,{'PYTHONPYCACHEPREFIX':loose}):
            with self.assertRaisesRegex(fixture.start.boundary.WorkerStartError,'bytecode cache prefix not private'):
                self.fx.fixture.before()

    def _payload(self, mode):
        """Rzeczywiste konwertery natywne, sztuczne klient/model bez sieci."""
        self.agent.api_mode = mode
        self.agent.provider = 'openai' if mode != 'anthropic_messages' else 'anthropic'
        self.agent._is_anthropic_oauth = False
        if mode == 'chat_completions':
            return self._chat()
        if mode == 'anthropic_messages':
            from agent.anthropic_adapter import convert_tools_to_anthropic
            return {'model':self.agent.model,'messages':[],'max_tokens':64,
                    'tools':convert_tools_to_anthropic(copy.deepcopy(self.agent.tools))}
        if mode == 'codex_responses':
            from agent.codex_responses_adapter import _responses_tools
            return {'model':self.agent.model,'input':[],
                    'tools':_responses_tools(copy.deepcopy(self.agent.tools))}
        raise AssertionError(mode)

    def test_real_anthropic_conversion_retains_exact_definitions(self):
        payload=self._payload('anthropic_messages')
        self.life.record_model_payload(self.agent,payload)
        self.life.check_model_payload(self.agent,payload)

    def test_real_responses_conversion_retains_exact_definitions(self):
        payload=self._payload('codex_responses')
        self.life.record_model_payload(self.agent,payload)
        self.life.check_model_payload(self.agent,payload)

    def test_builder_cannot_widen_explicit_subset(self):
        payload=self._chat()
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent,payload,expected_tools=self.agent.tools[:1])

    def test_registered_payload_model_change_refused(self):
        payload=self._chat();self.life.record_model_payload(self.agent,payload)
        payload['model']='changed-model'
        with self.assertRaises(self.life.WorkerLifetimeError):self.life.check_model_payload(self.agent,payload)

    def test_registered_payload_old_provider_route_refused(self):
        payload=self._chat();self.life.record_model_payload(self.agent,payload)
        self.agent.provider='other-provider'
        with self.assertRaises(self.life.WorkerLifetimeError):self.life.check_model_payload(self.agent,payload)

    def test_new_route_requires_a_fresh_validated_payload(self):
        payload=self._chat();self.life.record_model_payload(self.agent,payload)
        self.agent.provider='other-provider'
        self.life.record_model_payload(self.agent,payload)
        self.life.check_model_payload(self.agent,payload)
        self.assertEqual(len(self.state.payloads),2)

    def test_anthropic_final_sanitizer_injection_refused_before_sdk(self):
        import agent.anthropic_adapter as adapter
        payload=self._payload('anthropic_messages');self.life.record_model_payload(self.agent,payload)
        client=Mock()
        def mutate(kw, **unused):
            kw['tools'].append({'type':'computer_20250124','name':'unapproved_computer'})
            return kw
        with patch.object(adapter,'sanitize_anthropic_kwargs',side_effect=mutate):
            with self.assertRaises(self.life.WorkerLifetimeError):
                self.agent._anthropic_messages_create(payload,client=client)
        client.messages.stream.assert_not_called();client.messages.create.assert_not_called()

    def test_anthropic_raw_auxiliary_call_without_worker_proof_refused(self):
        import agent.anthropic_adapter as adapter
        payload=self._payload('anthropic_messages');client=Mock()
        with self.assertRaises(self.life.WorkerLifetimeError):
            adapter.create_anthropic_message(client,payload,prefer_stream=False)
        client.messages.create.assert_not_called()

    def test_anthropic_valid_create_reaches_actual_final_boundary(self):
        payload=self._payload('anthropic_messages');self.life.record_model_payload(self.agent,payload)
        client=Mock();client.messages.create.return_value=types.SimpleNamespace(content='fixture')
        self.agent._disable_streaming=True
        result=self.agent._anthropic_messages_create(payload,client=client)
        self.assertIs(result,client.messages.create.return_value)
        final=client.messages.create.call_args.kwargs
        self.assertIn(self.state.envelope(final),self.state.payloads)
        client.messages.create.assert_called_once();client.messages.stream.assert_not_called()

    def test_summary_valid_request_has_zero_tools_and_known_envelope(self):
        import agent.chat_completion_helpers as helpers
        self._chat();client=Mock();transport=Mock()
        transport.normalize_response.return_value=types.SimpleNamespace(content='OFFLINE SUMMARY')
        self.agent.reasoning_config=None
        observed=[]
        def response(**kw):
            observed.append(self.state.envelope(kw) in self.state.payloads)
            self.assertFalse(kw.get('tools'))
            return types.SimpleNamespace(content='fixture')
        client.chat.completions.create.side_effect=response
        with patch.object(self.agent,'_ensure_primary_openai_client',return_value=client), \
                patch.object(self.agent,'_get_transport',return_value=transport), \
                patch.object(self.agent,'_supports_reasoning_extra_body',return_value=False):
            text=helpers.handle_max_iterations(self.agent,[],1)
        self.assertEqual(text,'OFFLINE SUMMARY');self.assertEqual(observed,[True])

    def test_summary_close_during_response_cannot_return_normal_success(self):
        import agent.chat_completion_helpers as helpers
        self._chat();client=Mock();transport=Mock()
        transport.normalize_response.return_value=types.SimpleNamespace(content='MUST NOT RETURN')
        def response(**kw):
            self.life.close_worker(self.agent)
            return types.SimpleNamespace(content='fixture')
        client.chat.completions.create.side_effect=response
        with patch.object(self.agent,'_ensure_primary_openai_client',return_value=client), \
                patch.object(self.agent,'_get_transport',return_value=transport), \
                patch.object(self.agent,'_supports_reasoning_extra_body',return_value=False):
            with self.assertRaises(self.life.WorkerLifetimeError):helpers.handle_max_iterations(self.agent,[],1)
        client.chat.completions.create.assert_called_once()

    def _stream_boundary(self,mode,*,mutate):
        """Pełne wejście natywne; syntetyczne middleware wywołuje realny callback SDK.

        Nie jest to test sieci/odbioru SSE. Dodatni probe kończy się świadomie po
        wywołaniu sztucznego klienta, ujemny musi odmówić zanim klient będzie użyty.
        """
        from agent import relay_llm,chat_completion_helpers as helpers,codex_runtime
        payload=self._payload(mode);self.life.record_model_payload(self.agent,payload)
        import threading
        from hermes.tests_review_revisions.sdk_fixture import http_error_types
        client=MagicMock();seen=[];thread_errors=[]
        class ProbeComplete(RuntimeError):pass
        def stream(request,callback,**kwargs):
            final=copy.deepcopy(request)
            seen.append('middleware')
            if mutate:final['extra_body']={'tools':[{'type':'unapproved_server_tool'}]}
            callback(final)
            raise ProbeComplete()
        with http_error_types(), patch.object(threading,'excepthook',side_effect=thread_errors.append), \
                patch.object(relay_llm,'stream',side_effect=stream), \
                patch.object(self.agent,'_create_request_openai_client',return_value=client), \
                patch.object(self.agent,'_create_request_anthropic_client',return_value=client):
            exception=self.life.WorkerLifetimeError if mutate else ProbeComplete
            with self.assertRaises(exception):
                if mode=='codex_responses':codex_runtime.run_codex_stream(self.agent,payload,client=client)
                else:helpers.interruptible_streaming_api_call(self.agent,payload)
        self.assertEqual(thread_errors, [], 'błąd wątku nie może udawać ukończenia próby')
        self.assertEqual(seen,['middleware'])
        create=(client.messages.stream if mode=='anthropic_messages' else
                client.responses.create if mode=='codex_responses' else client.chat.completions.create)
        if mutate:
            create.assert_not_called();self.assertTrue(self.state.revoked.is_set())
        else:
            create.assert_called_once()
            self.assertIn(self.state.envelope(create.call_args.kwargs),self.state.payloads)

    def test_stream_chat_post_middleware_injection_refused(self):self._stream_boundary('chat_completions',mutate=True)
    def test_stream_responses_post_middleware_injection_refused(self):self._stream_boundary('codex_responses',mutate=True)
    def test_stream_anthropic_post_middleware_injection_refused(self):self._stream_boundary('anthropic_messages',mutate=True)
    def test_stream_chat_final_known_payload_reaches_sdk(self):self._stream_boundary('chat_completions',mutate=False)
    def test_stream_responses_final_known_payload_reaches_sdk(self):self._stream_boundary('codex_responses',mutate=False)
    def test_stream_anthropic_final_known_payload_reaches_sdk(self):self._stream_boundary('anthropic_messages',mutate=False)

class SourceContractTests(unittest.TestCase):
    def test_c01_guard_without_entrypoint_uses_single_hasher(self):
        package = types.ModuleType('_review_guard_without_entrypoint')
        plugin = ROOT/'hermes/plugins/factory-execution-guards'
        package.__path__=[str(plugin)]
        sys.modules[package.__name__]=package
        try:
            import importlib
            guard=importlib.import_module(package.__name__+'.guard')
            handoff=importlib.import_module(package.__name__+'.handoff')
            self.assertIs(guard._workspace_content_state,handoff.workspace_content_state)
        finally:
            for n in list(sys.modules):
                if n==package.__name__ or n.startswith(package.__name__+'.'):sys.modules.pop(n,None)

    def test_s12_no_executable_test_approval_hook(self):
        source=(ROOT/'hermes/plugins/factory-execution-guards/handoff.py').read_text()
        self.assertNotIn('_TEST_APPROVAL_HOOK',source)

    def test_d01_config_participates_in_native_pinset(self):
        self.assertIn('hermes_cli/config.py',fixture.start.boundary.SOURCE_PINS['native'])

    def test_d01_bad_profiles_source_refused_before_import_effect(self):
        """Wyłącznie granica before_agent_init; nie twierdzi, że chroni import run_agent."""
        boundary=fixture.start.boundary
        with tempfile.TemporaryDirectory() as td:
            root=Path(td); native=root/'native'; native.mkdir()
            home=root/'.hermes/profiles/coder-claude'
            shutil.copytree(ROOT/'hermes/plugins/factory-execution-guards',home/'plugins'/boundary.PLUGIN)
            for name in boundary.SOURCE_PINS['native']:
                target=native/name; target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(NATIVE/name,target)
            shutil.copy2(NATIVE/'factory_worker_start.py',native/'factory_worker_start.py')
            (native/'hermes_cli/__init__.py').write_text('')
            marker=root/'UNVERIFIED_IMPORT_EFFECT'
            (native/'hermes_cli/profiles.py').write_text(
                'from pathlib import Path\nPath('+repr(str(marker))+').write_text("EFFECT")\n'
                'def get_active_profile_name(): return "coder-claude"\n')
            code='''import sys,os
sys.path.insert(0,sys.argv[1])
import factory_worker_start as boundary
try:
 boundary.before_agent_init(sorted(boundary.TOOLSETS),None)
except boundary.WorkerStartError:
 print('REFUSED')
else:
 raise AssertionError('unexpected successful start')
'''
            env={'HOME':str(root),'HERMES_HOME':str(home),'HERMES_PROFILE':'coder-claude','PATH':os.defpath}
            proc=subprocess.run([sys.executable,'-I','-B','-c',code,str(native)],env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(proc.returncode,0,proc.stderr)
            self.assertIn('REFUSED',proc.stdout)
            self.assertFalse(marker.exists(),'odmowa nastąpiła dopiero PO wykonaniu podmienionego profiles.py')

    def test_d04_ci_has_manifest_consumer_before_apply(self):
        path=ROOT/'hermes/ci/verify_native_overlay.py'
        self.assertTrue(path.is_file(),'manifest nakładki nie ma konsumenta')
        source=(ROOT/'.github/workflows/candidate-native-gates.yml').read_text()
        self.assertIn('--phase before',source)
        self.assertIn('--phase after',source)
        self.assertLess(source.index('--phase before'),source.index('git -C native apply --check'))
        self.assertGreater(source.index('--phase after'),source.index('git -C native apply ../'))

if __name__=='__main__':unittest.main(verbosity=2)
