"""Review round 2: explicit unsupported routes, real builders, no provider traffic.

Uses the retained full native constructor fixture. SDK call endpoints are mocks;
provider profile and request conversion code are real unless stated otherwise.
"""
from __future__ import annotations
import ast
from contextlib import contextmanager
import copy
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(os.environ['FACTORY_NATIVE_ROOT']).resolve(strict=True)
spec = importlib.util.spec_from_file_location('round2_retained_fixture', ROOT/'hermes/tests_review_revisions/test_review_revisions.py')
prior = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = prior
spec.loader.exec_module(prior)


class StartRouteTests(unittest.TestCase):
    def setUp(self):
        self.fx = prior.fixture.start.NativeWorkerStartTests('test_valid_native_forwarder_loader_and_exact_eight_tools')
        self.fx.setUp()
    def tearDown(self):
        self.fx.tearDown()

    def test_moa_explicit_provider_refused_before_initializer(self):
        with self.assertRaisesRegex(prior.fixture.start.boundary.WorkerStartError, 'MoA'):
            self.fx.construct(provider='moa')
        self.assertEqual(self.fx.called, 0, 'a protected MoA facade must not be constructed')

    def test_moa_normalized_provider_refused_before_initializer(self):
        with self.assertRaisesRegex(prior.fixture.start.boundary.WorkerStartError, 'MoA'):
            self.fx.construct(provider=' MoA ')
        self.assertEqual(self.fx.called, 0)

    def test_moa_resolved_by_initializer_refused_before_binding(self):
        self.fx.transform = lambda agent: setattr(agent, 'provider', 'moa')
        with self.assertRaisesRegex(prior.fixture.start.boundary.WorkerStartError, 'MoA'):
            self.fx.construct()
        self.assertEqual(self.fx.called, 1, 'test must exercise the post-init route gate')


