"""Historical quality diagnostics reusing intraday numeric validators."""
import json
import numpy as np
import pandas as pd
from .normalize import NUMERIC
from .validate_ohlc import validate_ohlc, average_diagnostics
from .validate_time import validate_time

TOP = {'product','contractMonth','exchange','session','timeframe','sort','data'}
BAR = {'date','contractMonth','open','high','low','close','volume'}
ALLOWED = {'PASS','PASS WITH CAVEATS','FAIL','UNRESOLVED'}


def status_rollup(values):
    values=list(values)
    return 'FAIL' if 'FAIL' in values else 'UNRESOLVED' if not values or any(v not in {'PASS','PASS WITH CAVEATS'} for v in values) else 'PASS WITH CAVEATS' if 'PASS WITH CAVEATS' in values else 'PASS'


def validate_historical_schema(item,df):
    response=item['response'];params=item['params']
    keys=set(response) if isinstance(response,dict) else set()
    rows=response.get('data',[]) if isinstance(response,dict) else []
    raw=pd.DataFrame(rows) if isinstance(rows,list) and all(isinstance(r,dict) for r in rows) else pd.DataFrame()
    missing_top=TOP-keys
    missing_bar=BAR-set(raw.columns)
    identity = all(str(response.get(f)).lower()==str(params.get(f)).lower() for f in ['product','contractMonth','session','timeframe','sort']) if isinstance(response,dict) else False
    core_null=int(df[['open','high','low','close']].isna().sum().sum()) if len(df) else 0
    actual_month_missing=int(df.actual_contract_month.isna().sum()) if len(df) else 0
    actual_month_invalid=int((~df.actual_contract_month.str.fullmatch(r'[0-9]{6}',na=False)).sum()) if len(df) else 0
    exchange_match = response.get('exchange')=='TAIFEX' if isinstance(response,dict) else False
    return {'request':item['name'],'row_count':len(df),'top_level_keys':json.dumps(sorted(keys)),
        'missing_top_fields':json.dumps(sorted(missing_top)),'missing_bar_fields':json.dumps(sorted(missing_bar)),
        'extra_bar_fields':json.dumps(sorted(set(raw.columns)-BAR-{'average','transaction'})),
        'null_counts':json.dumps(df[NUMERIC].isna().sum().to_dict()),
        'dtypes':json.dumps(df.dtypes.astype(str).to_dict()),'request_response_identity_match':identity,
        'actual_contract_missing_rows':actual_month_missing,'invalid_actual_contract_rows':actual_month_invalid,
        'schema_status':'UNRESOLVED' if df.empty else 'FAIL' if missing_top or missing_bar or not identity or core_null or actual_month_missing or actual_month_invalid or not exchange_match else 'PASS',
        'evidence_type':'Observed fact','notes':'actual identity is per-bar contractMonth; actual symbol not invented'}


def validate_historical_ohlc(df):
    summary,invalid=validate_ohlc(df)
    finite=np.isfinite(df[['open','high','low','close','volume']].to_numpy(dtype=float))
    nonfinite=(~finite).any(axis=1)
    summary['nonfinite_core_values']=int(nonfinite.sum())
    if nonfinite.any():summary['status']='FAIL'
    return summary,df.loc[df.index.isin(invalid.index) | nonfinite].copy()


