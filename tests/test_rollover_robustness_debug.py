"""Regression for real expiry-day calendar geometry, offline replay and fatal reports."""
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
import pytest
from src.fatal_diagnostics import redact_sensitive_text
from src.historical_client import HistoricalFatalError, session_calendar
from src.rollover_robustness import load_robustness_config,run_rollover_robustness
from src.rollover_robustness_analysis import analyze_session,contract_coverage
from src.rollover_robustness_smoke import run_first_regular_smoke
from scripts.replay_rollover_robustness import replay_archive
from test_rollover_robustness import setup_runner,fixture


def june_full_frames():
    cfg=load_robustness_config();sample=cfg['rollover_samples'][0];local=dict(cfg,**sample)
    cal=session_calendar(cfg);cal=cal[cal.sample_id.eq(sample['sample_id'])].copy()
    def concrete(month,base):
        parts=[]
        for date in sample['trade_dates']:
            if month==sample['old_contract'] and date>sample['expiry_date']:continue
            end=date+('T13:30:00+08:00' if month==sample['old_contract'] and date==sample['expiry_date'] else 'T13:45:00+08:00')
            ts=pd.date_range(date+'T08:45:00+08:00',end,freq='min',inclusive='left').tz_convert('Asia/Taipei')
            prices=base+np.arange(len(ts),dtype=float)
            parts.append(pd.DataFrame(dict(timestamp=ts,trade_date=date,actual_contract_month=pd.Series([month]*len(ts),dtype='string'),
                response_contractMonth=month,open=prices,high=prices+2,low=prices-1,close=prices+1,volume=10.,average=prices+.5)))
        return pd.concat(parts,ignore_index=True)
    old=concrete(sample['old_contract'],100);new=concrete(sample['new_contract'],103)
    continuous=pd.concat([old,new[new.trade_date.gt(sample['expiry_date'])]],ignore_index=True)
    continuous['response_contractMonth']='1!'
    return {'1!':continuous,sample['old_contract']:old,sample['new_contract']:new},cal,local


def test_non_september_full_expiry_session_retains_1330_and_timezone():
    frames,cal,cfg=june_full_frames()
    tables,quality=analyze_session(frames,cfg,cal,'regular')
    assert quality=='PASS WITH CAVEATS'
    assert tables['fields'].match_rate.eq(1).all()
    missing=tables['missing']
    expiry=missing[missing.trade_date.eq('2026-06-17')].set_index('contract_query')
    assert expiry.loc['1!','reference_slot_count']==285
    assert expiry.loc['202606','reference_slot_count']==285
    assert expiry.loc['202607','reference_slot_count']==300
    assert expiry.loc['1!','missing_slot_count']==0
    assert tables['mapping'].timestamp.dt.tz.zone=='Asia/Taipei'
    assert tables['transition'].iloc[0].first_trade_date_new_contract=='2026-06-18'


def test_fixed_offset_and_named_zone_same_instant_but_range_requires_same_zone():
    start=pd.Timestamp('2026-06-17T08:45:00',tz='Asia/Taipei')
    end=pd.Timestamp('2026-06-17T13:30:00+08:00')
    assert end==end.tz_convert(start.tz)
    with pytest.raises(TypeError,match='different timezones'):
        pd.date_range(start,end,freq='min',inclusive='left')
    assert len(pd.date_range(start,end.tz_convert(start.tz),freq='min',inclusive='left'))==285


def test_coverage_month_aliases_are_compatibility_names_not_fixed_values():
    frames,cal,cfg=june_full_frames()
    coverage=contract_coverage(frames['1!'],cfg,cfg['trade_dates'])
    assert coverage.loc[coverage.trade_date.le(cfg['expiry_date']),'contains_202609'].all()
    assert coverage.loc[coverage.trade_date.gt(cfg['expiry_date']),'contains_202610'].all()
    tables,_=analyze_session(frames,cfg,cal,'regular')
    assert 'contains_old_contract' in tables['coverage'] and 'contains_202609' not in tables['coverage']
    assert 'contains_new_contract' in tables['coverage'] and 'contains_202610' not in tables['coverage']


def create_archive(tmp_path):
    frames,cal,local=june_full_frames();cfg=load_robustness_config()
    archive=tmp_path/'failed';(archive/'data').mkdir(parents=True);(archive/'diagnostics').mkdir()
    (archive/'config_snapshot.json').write_text(json.dumps(cfg))
    calendar=session_calendar(cfg);calendar.to_csv(archive/'diagnostics/rollover_calendar_snapshot.csv',index=False)
    pd.DataFrame(cfg['rollover_samples']).to_csv(archive/'diagnostics/rollover_sample_metadata.csv',index=False)
    for query,frame in frames.items():
        frame.to_parquet(archive/'data'/f"202606_202607_regular_{'1bang' if query=='1!' else query}.parquet",index=False)
    return archive


