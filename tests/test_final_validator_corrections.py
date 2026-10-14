"""Minimal validator regressions; synthetic bars and CSV, no market requests."""
import csv
import hashlib
import io
import json
import subprocess
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
import yaml
from src.config import ROOT
from src.final_sample_matrix import load_final_config,FrozenSampleMatrix
from src.final_integrity_analysis import internal_aggregation,compare_bucket_fields
from src.final_external import parse_official_daily,external_spot_check,fetch_official_daily,SOURCE
from src.final_integrity_report import yearly_summary
from scripts.replay_final_validator_corrections import replay_archive


def baseline_config():
    return yaml.safe_load(subprocess.check_output(['git','show','ce58e87:config/final_cross_year_validation.yaml'],cwd=ROOT,text=True))

def test_2018_corrected_official_dates_and_unchanged_other_samples():
    cfg=load_final_config();old=baseline_config()
    sample=next(s for s in cfg['samples'] if s['sample_id']=='2018_june_rollover')
    assert sample['trade_dates']==['2018-06-13','2018-06-14','2018-06-15','2018-06-19','2018-06-20','2018-06-21','2018-06-22','2018-06-25','2018-06-26']
    assert len(sample['trade_dates'])==9 and sample['trade_dates'][4]==sample['expiry_date']=='2018-06-20'
    assert '2018-06-16' not in sample['trade_dates']
    assert sample['window_from']=='2018-06-13'
    assert [s for s in cfg['samples'] if s['sample_id']!='2018_june_rollover']==[s for s in old['samples'] if s['sample_id']!='2018_june_rollover']
    assert sample['calendar_source']=='https://www.taifex.com.tw/chinese/4/2018Calendar_CH.pdf'

def test_calendar_only_2018_changed_and_night_next_regular_date_restored():
    old=pd.read_csv(io.StringIO(subprocess.check_output(['git','show','ce58e87:config/final_cross_year_sample_calendar.csv'],cwd=ROOT,text=True)),dtype=str)
    path=ROOT/'config/final_cross_year_sample_calendar.csv';new=pd.read_csv(path,dtype=str)
    pd.testing.assert_frame_equal(new[new.sample_id.ne('2018_june_rollover')].reset_index(drop=True),old[old.sample_id.ne('2018_june_rollover')].reset_index(drop=True))
    june=new[new.sample_id.eq('2018_june_rollover')]
    assert june.calendar_source.eq('https://www.taifex.com.tw/chinese/4/2018Calendar_CH.pdf').all()
    row=june[june.trade_date.eq('2018-06-19')].iloc[0]
    assert row.afterhours_scheduled=='true'
    assert row.afterhours_open=='2018-06-15T15:00:00+08:00' and row.afterhours_close=='2018-06-16T05:00:00+08:00'
    assert hashlib.sha256(path.read_bytes()).hexdigest()==load_final_config()['calendar_sha256']

def test_new_matrix_hash_and_correction_provenance():
    m=FrozenSampleMatrix(load_final_config(),now='2026-10-14T11:02:10.964874+08:00')
    assert m.sample_matrix_sha256!=m.metadata['previous_sample_matrix_sha256']=='878eb4457be463ee80da1d40cc5c93b3d35e16293ca7a933cad2cd333c95422d'
    assert m.metadata['previous_formal_run_id']=='20261014_110211_ce58e87c15cf'
    assert 'not Fubon market-data results' in m.metadata['calendar_correction_reason']
    assert m.metadata['eligible_windows']==29 and len(m.plan)==106


def aggregation_fixture(session='regular',count=None,terminal=False):
    start=pd.Timestamp('2018-03-01T08:45:00+08:00') if session=='regular' else pd.Timestamp('2018-02-28T15:00:00+08:00')
    minutes=300 if session=='regular' else 840;end=start+pd.Timedelta(minutes=minutes)
    n=minutes if count is None else count
    times=pd.date_range(start,periods=n,freq='min')
    if terminal:times=times.append(pd.DatetimeIndex([end]))
    frame=pd.DataFrame(dict(timestamp=times,trade_date='2018-03-01',actual_contract_month='201803',open=100,high=101,low=99,close=100,volume=1))
    cal=pd.DataFrame([dict(trade_date='2018-03-01',afterhours_scheduled='true',**{session+'_open':start,session+'_close':end})])
    return frame,cal

def test_numeric_dtype_equivalence_exact_and_nullable():
    frame,_=aggregation_fixture(count=2);expected=frame.copy()
    for field in ['open','high','low','close','volume']:expected[field]=expected[field].astype('Float64')
    result=compare_bucket_fields(frame,expected)
    assert result['equal'] and result['mismatch_bucket_count']==0
    assert all(result[c+'_match'] for c in ['timestamp','open','high','low','close','volume'])
    assert all(result['max_abs_diff_'+c]==0 for c in ['open','high','low','close','volume'])

