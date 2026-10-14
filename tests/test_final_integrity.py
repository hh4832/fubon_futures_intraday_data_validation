"""Final-phase synthetic evidence: no SDK or credentials. Short schedules are fixtures."""
from copy import deepcopy
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import subprocess
import numpy as np
import pandas as pd
import pytest
from src.final_sample_matrix import load_final_config,FrozenSampleMatrix,effective_calendar
from src.final_integrity_analysis import query_diagnostics,repeatability,internal_aggregation,availability,identity_checks
from src.final_external import compare_external_field,external_spot_check
from src.final_integrity_runner import run_final_integrity
from src.historical_client import session_calendar,HistoricalFatalError
from src.historical_normalization import normalize_historical
from src.rollover_robustness_analysis import analyze_session
from test_rollover_robustness import fixture as rollover_fixture
from test_rollover_robustness_debug import june_full_frames


def ordinary(year=2021,session='regular',times=None):
    cfg=load_final_config();s=next(s for s in cfg['samples'] if s['year']==year and s['window_type']=='ordinary_early')
    cal=session_calendar(cfg);cal=cal[cal.sample_id.eq(s['sample_id'])].copy()
    date=s['trade_dates'][0];c=cal.iloc[0]
    timestamps=pd.date_range(c[session+'_open'],periods=4,freq='min') if times is None else pd.DatetimeIndex(times)
    values=100+np.arange(len(timestamps),dtype=float)
    bars=pd.DataFrame(dict(date=timestamps.astype(str),contractMonth=s['old_contract'],open=values,high=values+2,low=values-1,close=values+1,volume=10.,average=values+.5))
    item=dict(name='synthetic',params=dict(product='TXF',session=session,timeframe='1',contractMonth='1!',sort='asc',**{'from':s['trade_dates'][0],'to':s['trade_dates'][-1]}),requested_dates=s['trade_dates'],response=dict(product='TXF',contractMonth='1!',exchange='TAIFEX',session=session.upper(),timeframe='1',sort='asc',data=bars.to_dict('records')))
    return normalize_historical(item,effective_calendar(cal,session)),item,s,cal


@pytest.mark.parametrize('year',[2017,2018,2019,2020,2021,2022,2023,2024,2025,2026])
def test_cross_year_normalized_schema_and_mapping(year):
    f,item,s,cal=ordinary(year)
    tables,quality,_=query_diagnostics(item,f,s,cal)
    assert tables['schema'].iloc[0].schema_status=='PASS'
    assert tables['timestamp'].iloc[0].timezone_aware
    assert f.trade_date.iloc[0]==s['trade_dates'][0]
    assert quality=='UNRESOLVED' # remaining four requested dates intentionally missing


def test_schema_drift_not_hidden_by_normalization():
    f,item,s,cal=ordinary();item['response']['data'][0]['unexpected_field']='retained'
    f=normalize_historical(item,cal);tables,_,_=query_diagnostics(item,f,s,cal)
    assert 'unexpected_field' in tables['schema'].iloc[0].raw_schema
    assert 'unexpected_field' in tables['schema'].iloc[0].normalized_schema


def test_naive_datetime_rejected_before_any_repair():
    _,item,s,cal=ordinary();item['response']['data'][0]['date']='2021-03-02T08:45:00'
    with pytest.raises(HistoricalFatalError):normalize_historical(item,cal)


def test_night_cross_midnight_terminal_and_0501_rejected():
    f,item,s,cal=ordinary(session='afterhours');c=cal.iloc[0]
    timestamps=[c.afterhours_open,c.afterhours_open.normalize()+pd.Timedelta(days=1),c.afterhours_close,c.afterhours_close+pd.Timedelta(minutes=1)]
    f,item,s,cal=ordinary(session='afterhours',times=timestamps)
    assert f.trade_date.iloc[:3].eq(s['trade_dates'][0]).all()
    assert f.terminal_boundary_bar.tolist()==[False,False,True,False]
    assert pd.isna(f.trade_date.iloc[3])
    tables,quality,_=query_diagnostics(item,f,s,cal)
    assert tables['session'].outside_session_rows.sum()==1 and quality=='UNRESOLVED'


def test_duplicate_and_invalid_numeric_rows_preserved():
    f,item,s,cal=ordinary();item['response']['data'].append(item['response']['data'][0].copy())
    item['response']['data'][1]['high']=1
    f=normalize_historical(item,cal);tables,quality,invalid=query_diagnostics(item,f,s,cal)
    assert len(f)==5 and tables['timestamp'].iloc[0].duplicate_rows==2
    assert quality=='FAIL' and len(invalid)==1


