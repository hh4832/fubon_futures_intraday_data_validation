import json
from types import SimpleNamespace
import pandas as pd
from src.config import load_config
from src.fubon_client import MarketReader
from src.pipeline import Validation
from src.normalize import normalize
from src.validate_schema import validate_schema
from src.discover_contracts import select_contract
from src.archive import finish_run
from src.diagnostics import write_summary

def test_pipeline_archive(tmp_path,bars):
    cfg=load_config()
    for name in ['raw','data','diagnostics']:(tmp_path/name).mkdir()
    items=[]
    for session in cfg['sessions']:
        for tf in cfg['timeframes']:
            items.append(dict(symbol='SYNTHETIC',session=session,timeframe=tf,response={
                'date':'2026-10-12','type':'FUTURE','exchange':'TAIFEX','symbol':'SYNTHETIC',
                'timeframe':str(tf),'data':[] if session=='afterhours' else [
                    dict(date=t.isoformat(),open=100,high=102,low=99,close=101,volume=2)
                    for t in bars.timestamp.iloc[:4]]}))
    items[-1]['response']=None
    v=Validation(items,tmp_path,cfg)
    for name in ['normalization','schema validation','OHLC validation','timestamp validation','session validation']:v.stage(name)
    v.aggregation()
    info=dict(git_commit='synthetic',fubon_sdk_version='not installed',login_status='NOT TESTED',futopt_account=False)
    write_summary(tmp_path,info,v.tables,{'historical_capability':'unresolved'})
    reader=MarketReader(SimpleNamespace(intraday=None),tmp_path,cfg)
    finish_run(reader,info)
    assert (tmp_path/'diagnostics'/'invalid_ohlc_rows.parquet').exists()
    assert (tmp_path/'validation_summary.md').is_file()
    assert pd.read_csv(tmp_path/'diagnostics'/'schema_validation.csv').schema_status.eq('FAIL').any()

def test_retry_bounded(tmp_path):
    (tmp_path/'raw').mkdir()
    cfg=dict(load_config(),request_spacing_seconds=0,backoff_seconds=0)
    class RateError(Exception):status_code=429
    class API:
        def candles(self,**kwargs):raise RateError('sensitive SDK error suppressed')
    reader=MarketReader(SimpleNamespace(intraday=API()),tmp_path,cfg)
    assert reader.request('candles','test',symbol='SYNTHETIC') is None
    assert reader.calls==3 and all(a['rate_limit_hit'] for a in reader.attempts)
    assert 'sensitive' not in ''.join(p.read_text() for p in (tmp_path/'raw').glob('*'))

def test_discovery_nearest_uses_dates():
    df=pd.DataFrame([{'symbol':'SYNTHETIC_A','settlementDate':'2099-12-01'},
                     {'symbol':'SYNTHETIC_B','settlementDate':'2099-01-01'}])
    assert select_contract(df)[0]=='SYNTHETIC_B'

def test_schema_partial_field_missing():
    response={'data':[{'date':'2026-10-12T08:45:00+08:00','open':1,'high':2,'low':1,'close':2},
                      {'date':'2026-10-12T08:46:00+08:00','open':1,'high':2,'low':1}],
              'date':'2026-10-12','symbol':'SYNTHETIC','type':'FUTURE','exchange':'TAIFEX','timeframe':'1'}
    item=dict(symbol='SYNTHETIC',session='regular',timeframe=1,response=response)
    assert validate_schema(item,normalize(response,'SYNTHETIC','regular',1))['schema_status']=='FAIL'