@pytest.mark.parametrize('session',['regular','afterhours'])
def test_complete_300_and_840_bar_exact_determinism(session):
    frame,cal=aggregation_fixture(session)
    rows,outputs=internal_aggregation(frame,cal,session,dict(year=2018,session=session))
    assert len(frame)==(300 if session=='regular' else 840)
    assert rows.deterministic_bucket.all() and rows.volume_sum_preserved.all()
    assert rows.status.eq('PASS').all() and rows.cross_session_leakage.eq(0).all()
    assert rows.repeat_aggregation_equal.all() and rows.mismatch_bucket_count.eq(0).all()
    assert len(outputs)==3

@pytest.mark.parametrize('field',['close','volume'])
def test_bucket_mismatch_never_hidden_by_normalization(field,monkeypatch):
    from src.final_integrity_analysis import aggregate
    def altered(*args,**kwargs):
        output=aggregate(*args,**kwargs);output.loc[0,field]+=1;return output
    monkeypatch.setattr('src.final_integrity_analysis.aggregate',altered)
    frame,cal=aggregation_fixture();rows,_=internal_aggregation(frame,cal,'regular',dict(year=2018))
    assert rows.status.eq('UNRESOLVED').all()
    assert rows[field+'_match'].eq(False).all() and rows.mismatch_bucket_count.eq(1).all()
    assert rows['max_abs_diff_'+field].eq(1).all()

def test_repeat_aggregation_mismatch_blocks(monkeypatch):
    from src.final_integrity_analysis import aggregate
    calls=0
    def alternating(*args,**kwargs):
        nonlocal calls
        calls+=1;output=aggregate(*args,**kwargs)
        if calls%2==0:output.loc[0,'close']+=1
        return output
    monkeypatch.setattr('src.final_integrity_analysis.aggregate',alternating)
    frame,cal=aggregation_fixture();rows,_=internal_aggregation(frame,cal,'regular',dict(year=2018))
    assert rows.close_match.all() and rows.status.eq('UNRESOLVED').all()
    assert rows.repeat_aggregation_equal.eq(False).all() and rows.repeat_mismatch_bucket_count.eq(1).all()

def test_partial_observed_buckets_preserved_with_caveats():
    frame,cal=aggregation_fixture(count=297);before=frame.copy(deep=True)
    rows,outputs=internal_aggregation(frame,cal,'regular',dict(year=2018))
    assert rows.deterministic_bucket.all() and rows.status.eq('PASS WITH CAVEATS').all()
    assert rows.partial_bucket_count.eq(1).all() and rows.incomplete_bucket_count.eq(1).all()
    assert all(output.minute_count.sum()==297 for _,output in outputs)
    pd.testing.assert_frame_equal(frame,before)

@pytest.mark.parametrize('session',['regular','afterhours'])
def test_terminal_only_1345_and_0500_preserved(session):
    frame,cal=aggregation_fixture(session,terminal=True)
    rows,outputs=internal_aggregation(frame,cal,session,dict(year=2018))
    assert rows.terminal_only_bucket_count.eq(1).all() and rows.deterministic_bucket.all()
    assert rows.status.eq('PASS WITH CAVEATS').all()
    assert all(output.timestamp.iloc[-1]==frame.timestamp.iloc[-1] and output.volume.sum()==len(frame) for _,output in outputs)

def test_outside_session_leakage_blocks_without_trim():
    frame,cal=aggregation_fixture();frame.loc[0,'timestamp']=cal.regular_open.iloc[0]-pd.Timedelta(minutes=1)
    rows,outputs=internal_aggregation(frame,cal,'regular',dict(year=2018))
    assert not outputs and rows.status.eq('UNRESOLVED').all()
    assert rows.cross_session_leakage.eq(1).all() and len(frame)==300


HEADER=['交易日期','契約','到期月份(週別)','開盤價','最高價','最低價','收盤價','漲跌價','漲跌%','成交量','結算價','未沖銷契約數','最後最佳買價','最後最佳賣價','歷史最高價','歷史最低價','是否因訊息面暫停交易','交易時段','價差對單式委託成交量']
def official_bytes(duplicate=False,extra=''):
    buf=io.StringIO();writer=csv.writer(buf);writer.writerow(HEADER)
    row=['2018/03/01','TX','201803  ','10604','10778','10600','10735','-54','-0.50%','150471','10734','89157','10735','10737','11255','9146','','一般','',extra]
    writer.writerow(row)
    if duplicate:writer.writerow(row)
    return buf.getvalue().encode('cp950')

def external_frame():
    return pd.DataFrame([dict(trade_date='2018-03-01',actual_contract_month='201803',high=10778,low=10600,close=10735,volume=150448)])