def test_special_1345_preserved_no_fixed_300_gate():
    f,item,s,cal=ordinary(times=[pd.Timestamp('2021-03-02T13:45:00+08:00')])
    tables,_,_=query_diagnostics(item,f,s,cal)
    assert len(f)==1 and tables['bar_count'].iloc[0].special_1345_bar==1


def test_expiry_reference_shorten_and_raw_concatenation():
    f,cal,cfg=june_full_frames();tables,status=analyze_session(f,cfg,cal,'regular')
    assert status=='PASS WITH CAVEATS'
    expiry=tables['missing'][tables['missing'].trade_date.eq('2026-06-17')]
    assert expiry.loc[expiry.contract_query.eq('1!'),'reference_slot_count'].iloc[0]==285
    assert tables['adjustment'].status.eq('PASS').all()


@pytest.mark.parametrize('kind',['shared','active','inactive'])
def test_missing_minutes_roles(kind):
    f,cal,cfg=rollover_fixture()
    if kind=='shared':
        for q in ['1!',cfg['old_contract']]:f[q]=f[q].drop(index=1)
    else:
        q='1!' if kind=='active' else cfg['new_contract'];f[q]=f[q].drop(index=1)
    tables,status=analyze_session(f,cfg,cal,'regular')
    assert status==('UNRESOLVED' if kind=='active' else 'PASS WITH CAVEATS')
    if kind=='active':assert tables['missing'].active_asymmetric_missing_rows.gt(0).any()


@pytest.mark.parametrize('kind',['missing','third','partial','ohlc','volume','average','additive','ratio'])
def test_yearly_rollover_structural_price_and_average_cases(kind):
    f,cal,cfg=rollover_fixture(0)
    f['1!']['actual_contract_month']=f['1!'].actual_contract_month.astype('string')
    if kind in ['missing','third']:f['1!'].loc[0,'actual_contract_month']=pd.NA if kind=='missing' else '202608'
    elif kind=='partial':f,cal,cfg=rollover_fixture(0,partial=True)
    elif kind in ['ohlc','volume','average']:f['1!'].loc[0,'close' if kind=='ohlc' else kind]+=.1
    else:
        mask=f['1!'].actual_contract_month.eq(cfg['old_contract'])
        for col in ['open','high','low','close']:
            if kind=='additive':f['1!'].loc[mask,col]+=2
            else:f['1!'].loc[mask,col]*=1.02
    if kind=='missing':
        missing,invalid,wrong=identity_checks(f['1!'],dict(cfg,window_type='june_rollover'),'1!')
        assert missing.iloc[0] and invalid.iloc[0] and wrong.iloc[0]
        return # The final runner blocks invalid identity before legacy analysis.
    tables,status=analyze_session(f,cfg,cal,'regular')
    assert status=='PASS WITH CAVEATS' if kind=='average' else status in {'FAIL','UNRESOLVED'}
    if kind=='additive':assert tables['adjustment'].constant_offset_detected.any()
    if kind=='ratio':assert tables['adjustment'].ratio_adjustment_detected.any()
    if kind=='partial':assert tables['sessions'].partial_session_switch.any()


@pytest.mark.parametrize('changed',[False,True])
def test_repeatability_exact_and_mismatch(changed):
    f,_,s,_=ordinary();second=f.copy()
    if changed:second.loc[0,'volume']+=1
    row=repeatability(f,second,dict(year=2021,session='regular'))
    assert row['status']==('UNRESOLVED' if changed else 'PASS')


@pytest.mark.parametrize('timeframe',[5,15,60])
def test_internal_aggregation_deterministic_volume_session_anchor(timeframe):
    f,item,s,cal=ordinary(times=pd.date_range('2021-03-02T08:45:00+08:00',periods=120,freq='min'))
    rows,outputs=internal_aggregation(f,cal,'regular',dict(year=2021,session='regular'))
    row=rows[rows.timeframe.eq(timeframe)].iloc[0]
    output=next(frame for tf,frame in outputs if tf==timeframe)
    assert row.deterministic_bucket and row.volume_sum_preserved and row.cross_session_leakage==0
    assert output.timestamp.iloc[0]==pd.Timestamp('2021-03-02T08:45:00+08:00')
    assert len(output)==120//timeframe and output.volume.sum()==1200


def test_external_definition_mismatch_not_fail_and_price_contradiction_fail():
    assert compare_external_field(2021,'2021-03-02','regular','volume',10,20,False)['status']=='NOT COMPARABLE'
    assert compare_external_field(2021,'2021-03-02','regular','high',10,20,True)['status']=='FAIL'
    assert compare_external_field(2021,'2021-03-02','regular','high',10,None,True)['status']=='UNRESOLVED'


