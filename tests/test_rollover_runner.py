from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import numpy as np
import pandas as pd
import pytest
import yaml
from src.continuous_rollover import load_rollover_config,run_rollover
from src.historical_client import session_calendar,HistoricalFatalError


def clean_repo(root):
    root.mkdir()
    subprocess.run(['git','init','-b','synthetic-test',str(root)],capture_output=True,check=True)
    (root/'README.md').write_text('synthetic offline fixture')
    subprocess.run(['git','add','README.md'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Synthetic','-c','user.email=synthetic@example.test',
        'commit','-m','synthetic test fixture'],cwd=root,capture_output=True,check=True)


def fake_rest(cfg,empty_contract=None,unsafe=False,authentication=False):
    calendar=session_calendar(cfg)
    dates=calendar.loc[calendar.trade_date.between(cfg['window_from'],cfg['window_to']),'trade_date']
    calls=[]
    class Synthetic:
        def candles(self,**params):
            calls.append(params.copy())
            if authentication:
                class AuthError(Exception):status_code=401
                raise AuthError('do-not-archive-this-message')
            query=params['contractMonth']
            rows=[]
            for date in dates:
                if query==cfg['old_contract'] and date>cfg['expiry_date']:continue
                actual=('202609' if date<=cfg['expiry_date'] else '202610') if query=='1!' else query
                base=100 if actual=='202609' else 103
                timestamps=pd.date_range(date+'T08:45:00+08:00',periods=4,freq='min')
                values=base+np.arange(4,dtype=float)
                frame=pd.DataFrame({'date':timestamps.astype(str),'contractMonth':actual,
                    'open':values,'high':values+2,'low':values-1,'close':values+1,'volume':10,'average':values+0.5})
                rows.extend(frame.to_dict('records'))
            if query==empty_contract:rows=[]
            if unsafe and query=='1!' and rows:rows[0]['date']='2026-09-10T08:45:00'
            return {'product':'TXF','contractMonth':query,'exchange':'TAIFEX','session':'REGULAR',
                'timeframe':'1','sort':'asc','data':rows}
    class NoIntraday:
        def candles(self,**params):pytest.fail('rollover-only must not query intraday or another phase')
        def products(self,**params):pytest.fail('discovery not part of fixed rollover request scope')
    return SimpleNamespace(historical=Synthetic(),intraday=NoIntraday()),calls


def fixture_setup(tmp_path,monkeypatch):
    root=tmp_path/'repo';clean_repo(root)
    cfg=dict(load_rollover_config(),drive_root=str(tmp_path/'archives'),request_spacing_seconds=0)
    monkeypatch.setattr('src.continuous_rollover.read_canonical_documentation',lambda cfg:(b'synthetic canonical evidence',{'documentation_sha256':'synthetic'}))
    sdk={'version':'SYNTHETIC','download_url':'offline synthetic fixture'}
    auth={'account_types':[],'futopt_account':False,'login_status':'NOT LIVE TESTED'}
    return root,cfg,sdk,auth


def test_three_equal_ranges_and_complete_archive(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    rest,calls=fake_rest(cfg)
    output,summary=run_rollover(rest,root,sdk,auth,cfg)
    assert len(calls)==3 and [c['contractMonth'] for c in calls]==['1!','202609','202610']
    comparable=[{k:v for k,v in c.items() if k!='contractMonth'} for c in calls]
    assert comparable[0]==comparable[1]==comparable[2]
    assert all(c['from']=='2026-09-10' and c['to']=='2026-09-22' for c in calls)
    for name in ['rollover_contract_coverage','rollover_mapping','rollover_transition_summary','rollover_price_comparison',
                 'rollover_volume_comparison','rollover_adjustment_test','rollover_daily_summary','rollover_anomalies']:
        pd.read_csv(output/'diagnostics'/f'{name}.csv')
    for name in ['rollover_1bang','rollover_202609','rollover_202610']:
        assert (output/'raw'/f'{name}_attempt1.json').is_file()
        pd.read_parquet(output/'data'/f'{name}.parquet')
    info=(output/'run_info.txt').read_text()
    assert 'validation_phase="rollover"' in info and 'api_request_count=3' in info
    assert '修改後再測' in summary and 'not market return' in summary
    assert '2017' not in str(calls) and all(c['session']=='regular' and c['timeframe']=='1' for c in calls)


def test_no_fallback_when_old_contract_empty(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    rest,calls=fake_rest(cfg,empty_contract='202609')
    output,_=run_rollover(rest,root,sdk,auth,cfg)
    assert len(calls)==3
    assert 'rollover_validation_status="UNRESOLVED"' in (output/'run_info.txt').read_text()


def test_dirty_formal_run_no_api_calls(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    (root/'uncommitted.txt').write_text('must preserve')
    rest,calls=fake_rest(cfg)
    with pytest.raises(RuntimeError,match='clean Git'):run_rollover(rest,root,sdk,auth,cfg)
    assert not calls and (root/'uncommitted.txt').read_text()=='must preserve'


def test_unsafe_time_stops_with_safe_archive(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    rest,calls=fake_rest(cfg,unsafe=True)
    with pytest.raises(HistoricalFatalError):run_rollover(rest,root,sdk,auth,cfg)
    assert len(calls)==3 # all raw responses saved before normalization
    archive=next(Path(cfg['drive_root']).iterdir())
    assert 'execution_status="STOPPED"' in (archive/'run_info.txt').read_text()
    assert (archive/'validation_summary.md').is_file()


def test_authentication_failure_no_more_requests(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    rest,calls=fake_rest(cfg,authentication=True)
    with pytest.raises(HistoricalFatalError):run_rollover(rest,root,sdk,auth,cfg)
    assert len(calls)==1
    archive=next(Path(cfg['drive_root']).iterdir())
    assert 'do-not-archive-this-message' not in ''.join(p.read_text() for p in archive.rglob('*.json'))


def test_scope_change_rejected_before_requests(tmp_path,monkeypatch):
    root,cfg,sdk,auth=fixture_setup(tmp_path,monkeypatch)
    cfg['window_to']='2026-09-23'
    rest,calls=fake_rest(cfg)
    with pytest.raises(HistoricalFatalError):run_rollover(rest,root,sdk,auth,cfg)
    assert not calls
