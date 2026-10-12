"""Full synthetic phase orchestration; never imports SDK or uses credentials."""
from types import SimpleNamespace
import numpy as np
import pandas as pd
from src.historical_client import load_historical_config,session_calendar
from src.historical_runner import HistoricalValidationRun
from src.validate_aggregation import aggregate


def test_all_phases_synthetic(tmp_path,monkeypatch):
    cfg=dict(load_historical_config(),max_phase=7,request_spacing_seconds=0,backoff_seconds=0)
    cal=session_calendar(cfg)
    recent=['2026-10-05','2026-10-06','2026-10-07','2026-10-08','2026-10-12']
    monkeypatch.setattr('src.historical_runner.recent_complete_dates',lambda *a,**kw:recent)
    all_dates=sorted(set(cal.trade_date).union(*(s['dates'] for s in cfg['cross_year_samples'])))
    class SyntheticAPI:
        def products(self,**params):return {'data':[{'symbol':'TXF','statusCode':'N'}]}
        def tickers(self,**params):return {'data':[{'symbol':'TXF_SYNTHETIC','settlementDate':'2026-10-21',
            'startDate':'2026-07-01','endDate':'2026-10-21'}]}
        def candles(self,**params):
            intraday='symbol' in params
            dates=[recent[-1]] if intraday else [d for d in all_dates if params['from']<=d<=params['to']]
            tf=int(params.get('timeframe','1'));session=params.get('session','regular')
            query=params.get('contractMonth','202610')
            arrays=[]
            for date in dates:
                actual=('202609' if date<='2026-09-16' else '202610') if query=='1!' else query
                base=100 if actual=='202609' else 200
                if session=='regular':start=pd.Timestamp(date+'T08:45:00+08:00');n=300
                else:
                    row=cal.loc[cal.trade_date.eq(date)].iloc[0]
                    start=row.afterhours_open;n=840
                timestamps=pd.date_range(start,periods=n,freq='min')
                price=base+np.arange(n,dtype=float)/100
                one=pd.DataFrame({'timestamp':timestamps,'open':price,'high':price+2,'low':price-1,'close':price+1,'volume':2})
                frame=one if tf==1 else aggregate(one,tf,start)
                records=frame[['open','high','low','close','volume']].copy()
                records['date']=frame.timestamp.astype(str)
                records['average']=(records.open+records.close)/2
                if not intraday:records['contractMonth']=actual
                arrays.append(records)
            response=pd.concat(arrays,ignore_index=True).to_dict('records') if arrays else []
            if intraday:return {'date':recent[-1],'symbol':params['symbol'],'type':'FUTURE','exchange':'TAIFEX','timeframe':'1','data':response}
            return {'product':'TXF','contractMonth':query,'exchange':'TAIFEX','session':session.upper(),
                'timeframe':str(tf),'sort':'asc','data':response}
    rest=SimpleNamespace(historical=SyntheticAPI(),intraday=SyntheticAPI())
    for d in ['raw','data','diagnostics']:(tmp_path/d).mkdir()
    info={'git_commit':'synthetic','git_branch':'synthetic','fubon_sdk_version':'NOT INSTALLED','login_status':'NOT TESTED'}
    task=HistoricalValidationRun(rest,tmp_path,cfg,info,cal)
    task.run()
    assert len(task.stages)==7
    assert all(s['status'] in {'PASS','PASS WITH CAVEATS'} for s in task.stages),task.stages
    text=task.summary()
    assert '修改後再測' in text # Even full synthetic success is not source promotion.
    assert (tmp_path/'diagnostics'/'historical_vs_intraday_overlap.csv').is_file()
    assert (tmp_path/'diagnostics'/'rollover_mapping.csv').is_file()
    cross=pd.read_csv(tmp_path/'diagnostics'/'cross_year_coverage.csv')
    assert set(cross.year)=={2017,2018,2020,2022,2024,2025,2026}
