"""Lightweight progress must preserve durable work and terminal authority."""
from copy import deepcopy
from types import SimpleNamespace
import json

import pytest

import strategy_lab_jobs as jobs
from strategy_lab_progress import PROGRESS_FORMAT, merge_progress, progress_path, progress_store
from strategy_lab_persistence import save_strategy_lab_checkpoint, load_latest_strategy_lab_checkpoint
from youtube_strategy_engine import StrategyStore, AppError, GitHubCloudBackup
from hybrid_runtime.strategy_lab_bridge import read_strategy_lab_progress
from hybrid_runtime.github_library import GitHubLibraryConfig


def base_record(**changes):
    return {'id': 'sls-run', 'ticker': 'SLS', 'attempt': 2,
            'started_at': '2026-09-05T01:00:00Z', 'saved_at': '2026-09-05T01:01:00Z',
            'status': 'running', 'stage': 'optimization', 'progress': .4,
            'progress_storage': PROGRESS_FORMAT, 'record_type': 'strategy_lab_checkpoint',
            'optimizer_state': {'completed_strategy_ids': ['one', 'two', 'three']}, **changes}


def sidecar(**changes):
    return {'validation_runs': [{**base_record(), 'record_type': PROGRESS_FORMAT,
             'saved_at': '2026-09-05T01:02:00Z', 'message': 'Next trial', 'progress': .46, **changes}]}


@pytest.mark.parametrize('changes', [{'id':'another-run'}, {'ticker':'CHPT'}, {'attempt':1}, {'attempt':3},
    {'started_at':'2026-09-04T01:00:00Z'}, {'status':'complete'}, {'progress':1}, {'progress':float('nan')},
    {'progress':-.1}, {'progress':.2}, {'saved_at':'2026-09-05T01:00:00Z'}, {'saved_at':'bad'},
    {'record_type':'unknown'}])
def test_wrong_stale_or_terminal_progress_cannot_replace_checkpoint(changes):
    base = base_record()
    assert merge_progress(base, sidecar(**changes)) == base


def test_progress_overlay_never_copies_results_or_changes_promotion_and_resume_state():
    base = base_record(promotion_status='research_only')
    result = merge_progress(base, sidecar(result={'fake':'pass'}, optimizer_state={}, promotion_status='eligible'))
    assert result['progress'] == .46 and result['message'] == 'Next trial'
    assert result['optimizer_state'] == base['optimizer_state']
    assert result['promotion_status'] == 'research_only' and 'result' not in result
    for status in ['failed','complete','cancelled']:
        terminal = {**base, 'status': status}
        assert merge_progress(terminal, sidecar()) == terminal
    assert base['progress'] == .4


def test_progress_path_is_exact_and_safe():
    path = progress_path('trading-intelligence-lab/strategy_lab_latest.json', '../../run?secret=no')
    assert path.startswith('trading-intelligence-lab/strategy_lab_latest-progress/')
    assert '..' not in path and '?' not in path
    assert path != progress_path('trading-intelligence-lab/strategy_lab_latest.json', 'another')
    with pytest.raises(ValueError): progress_path('checkpoint.json', '')


def test_optional_progress_failure_cannot_break_checkpoint_loading(monkeypatch):
    import strategy_lab_progress as module
    base = base_record(progress='invalid')
    assert merge_progress(base, sidecar()) == base
    base = base_record()
    def unavailable(*args):
        raise OSError('Fixture progress directory unavailable')
    monkeypatch.setattr(module, 'progress_store', unavailable)
    assert module.read_progress(object(), base) == base


def test_real_store_progress_does_not_rewrite_large_history_and_completion_stays_durable(tmp_path, monkeypatch):
    full = StrategyStore(tmp_path/'full')
    historical = {'ticker':'LIDR','large_evidence':'keep-this-' * 200000}
    save_strategy_lab_checkpoint(full,run_id='older',status='complete',ticker='LIDR',result=historical)
    clock = [100.0]
    monkeypatch.setattr(jobs.time,'monotonic',lambda:clock[0])
    writes=[];original_save=StrategyStore.save
    def observed(store,data):
        writes.append((store is full,len(json.dumps(data)),deepcopy(data['validation_runs'][0])))
        return original_save(store,data)
    monkeypatch.setattr(StrategyStore,'save',observed)
    observed_state={}
    def execute(job,**callbacks):
        for index in range(12):
            clock[0]+=11
            callbacks['progress'](.4+index*.01,'optimization','Trial '+str(index))
        loaded=load_latest_strategy_lab_checkpoint(full,run_id='sls-run')
        assert loaded['progress'] == pytest.approx(.51)
        assert loaded['message'] == 'Trial 11' and loaded['status']=='running'
        callbacks['optimizer_checkpoint']({'completed_strategy_ids':['one'], 'rankings':[{'source_strategy_id':'one'}]})
        observed_state.update(full.load()['validation_runs'][0]['optimizer_state'])
        return {'ticker':'SLS','verdict':'research_only','evidence':{'untouched':True}}
    result=jobs.execute_strategy_lab_job_once(run_id='sls-run',job={'ticker':'SLS','search_depth':160},
        checkpoint_store=full,market=object(),main_store=object(),executor=execute)
    assert result['status']=='complete'
    assert len([w for w in writes if w[0]])==3  # initial, completed family, terminal
    small=[w for w in writes if not w[0]]
    assert len(small)==12 and max(w[1] for w in small)<2500
    assert all('optimizer_state' not in w[2] and 'result' not in w[2] and 'job' not in w[2] for w in small)
    assert observed_state['completed_strategy_ids']==['one']
    completed=load_latest_strategy_lab_checkpoint(full,run_id='sls-run')
    assert completed['status']=='complete' and completed['result']==result['result']
    # An older completed result remains losslessly restorable after retention compaction.
    older=load_latest_strategy_lab_checkpoint(full,run_id='older')
    assert older['result']==historical