def test_csv_trailing_comma_preserves_19_header_20_fields_and_known_case():
    raw=official_bytes();df,meta=parse_official_daily(raw)
    assert meta['header_field_count']==19 and meta['data_field_counts']==[20]
    assert meta['trailing_empty_field_count']==1 and df.loc[0,'交易日期']=='2018/03/01'
    assert df.loc[0,'契約']=='TX' and df.loc[0,'canonical_contract_month']=='201803'
    result=external_spot_check(external_frame(),'2018-03-01',2018,'regular',df,meta)
    assert result[result.definition_compatible].status.eq('PASS').all()
    assert result[result.field.isin(['close','volume'])].status.eq('NOT COMPARABLE').all()
    assert result.attrs['selection_diagnostics']['selected_row_count']==1

def test_fetch_archives_raw_then_uses_explicit_parser(tmp_path,monkeypatch):
    raw=official_bytes();(tmp_path/'raw').mkdir();(tmp_path/'diagnostics').mkdir()
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def geturl(self):return SOURCE
        def read(self,*args):return raw
    monkeypatch.setattr('src.final_external.urlopen',lambda *args,**kwargs:Response())
    df,meta=fetch_official_daily('2018-03-01',tmp_path)
    assert meta['status']=='PASS' and df.canonical_contract.eq('TX').all()
    assert (tmp_path/'raw/taifex_2018-03-01.csv').read_bytes()==raw
    assert meta['sha256']==hashlib.sha256(raw).hexdigest()

def test_unicode_fixed_width_date_session_canonicalization():
    df,_=parse_official_daily(official_bytes());df.loc[0,'契約']='\u3000ＴＸ\u00a0';df.loc[0,'到期月份(週別)']='２０１８０３\u2003';df.loc[0,'交易日期']='2018/3/1 ';df.loc[0,'交易時段']=' 一般\u3000'
    result=external_spot_check(external_frame(),'2018-03-01',2018,'regular',df,{})
    assert result[result.definition_compatible].status.eq('PASS').all()
    diagnostic=result.attrs['selection_diagnostics']
    assert diagnostic['post_contract_filter_count']==diagnostic['selected_row_count']==1
    assert json.loads(diagnostic['canonicalized_unique_session_values'])==['regular']

@pytest.mark.parametrize('case',['duplicate','date','month','session','nonempty_extra'])
def test_no_ambiguous_row_or_guessed_key_fallback(case):
    if case=='nonempty_extra':
        with pytest.raises(ValueError,match='nonempty CSV'):parse_official_daily(official_bytes(extra='unexpected'))
        return
    df,_=parse_official_daily(official_bytes(duplicate=case=='duplicate'))
    if case=='date':df.loc[0,'交易日期']='2018/03/02'
    if case=='month':df.loc[0,'到期月份(週別)']='201803/201804'
    if case=='session':df.loc[0,'交易時段']='regular' # English is not an official label mapping.
    result=external_spot_check(external_frame(),'2018-03-01',2018,'regular',df,{})
    assert result[result.definition_compatible].status.eq('UNRESOLVED').all()
    assert result.attrs['selection_diagnostics']['selected_row_count']==(2 if case=='duplicate' else 0)

def test_known_row_high_contradiction_is_fail_not_selection_error():
    df,_=parse_official_daily(official_bytes());df.loc[0,'最高價']='10779'
    result=external_spot_check(external_frame(),'2018-03-01',2018,'regular',df,{})
    assert result.loc[result.field.eq('high'),'status'].iloc[0]=='FAIL'
    assert result.attrs['selection_diagnostics']['status']=='PASS'


def test_validator_blocker_alone_not_major_data_anomaly():
    summary=yearly_summary({},load_final_config(),request_complete=False)
    assert summary.validation_blocker.all()
    assert not summary.major_data_anomaly.any() and not summary.major_anomaly.any()

@pytest.mark.parametrize('case',['ohlc','duplicate','outside','outside_reference','metadata','partial','rollover','volume','active_missing','repeat','external'])
def test_actual_observed_anomaly_sets_major_data_anomaly(case):
    tags=dict(year=2018,role='primary',contract_query='1!');tables={}
    if case=='ohlc':tables['ohlc']=pd.DataFrame([dict(tags,OHLC_invalid_rows=1,status='FAIL')])
    elif case=='duplicate':tables['timestamp']=pd.DataFrame([dict(tags,duplicate_rows=1,status='FAIL')])
    elif case in ['outside','outside_reference']:tables['session']=pd.DataFrame([{**tags,'contract_query':'201806' if case=='outside_reference' else '1!','session':'regular','row_count':1,'outside_session_rows':1,'status':'UNRESOLVED'}])
    elif case in ['metadata','partial']:tables['traceability']=pd.DataFrame([dict(tags,missing_contract_metadata_rows=int(case=='metadata'),partial_switch=case=='partial',status='FAIL')])
    elif case in ['rollover','volume']:tables['rollover_fields']=pd.DataFrame([dict(year=2018,field='close' if case=='rollover' else 'volume',continuous_rows=1,exact_match_rows=0,mismatch_rows=1,unavailable_rows=0)])
    elif case=='active_missing':tables['missing']=pd.DataFrame([dict(tags,absence_type='active asymmetric break',missing_slot_count=1)])
    elif case=='repeat':tables['repeatability']=pd.DataFrame([dict(year=2018,role='repeat',status='UNRESOLVED',mismatch_rows=1)])
    else:tables['external']=pd.DataFrame([dict(year=2018,session='regular',field='high',definition_compatible=True,status='FAIL')])
    row=yearly_summary(tables,load_final_config(),False).query('year==2018').iloc[0]
    assert row.validation_blocker and row.major_data_anomaly


