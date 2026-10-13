"""Offline robustness fixtures. Prices/schedules here are synthetic, never evidence."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import numpy as np
import pandas as pd
import pytest
from src.rollover_robustness import (load_robustness_config, run_rollover_robustness,
    validate_scope, assemble, robustness_summary)
from src.rollover_robustness_analysis import analyze_session, compare_sessions, summarize_sample
from src.historical_client import session_calendar, HistoricalFatalError
from src.historical_normalization import normalize_historical


def fixture(sample_index=2, session='regular', partial=False, delayed=False):
    base=load_robustness_config();cfg=dict(base,**base['rollover_samples'][sample_index])
    calendar=session_calendar(base);calendar=calendar[calendar.sample_id.eq(cfg['sample_id'])].copy()
    # Short synthetic sessions give complete expected grids and a midnight crossing.
    calendar['regular_close']=calendar.regular_open+pd.Timedelta(minutes=4)
    calendar['afterhours_open']=calendar.afterhours_open.dt.normalize()+pd.Timedelta(hours=23,minutes=59)
    calendar['afterhours_close']=calendar.afterhours_open+pd.Timedelta(minutes=4)
    ts=pd.DatetimeIndex(np.concatenate([pd.date_range(start,periods=4,freq='min').to_numpy() for start in calendar[session+'_open']])).tz_convert('Asia/Taipei')
    dates=np.repeat(calendar.trade_date.to_numpy(),4)
    def frame(month,price):
        values=price+np.arange(len(ts),dtype=float)
        return pd.DataFrame(dict(timestamp=ts,trade_date=dates,calendar_date=ts.strftime('%Y-%m-%d'),session=session,
            actual_contract_month=month,response_contractMonth=month,open=values,high=values+2,low=values-1,close=values+1,volume=10.,average=values+.5))
    old=frame(cfg['old_contract'],100);new=frame(cfg['new_contract'],103)
    continuous=old.copy();switch=next(d for d in cfg['trade_dates'] if d>cfg['expiry_date'])
    if delayed:switch=cfg['trade_dates'][cfg['trade_dates'].index(switch)+1]
    after=continuous.trade_date.ge(switch)
    if partial:after=continuous.trade_date.gt(switch) | (continuous.trade_date.eq(switch) & (np.arange(len(continuous))%4>=1))
    cols=['actual_contract_month','open','high','low','close','volume','average']
    continuous.loc[after,cols]=new.loc[after,cols]
    continuous['response_contractMonth']='1!'
    return {'1!':continuous,cfg['old_contract']:old,cfg['new_contract']:new},calendar,cfg


def analyze(frames,calendar,cfg,session='regular'):
    return analyze_session(frames,cfg,calendar,session)


@pytest.mark.parametrize('index',range(6))
def test_multiple_samples_full_regular_identity_exact_raw(index):
    frames,cal,cfg=fixture(index)
    tables,status=analyze(frames,cal,cfg)
    assert status=='PASS WITH CAVEATS'
    assert tables['transition'].iloc[0].full_session_switch
    assert tables['fields'].match_rate.eq(1).all()
    assert tables['mapping'].matched_contract.isin([cfg['old_contract'],cfg['new_contract']]).all()
    assert tables['adjustment'].status.eq('PASS').all()
    assert not tables['adjustment'].constant_offset_detected.any()
    assert not tables['adjustment'].ratio_adjustment_detected.any()


def test_night_full_session_and_cross_midnight_mapping():
    frames,cal,cfg=fixture(session='afterhours')
    tables,status=analyze(frames,cal,cfg,'afterhours')
    assert status=='PASS WITH CAVEATS'
    assert tables['transition'].iloc[0].transition_type=='full-afterhours-session switch'
    bars=frames['1!'].rename(columns={'timestamp':'date','actual_contract_month':'contractMonth'})[['date','contractMonth','open','high','low','close','volume','average']].copy()
    bars['date']=bars.date.astype(str)
    item={'params':dict(product='TXF',session='afterhours',timeframe=1,contractMonth='1!',**{'from':cfg['window_from'],'to':cfg['window_to']}),'response':dict(contractMonth='1!',data=bars.to_dict('records'))}
    normalized=normalize_historical(item,cal)
    assert normalized.trade_date.tolist()==frames['1!'].trade_date.tolist()
    assert normalized.calendar_date.nunique()>normalized.trade_date.nunique()
    assert normalized.session.eq('afterhours').all()


def test_holiday_night_is_next_regular_not_next_calendar_date():
    cfg=load_robustness_config();cal=session_calendar(cfg)
    row=cal[cal.trade_date.eq('2026-06-22')].iloc[0]
    assert row.afterhours_open==pd.Timestamp('2026-06-18T15:00:00+08:00')
    assert row.afterhours_close==pd.Timestamp('2026-06-19T05:00:00+08:00')


@pytest.mark.parametrize('session',['regular','afterhours'])
def test_partial_session_blocks_and_cross_midnight_detected(session):
    frames,cal,cfg=fixture(session=session,partial=True)
    tables,status=analyze(frames,cal,cfg,session)
    assert status=='UNRESOLVED'
    assert tables['sessions'].partial_session_switch.any()
    row=tables['transition'].iloc[0]
    assert row.transition_type=='partial-session switch'
    assert bool(row.cross_midnight_switch)==(session=='afterhours')


def test_regular_night_different_boundaries_are_explicit_findings():
    r,rc,cfg=fixture();n,nc,_=fixture(session='afterhours',delayed=True)
    regular,_=analyze(r,rc,cfg);night,_=analyze(n,nc,cfg,'afterhours')
    row,expiry=compare_sessions(dict(regular=regular,afterhours=night),cfg)
    assert not row['regular_afterhours_consistent']
    assert row['afterhours_contract_month']==cfg['old_contract']
    assert row['next_regular_contract_month']==cfg['new_contract']
    assert row['afterhours_switch_date']!=expiry['first_new_contract_regular_date']


def test_expiry_afternoon_answer_uses_calendar_start_and_next_trade_date():
    r,rc,cfg=fixture();n,nc,_=fixture(session='afterhours')
    regular,_=analyze(r,rc,cfg);night,_=analyze(n,nc,cfg,'afterhours')
    # Fixture opens at 23:59 but still belongs to expiry calendar evening.
    row,_=compare_sessions(dict(regular=regular,afterhours=night),cfg)
    assert row['regular_afterhours_consistent'] and row['afterhours_contract_month']==cfg['new_contract']
    assert str(pd.Timestamp(row['first_afterhours_timestamp']).date())==cfg['expiry_date']
    assert row['afterhours_switch_date']>cfg['expiry_date']


@pytest.mark.parametrize('field',['open','high','low','close','volume'])
def test_exact_mismatch_blocks(field):
    frames,cal,cfg=fixture();frames['1!'].loc[0,field]+=1e-10
    tables,status=analyze(frames,cal,cfg)
    assert status=='UNRESOLVED'
    assert tables['fields'].set_index('field').loc[field,'mismatch_rows']==1


def test_average_mismatch_isolated_from_ohlc_and_promotion():
    frames,cal,cfg=fixture();frames['1!'].loc[0,'average']+=.1
    tables,status=analyze(frames,cal,cfg)
    assert status=='PASS WITH CAVEATS' and tables['mapping'].expected_ohlc_exact_match.all()
    assert tables['fields'].set_index('field').loc['average','mismatch_rows']==1


@pytest.mark.parametrize('model',['additive','ratio'])
def test_adjustment_evidence_not_repaired(model):
    frames,cal,cfg=fixture();mask=frames['1!'].actual_contract_month.eq(cfg['old_contract'])
    for col in ['open','high','low','close']:
        if model=='additive':frames['1!'].loc[mask,col]+=2
        else:frames['1!'].loc[mask,col]*=1.02
    before=frames['1!'].copy(deep=True)
    tables,status=analyze(frames,cal,cfg)
    segment=tables['adjustment'].set_index('segment').loc['pre_roll']
    assert status=='UNRESOLVED'
    assert segment.constant_offset_detected if model=='additive' else segment.ratio_adjustment_detected
    pd.testing.assert_frame_equal(before,frames['1!'])


def test_roll_gap_is_raw_boundary_gap():
    frames,cal,cfg=fixture();tables,_=analyze(frames,cal,cfg)
    row=tables['transition'].iloc[0];c=frames['1!']
    last=c[c.actual_contract_month.eq(cfg['old_contract'])].iloc[-1]
    first=c[c.actual_contract_month.eq(cfg['new_contract'])].iloc[0]
    assert row.roll_gap_points==first.open-last.close
    assert row.roll_gap_return==first.open/last.close-1
    assert 'not market return' in row.notes


def test_inactive_missing_does_not_degrade_continuous():
    frames,cal,cfg=fixture();frames[cfg['new_contract']]=frames[cfg['new_contract']].iloc[1:].copy()
    tables,status=analyze(frames,cal,cfg)
    row=tables['missing'][tables['missing'].contract_query.eq(cfg['new_contract'])].iloc[0]
    assert not row.is_continuous_underlying and row.missing_slot_count==1
    assert status=='PASS WITH CAVEATS'


@pytest.mark.parametrize('target',['1!','underlying'])
def test_active_asymmetric_missing_blocks(target):
    frames,cal,cfg=fixture();query='1!' if target=='1!' else cfg['old_contract']
    frames[query]=frames[query].iloc[1:].copy()
    tables,status=analyze(frames,cal,cfg)
    assert status=='UNRESOLVED'
    assert tables['missing'].active_asymmetric_missing_rows.gt(0).any()


def test_shared_scheduled_gap_is_caveat_not_confirmed_loss():
    frames,cal,cfg=fixture()
    for query in ['1!',cfg['old_contract']]:frames[query]=frames[query].drop(index=1)
    tables,status=analyze(frames,cal,cfg)
    assert status=='PASS WITH CAVEATS'
    assert tables['missing'].missing_slot_count.gt(0).any()
    assert not tables['missing'].active_asymmetric_missing_rows.gt(0).any()


@pytest.mark.parametrize('month',[None,'202611'])
def test_unknown_or_third_identity_blocks(month):
    frames,cal,cfg=fixture();frames['1!'].loc[0,'actual_contract_month']=month
    tables,status=analyze(frames,cal,cfg)
    assert status=='FAIL'
    assert tables['anomalies'].anomaly_type.isin(['missing_actual_contract_month','unexpected_contract_month']).any()


def test_special_1345_bar_preserved():
    frames,cal,cfg=fixture()
    timestamp=pd.Timestamp(cfg['trade_dates'][0]+'T13:45:00+08:00')
    for query in ['1!',cfg['old_contract']]:
        extra=frames[query].iloc[[0]].copy();extra['timestamp']=pd.DatetimeIndex([timestamp]).tz_convert('Asia/Taipei')
        frames[query]=pd.concat([frames[query],extra],ignore_index=True)
    tables,_=analyze(frames,cal,cfg)
    assert tables['mapping'].timestamp.eq(timestamp).any()
    assert tables['anomalies'].anomaly_type.eq('special_13_45_bar').sum()==2


def test_expired_old_volume_remains_nan():
    frames,cal,cfg=fixture();frames[cfg['old_contract']]=frames[cfg['old_contract']][frames[cfg['old_contract']].trade_date.le(cfg['expiry_date'])]
    tables,status=analyze(frames,cal,cfg)
    assert status=='PASS WITH CAVEATS'
    assert tables['volume'].loc[tables['volume'].trade_date.gt(cfg['expiry_date']),'old_contract_volume'].isna().all()


def test_scope_mutations_rejected():
    for key,value in [('sessions',['regular']),('timeframe',5),('product','MTXF')]:
        cfg=load_robustness_config();cfg[key]=value
        with pytest.raises(HistoricalFatalError):validate_scope(cfg)
    cfg=load_robustness_config();cfg['rollover_samples'][0]['window_to']='2026-06-23'
    with pytest.raises(HistoricalFatalError):validate_scope(cfg)


def setup_runner(tmp_path,monkeypatch,auth=False,unsafe=False,empty=False):
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','init','-b','synthetic',str(root)],check=True,capture_output=True)
    (root/'README.md').write_text('synthetic test')
    subprocess.run(['git','add','README.md'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Synthetic','-c','user.email=synthetic@example.test','commit','-m','fixture'],cwd=root,check=True,capture_output=True)
    cfg=load_robustness_config();cfg.update(drive_root=str(tmp_path/'archives'),request_spacing_seconds=0)
    calendars=[];responses={}
    for i in range(6):
        for session in cfg['sessions']:
            frames,cal,local=fixture(i,session)
            if session=='regular':calendars.append(cal)
            for query,frame in frames.items():
                if query==local['old_contract']:frame=frame[frame.trade_date.le(local['expiry_date'])]
                bars=frame.rename(columns={'timestamp':'date','actual_contract_month':'contractMonth'})[['date','contractMonth','open','high','low','close','volume','average']].copy()
                bars['date']=bars.date.astype(str)
                data=bars.to_dict('records')
                if unsafe and i==0 and session=='regular' and query=='1!':data[0]['date']='2026-06-11T08:45:00'
                if empty and query=='1!':data=[]
                responses[(local['window_from'],session,query)]=dict(product='TXF',contractMonth=query,exchange='TAIFEX',session=session.upper(),timeframe='1',sort='asc',data=data)
    calendar_path=tmp_path/'synthetic_calendar.csv';pd.concat(calendars).to_csv(calendar_path,index=False);cfg['calendar_file']=str(calendar_path)
    monkeypatch.setattr('src.rollover_robustness.read_canonical_documentation',lambda cfg:(b'synthetic documentation',{'documentation_sha256':'synthetic'}))
    calls=[]
    class API:
        def candles(self,**params):
            calls.append(params.copy())
            if auth:
                class Error(Exception):status_code=401
                raise Error('secret-sentinel-not-to-archive')
            return responses[(params['from'],params['session'],params['contractMonth'])]
    class NoIntraday:
        def candles(self,**kwargs):pytest.fail('no intraday requests permitted')
    rest=SimpleNamespace(historical=API(),intraday=NoIntraday())
    sdk=dict(version='SYNTHETIC',download_url='offline fixture');login=dict(account_types=[],futopt_account=False,login_status='NOT LIVE TESTED')
    return root,cfg,rest,calls,sdk,login


def test_runner_36_equal_ranges_archives_and_sampled_promotion(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch)
    output,summary=run_rollover_robustness(rest,root,sdk,login,cfg)
    assert len(calls)==36
    for offset in range(0,36,3):
        group=[{k:v for k,v in c.items() if k!='contractMonth'} for c in calls[offset:offset+3]]
        assert group[0]==group[1]==group[2]
    assert len(list((output/'raw').glob('*.json')))==36
    assert len(list((output/'data').glob('*.parquet')))==36
    from src.rollover_robustness import OUTPUTS
    for name in [*OUTPUTS,'rollover_afterhours_transition_summary','rollover_gap_summary','rollover_expiry_relation','rollover_robustness_summary']:
        assert (output/'diagnostics'/f'{name}.csv').is_file()
        pd.read_csv(output/'diagnostics'/f'{name}.csv')
    info=(output/'run_info.txt').read_text()
    assert 'validation_phase="rollover_robustness"' in info and 'api_request_count=36' in info
    assert 'logical_request_count=36' in info and 'research_decision="保留"' in info
    assert 'External validation: NOT PERFORMED' in summary
    assert all('## '+letter+'.' in summary for letter in 'ABCDEFGHIJKLMNOPQR')
    assert '15. 還有 rollover blocker' in summary
    assert 'Private trigger' in summary


@pytest.mark.parametrize('kind',['auth','unsafe'])
def test_fatal_stops_and_preserves_safe_summary(tmp_path,monkeypatch,kind):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch,**{kind:True})
    with pytest.raises(HistoricalFatalError):run_rollover_robustness(rest,root,sdk,login,cfg)
    assert len(calls)==(1 if kind=='auth' else 3)
    archive=next(Path(cfg['drive_root']).iterdir())
    info=(archive/'run_info.txt').read_text()
    assert 'execution_status="STOPPED"' in info and 'research_decision="修改後再測"' in info
    assert (archive/'validation_summary.md').is_file()
    assert 'secret-sentinel-not-to-archive' not in ''.join(p.read_text() for p in archive.rglob('*.json'))


def test_empty_continuous_keeps_all_fixed_samples_and_blocks(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch,empty=True)
    output,summary=run_rollover_robustness(rest,root,sdk,login,cfg)
    assert len(calls)==36
    assert len(pd.read_csv(output/'diagnostics'/'rollover_robustness_summary.csv'))==6
    assert 'research_decision="修改後再測"' in (output/'run_info.txt').read_text()


def test_dirty_run_stops_before_requests(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,login=setup_runner(tmp_path,monkeypatch)
    (root/'pending.txt').write_text('preserve')
    with pytest.raises(RuntimeError,match='clean Git'):run_rollover_robustness(rest,root,sdk,login,cfg)
    assert not calls and (root/'pending.txt').read_text()=='preserve'


def test_unknown_night_trade_date_never_inferred_or_promoted():
    frames,cal,cfg=fixture(session='afterhours')
    frames['1!'].loc[0,'trade_date']=pd.NA
    tables,status=analyze(frames,cal,cfg,'afterhours')
    assert status=='UNRESOLVED'
    assert pd.isna(tables['mapping'].iloc[0].trade_date)


def test_duplicate_reference_retained_and_blocks_without_join_explosion():
    frames,cal,cfg=fixture();count=len(frames['1!'])
    frames[cfg['old_contract']]=pd.concat([frames[cfg['old_contract']],frames[cfg['old_contract']].iloc[[0]]],ignore_index=True)
    tables,status=analyze(frames,cal,cfg)
    assert status=='FAIL' and len(tables['mapping'])==count
    assert len(frames[cfg['old_contract']])==count+1


def test_volume_counterexample_rejects_only_simple_observed_rule():
    frames,cal,cfg=fixture();frames[cfg['new_contract']]['volume']=100.
    after=frames['1!'].actual_contract_month.eq(cfg['new_contract']);frames['1!'].loc[after,'volume']=100.
    tables,status=analyze(frames,cal,cfg)
    assert status=='PASS WITH CAVEATS'
    assert tables['volume'].simple_daily_max_rule_counterexample.any()
    assert tables['volume'].continuous_matches_volume_leader.eq(False).any()


def test_missing_post_transition_session_not_replaced():
    frames,cal,cfg=fixture();date=cfg['trade_dates'][6]
    frames['1!']=frames['1!'][frames['1!'].trade_date.ne(date)]
    tables,status=analyze(frames,cal,cfg)
    assert status=='UNRESOLVED'
    assert tables['sessions'].loc[tables['sessions'].trade_date.eq(date),'row_count'].iloc[0]==0
    assert len(tables['sessions'])==len(cfg['trade_dates'])