@pytest.mark.parametrize('fail', [False, True])
def test_slow_upload_does_not_immediately_repeat_and_failures_back_off(tmp_path, monkeypatch, fail):
    full=StrategyStore(tmp_path/'full');clock=[100.0];saved=[]
    monkeypatch.setattr(jobs.time,'monotonic',lambda:clock[0])
    def slow_save(*args,**kwargs):
        saved.append(clock[0]);clock[0]+=20
        if fail: raise AppError('Fixture storage unavailable')
    monkeypatch.setattr(jobs,'save_progress',slow_save)
    def execute(job,**callbacks):
        callbacks['progress'](.4,'optimization','first')
        for index in range(20): callbacks['progress'](.4,'optimization','same step')
        assert len(saved)==1
        # Even a changed stage is throttled after failed storage.
        if fail:
            callbacks['progress'](.41,'validation','changed stage')
            assert len(saved)==1
        clock[0]+=11
        callbacks['progress'](.42,'optimization','later')
        assert len(saved)==2
        return {'ticker':'SLS','evidence':'fixture'}
    result=jobs.execute_strategy_lab_job_once(run_id='sls-run',job={'ticker':'SLS'},checkpoint_store=full,
        market=object(),main_store=object(),executor=execute)
    assert result['status']=='complete'
    assert bool(result['progress_warnings'])==fail


def test_exact_resume_ignores_newer_unrelated_record_and_preserves_attempt_budget(tmp_path):
    full=StrategyStore(tmp_path/'full')
    state={'completed_strategy_ids':['saved-family'], 'rankings':[{'source_strategy_id':'saved-family'}]}
    save_strategy_lab_checkpoint(full,run_id='sls-run',status='running',ticker='SLS',attempt=2,optimizer_state=state)
    save_strategy_lab_checkpoint(full,run_id='newer',status='complete',ticker='CHPT',result={'keep':True})
    seen=[]
    def execute(job,**kwargs):
        seen.append(kwargs['optimizer_resume_state']);return {'ticker':'SLS','evidence':'fixture'}
    result=jobs.execute_strategy_lab_job_once(run_id='sls-run',job={'ticker':'SLS'},checkpoint_store=full,
        market=object(),main_store=object(),executor=execute)
    assert result['status']=='complete' and seen==[state]
    assert load_latest_strategy_lab_checkpoint(full,run_id='sls-run')['attempt']==3
    save_strategy_lab_checkpoint(full,run_id='exhausted',status='failed',ticker='SLS',attempt=3,optimizer_state=state)
    result=jobs.execute_strategy_lab_job_once(run_id='exhausted',job={'ticker':'SLS'},checkpoint_store=full,
        market=object(),main_store=object(),executor=execute)
    assert result['status']=='failed' and len(seen)==1


def test_cloud_reader_overlays_only_small_bound_sidecar_and_falls_back_on_missing():
    library={'validation_runs':[base_record()]};calls=[]
    config=GitHubLibraryConfig(repository='fixture/private')
    def factory(cfg,token):
        calls.append(cfg);return SimpleNamespace(read=lambda:SimpleNamespace(data=sidecar()))
    result=read_strategy_lab_progress(library,config,'fixture',factory)
    assert result['validation_runs'][0]['progress']==.46
    assert calls[0].repository==config.repository and calls[0].branch==config.branch
    assert calls[0].path==progress_path('trading-intelligence-lab/strategy_lab_latest.json','sls-run')
    def broken(*args):raise RuntimeError('missing sidecar')
    assert read_strategy_lab_progress(library,config,'fixture',broken)==library