def test_sample_plan_108_after_all_planned_windows_complete():
    cfg=load_final_config();matrix=FrozenSampleMatrix(cfg,now="2027-01-01T00:00:00+08:00")
    assert len(matrix.plan)==108
    assert len([e for e in matrix.plan if e['role']=='repeat'])==6
    with pytest.raises(HistoricalFatalError,match='future'):matrix.require_completed(pd.Timestamp('2026-10-14T07:00:00+08:00'))
    matrix.require_completed(pd.Timestamp('2026-11-07T00:00:00+08:00'))
    assert next(s for s in cfg['samples'] if s['year']==2017 and s['window_type']=='june_rollover')['old_contract']=='201709'


def test_matrix_immutability_after_first_request_and_calendar_change(tmp_path):
    cfg=load_final_config();matrix=FrozenSampleMatrix(cfg)
    cfg['samples'][0]['trade_dates'][0]='2017-07-04'
    with pytest.raises(HistoricalFatalError,match='mutated'):matrix.verify(cfg)
    cfg=load_final_config();cfg['calendar_sha256']='changed'
    with pytest.raises(HistoricalFatalError,match='hash changed'):FrozenSampleMatrix(cfg)


def runner_fixture(tmp_path,monkeypatch,external_bad=False,mutate=False):
    root=tmp_path/'repo';root.mkdir()
    subprocess.run(['git','init','-b','synthetic',str(root)],check=True,capture_output=True)
    (root/'README.md').write_text('offline fixture');subprocess.run(['git','add','README.md'],cwd=root,check=True)
    subprocess.run(['git','-c','user.name=Synthetic','-c','user.email=synthetic@example.test','commit','-m','fixture'],cwd=root,check=True,capture_output=True)
    cfg=load_final_config();cfg['baseline_commit']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
    cal=session_calendar(cfg)
    cal['regular_close']=cal.regular_open+pd.Timedelta(minutes=4)
    cal['afterhours_close']=cal.afterhours_open+pd.Timedelta(minutes=4)
    calendar_path=tmp_path/'synthetic_calendar.csv';cal.to_csv(calendar_path,index=False)
    cfg.update(calendar_file=str(calendar_path),calendar_sha256=hashlib.sha256(calendar_path.read_bytes()).hexdigest(),drive_root=str(tmp_path/'archives'),request_spacing_seconds=0)
    monkeypatch.setattr('src.final_integrity_runner.read_canonical_documentation',lambda cfg:(b'offline synthetic document',{}))
    plan=FrozenSampleMatrix(cfg,now="2027-01-01T00:00:00+08:00").plan;calls=[]
    class API:
        def candles(self,**params):
            calls.append(params.copy())
            entry=next(e for e in plan if e['window_from']==params['from'] and e['window_to']==params['to'] and e['session']==params['session'] and e['contract_query']==params['contractMonth'])
            if mutate and len(calls)==1:cfg['samples'][0]['trade_dates'][0]='2017-07-04'
            sample=next((s for s in cfg['samples'] if s['sample_id']==entry['sample_id']),None)
            parts=[]
            dates=entry['dates']
            for date in dates:
                c=cal[cal.sample_id.eq(entry['sample_id']) & cal.trade_date.eq(date)].iloc[0]
                if params['session']=='afterhours' and c.afterhours_scheduled!='true':continue
                if sample and sample['window_type']=='june_rollover' and params['contractMonth']==sample['old_contract'] and date>sample['expiry_date']:continue
                month=('201706' if sample is None else sample['old_contract'] if sample['window_type']!='june_rollover' or date<=sample['expiry_date'] else sample['new_contract']) if params['contractMonth']=='1!' else params['contractMonth']
                ts=pd.date_range(c[params['session']+'_open'],periods=4,freq='min')
                prices=100+np.arange(4,dtype=float)
                parts.extend(pd.DataFrame(dict(date=ts.astype(str),contractMonth=month,open=prices,high=prices+2,low=prices-1,close=prices+1,volume=10.,average=prices+.5)).to_dict('records'))
            return dict(product='TXF',contractMonth=params['contractMonth'],exchange='TAIFEX',session=params['session'].upper(),timeframe='1',sort='asc',data=parts)
    def official(date,path):
        sample=next(s for s in cfg['samples'] if s['window_type']=='ordinary_early' and s['trade_dates'][0]==date)
        records=[{'交易日期':date.replace('-','/'),'契約':'TX','到期月份(週別)':sample['old_contract'],'最高價':'999' if external_bad else '105','最低價':'99','交易時段':market,'收盤價':'104','成交量':'40'} for market in ['一般','盤後']]
        return pd.DataFrame(records),dict(status='PASS',source_url='https://www.taifex.com.tw/cht/3/futDataDown')
    sdk=dict(version='SYNTHETIC',download_url='offline fixture');auth=dict(account_types=[],futopt_account=False,login_status='NOT LIVE TESTED')
    return root,cfg,SimpleNamespace(historical=API(),intraday=SimpleNamespace(candles=lambda **kwargs:pytest.fail('No intraday queries in final validation'))),calls,sdk,auth,official