def test_offline_replay_preserves_old_calendar_and_never_promotes(tmp_path,monkeypatch):
    archive=tmp_path/'synthetic_source';archive.mkdir();(archive/'diagnostics').mkdir();(archive/'data').mkdir();(archive/'raw').mkdir()
    cfg=baseline_config();(archive/'config_snapshot.json').write_text(json.dumps(cfg))
    calendar_bytes=subprocess.check_output(['git','show','ce58e87:config/final_cross_year_sample_calendar.csv'],cwd=ROOT)
    (archive/'diagnostics/final_calendar_snapshot.csv').write_bytes(calendar_bytes)
    calendar=pd.read_csv(io.BytesIO(calendar_bytes),dtype=str)
    matrix=[dict(s,planned_trade_dates=json.dumps(s['trade_dates']),included_in_formal_run=not(s['year']==2026 and s['window_type']=='ordinary_late')) for s in cfg['samples']]
    pd.DataFrame(matrix).to_csv(archive/'diagnostics/predeclared_sample_matrix.csv',index=False)
    (archive/'diagnostics/sample_eligibility_metadata.json').write_text(json.dumps(dict(as_of_timestamp='2026-10-14T11:02:10+08:00')))
    source_info=dict(git_commit='ce58e87c15cf767b742e0cb115a3482f091df02d',git_branch='synthetic',sample_matrix_sha256='SYNTHETIC',api_request_count=106,sample_trade_date_count=185,execution_status='COMPLETED',logical_request_count=106,expected_logical_request_count=106)
    (archive/'run_info.txt').write_text('\n'.join(key+'='+json.dumps(value) for key,value in source_info.items()))
    for sample in cfg['samples']:
        if sample['window_type']!='ordinary_early':continue
        date=sample['trade_dates'][0];cal=calendar[(calendar.sample_id==sample['sample_id']) & (calendar.trade_date==date)].iloc[0]
        for session in cfg['sessions']:
            frame=external_frame();frame.trade_date=date;frame.actual_contract_month=sample['old_contract'];frame['timestamp']=pd.to_datetime([cal[session+'_open']]);frame['open']=10700
            frame.to_parquet(archive/'data'/f"{sample['year']}_ordinary_early_{session}_1bang.parquet",index=False)
        text=official_bytes().decode('cp950').replace('2018/03/01',date.replace('-','/')).replace('201803  ',sample['old_contract']+'  ')
        records=list(csv.reader(io.StringIO(text)));night=records[1].copy();night[17]='盤後'
        buf=io.StringIO();writer=csv.writer(buf);writer.writerows(records+[night]);raw=buf.getvalue().encode('cp950')
        (archive/'raw'/f'taifex_{date}.csv').write_bytes(raw)
        (archive/'diagnostics'/f'taifex_{date}_source.json').write_text(json.dumps(dict(sha256=hashlib.sha256(raw).hexdigest(),status='PASS')))
    before={str(p.relative_to(archive)):hashlib.sha256(p.read_bytes()).hexdigest() for p in archive.rglob('*') if p.is_file()}
    def forbidden(*args,**kwargs):pytest.fail('Replay cannot call official network download')
    monkeypatch.setattr('src.final_external.urlopen',forbidden)
    output,info=replay_archive(archive,tmp_path/'replay')
    assert info['replay_status']=='PASS' and info['known_2018_external_case']=='PASS'
    assert info['api_request_count']==0 and not info['canonical_promotion_allowed'] and info['formal_rerun_required']
    assert info['recomputed_validator_classification']=='UNRESOLVED'
    assert before=={str(p.relative_to(archive)):hashlib.sha256(p.read_bytes()).hexdigest() for p in archive.rglob('*') if p.is_file()}
    assert (output/'diagnostics/external_taifex_selection_diagnostics.csv').is_file()
    assert (output/'diagnostics/cross_year_aggregation_validation.csv').is_file()
    assert 'NOT a formal run' in (output/'validator_correction_replay_summary.md').read_text()
