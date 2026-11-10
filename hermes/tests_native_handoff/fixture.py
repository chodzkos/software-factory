"""SQLite/Git fixture; dowody wykonania syntetyczne, nie test live."""
from __future__ import annotations
import ast
import contextlib
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch
BASE = Path(__file__).resolve().parent
PLUGIN = Path(os.environ.get("SF_CANDIDATE_PLUGIN", str(BASE.parent/"plugins/factory-execution-guards")))
NATIVE = Path(os.environ.get("SF_NATIVE_SQL_SOURCE", str(BASE/"fixtures/native/hermes_cli/kanban_db.py")))

def native_functions():
    tree = ast.parse(NATIVE.read_text())
    body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in {'_end_run', 'request_review', '_set_worker_pid', '_current_run_id', '_append_event'}]
    assert {n.name for n in body} == {'_end_run', 'request_review', '_set_worker_pid', '_current_run_id', '_append_event'}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]+body, type_ignores=[])
    ast.fix_missing_locations(module)
    @contextlib.contextmanager
    def txn(conn):
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        else:
            conn.commit()
    def event(conn, tid, kind, payload, run_id=None):
        conn.execute('INSERT INTO task_events(task_id,run_id,kind,payload,created_at) VALUES(?,?,?,?,?)',
                     (tid,run_id,kind,json.dumps(payload),int(time.time())))
    def unexpected(*args, **kwargs):
        raise AssertionError('Nie wolno syntetyzować zakończonego runu w tej ścieżce')
    ns={'json':json,'time':time,'write_txn':txn,'redact_review_value':lambda v:v,
        '_parents_satisfied':lambda c,t:True,'_canonical_assignee':lambda v:v,
        '_append_event':event,'_synthesize_ended_run':unexpected}
    exec(compile(module,str(NATIVE)+'#original-function-bodies','exec'),ns)
    return ns
NATIVE_FUNCTIONS = native_functions()