class RuntimeRouteTests(unittest.TestCase):
    def setUp(self):
        self.fx = prior.ReviewRuntimeTests('test_d02_original_chat_surface_is_accepted')
        self.fx.setUp()
        self.agent, self.life, self.state = self.fx.agent, self.fx.life, self.fx.state
    def tearDown(self):
        self.fx.tearDown()

    def test_moa_route_drift_is_refused_before_any_model_phase(self):
        self.agent.provider = 'moa'
        with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'MoA'):
            self.life.check_agent(self.agent)
        self.assertTrue(self.state.revoked.is_set())

    def test_moa_marker_not_silently_whitelisted(self):
        payload = self.fx._chat()
        payload['_moa_prepared_request'] = {'messages': [], 'tools': [{'type':'web_search'}]}
        # Registration and final-call validation need separate live states:
        # this registration fails first and permanently revokes its worker.
        # The final-call case is independently exercised in review followups.
        with self.assertRaises(self.life.WorkerLifetimeError):
            self.life.record_model_payload(self.agent, payload)
        self.assertTrue(self.state.revoked.is_set())

    def _turn_moa(self, *, encoded):
        from agent import conversation_loop
        from hermes_cli import moa_config
        self.agent._last_compaction_in_place = 'ROUND2_UNTOUCHED'
        # On the input candidate the next line is observable before any refusal;
        # on the fix, turn-level MoA is rejected before per-turn state changes.
        config = {'reference_models': [{'provider':'fixture'}], 'aggregator': {}}
        captured = None
        with patch.object(moa_config, 'decode_moa_turn', return_value=('fixture prompt', config)) as decode, \
                patch.object(self.agent, '_try_refresh_env_client_credentials'), \
                patch.object(conversation_loop, 'build_turn_context', side_effect=AssertionError('UNAPPROVED_TURN_SETUP_REACHED')) as setup:
            try:
                conversation_loop.run_conversation(self.agent, 'fixture prompt',
                    moa_config=None if encoded else config)
            except Exception as exc:
                captured = exc
        self.assertIsInstance(captured, self.life.WorkerLifetimeError)
        self.assertIn('MoA', str(captured))
        self.assertEqual(self.agent._last_compaction_in_place, 'ROUND2_UNTOUCHED')
        self.assertTrue(self.state.revoked.is_set())
        setup.assert_not_called()
        if encoded:
            decode.assert_called_once()
        else:
            decode.assert_not_called()

    def test_explicit_turn_moa_refused_before_turn_mutation(self):
        self._turn_moa(encoded=False)

    def test_encoded_turn_moa_refused_before_turn_mutation(self):
        self._turn_moa(encoded=True)

    def test_nous_messages_real_builder_explicitly_refuses_extra_body(self):
        from agent import chat_completion_helpers as helpers
        self.agent.api_mode = 'anthropic_messages'
        self.agent.provider = 'nous'
        self.agent._is_anthropic_oauth = False
        self.agent.reasoning_config = None
        observed = []
        original = self.state.envelope
        def envelope(payload, *args):
            observed.append(copy.deepcopy(payload))
            return original(payload, *args)
        with patch.object(self.state, 'envelope', side_effect=envelope), \
                patch.object(self.agent, '_anthropic_messages_create') as client:
            with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'extra_body'):
                helpers.build_api_kwargs(self.agent, [{'role':'user','content':'fixture'}])
            client.assert_not_called()
        self.assertEqual(len(observed), 1, 'must reach actual payload validation')
        self.assertTrue(observed[0]['extra_body']['tags'], 'real Nous profile must actually emit tags')
        self.assertIn('tools', observed[0], 'real Messages converter must have run')
        self.assertTrue(self.state.revoked.is_set())

    def _summary_extra_body(self, case):
        from agent import chat_completion_helpers as helpers
        self.fx._chat()
        self.agent.reasoning_config = {'enabled': True, 'effort': 'medium'}
        self.agent.provider = 'openrouter' if case in ('provider','plugins') else ('nous' if case=='tags' else 'openai')
        self.agent.providers_allowed = ['fixture-upstream'] if case=='provider' else None
        if case == 'plugins':
            self.agent.model='openrouter/pareto-code'
            self.agent.openrouter_min_coding_score=0.7
        client=Mock(); observed=[]
        original=self.state.envelope
        def envelope(payload, *args):
            observed.append(copy.deepcopy(payload));return original(payload,*args)
        with patch.object(self.state, 'envelope', side_effect=envelope), \
                patch.object(self.agent, '_ensure_primary_openai_client', return_value=client), \
                patch.object(self.agent, '_supports_reasoning_extra_body', return_value=(case=='reasoning')):
            with self.assertRaisesRegex(self.life.WorkerLifetimeError, 'extra_body'):
                helpers.handle_max_iterations(self.agent, [], 1)
        client.chat.completions.create.assert_not_called()
        self.assertEqual(len(observed),1, 'summary must reach the envelope, not an earlier unrelated failure')
        self.assertIn(case, observed[0]['extra_body'])
        self.assertTrue(observed[0]['extra_body'][case])
        self.assertFalse(observed[0].get('tools'))
        self.assertTrue(self.state.revoked.is_set())
        self.agent.reasoning_config=None
        with self.assertRaisesRegex(self.life.WorkerLifetimeError,'revoked'):
            self.life.check_agent(self.agent)

    def test_summary_reasoning_explicitly_refused_and_revoked(self): self._summary_extra_body('reasoning')
    def test_summary_nous_tags_explicitly_refused_and_revoked(self): self._summary_extra_body('tags')
    def test_summary_openrouter_routing_explicitly_refused_and_revoked(self): self._summary_extra_body('provider')
    def test_summary_pareto_plugins_explicitly_refused_and_revoked(self): self._summary_extra_body('plugins')

    def test_responses_summary_retry_builds_empty_tools_both_times(self):
        from agent import chat_completion_helpers as helpers
        # Generic Responses wire mode, NOT the ChatGPT Codex backend. Its
        # extra_headers on an unapproved endpoint are separately refused below.
        self.agent.api_mode='codex_responses';self.agent.provider='openai'
        self.agent.reasoning_config=None
        transport=self.agent._get_transport()
        observed=[]; build=self.agent._build_api_kwargs
        def request(payload):
            # This endpoint is a spy, not an SDK implementation. The real builder
            # and real lifetime validation must authorize EACH exact request.
            self.life.check_model_payload(self.agent,payload)
            self.assertFalse(payload.get('tools'))
            observed.append(copy.deepcopy(payload))
            return {'output': [], 'status':'completed'}
        with patch.object(self.agent,'_build_api_kwargs',wraps=build) as builder, \
                patch.object(self.agent,'_run_codex_stream',side_effect=request) as endpoint, \
                patch.object(transport,'normalize_response',side_effect=[types.SimpleNamespace(content=''),types.SimpleNamespace(content='ROUND2 SUMMARY')]):
            result=helpers.handle_max_iterations(self.agent, [], 1)
        self.assertEqual(result,'ROUND2 SUMMARY')
        self.assertEqual(len(observed),2,'both summary attempts must reach the endpoint')
        self.assertEqual(endpoint.call_count,2)
        self.assertEqual([c.kwargs.get('tools_for_api') for c in builder.call_args_list],[[],[]])
        self.assertFalse(self.state.revoked.is_set())

    def test_codex_backend_session_headers_refused_on_unapproved_endpoint(self):
        from agent import chat_completion_helpers as helpers
        self.agent.api_mode='codex_responses'; self.agent.provider='openai-codex'
        self.agent.reasoning_config=None
        observed=[]; original=self.state.envelope
        def envelope(payload, *args):
            observed.append(copy.deepcopy(payload)); return original(payload,*args)
        with patch.object(self.state,'envelope',side_effect=envelope), \
                patch.object(self.agent,'_run_codex_stream') as endpoint:
            with self.assertRaisesRegex(self.life.WorkerLifetimeError,'approved provider and endpoint'):
                helpers.handle_max_iterations(self.agent,[],1)
        endpoint.assert_not_called()
        self.assertEqual(len(observed),1)
        self.assertEqual(observed[0]['extra_headers']['session_id'],self.agent.session_id)
        self.assertTrue(observed[0]['extra_headers']['x-client-request-id'])
        self.assertTrue(self.state.revoked.is_set())

    @contextmanager
    def _general_profile(self):
        home=self.fx.fx.fixture.root/'.hermes/profiles/general'
        home.mkdir(parents=True)
        (home/'config.yaml').write_text(json.dumps({
            'tools':{'tool_search':{'enabled':'off'}},
            'model':{'context_length':131072},'compression':{'enabled':False},
            'agent':{'environment_probe':False},'plugins':{'enabled':[]}}))
        with patch.dict(os.environ,HERMES_HOME=str(home),HERMES_PROFILE='general',
                        HERMES_KANBAN_TASK='',HERMES_KANBAN_RUN_ID=''):
            general=prior.fixture.start.run_agent.AIAgent(
                enabled_toolsets=['file'],provider='bedrock',api_mode='bedrock_converse',
                model='local-fixture',api_key='test-not-real',base_url='http://127.0.0.1:9/v1',
                skip_memory=True,skip_context_files=True,skip_background_review=True,
                quiet_mode=True,platform='cli',max_iterations=1)
            try:
                self.assertIsNone(self.life.state_for(general))
                yield general
            finally: general.close()

    def _general_moa_dispatch(self, facade):
        from agent import chat_completion_helpers as helpers
        with self._general_profile() as general:
            general.provider='moa';general.api_mode='chat_completions'
            create=Mock(return_value='MOCK_MOA_DISPATCH_RESULT')
            completions=types.SimpleNamespace(create=create)
            if facade: completions.prepare=Mock()
            general.client=types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions))
            payload={'model':'fixture','messages':[],
                     '_moa_prepared_request':{'messages':[], 'fixture_only':True}}
            make_client=Mock()
            result=helpers._dispatch_nonstreaming_api_request(general,payload,make_client=make_client)
            self.assertEqual(result,'MOCK_MOA_DISPATCH_RESULT')
            create.assert_called_once();make_client.assert_not_called()
            self.assertEqual('_moa_prepared_request' in create.call_args.kwargs,facade)
            if facade: completions.prepare.assert_not_called()
            self.assertIsNone(self.life.state_for(general))

    def test_general_moa_facade_retains_private_marker(self):
        self._general_moa_dispatch(True)

    def test_general_replaced_moa_client_still_strips_private_marker(self):
        self._general_moa_dispatch(False)

    def test_general_turn_moa_reaches_native_setup_not_factory_refusal(self):
        from agent import conversation_loop
        with self._general_profile() as general:
            general._last_compaction_in_place='UNTOUCHED'
            config={'reference_models':[{'provider':'fixture'}]}
            self.life.check_moa_turn(general,config)
            with patch.object(general,'_try_refresh_env_client_credentials'), \
                    patch.object(conversation_loop,'build_turn_context',side_effect=LookupError('GENERAL_SETUP_PROBE')) as setup:
                with self.assertRaisesRegex(LookupError,'GENERAL_SETUP_PROBE'):
                    conversation_loop.run_conversation(general,'fixture',moa_config=config)
            setup.assert_called_once()
            self.assertIs(general._last_compaction_in_place,False)
            self.assertIsNone(self.life.state_for(general))


class SQLCallsiteTests(unittest.TestCase):
    def test_n4_all_kanban_migration_arguments_are_literals(self):
        """This closes only the reviewed kanban_db call-sites, not arbitrary SQL."""
        source=(NATIVE/'hermes_cli/kanban_db.py').read_text()
        tree=ast.parse(source)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and (
            isinstance(n.func,ast.Name) and n.func.id in {'_add_column_if_missing','add_column_if_missing'}
            or isinstance(n.func,ast.Attribute) and n.func.attr in {'_add_column_if_missing','add_column_if_missing'})]
        self.assertEqual(len(calls),28,'the pinned full kanban_db call-site inventory changed')
        for call in calls:
            self.assertEqual(len(call.args),4)
            self.assertEqual(call.keywords,[])
            for arg in call.args[1:]:
                self.assertIsInstance(arg,ast.Constant, f'nonliteral at line {call.lineno}')
                self.assertIs(type(arg.value),str)
            self.assertRegex(call.args[1].value, r'^[a-z][a-z0-9_]*$')
            self.assertRegex(call.args[2].value, r'^[a-z][a-z0-9_]*$')
            self.assertTrue(call.args[3].value.startswith(call.args[2].value+' '))

if __name__=='__main__': unittest.main(verbosity=2)
