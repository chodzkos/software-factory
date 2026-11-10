"""Regresje rzeczywistych ścieżek runtime; initializer i końcowe efekty są fixture."""
from __future__ import annotations
import importlib.util
import json
import copy
import threading
import contextvars
from types import SimpleNamespace
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('start_fixture', Path(__file__).parents[1]/'tests_worker_start/test_native_worker_start.py')
start = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = start
spec.loader.exec_module(start)


class LifetimeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = start.NativeWorkerStartTests('test_valid_native_forwarder_loader_and_exact_eight_tools')
        self.fixture.setUp()
        self.agent = self.fixture.construct()
        self.agent.session_id = 'offline-fixture'
        self.agent._memory_manager = None

    def tearDown(self):
        self.fixture.tearDown()

    def invoke(self, **kwargs):
        return self.agent._invoke_tool('terminal', {'command':'NEVER_EXECUTED','workdir':str(self.fixture.root)}, 't_fixture', **kwargs)

    def test_unload_after_start_refuses_before_dispatcher(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        with patch.object(start.run_agent, 'handle_function_call', return_value='UNSAFE') as effect:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.invoke()
            effect.assert_not_called()

    def test_definition_drift_after_start_refuses(self):
        self.agent.tools[0]['function']['description'] = 'changed'
        with patch.object(start.run_agent, 'handle_function_call', return_value='UNSAFE') as effect:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.invoke()
            effect.assert_not_called()

    def test_profile_drift_does_not_detach_existing_worker(self):
        os.environ['HERMES_PROFILE'] = 'general'
        with patch.object(start.run_agent, 'handle_function_call', return_value='UNSAFE') as effect:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.invoke(pre_tool_block_checked=True)
            effect.assert_not_called()

    def test_unknown_tool_refused_with_skip_flags(self):
        with patch.object(start.run_agent, 'handle_function_call', return_value='UNSAFE') as effect:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.agent._invoke_tool('write_file', {'path':'/tmp/no-write','content':'NO'}, 't_fixture', pre_tool_block_checked=True,skip_tool_request_middleware=True,skip_tool_execution_middleware=True)
            effect.assert_not_called()

    def test_unload_blocks_model_payload_builder(self):
        import agent.chat_completion_helpers as helpers
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        with patch.object(helpers, 'build_api_kwargs', return_value={}) as model:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.agent._build_api_kwargs([])
            model.assert_not_called()

    def test_unload_blocks_model_request(self):
        import agent.chat_completion_helpers as helpers
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        with patch.object(helpers, 'interruptible_api_call', return_value={}) as model:
            with self.assertRaisesRegex(RuntimeError, 'FACTORY_WORKER_'):
                self.agent._interruptible_api_call({})
            model.assert_not_called()


class NativeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.fixture = start.NativeWorkerStartTests('test_valid_native_forwarder_loader_and_exact_eight_tools')
        self.fixture.setUp()
        self.fixture.config.update(model={'context_length':131072}, compression={'enabled':False}, agent={'environment_probe':False})
        self.fixture.save_config()
        self.fixture.init_patch.stop()
        import socket
        self.network = patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK_FORBIDDEN'))
        self.network_spy = self.network.start()
        self.agent = self.fixture.construct(provider='bedrock', api_mode='bedrock_converse', model='local-fixture',
            api_key='test-not-real', base_url='http://127.0.0.1:9/v1', skip_memory=True,
            skip_context_files=True, skip_background_review=True, quiet_mode=True, platform='cli', max_iterations=1)
        self.path = self.fixture.root/'actual_read_fixture.txt'
        self.path.write_text('FACTORY_LIFETIME_READ_OK\n')
        import factory_worker_lifetime as lifetime
        self.life = lifetime
        self.state = lifetime.state_for(self.agent)
        self.guard = self.state.proof.module._guard
        from tools import file_tools
        self.file_tools = file_tools

    def tearDown(self):
        try:
            self.agent.close()
            self.network_spy.assert_not_called()
        finally:
            self.network.stop()
            self.fixture.init_patch.start()
            self.fixture.tearDown()

    def read(self, **kwargs):
        return self.agent._invoke_tool('read_file', {'path':str(self.path)}, 't_fixture', tool_call_id='call_read', **kwargs)

    def test_actual_read_and_required_hooks_exactly_once(self):
        with patch.object(self.guard, 'on_pre_tool_call', wraps=self.guard.on_pre_tool_call) as pre:
            with patch.object(self.guard, 'on_post_tool_call', wraps=self.guard.on_post_tool_call) as post:
                result = self.read()
        self.assertIn('FACTORY_LIFETIME_READ_OK', result)
        self.assertEqual(pre.call_count, 1)
        self.assertEqual(post.call_count, 1)

    def test_skip_flags_cannot_bypass_mandatory_guard(self):
        from tools import terminal_tool
        with patch.object(terminal_tool, 'terminal_tool', side_effect=AssertionError('TERMINAL_FORBIDDEN')) as terminal:
            result = self.agent._invoke_tool('terminal', {'command':'NEVER_EXECUTED','workdir':str(self.fixture.root)}, 't_fixture', pre_tool_block_checked=True, skip_tool_request_middleware=True, skip_tool_execution_middleware=True)
        self.assertIn('error', json.loads(result))
        terminal.assert_not_called()

    def test_pre_exception_refuses_real_handler_and_revokes(self):
        with patch.object(self.guard, 'on_pre_tool_call', side_effect=RuntimeError('INJECTED_PRE')):
            with patch.object(self.file_tools, 'read_file_tool') as effect:
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
                effect.assert_not_called()
        with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'revoked'): self.read()

    def test_post_exception_keeps_effect_but_revokes_no_false_rollback(self):
        with patch.object(self.guard, 'on_post_tool_call', side_effect=RuntimeError('INJECTED_POST')):
            with patch.object(self.file_tools, 'read_file_tool', return_value='READ_ALREADY_HAPPENED') as effect:
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
                self.assertEqual(effect.call_count, 1)
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
                self.assertEqual(effect.call_count, 1)

    def test_optional_hooks_cannot_duplicate_mandatory_calls(self):
        with patch.object(self.guard, 'on_pre_tool_call') as pre:
            with patch.object(self.guard, 'on_post_tool_call') as post:
                manager = start.get_plugin_manager()
                self.assertEqual(manager.invoke_hook('pre_tool_call', tool_name='read_file', args={}), [])
                self.assertEqual(manager.invoke_hook('post_tool_call', tool_name='read_file', args={}, result='FORGED'), [])
                pre.assert_not_called(); post.assert_not_called()

    def test_low_level_registry_requires_agent_operation(self):
        with patch.object(self.file_tools, 'read_file_tool') as effect:
            with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'mandatory agent operation'):
                start.registry.dispatch('read_file', {'path':str(self.path)}, task_id='t_fixture')
            effect.assert_not_called()

    def test_low_level_model_dispatch_skip_flags_no_bypass(self):
        from model_tools import handle_function_call
        with patch.object(self.file_tools, 'read_file_tool') as effect:
            result = handle_function_call('read_file', {'path':str(self.path)}, task_id='t_fixture', skip_pre_tool_call_hook=True, skip_tool_execution_middleware=True, skip_tool_request_middleware=True)
            self.assertIn('FACTORY_WORKER_LIFETIME_REFUSED', result)
            effect.assert_not_called()

    def test_closed_agent_cannot_resume(self):
        self.life.close_worker(self.agent)
        with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'revoked'): self.read()

    def test_unload_reload_does_not_rebind_old_agent(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        start.discover_plugins(force=True)
        with self.assertRaises(self.life.WorkerLifetimeError): self.read()
        self.assertTrue(self.state.revoked.is_set())

    def test_restore_hook_does_not_rearm_failed_generation(self):
        manager = start.get_plugin_manager()
        original = manager._hooks['pre_tool_call'][:]
        manager._hooks['pre_tool_call'].clear()
        with self.assertRaises(self.life.WorkerLifetimeError): self.read()
        manager._hooks['pre_tool_call'][:] = original
        with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'revoked'): self.read()

    def test_same_thread_unload_in_pre_refuses_before_effect(self):
        original = self.guard.on_pre_tool_call
        def pre(**kwargs):
            result = original(**kwargs)
            start.get_plugin_manager().unload(start.boundary.PLUGIN)
            return result
        with patch.object(self.guard, 'on_pre_tool_call', side_effect=pre):
            with patch.object(self.file_tools, 'read_file_tool') as effect:
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
                effect.assert_not_called()

    def test_scoped_registry_replacement_in_pre_never_executes_new_handler(self):
        original = self.guard.on_pre_tool_call
        entry = start.registry.get_entry('read_file')
        effects = []
        def pre(**kwargs):
            result = original(**kwargs)
            start.registry.register('read_file', entry.toolset, entry.schema, lambda *a,**kw:effects.append('UNSAFE'), scope=str(self.fixture.home))
            return result
        try:
            with patch.object(self.guard, 'on_pre_tool_call', side_effect=pre):
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
            self.assertEqual(effects, [])
        finally:
            current = start.registry.snapshot_registration('read_file', scope=str(self.fixture.home))
            start.registry.restore_registration('read_file', current, None, scope=str(self.fixture.home))

    def test_unload_other_thread_waits_for_mandatory_post(self):
        entered, attempted, release, done = (threading.Event() for _ in range(4))
        phases, errors = [], []
        manager = start.get_plugin_manager()
        def effect(**kwargs):
            phases.append('handler')
            entered.set()
            if not release.wait(5): raise AssertionError('release missing')
            self.assertFalse(done.is_set())
            return 'CONTROLLED_READ'
        def post(**kwargs): phases.append('post')
        def tool():
            try: self.read()
            except self.life.WorkerLifetimeError: pass  # unload może wygrać kontrolę po zakończonej operacji
            except BaseException as exc: errors.append(repr(exc))
        def unload():
            attempted.set()
            manager.unload(start.boundary.PLUGIN)
            phases.append('unloaded');done.set()
        with patch.object(self.file_tools, 'read_file_tool', side_effect=effect):
            with patch.object(self.guard, 'on_post_tool_call', side_effect=post):
                t = threading.Thread(target=contextvars.copy_context().run, args=(tool,),daemon=True);t.start()
                try:
                    self.assertTrue(entered.wait(5))
                    u = threading.Thread(target=unload,daemon=True);u.start()
                    self.assertTrue(attempted.wait(5));self.assertFalse(done.is_set())
                finally: release.set()
                t.join(5);u.join(5)
                self.assertFalse(t.is_alive());self.assertFalse(u.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(phases, ['handler','post','unloaded'])
        with self.assertRaises(self.life.WorkerLifetimeError): self.read()

    def calls(self, count=2):
        paths = [self.fixture.root / ('read_' + str(i) + '.txt') for i in range(count)]
        for path in paths:
            path.write_text('FACTORY_LIFETIME_READ_OK\n')
        return SimpleNamespace(tool_calls=[SimpleNamespace(id='call_'+str(i), function=SimpleNamespace(name='read_file', arguments=json.dumps({'path':str(paths[i])}))) for i in range(count)])

    def test_actual_sequential_executor_reads_file(self):
        from agent.tool_executor import execute_tool_calls_sequential
        messages=[]
        execute_tool_calls_sequential(self.agent, self.calls(), messages, 't_fixture', finalize=False)
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('FACTORY_LIFETIME_READ_OK' in str(m) for m in messages), messages)

    def test_actual_concurrent_executor_reads_file(self):
        from agent.tool_executor import execute_tool_calls_concurrent
        messages=[]
        execute_tool_calls_concurrent(self.agent, self.calls(), messages, 't_fixture', finalize=False)
        self.assertEqual(len(messages), 2)
        self.assertTrue(all('FACTORY_LIFETIME_READ_OK' in str(m) for m in messages), messages)

    def test_actual_segmented_executor_reads_file(self):
        from agent.tool_executor import execute_tool_calls_segmented
        messages=[]
        calls = self.calls(3)
        segments = [('parallel', calls.tool_calls[:2]), ('sequential', calls.tool_calls[2:])]
        execute_tool_calls_segmented(self.agent, calls, messages, 't_fixture', segments=segments)
        self.assertEqual(len(messages), 3)
        self.assertTrue(all('FACTORY_LIFETIME_READ_OK' in str(m) for m in messages), messages)

    def test_actual_sequential_after_unload_stops_before_effect(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        from agent.tool_executor import execute_tool_calls_sequential
        with patch.object(self.file_tools, 'read_file_tool') as effect:
            with self.assertRaises(self.life.WorkerLifetimeError): execute_tool_calls_sequential(self.agent, self.calls(), [], 't_fixture', finalize=False)
            effect.assert_not_called()

    def test_actual_concurrent_after_unload_stops_before_effect(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        from agent.tool_executor import execute_tool_calls_concurrent
        with patch.object(self.file_tools, 'read_file_tool') as effect:
            with self.assertRaises(self.life.WorkerLifetimeError): execute_tool_calls_concurrent(self.agent, self.calls(), [], 't_fixture', finalize=False)
            effect.assert_not_called()

    def test_actual_segmented_after_unload_stops_before_effect(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        from agent.tool_executor import execute_tool_calls_segmented
        with patch.object(self.file_tools, 'read_file_tool') as effect:
            with self.assertRaises(self.life.WorkerLifetimeError): execute_tool_calls_segmented(self.agent, self.calls(), [], 't_fixture')
            effect.assert_not_called()

    def test_tool_definition_drift_rejected_before_api_builder(self):
        tools=copy.deepcopy(self.agent.tools);tools[0]['function']['description']='INJECTED'
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._build_api_kwargs([], tools_for_api=tools)

    def test_actual_bedrock_builder_registers_payload_without_network(self):
        payload=self.agent._build_api_kwargs([{'role':'user','content':'offline fixture'}])
        self.life.check_model_payload(self.agent,payload)
        self.assertIn('toolConfig',payload)

    def test_payload_tool_change_rejected_before_model(self):
        payload=self.agent._build_api_kwargs([{'role':'user','content':'offline fixture'}])
        payload['toolConfig']['tools'].append({'toolSpec':{'name':'INJECTED','description':'bad','inputSchema':{'json':{}}}})
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._interruptible_api_call(payload)

    def test_unknown_payload_does_not_start_provider(self):
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._interruptible_api_call({'toolConfig':{'tools':[]}})

    def test_direct_conversation_module_entry_rejects_after_unload(self):
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        from agent.conversation_loop import run_conversation
        with self.assertRaises(self.life.WorkerLifetimeError): run_conversation(self.agent, 'NEVER_SENT')

    def test_streaming_forwarder_rejects_after_unload(self):
        payload=self.agent._build_api_kwargs([{'role':'user','content':'offline fixture'}])
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._interruptible_streaming_api_call(payload)


    def test_direct_native_builder_tracks_payload(self):
        from agent.chat_completion_helpers import build_api_kwargs
        payload = build_api_kwargs(self.agent, [{'role':'user','content':'offline fixture'}])
        self.life.check_model_payload(self.agent, payload)

    def test_direct_native_nonstreaming_dispatch_refuses_unloaded_worker(self):
        from agent.chat_completion_helpers import _dispatch_nonstreaming_api_request
        payload = self.agent._build_api_kwargs([])
        start.get_plugin_manager().unload(start.boundary.PLUGIN)
        with patch.object(self.agent, '_create_request_openai_client') as provider:
            with self.assertRaises(self.life.WorkerLifetimeError):
                _dispatch_nonstreaming_api_request(self.agent, payload, make_client=provider)
            provider.assert_not_called()

    def test_unload_during_model_helper_does_not_return_success(self):
        import agent.chat_completion_helpers as helpers
        payload = self.agent._build_api_kwargs([])
        def model(agent, request):
            start.get_plugin_manager().unload(start.boundary.PLUGIN)
            return {'UNSAFE_SUCCESS':True}
        with patch.object(helpers, 'interruptible_api_call', side_effect=model) as effect:
            with self.assertRaises(self.life.WorkerLifetimeError): self.agent._interruptible_api_call(payload)
            self.assertEqual(effect.call_count, 1)
        self.assertTrue(self.state.revoked.is_set())

    def test_model_helper_uses_separate_payload_copy(self):
        import agent.chat_completion_helpers as helpers
        payload = self.agent._build_api_kwargs([])
        expected = copy.deepcopy(payload)
        def model(agent, request):
            payload['toolConfig']['tools'].clear()
            self.assertEqual(request, expected)
            self.assertIsNot(request, payload)
            return 'CONTROLLED_MODEL_STUB'
        with patch.object(helpers, 'interruptible_api_call', side_effect=model):
            self.assertEqual(self.agent._interruptible_api_call(payload), 'CONTROLLED_MODEL_STUB')
        with self.assertRaises(self.life.WorkerLifetimeError): self.life.check_model_payload(self.agent, payload)

    def test_explicit_protected_scope_cannot_escape_through_general_profile(self):
        general = self.fixture.root/'.hermes/profiles/general'
        general.mkdir(parents=True)
        with patch.dict(os.environ, HERMES_HOME=str(general), HERMES_PROFILE='general'):
            with patch.object(self.file_tools, 'read_file_tool') as effect:
                with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'mandatory agent operation'):
                    start.registry.dispatch('read_file', {'path':str(self.path)}, scope=str(self.fixture.home))
                effect.assert_not_called()

    def test_unprotected_scope_keeps_native_dispatch(self):
        general = self.fixture.root/'.hermes/profiles/general'
        general.mkdir(parents=True)
        with patch.dict(os.environ, HERMES_HOME=str(general), HERMES_PROFILE='general'):
            result = start.registry.dispatch('read_file', {'path':str(self.path)})
        self.assertIn('FACTORY_LIFETIME_READ_OK', result)

    def test_mandatory_pre_argument_mutation_refuses_handler(self):
        def pre(**kwargs):
            kwargs['args']['path'] = '/tmp/UNAUTHORIZED'
            return None
        with patch.object(self.guard, 'on_pre_tool_call', side_effect=pre):
            with patch.object(self.file_tools, 'read_file_tool') as effect:
                with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'changed arguments'): self.read()
                effect.assert_not_called()

    def test_mandatory_approval_directive_never_prompts(self):
        with patch.object(self.guard, 'on_pre_tool_call', return_value={'action':'approve','message':'prompt'}):
            with patch.object(self.file_tools, 'read_file_tool') as effect:
                with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'no interactive approval'): self.read()
                effect.assert_not_called()

    def test_same_thread_unload_inside_handler_marks_effect_uncertain(self):
        def effect(**kwargs):
            start.get_plugin_manager().unload(start.boundary.PLUGIN)
            return 'EFFECT_OCCURRED'
        with patch.object(self.file_tools, 'read_file_tool', side_effect=effect) as handler:
            with patch.object(self.guard, 'on_post_tool_call') as post:
                with self.assertRaises(self.life.WorkerLifetimeError): self.read()
                self.assertEqual(handler.call_count, 1)
                post.assert_not_called()
        self.assertTrue(self.state.revoked.is_set())

    def test_registry_operation_is_single_use(self):
        @self.life.tool_entry
        def twice(agent, function_name, effective_task_id):
            first = start.registry.dispatch(function_name, {'path':str(self.path)}, task_id=effective_task_id)
            self.assertIn('FACTORY_LIFETIME_READ_OK', first)
            return start.registry.dispatch(function_name, {'path':str(self.path)}, task_id=effective_task_id)
        with patch.object(self.file_tools, 'read_file_tool', wraps=self.file_tools.read_file_tool) as handler:
            with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'replay'):
                twice(self.agent, 'read_file', 't_fixture')
            self.assertEqual(handler.call_count, 1)

    def test_registry_task_drift_refuses_handler(self):
        @self.life.tool_entry
        def invalid_task(agent, function_name, effective_task_id):
            return start.registry.dispatch(function_name, {'path':str(self.path)}, task_id='t_other')
        with patch.object(self.file_tools, 'read_file_tool') as handler:
            with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'identity changed'):
                invalid_task(self.agent, 'read_file', 't_fixture')
            handler.assert_not_called()

    def test_sequential_hook_exception_never_reaches_any_handler(self):
        from agent.tool_executor import execute_tool_calls_sequential
        with patch.object(self.guard, 'on_pre_tool_call', side_effect=RuntimeError('REQUIRED_HOOK_FAILED')):
            with patch.object(self.file_tools, 'read_file_tool') as handler:
                try: execute_tool_calls_sequential(self.agent, self.calls(), [], 't_fixture', finalize=False)
                except self.life.WorkerLifetimeError: pass
                handler.assert_not_called()
        self.assertTrue(self.state.revoked.is_set())
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._build_api_kwargs([])

    def test_concurrent_hook_exception_never_reaches_any_handler(self):
        from agent.tool_executor import execute_tool_calls_concurrent
        with patch.object(self.guard, 'on_pre_tool_call', side_effect=RuntimeError('REQUIRED_HOOK_FAILED')):
            with patch.object(self.file_tools, 'read_file_tool') as handler:
                try: execute_tool_calls_concurrent(self.agent, self.calls(), [], 't_fixture', finalize=False)
                except self.life.WorkerLifetimeError: pass
                handler.assert_not_called()
        self.assertTrue(self.state.revoked.is_set())
        with self.assertRaises(self.life.WorkerLifetimeError): self.agent._build_api_kwargs([])


    def setup_review_fixture(self):
        from review_fixture import prepare_review_fixture
        return prepare_review_fixture(self)

    def test_actual_registry_handoff_commits_native_receipt(self):
        args=self.setup_review_fixture()
        with patch.object(self.guard,'on_pre_tool_call',wraps=self.guard.on_pre_tool_call) as pre:
            with patch.object(self.guard,'on_post_tool_call',wraps=self.guard.on_post_tool_call) as post:
                result=json.loads(self.agent._invoke_tool('kanban_request_review',args,self.review_tid,tool_call_id='native_review'))
        self.assertEqual(result,{'ok':True,'task_id':self.review_tid,'run_id':7,'status':'review'})
        self.assertEqual(pre.call_count,1);self.assertEqual(post.call_count,1)
        from review_fixture import assert_receipt_and_null_pids
        assert_receipt_and_null_pids(self)


    def _handoff_executor(self, executor):
        from review_fixture import assert_executor_handoff
        return assert_executor_handoff(self, executor)

    def test_sequential_registry_handoff_commits_receipt(self):
        from agent.tool_executor import execute_tool_calls_sequential
        self._handoff_executor(execute_tool_calls_sequential)

    def test_concurrent_registry_handoff_commits_receipt(self):
        from agent.tool_executor import execute_tool_calls_concurrent
        self._handoff_executor(execute_tool_calls_concurrent)

    def test_native_handoff_unload_after_sql_rolls_back_all_three_tables(self):
        args=self.setup_review_fixture()
        def rows():
            conn=self.review_connect(board='fixture')
            try:return {t:[dict(r) for r in conn.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('tasks','task_runs','task_events')}
            finally:conn.close()
        before=rows();original=self.review_kb.request_review
        def transition(*a,**kw):
            result=original(*a,**kw)
            self.assertEqual(result,(True,None))
            start.get_plugin_manager().unload(start.boundary.PLUGIN)
            return result
        with patch.object(self.review_kb,'request_review',side_effect=transition) as native:
            with self.assertRaises(self.life.WorkerLifetimeError):
                self.agent._invoke_tool('kanban_request_review',args,self.review_tid,tool_call_id='native_review_unload')
            self.assertEqual(native.call_count,1)
        self.assertEqual(rows(),before)
        h=self.state.proof.module._handoff
        with self.assertRaises(h.HandoffError):h.load_handoff_seal('fixture',self.review_tid,7)


    def test_expired_scope_binding_never_restores_raw_dispatch(self):
        """Kontrolowana utrata weak-map wpisu; nie deklaruje testu całego GC runtime."""
        with self.life._MAP_LOCK:
            state = self.life._SCOPES.pop(str(self.fixture.home))
        try:
            general = self.fixture.root/'.hermes/profiles/general'
            general.mkdir(parents=True)
            with patch.dict(os.environ,HERMES_HOME=str(general),HERMES_PROFILE='general'):
                with patch.object(self.file_tools,'read_file_tool') as effect:
                    with self.assertRaisesRegex(self.life.WorkerLifetimeError,'mandatory agent operation'):
                        start.registry.dispatch('read_file',{'path':str(self.path)},scope=str(self.fixture.home))
                    effect.assert_not_called()
        finally:
            with self.life._MAP_LOCK:self.life._SCOPES[str(self.fixture.home)] = state


if __name__ == '__main__':
    unittest.main(verbosity=2)