def daily_coverage(df,item,calendar):
    work=df.copy()
    key=['trade_date']
    valid=work.trade_date.notna()
    w=work[valid].copy().sort_values(['trade_date','timestamp'])
    w['_duplicate']=w.duplicated(['trade_date','timestamp'])
    # Duplicate rows are retained; missing-slot arithmetic uses unique counts only.
    delta=w.groupby('trade_date',dropna=False).timestamp.diff().dt.total_seconds()/60
    step=int(item['params']['timeframe'])
    w['_gap']=delta.gt(step)
    w['_invalid']=((w[['open','high','low','close']].le(0).any(axis=1)) |
        w[['open','high','low','close']].isna().any(axis=1) |
        (w.high<w[['open','close','low']].max(axis=1)) |
        (w.low>w[['open','close','high']].min(axis=1)) |
        w.volume.lt(0) | w.average.le(0))
    w['_null']=w[['open','high','low','close','volume']].isna().sum(axis=1)
    summary=w.groupby('trade_date',dropna=False).agg(row_count=('timestamp','size'),
        first_timestamp=('timestamp','min'),last_timestamp=('timestamp','max'),
        duplicate_count=('_duplicate','sum'),gap_event_count=('_gap','sum'),
        invalid_ohlc_count=('_invalid','sum'),null_count=('_null','sum'),
        unique_timestamps=('timestamp','nunique'),actual_symbol_if_available=('actual_symbol_if_available','first'),
        actual_contracts_observed=('actual_contract_month','nunique')).reset_index()
    requested=pd.DataFrame({'trade_date':item['requested_dates']})
    # Include requested dates absent from response, plus any unexpected dates returned.
    summary=requested.merge(summary,on='trade_date',how='outer',indicator='date_membership')
    counts=['row_count','duplicate_count','gap_event_count','invalid_ohlc_count','null_count','unique_timestamps','actual_contracts_observed']
    for c in counts:summary[c]=summary[c].fillna(0).astype(int) # diagnostics counts, never OHLC repair
    session=item['params']['session']
    reference=calendar[['trade_date',session+'_open',session+'_close','calendar_source']].copy()
    summary=summary.merge(reference,on='trade_date',how='left',validate='one_to_one')
    reference_minutes=(summary[session+'_close']-summary[session+'_open']).dt.total_seconds()/60
    summary['reference_slot_count']=np.ceil(reference_minutes/step)
    summary['missing_slot_count']=(summary.reference_slot_count-summary.unique_timestamps).clip(lower=0)
    summary['session']=session
    summary['contract_query']=item['params']['contractMonth']
    summary['cross_day_contamination']=summary.date_membership.eq('right_only')
    summary['outside_reference_window']=False
    # Broad scheduled reference only; expiry and exceptional session changes do not automatically FAIL.
    if len(w):
        wr=w.merge(reference,on='trade_date',how='left')
        outside=((wr.timestamp<wr[session+'_open']) | (wr.timestamp>=wr[session+'_close'])) & wr[session+'_open'].notna()
        outs=wr.assign(_outside=outside).groupby('trade_date')._outside.sum()
        summary['outside_reference_window']=summary.trade_date.map(outs).fillna(0).gt(0)
    bad=summary.duplicate_count.gt(0) | summary.invalid_ohlc_count.gt(0) | summary.cross_day_contamination
    absent=summary.row_count.eq(0)
    caveat=summary.gap_event_count.gt(0) | summary.missing_slot_count.gt(0) | summary.outside_reference_window | summary.calendar_source.isna()
    summary['status']=np.select([bad,absent,caveat],['FAIL','UNRESOLVED','PASS WITH CAVEATS'],default='PASS')
    summary['coverage_classification']=np.where(absent | caveat,'unresolved','observed coverage consistent with scheduled reference')
    summary['confirmed_missing_data']=False
    summary['evidence_type']='Observed fact'
    summary['notes']='slot counts are scheduled-reference diagnostics only; holiday/partial session/no-trade suppression/provider omission remain unresolved'
    return summary


def evaluate(item,df,calendar):
    schema=validate_historical_schema(item,df)
    ohlc,invalid=validate_historical_ohlc(df)
    coverage=daily_coverage(df,item,calendar)
    timestamp,_=validate_time(df)
    # Long overnight differences are not intraday missing-minute events.
    timestamp['observed_gaps']=int(coverage.gap_event_count.sum())
    timestamp['notes']='global monotonicity from source_row; gaps calculated within mapped trade-date/session only'
    sessions={'row_count':len(df),'unmapped_trade_date_rows':int(df.trade_date.isna().sum()),
        'cross_day_contamination_rows':int((~df.trade_date.isin(item['requested_dates'])).sum()),
        'calendar_dates':json.dumps(df.calendar_date.dropna().unique().tolist()),
        'trade_dates':json.dumps(df.trade_date.dropna().unique().tolist()),
        'status':'UNRESOLVED' if df.empty or df.trade_date.isna().any() else 'FAIL' if (~df.trade_date.isin(item['requested_dates'])).any() else status_rollup(coverage.status),
        'evidence_type':'Inference','notes':'trade_date mapping follows sourced reference; observed API contractMonth retained independently'}
    if 'provider_trade_date_matches_mapping' in df and not df.provider_trade_date_matches_mapping.fillna(False).all():
        sessions['status']='FAIL'
        sessions['notes']='observed provider trade-date metadata disagrees with calendar mapping'
    for record in [ohlc,timestamp]:record['evidence_type']='Observed fact'
    average=average_diagnostics(df)
    average['evidence_type']='Unresolved'
    average['documented_definition']='Documented fact: historical per-bar volume-weighted average; not assumed equal to intraday average'
    statuses=[schema['schema_status'],ohlc['status'],timestamp['status'],sessions['status'],status_rollup(coverage.status)]
    return {'schema':schema,'ohlc':ohlc,'timestamp':timestamp,'session':sessions,
            'daily':coverage,'average':average,'invalid':invalid,'status':status_rollup(statuses)}