def test_offline_replay_uses_saved_config_calendar_metadata_and_only_parquet(tmp_path,monkeypatch):
    archive=create_archive(tmp_path)
    def no_api(*args,**kwargs):pytest.fail('offline replay must never construct market readers')
    monkeypatch.setattr('src.rollover_robustness.HistoricalReader',no_api)
    output,info=replay_archive(archive,tmp_path/'replay')
    assert info['api_request_count']==0 and info['replay_status']=='PASS'
    assert info['analysis_classification']=='PASS WITH CAVEATS'
    assert info['row_counts']=={'1!':2685,'202606':1485,'202607':2700}
    assert len(info['input_sha256'])==6
    assert pd.read_csv(output/'diagnostics/replay_fields.csv').match_rate.eq(1).all()
    with pytest.raises(ValueError):replay_archive(archive,archive/'replay')
    with pytest.raises(FileExistsError):replay_archive(archive,output)


def test_replay_keeps_semantic_unresolved_not_forced_pass(tmp_path):
    archive=create_archive(tmp_path);path=archive/'data/202606_202607_regular_1bang.parquet'
    frame=pd.read_parquet(path);frame.loc[0,'close']+=.1;frame.to_parquet(path,index=False)
    _,info=replay_archive(archive,tmp_path/'replay')
    assert info['replay_status']=='PASS' # execution, not data-quality override
    assert info['analysis_classification']=='UNRESOLVED'


def test_replay_refuses_changed_archived_sample_metadata(tmp_path):
    archive=create_archive(tmp_path);path=archive/'diagnostics/rollover_sample_metadata.csv'
    metadata=pd.read_csv(path,dtype=str);metadata.loc[0,'new_contract']='202608';metadata.to_csv(path,index=False)
    with pytest.raises(HistoricalFatalError,match='metadata differs'):
        replay_archive(archive,tmp_path/'replay')


def test_fatal_traceback_message_and_original_cause_preserved(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch)
    def fail(*args):raise TypeError('Start and end cannot both be tz-aware with different timezones')
    monkeypatch.setattr('src.rollover_robustness.analyze_session',fail)
    with pytest.raises(HistoricalFatalError) as caught:
        run_rollover_robustness(rest,root,sdk,login,cfg)
    assert isinstance(caught.value.__cause__,TypeError)
    assert len(calls)==3
    output=next(Path(cfg['drive_root']).iterdir())
    trace=(output/'diagnostics/fatal_traceback.txt').read_text()
    assert 'TypeError: Start and end' in trace and 'fail' in trace
    info=(output/'run_info.txt').read_text()
    assert 'fatal_error_message="Start and end' in info and 'fatal_error_type="TypeError"' in info
    assert 'execution_status="STOPPED"' in info


def test_exception_reports_redact_credentials_and_account_number(monkeypatch):
    secret='synthetic-private-key-for-test'
    monkeypatch.setenv('FUBON_API_KEY',secret)
    text=redact_sensitive_text('TypeError: '+secret+'\naccount number=123456789012\ncertificate password=unsafe\nAPI key=unsafe')
    assert secret not in text and '123456789012' not in text and 'unsafe' not in text
    assert 'TypeError' in text


def test_live_smoke_gate_and_exactly_three_unchanged_regular_queries(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch)
    monkeypatch.setattr('src.rollover_robustness_smoke.read_canonical_documentation',lambda cfg:(b'offline fixture',{}))
    with pytest.raises(HistoricalFatalError,match='offline replay'):
        run_first_regular_smoke(rest,root,sdk,login,{},cfg)
    assert not calls
    replay=dict(replay_status='PASS',analysis_classification='PASS WITH CAVEATS',sample_id='202606_202607',session='regular',api_request_count=0)
    output,info=run_first_regular_smoke(rest,root,sdk,login,replay,cfg)
    assert len(calls)==3 and info['logical_request_count']==3 and info['expected_logical_request_count']==3
    assert info['smoke_status']=='PASS WITH CAVEATS'
    assert all(c['session']=='regular' and c['from']=='2026-06-11' and c['to']=='2026-06-24' and c['timeframe']=='1' for c in calls)
    assert [c['contractMonth'] for c in calls]==['1!','202606','202607']
    assert (output/'diagnostics/smoke_fields.csv').is_file()
    assert info['validation_phase']=='rollover_robustness_smoke'


def test_formal_fatal_files_redact_env_secret_and_labelled_account(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch)
    secret='offline-sensitive-secret-value'
    monkeypatch.setenv('FUBON_API_KEY',secret)
    def fail(*args):raise TypeError(secret+' account number=123456789012')
    monkeypatch.setattr('src.rollover_robustness.analyze_session',fail)
    with pytest.raises(HistoricalFatalError):run_rollover_robustness(rest,root,sdk,login,cfg)
    archive=next(Path(cfg['drive_root']).iterdir())
    for name in ['run_info.txt','diagnostics/fatal_traceback.txt']:
        text=(archive/name).read_text()
        assert secret not in text and '123456789012' not in text
        assert '[REDACTED]' in text
