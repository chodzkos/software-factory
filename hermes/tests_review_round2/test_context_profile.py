"""H-1: actual ContextVar profile homes must not hide mandatory start checks.

The native initializer is deliberately a spy fixture. These tests prove refusal
before that initializer, not a provider/dispatcher execution or profile lifetime.
"""
from __future__ import annotations
import importlib.util
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('round2_profile_fixture',ROOT/'hermes/tests_review_revisions/test_profile_start.py')
prior=importlib.util.module_from_spec(spec);sys.modules[spec.name]=prior;spec.loader.exec_module(prior)


class ContextProfileTests(unittest.TestCase):
    def _case(self, name, forged):
        from hermes_constants import set_hermes_home_override, reset_hermes_home_override, get_hermes_home
        from hermes_cli.profiles import get_active_profile_name
        if name=='coder-claude':
            fx=prior.fx.NativeWorkerStartTests('test_valid_native_forwarder_loader_and_exact_eight_tools')
            fx.setUp()
            try:self._attempt(fx,name,forged,set_hermes_home_override,reset_hermes_home_override,get_hermes_home,get_active_profile_name)
            finally:fx.tearDown()
        else:
            fx=prior.ProfileStartTests('test_d05_unapproved_plugin_refused_before_initializer')
            with fx.profile(name):
                self._attempt(fx,name,forged,set_hermes_home_override,reset_hermes_home_override,get_hermes_home,get_active_profile_name)

    def _attempt(self,fx,name,forged,set_home,reset_home,get_home,get_profile):
        with patch.dict(os.environ,{}):
            os.environ.pop('HERMES_PROFILE',None);os.environ.pop('HERMES_HOME',None)
            if forged:
                os.environ['HERMES_PROFILE']='general'
                os.environ['HERMES_HOME']=str(Path.home()/'.hermes')
            token=set_home(fx.home)
            try:
                # Actual native resolution, not a mock profile-name return value.
                self.assertEqual(get_home(),fx.home)
                self.assertEqual(get_profile(),'custom' if name=='release-manager' else name)
                with self.assertRaisesRegex(RuntimeError,'FACTORY_.*START_REFUSED'):
                    fx.construct()
                self.assertEqual(fx.called,0,'native initializer was reached without mandatory profile proof')
            finally:reset_home(token)

    def test_reviewer_context_without_env_refused_before_initializer(self): self._case('reviewer-gpt',False)
    def test_release_context_without_env_refused_before_initializer(self): self._case('release-manager',False)
    def test_coder_context_without_env_refused_before_initializer(self): self._case('coder-claude',False)
    def test_reviewer_context_with_forged_general_env_refused(self): self._case('reviewer-gpt',True)
    def test_release_context_with_forged_general_env_refused(self): self._case('release-manager',True)
    def test_coder_context_with_forged_general_env_refused(self): self._case('coder-claude',True)

    def test_context_selector_uses_no_native_getter_before_pin_gate(self):
        import hermes_constants
        import hermes_cli.profiles
        from factory_profile_start import selected_profile
        fx=prior.ProfileStartTests('test_d05_unapproved_plugin_refused_before_initializer')
        with fx.profile('reviewer-gpt'), patch.dict(os.environ,{}):
            os.environ.pop('HERMES_PROFILE',None);os.environ.pop('HERMES_HOME',None)
            token=hermes_constants.set_hermes_home_override(fx.home)
            try:
                with patch.object(hermes_constants,'get_hermes_home',side_effect=AssertionError('native getter called before pins')) as home, \
                        patch.object(hermes_cli.profiles,'get_active_profile_name',side_effect=AssertionError('native profile getter called before pins')) as profile:
                    self.assertEqual(selected_profile(),'reviewer-gpt')
                    plain=hermes_constants.set_hermes_home_override(fx.home.parent/'general')
                    try:self.assertIsNone(selected_profile(), 'nonprotected context must remain native')
                    finally:hermes_constants.reset_hermes_home_override(plain)
                    home.assert_not_called();profile.assert_not_called()
            finally:hermes_constants.reset_hermes_home_override(token)
