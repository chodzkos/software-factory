"""Kontrolowane dane do testów pełnego natywnego SQL; bez uruchamiania modeli."""
from __future__ import annotations
import json
import os
from pathlib import Path
from unittest.mock import patch

def prepare_review_fixture(case):
    """Pełny natywny schema/SQL; claim, attestation i connect do WAL są fixture.

    Tylko tymczasowa baza: lokalny SQLite domyślnie nie dostaje WAL od
    Hermesa. Test celowo modeluje zastany WAL; nie zmienia zasad produktu.
    """
    import hashlib
    import sqlite3
    import subprocess
    import time
    from hermes_cli import kanban_db as kb
    case.review_kb = kb
    case.review_env = patch.dict(os.environ, HERMES_KANBAN_HOME=str(case.fixture.root/'kb-home'))
    case.review_env.start(); case.addCleanup(case.review_env.stop)
    kb.create_board('fixture')
    db = kb.kanban_db_path(board='fixture')
    conn = kb.connect(board='fixture')
    case.addCleanup(conn.close)
    repo = case.fixture.root/'review-repository';repo.mkdir()
    tid = kb.create_task(conn,title='LOCAL NATIVE REVIEW FIXTURE',assignee='coder-claude',workspace_kind='worktree',workspace_path=str(repo),initial_status='running',board='fixture')
    ws = repo/'.worktrees'/tid;ws.mkdir(parents=True)
    (ws/'tracked.txt').write_text('offline review fixture\n')
    git_env={'PATH':os.defpath,'HOME':str(case.fixture.root),'GIT_CONFIG_NOSYSTEM':'1','GIT_CONFIG_GLOBAL':os.devnull,'GIT_TERMINAL_PROMPT':'0'}
    for args in (['init','-q'],['add','.'],['-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','fixture']):
        subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','-C',str(ws),*args],env=git_env,check=True,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
    now = int(time.time());meta={'task_id':tid,'workspace':str(ws),'workspace_path':str(ws)}
    conn.execute('UPDATE tasks SET body=?,workspace_path=?,status=?,claim_lock=?,claim_expires=?,worker_pid=?,current_run_id=? WHERE id=?',
        ('WORKSPACE: worktree:'+str(repo),str(ws),'running','fixture-owner',now+600,os.getpid(),7,tid))
    conn.execute('INSERT INTO task_runs(id,task_id,profile,status,started_at,metadata,claim_lock,claim_expires,worker_pid) VALUES(?,?,?,?,?,?,?,?,?)',
        (7,tid,'coder-claude','running',now-1,json.dumps(meta),'fixture-owner',now+600,os.getpid()))
    conn.commit()
    case.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower(),'wal')
    conn.close()
    def connect(db_path=None,*,board=None):
        case.assertIn(board,(None,'fixture'))
        if db_path is not None:case.assertEqual(Path(db_path),db)
        connection=sqlite3.connect(db,timeout=.25);connection.row_factory=sqlite3.Row
        return connection
    case.review_connect=connect
    case.connect_patch=patch.object(kb,'connect',side_effect=connect)
    case.connect_patch.start();case.addCleanup(case.connect_patch.stop)
    os.environ.update(HERMES_KANBAN_TASK=tid,HERMES_KANBAN_RUN_ID='7',HERMES_KANBAN_WORKSPACE=str(ws))
    h=case.state.proof.module._handoff
    binary=case.fixture.root/'SYNTHETIC_CLAUDE_NOT_EXECUTABLE';binary.write_text('test only, no CLI executed')
    binary_sha=hashlib.sha256(binary.read_bytes()).hexdigest()
    canonical=patch.object(case.guard,'_canonical_claude_identity',return_value=(str(binary),binary_sha))
    canonical.start();case.addCleanup(canonical.stop)
    content_state=h.workspace_content_state(str(ws));case.assertIsNotNone(content_state)
    evidence={'schema':6,'profile':'coder-claude','task_id':tid,'run_id':'7','model_class':'sonnet',
        'workspace':str(ws),'execution_cwd':str(ws),'terminal_args_sha256':'a'*64,
        'claude_binary':str(binary),'claude_binary_sha256':binary_sha,
        'session_id':'SYNTHETIC_NO_CLAUDE_SESSION','success':True,'command_sha256':'b'*64,
        'attestation_id':'c'*64,'git_head_before':content_state[0],'git_head_after':content_state[0],
        'workspace_content_state_before_sha256':content_state[1],'workspace_content_state_after_sha256':content_state[1],
        'recorded_at':now}
    evidence_path=h.execution_evidence_path('fixture',tid,7);evidence_path.parent.mkdir(parents=True)
    evidence_path.write_text(json.dumps(evidence,sort_keys=True)+'\n')
    key=case.guard._attestation_key('coder-claude',tid,'7')
    case.guard._COMPLETED_ATTESTATIONS[key]={k:evidence[k] for k in ('attestation_id','command_sha256','execution_cwd','terminal_args_sha256')}
    case.assertTrue(case.guard._evidence_exists('coder-claude',tid))
    case.review_tid=tid
    return {'summary':'LOCAL FIXTURE ONLY','reviewer':'reviewer-gpt','metadata':meta}


from types import SimpleNamespace

def assert_executor_handoff(case, executor):
    args=case.setup_review_fixture()
    response=SimpleNamespace(tool_calls=[SimpleNamespace(id='native_review_executor',function=SimpleNamespace(name='kanban_request_review',arguments=json.dumps(args)))])
    messages=[]
    executor(case.agent,response,messages,case.review_tid,finalize=False)
    case.assertEqual(len(messages),1)
    result=json.loads(messages[0]['content'])
    case.assertEqual(result,{'ok':True,'task_id':case.review_tid,'run_id':7,'status':'review'})
    seal=case.state.proof.module._handoff.load_handoff_seal('fixture',case.review_tid,7)
    case.assertEqual(seal['schema'],3)


def assert_receipt_and_null_pids(case):
    h=case.state.proof.module._handoff
    seal=h.load_handoff_seal('fixture',case.review_tid,7)
    case.assertEqual(seal['schema'],3)
    conn=case.review_connect(board='fixture')
    try:
        task=case.review_kb.get_task(conn,case.review_tid)
        case.assertEqual(task.status,'review');case.assertEqual(task.assignee,'reviewer-gpt');case.assertIsNone(task.worker_pid)
        case.assertIsNone(conn.execute('SELECT worker_pid FROM task_runs WHERE id=7').fetchone()[0])
        events=[row[0] for row in conn.execute('SELECT kind FROM task_events WHERE task_id=? ORDER BY id',(case.review_tid,))]
        case.assertEqual(events[-2:],['review_requested','factory_handoff_committed'])
        case.assertEqual(events.count('factory_handoff_committed'),1)
        conn.execute("DELETE FROM task_events WHERE kind='factory_handoff_committed'");conn.commit()
        with case.assertRaises(h.HandoffError):h.load_handoff_seal('fixture',case.review_tid,7)
    finally:conn.close()