def test_final_runner_108_requests_complete_archive_and_evidence_gates(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,auth,official=runner_fixture(tmp_path,monkeypatch)
    path,summary=run_final_integrity(rest,root,sdk,auth,cfg,now=pd.Timestamp('2027-01-01T00:00:00+08:00'),external_fetcher=official)
    assert len(calls)==108 and all(c['timeframe']=='1' for c in calls)
    assert len(list((path/'raw').glob('*attempt*.json')))==108
    info=(path/'run_info.txt').read_text()
    assert 'expected_logical_request_count=108' in info and 'logical_request_count=108' in info
    assert 'final_integrity_status="PASS WITH CAVEATS"' in info
    assert 'research_decision="保留"' in info and 'sample_matrix_sha256=' in info
    assert (path/'diagnostics/predeclared_sample_matrix.csv').is_file()
    assert len(pd.read_csv(path/'diagnostics/cross_year_yearly_summary.csv'))==10
    assert len(pd.read_csv(path/'diagnostics/cross_year_rollover_field_comparison.csv'))==120
    assert len(pd.read_csv(path/'diagnostics/cross_year_adjustment_validation.csv'))==60
    assert len(pd.read_csv(path/'diagnostics/cross_year_repeatability.csv'))==6
    assert len(pd.read_csv(path/'diagnostics/cross_year_aggregation_validation.csv'))==60
    assert len(pd.read_csv(path/'diagnostics/external_taifex_spot_check.csv'))==120
    assert (path/'diagnostics/cross_year_invalid_ohlc_rows.parquet').is_file()
    assert all('## '+chr(65+i)+'.' in summary for i in range(24))
    assert '20. 正式dataset restrictions' in summary


def test_external_contradiction_blocks_canonical_promotion(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,auth,official=runner_fixture(tmp_path,monkeypatch,external_bad=True)
    path,_=run_final_integrity(rest,root,sdk,auth,cfg,now=pd.Timestamp('2027-01-01T00:00:00+08:00'),external_fetcher=official)
    assert 'final_integrity_status="FAIL"' in (path/'run_info.txt').read_text()
    assert 'research_decision="修改後再測"' in (path/'run_info.txt').read_text()


def test_mutated_matrix_stops_whole_run_after_one_request(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,auth,official=runner_fixture(tmp_path,monkeypatch,mutate=True)
    with pytest.raises(HistoricalFatalError):run_final_integrity(rest,root,sdk,auth,cfg,now=pd.Timestamp('2027-01-01T00:00:00+08:00'),external_fetcher=official)
    assert len(calls)==1
    path=next(Path(cfg['drive_root']).iterdir())
    assert 'execution_status="STOPPED"' in (path/'run_info.txt').read_text()
    assert (path/'diagnostics/fatal_traceback.txt').is_file()


def test_current_year_runner_only_106_eligible_requests(tmp_path,monkeypatch):
    root,cfg,rest,calls,sdk,auth,official=runner_fixture(tmp_path,monkeypatch)
    path,summary=run_final_integrity(rest,root,sdk,auth,cfg,now=pd.Timestamp('2026-10-14T10:45:25+08:00'),external_fetcher=official)
    assert len(calls)==106
    assert not any(c['from']=='2026-11-02' for c in calls)
    info=(path/'run_info.txt').read_text()
    assert 'expected_logical_request_count=106' in info
    assert 'sample_trade_date_count=185' in info
    assert 'api_request_count_before_revision=0' in info
    assert 'final_integrity_status="PASS WITH CAVEATS"' in info
    yearly=pd.read_csv(path/'diagnostics/cross_year_yearly_summary.csv')
    row=yearly[yearly.year.eq(2026)].iloc[0]
    assert row['2026_late_year_status']=='NOT_YET_ELIGIBLE'
    assert row.late_year_test_scope=='NOT TESTED — FUTURE WINDOW'
    assert row.sampled_trade_dates==14 and row.classification=='PASS WITH CAVEATS'
    assert '2026 ordinary_late not yet eligible as of run date.' in summary
    assert 'Validated deterministic historical samples from 2017-06 through the latest completed eligible window as of the formal run timestamp.' in summary
    assert 'validated full calendar year 2026' not in summary
    samples=pd.read_csv(path/'diagnostics/predeclared_sample_matrix.csv')
    assert len(samples)==30 and samples.included_in_formal_run.sum()==29
