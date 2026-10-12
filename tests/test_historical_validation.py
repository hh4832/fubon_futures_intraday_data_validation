import json
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import pytest
from src.historical_client import HistoricalReader,HistoricalFatalError,load_historical_config,recent_complete_dates,read_canonical_documentation
from src.historical_normalization import normalize_historical
from src.historical_validation import evaluate,daily_coverage,status_rollup
from src.historical_overlap import compare_frames,compare_external
from src.historical_runner import HistoricalValidationRun
from test_historical_normalization import calendar_fixture,item_fixture


def test_daily_gap_duplicate_ohlc():
    item=item_fixture(['2026-10-12T08:45:00+08:00','2026-10-12T08:47:00+08:00','2026-10-12T08:47:00+08:00'])
    item['response']['data'][0]['high']='98'
    df=normalize_historical(item,calendar_fixture())
    result=evaluate(item,df,calendar_fixture())
    day=result['daily'].set_index('trade_date').loc['2026-10-12']
    assert day.duplicate_count==1 and day.gap_event_count==1
    assert day.missing_slot_count==298
    assert day.invalid_ohlc_count==1 and result['status']=='FAIL'
    assert not day.confirmed_missing_data


def test_missing_requested_date_unresolved():
    item=item_fixture(['2026-10-12T08:45:00+08:00'])
    result=evaluate(item,normalize_historical(item,calendar_fixture()),calendar_fixture())
    assert len(result['daily'])==3
    assert result['daily'].loc[result['daily'].trade_date.eq('2026-10-08'),'status'].iloc[0]=='UNRESOLVED'
    assert result['status']=='UNRESOLVED'


def test_no_cross_day_gap():
    item=item_fixture(['2026-10-08T08:45:00+08:00','2026-10-12T08:45:00+08:00','2026-10-13T08:45:00+08:00'])
    result=evaluate(item,normalize_historical(item,calendar_fixture()),calendar_fixture())
    assert result['timestamp']['observed_gaps']==0


def test_schema_identity():
    item=item_fixture(['2026-10-12T08:45:00+08:00'])
    item['response']['contractMonth']='2!'
    assert evaluate(item,normalize_historical(item,calendar_fixture()),calendar_fixture())['schema']['schema_status']=='FAIL'


def test_exact_comparison_and_unmatched_rows():
    df=normalize_historical(item_fixture(['2026-10-12T08:45:00+08:00','2026-10-12T08:46:00+08:00']),calendar_fixture())
    detail,summary=compare_frames(df,df.copy())
    assert summary.exact_match_rows.eq(2).all() and summary.match_rate.eq(1).all()
    altered=df.copy();altered.loc[0,'close']+=1
    _,summary=compare_frames(df,altered)
    assert summary.set_index('field').loc['close','status']=='FAIL'
    _,summary=compare_frames(df,df.iloc[:1])
    assert summary.unmatched_rows.eq(1).all()
    assert summary.status.eq('PASS WITH CAVEATS').all()


def test_duplicate_comparison_rejected():
    df=normalize_historical(item_fixture(['2026-10-12T08:45:00+08:00']*2),calendar_fixture())
    _,summary=compare_frames(df,df)
    assert summary.status.eq('FAIL').all()


def test_external_not_performed():
    result=compare_external(None,None,None)
    assert result.external_validation.iloc[0]=='NOT PERFORMED'


def test_complete_sessions_excludes_unfinished():
    cal=calendar_fixture()
    assert recent_complete_dates(cal,'regular',now='2026-10-13T10:00:00+08:00',count=2)==['2026-10-08','2026-10-12']
    assert recent_complete_dates(cal,'afterhours',now='2026-10-13T04:59:00+08:00',count=2)==['2026-10-08','2026-10-12']
    with pytest.raises(HistoricalFatalError):recent_complete_dates(cal,'regular',now='2026-11-01T10:00:00+08:00',count=2)


def test_reader_contract_and_auth_guard(tmp_path):
    for d in ['raw','diagnostics']:(tmp_path/d).mkdir()
    cfg=load_historical_config();cfg['request_spacing_seconds']=0
    class AuthenticationError(Exception):status_code=401
    class API:
        def candles(self,**kwargs):raise AuthenticationError('secret-like SDK details')
    rest=SimpleNamespace(historical=API(),intraday=None)
    r=HistoricalReader(rest,tmp_path,cfg)
    with pytest.raises(HistoricalFatalError):r.fetch('test','1!',['2026-10-12'])
    assert r.calls==1
    assert 'secret-like' not in (tmp_path/'raw'/'test_attempt1.json').read_text()
    with pytest.raises(ValueError):r.fetch('test','1!',['2025-10-12','2026-10-12'])


def test_document_missing_fail_safe(tmp_path):
    cfg=dict(load_historical_config(),drive_root=str(tmp_path))
    with pytest.raises(HistoricalFatalError):read_canonical_documentation(cfg)


def test_blocked_stage_does_not_run_later_phases(tmp_path):
    for d in ['raw','data','diagnostics']:(tmp_path/d).mkdir()
    cfg=dict(load_historical_config(),max_phase=7)
    fake=SimpleNamespace(attempts=[],calls=0)
    info={'git_commit':'synthetic','git_branch':'synthetic','fubon_sdk_version':'not installed','login_status':'NOT TESTED'}
    r=HistoricalValidationRun(None,tmp_path,cfg,info,calendar_fixture(),reader=fake)
    r.phase1=lambda:'UNRESOLVED'
    r.phase2=lambda:pytest.fail('must not skip unresolved gate')
    r.run()
    assert len(r.stages)==7 and all(s['notes'].startswith('BLOCKED') for s in r.stages[1:])
    text=r.summary()
    assert '修改後再測' in text and 'NOT PERFORMED' in text
    assert (tmp_path/'validation_summary.txt').is_file()


def test_status_rollup():
    assert status_rollup(['PASS','PASS WITH CAVEATS'])=='PASS WITH CAVEATS'
    assert status_rollup(['PASS','UNRESOLVED'])=='UNRESOLVED'
    assert status_rollup(['FAIL','UNRESOLVED'])=='FAIL'


def test_historical_empty_response_unresolved():
    item=item_fixture([])
    df=normalize_historical(item,calendar_fixture())
    result=evaluate(item,df,calendar_fixture())
    assert result['status']=='UNRESOLVED'
    assert result['daily'].row_count.eq(0).all()


def test_actual_month_alias_is_not_identity():
    item=item_fixture(['2026-10-12T08:45:00+08:00'],actual='1!')
    result=evaluate(item,normalize_historical(item,calendar_fixture()),calendar_fixture())
    assert result['schema']['schema_status']=='FAIL'


def test_explicit_response_authentication_failure_stops(tmp_path):
    for d in ['raw','diagnostics']:(tmp_path/d).mkdir()
    class API:
        def candles(self,**kwargs):return {'statusCode':403,'data':[]}
    cfg=dict(load_historical_config(),request_spacing_seconds=0)
    r=HistoricalReader(SimpleNamespace(historical=API(),intraday=None),tmp_path,cfg)
    with pytest.raises(HistoricalFatalError):r.fetch('test','1!',['2026-10-12'])
