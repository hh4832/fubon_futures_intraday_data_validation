"""High-level staged validation, shared by Colab and synthetic integration tests."""
import pandas as pd
from tqdm.auto import tqdm
from .normalize import normalize
from .validate_schema import validate_schema, documentation_comparison
from .validate_ohlc import validate_ohlc, average_diagnostics
from .validate_time import validate_time
from .validate_sessions import validate_session
from .validate_aggregation import compare_aggregation
from .diagnostics import timed, write_table


class Validation:
    def __init__(self, items, path, cfg):
        self.items,self.path,self.cfg = items,path,cfg
        self.frames = {}
        self.metrics = []
        self.tables = {}

    def identity(self,item):
        return {k:item[k] for k in ['symbol','session','timeframe']}

    def key(self,item):
        return item['session'],item['timeframe']

    def stage(self,name):
        records = []
        for item in tqdm(self.items,desc=name):
            key = self.key(item)
            df = self.frames.get(key)
            ident = self.identity(item)
            raw_response = item.get('response')
            raw_rows = raw_response.get('data', []) if isinstance(raw_response, dict) else []
            row_count = len(df) if df is not None else len(raw_rows) if isinstance(raw_rows, list) else 0
            with timed(self.metrics,name,row_count):
                if name=='normalization':
                    try:
                        df = normalize(item['response'],**ident)
                        self.frames[key] = df
                        df.to_parquet(self.path/'data'/f"{item['symbol']}_{key[0]}_{key[1]}m.parquet",index=False)
                        records.append(dict(ident,status='PASS',row_count=len(df)))
                    except (ValueError,TypeError,KeyError) as e:
                        records.append(dict(ident,status='FAIL',notes=str(e)))
                elif name=='schema validation':
                    records.append(validate_schema(item,df))
                elif df is None:
                    records.append(dict(ident,status='UNRESOLVED',notes='normalization unavailable'))
                elif name=='OHLC validation':
                    summary,invalid = validate_ohlc(df)
                    invalid.to_parquet(self.path/'data'/f"invalid_ohlc_{key[0]}_{key[1]}m.parquet",index=False)
                    records.append(dict(ident,**summary))
                elif name=='timestamp validation':
                    summary,gaps = validate_time(df)
                    gaps.to_csv(self.path/'diagnostics'/f'observed_gaps_{key[0]}_{key[1]}m.csv',index=False)
                    records.append(dict(ident,**summary))
                elif name=='session validation':
                    records.append(dict(ident,**validate_session(df,key[0],self.cfg)))
        names = {'normalization':'normalization','schema validation':'schema_validation',
            'OHLC validation':'ohlc_validation','timestamp validation':'timestamp_validation','session validation':'session_validation'}
        self.tables[names[name]] = write_table(self.path,names[name],records)
        if name=='schema validation':
            write_table(self.path,'documentation_vs_observed',[r for item in self.items for r in documentation_comparison(item)])
        if name=='OHLC validation':
            invalids = [pd.read_parquet(p) for p in (self.path/'data').glob('invalid_ohlc_*m.parquet')]
            combined = pd.concat(invalids,ignore_index=True) if invalids else normalize({'data':[]},'','regular',1)
            combined.to_parquet(self.path/'diagnostics'/'invalid_ohlc_rows.parquet',index=False)
            self.tables['average_validation'] = write_table(self.path,'average_validation',[
                dict(session=s,timeframe=t,**average_diagnostics(df)) for (s,t),df in self.frames.items()])
        return self.tables[names[name]]

    def aggregation(self):
        records = []
        for session in tqdm(self.cfg['sessions'],desc='aggregation validation'):
            one = self.frames.get((session,1),pd.DataFrame())
            for timeframe in [5,15,60]:
                with timed(self.metrics,'aggregation validation',len(one)):
                    summary,detail = compare_aggregation(one,self.frames.get((session,timeframe),pd.DataFrame()),
                        timeframe,self.cfg['aggregation_price_atol'],self.cfg['aggregation_volume_atol'])
                detail.to_csv(self.path/'diagnostics'/f'aggregation_detail_{session}_{timeframe}m.csv',index=False)
                records.append(dict(session=session,timeframe=timeframe,**summary))
        self.tables['aggregation_validation'] = write_table(self.path,'aggregation_validation',records)
        write_table(self.path,'performance',self.metrics)
        return self.tables['aggregation_validation']