class BaseFixture(unittest.TestCase):
    def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix='sf-unit-handoff-')
            self.root = Path(self.temp.name)
            self.home = self.root/'home'; self.home.mkdir()
            self.repo = self.root/'repository'; self.repo.mkdir()
            self.tid='t_unit_handoff'; self.rid=1; self.board='unit-handoff-intent'
            self.workspace=self.repo/'.worktrees'/self.tid; self.workspace.mkdir(parents=True)
            self.db=self.root/'unit.db'
            self.env=patch.dict(os.environ,{'HOME':str(self.home), 'HERMES_PROFILE':'coder-claude',
                'HERMES_KANBAN_TASK':self.tid,'HERMES_KANBAN_RUN_ID':'1','HERMES_KANBAN_BOARD':self.board,
                'HERMES_KANBAN_WORKSPACE':str(self.workspace)}, clear=False)
            self.env.start();self.addCleanup(self.env.stop)
            for cmd in (['init','-q'],['add','.'],['-c','user.name=Unit Test','-c','user.email=unit@example.invalid','commit','-qm','unit fixture']):
                if cmd[0]=='add':
                    (self.workspace/'tracked.txt').write_text('tracked fixture\n')
                    (self.workspace/'.gitignore').write_text('ignored.txt\n')
                    (self.workspace/'ignored.txt').write_text('ignored original\n')
                subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(self.workspace),*cmd],
                               check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                               env={'PATH':os.defpath,'HOME':str(self.home),'GIT_CONFIG_NOSYSTEM':'1',
                                    'GIT_CONFIG_GLOBAL':os.devnull,'GIT_TERMINAL_PROMPT':'0'})
            name='_sf_candidate_unit_'+self.root.name.replace('-','_')
            spec=importlib.util.spec_from_file_location(name,PLUGIN/'__init__.py',submodule_search_locations=[str(PLUGIN)])
            self.plugin=importlib.util.module_from_spec(spec);sys.modules[name]=self.plugin;spec.loader.exec_module(self.plugin)
            self.h=self.plugin._handoff;self.g=self.plugin._guard
            self.addCleanup(lambda: [sys.modules.pop(n,None) for n in list(sys.modules) if n==name or n.startswith(name+'.')])
            self.meta={'task_id':self.tid,'workspace':str(self.workspace),'workspace_path':str(self.workspace)}
            self.args={'summary':'UNIT TEST fixture only','reviewer':'reviewer-gpt','metadata':copy.deepcopy(self.meta)}
            self.result=json.dumps({'ok':True,'task_id':self.tid,'run_id':self.rid,'status':'review'})
            with self.conn() as c:
                c.executescript('''
    CREATE TABLE tasks(id TEXT PRIMARY KEY,body TEXT,workspace_kind TEXT,workspace_path TEXT,assignee TEXT,status TEXT,claim_lock TEXT,claim_expires INTEGER,worker_pid INTEGER,current_run_id INTEGER);
    CREATE TABLE task_runs(id INTEGER PRIMARY KEY,task_id TEXT,profile TEXT,status TEXT,started_at INTEGER,outcome TEXT,summary TEXT,error TEXT,metadata TEXT,ended_at INTEGER,claim_lock TEXT,claim_expires INTEGER,worker_pid INTEGER);
    CREATE TABLE task_events(id INTEGER PRIMARY KEY,task_id TEXT,run_id INTEGER,kind TEXT,payload TEXT,created_at INTEGER);
    ''')
                c.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(self.tid,'WORKSPACE: worktree:'+str(self.repo),'worktree',str(self.workspace),'coder-claude','running','owner',int(time.time())+600,os.getpid(),1))
                c.execute('INSERT INTO task_runs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(1,self.tid,'coder-claude','running',int(time.time())-1,None,None,None,json.dumps(self.meta),None,'owner',int(time.time())+600,os.getpid()))
                c.commit()
            def get_task(c,tid):
                row=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
                return types.SimpleNamespace(**dict(row)) if row else None
            self.kb=types.SimpleNamespace(get_current_board=lambda:self.board,board_exists=lambda x:x==self.board,
                connect_closing=lambda board:self.conn(),get_task=get_task)
            self.load=patch.object(self.h,'_load_kanban_db',return_value=self.kb);self.load.start();self.addCleanup(self.load.stop)
            self.state=self.h.workspace_content_state(str(self.workspace));self.assertIsNotNone(self.state)
            self.binary=self.root/'SYNTHETIC_CLAUDE_NOT_EXECUTABLE';self.binary.write_text('unit fixture; not real CLI')
            self.binary_sha=hashlib.sha256(self.binary.read_bytes()).hexdigest()
            self.canon=patch.object(self.g,'_canonical_claude_identity',return_value=(str(self.binary),self.binary_sha));self.canon.start();self.addCleanup(self.canon.stop)
            self.evidence={'schema':6,'profile':'coder-claude','task_id':self.tid,'run_id':'1','model_class':'sonnet',
                'workspace':str(self.workspace),'execution_cwd':str(self.workspace),'terminal_args_sha256':'a'*64,
                'claude_binary':str(self.binary),'claude_binary_sha256':self.binary_sha,
                'session_id':'UNIT_TEST_NOT_A_REAL_CLAUDE_SESSION','success':True,'command_sha256':'b'*64,
                'attestation_id':'c'*64,'git_head_before':self.state[0],'git_head_after':self.state[0],
                'workspace_content_state_before_sha256':self.state[1],'workspace_content_state_after_sha256':self.state[1],
                'recorded_at':int(time.time())}
            self.evidence_path=self.h.execution_evidence_path(self.board,self.tid,1)
            self.evidence_path.parent.mkdir(parents=True);self.write_evidence()
            self.key=self.g._attestation_key('coder-claude',self.tid,'1')
            self.g._COMPLETED_ATTESTATIONS[self.key]={k:self.evidence[k] for k in ('attestation_id','command_sha256','execution_cwd','terminal_args_sha256')}
            self.seal_path=self.h.handoff_path(self.board,self.tid,1)
            self.assertTrue(self.g._evidence_exists('coder-claude',self.tid))
            self.addCleanup(self.temp.cleanup)
    @contextlib.contextmanager
    def conn(self):
            c=sqlite3.connect(self.db);c.row_factory=sqlite3.Row
            try:yield c
            finally:c.close()
    def sql(self,statement,params=()):
            with self.conn() as c:c.execute(statement,params);c.commit()
    def rows(self):
            with self.conn() as c:return {t:[dict(r) for r in c.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('tasks','task_runs','task_events')}
    def write_evidence(self):self.evidence_path.write_text(json.dumps(self.evidence,sort_keys=True)+'\n')
    def before(self,args=None):
            return self.plugin.on_pre_tool_call(tool_name='kanban_request_review',args=self.args if args is None else args)
    def transition(self,**kwargs):
            kw={'metadata':self.meta,'summary':self.args['summary'],'reviewer':'reviewer-gpt','expected_run_id':1,'force':False,'with_reason':True};kw.update(kwargs)
            with self.conn() as c:return NATIVE_FUNCTIONS['request_review'](c,self.tid,**kw)
    def after(self,result=None,args=None):
            return self.plugin.on_post_tool_call(tool_name='kanban_request_review',args=self.args if args is None else args,result=self.result if result is None else result)
    def prepare_and_transition(self):
            self.assertIsNone(self.before());self.assertEqual(self.transition(),(True,None))
    def assert_no_seal(self):self.assertFalse(self.seal_path.exists());self.assertNotIn(self.key,self.g._SEALED_CODER_RUNS)