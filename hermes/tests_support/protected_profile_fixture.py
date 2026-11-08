"""Real native profile constructor/registry; synthetic task claim, no SDK traffic.

No dispatcher or Claude runs. Passing an existing NativeReviewDatabase binds the
same temporary WAL DB; it does NOT prove native production connection policy.
"""
from __future__ import annotations
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import socket
from unittest.mock import patch


class NativeProtectedProfile:
    def __init__(self, root: Path, profile: str, *, database=None):
        if profile not in ('reviewer-gpt', 'release-manager'):
            raise ValueError('unsupported fixture profile')
        from factory_profile_start import GUARD, READONLY, DISABLED
        import factory_worker_lifetime as life
        from hermes_cli.plugins import get_plugin_manager
        import run_agent
        self.root = root.resolve(strict=True)
        self.profile, self.stack = profile, ExitStack()
        self.agent, self.manager = None, None
        self.plugins = (GUARD, READONLY)
        self.life = life
        self.workspace = database.workspace if database else self.root/'workspace'
        self.workspace.mkdir(parents=True, exist_ok=True)
        if database is None:
            (self.workspace/'tracked.txt').write_text('PROFILE_READ_OK\n')
        self.task_id = database.task_id if database else 't_fixture'
        self.home = self.root/'.hermes'/('profiles' if profile=='reviewer-gpt' else 'factory-profiles')/profile
        self.home.mkdir(parents=True)
        source = Path(__file__).resolve().parents[1]/'plugins'
        for plugin in self.plugins:
            shutil.copytree(source/plugin, self.home/'plugins'/plugin)
        env = dict(HOME=str(self.root), HERMES_HOME=str(self.home), HERMES_PROFILE=profile,
                   TERMINAL_ENV='local', FACTORY_RELEASE_TASK=self.task_id,
                   FACTORY_RELEASE_WORKSPACE=str(self.workspace), PYTHONDONTWRITEBYTECODE='1')
        if profile=='reviewer-gpt':
            env.update(HERMES_KANBAN_TASK=self.task_id,
                       HERMES_KANBAN_WORKSPACE=str(self.workspace),
                       HERMES_KANBAN_BOARD=database.board if database else 'fixture',
                       HERMES_KANBAN_RUN_ID=str(database.run_id+1 if database else 7))
        self.stack.enter_context(patch.dict(os.environ, env))
        if profile=='release-manager':
            for key in list(os.environ):
                if key.startswith('HERMES_KANBAN_'):
                    del os.environ[key]
        config = {
            'plugins': {'enabled':[GUARD,READONLY], 'disabled':[],
                        'entries':{GUARD:{'allow_tool_override':True},READONLY:{'allow_tool_override':False}}},
            'toolsets':[GUARD,READONLY,'kanban'], 'mcp_servers':{},
            'tools':{'tool_search':{'enabled':'off'}},
            'agent':{'disabled_toolsets':sorted(DISABLED),'environment_probe':False},
            'factory':{'execution_backend':'native-openai' if profile=='reviewer-gpt' else 'read-only-release-decision'},
            'model':{'context_length':131072}, 'compression':{'enabled':False},
        }
        (self.home/'config.yaml').write_text(json.dumps(config))
        self.network=[]
        try:
            for name in ('connect','connect_ex'):
                self.network.append(self.stack.enter_context(patch.object(socket.socket,name,side_effect=AssertionError('NETWORK_FORBIDDEN'))))
            self.manager=get_plugin_manager()
            self.agent=run_agent.AIAgent(
                enabled_toolsets=[GUARD,READONLY,'kanban'],disabled_toolsets=sorted(DISABLED),
                provider='bedrock',api_mode='bedrock_converse',model='local-fixture',
                api_key='test-not-real',base_url='http://127.0.0.1:9/v1',skip_memory=True,
                skip_context_files=True,skip_background_review=True,quiet_mode=True,
                platform='cli',max_iterations=1)
            self.module=self.manager._plugins[GUARD].module
            self.readonly=self.manager._plugins[READONLY].module
            self.handoff=self.module._handoff
            self.state=life.state_for(self.agent)
        except BaseException:
            self.close()
            raise

    def read(self):
        return self.agent._invoke_tool('factory_repo_read',{'path':'tracked.txt'},self.task_id)

    def approve(self):
        return self.agent._invoke_tool('factory_review_approve',{'summary':'OFFLINE APPROVAL'},self.task_id)

    def close(self):
        try:
            if self.agent is not None:
                self.agent.close()
            if self.manager is not None:
                for plugin in self.plugins:
                    self.manager.unload(plugin)
            for spy in self.network:
                spy.assert_not_called()
        finally:
            self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
