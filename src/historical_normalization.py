"""Historical normalization preserving evidence; no silent time or data repair."""
import numpy as np
import pandas as pd
from .normalize import normalize, NUMERIC
from .historical_client import HistoricalFatalError


def normalize_historical(item, calendar):
    response,params = item['response'],item['params']
    if not isinstance(response,dict) or not isinstance(response.get('data'),list):
        raise HistoricalFatalError('Historical response must be object with list data')
    rows = response['data']
    if any(not isinstance(row,dict) for row in rows):
        raise HistoricalFatalError('Historical bar is not a JSON object')
    if rows and not {'date','open','high','low','close'}.issubset(set().union(*(r.keys() for r in rows))):
        raise HistoricalFatalError('Historical schema completely incompatible')
    df = normalize(response,params['product'],params['session'],int(params['timeframe']),sort=False)
    if df.timestamp.isna().any():
        raise HistoricalFatalError('Historical timestamp naive/missing/unparseable; no inferred repair')
    # Keep API contract month distinct from query alias; never derive a symbol from month codes.
    df['contract_query'] = params['contractMonth']
    df['product'] = params['product']
    df['query_from'] = params['from']
    df['query_to'] = params['to']
    df['actual_contract_month'] = df.get('contractMonth',pd.Series(pd.NA,index=df.index,dtype='string')).astype('string')
    df['actual_symbol_if_available'] = df.get('raw_symbol',pd.Series(pd.NA,index=df.index,dtype='string'))
    df['response_contractMonth'] = response.get('contractMonth')
    df['response_session'] = response.get('session')
    df['timestamp_bucket_definition'] = 'Documented fact: start timestamp; empirical alignment checked separately'
    if params['session']=='regular':
        df['trade_date'] = df.calendar_date
        df['trade_date_evidence'] = 'Inference: regular calendar date; verify against requested dates / session window'
    else:
        # Map to explicitly sourced session windows; midnight/weekends/holidays are not +1 calendar day.
        cal = calendar.sort_values('afterhours_open')
        starts = pd.DatetimeIndex(cal.afterhours_open).tz_convert('Asia/Taipei')
        ends = pd.DatetimeIndex(cal.afterhours_close).tz_convert('Asia/Taipei')
        idx = pd.IntervalIndex.from_arrays(starts,ends,closed='left')
        if idx.is_overlapping:
            raise HistoricalFatalError('Overlapping night session calendar')
        positions = idx.get_indexer(pd.DatetimeIndex(df.timestamp)) if len(df) else np.array([],dtype=int)
        lookup = cal.trade_date.to_numpy()
        values = np.full(len(df),None,dtype=object)
        matched = positions>=0
        values[matched]=lookup[positions[matched]]
        df['trade_date'] = pd.Series(values,index=df.index,dtype='string')
        df['trade_date_evidence'] = 'Inference: sourced scheduled calendar session windows; API per-bar trade date not assumed'
    if 'raw_trade_date' in df:
        df['provider_trade_date'] = df.raw_trade_date
        df['provider_trade_date_matches_mapping'] = df.provider_trade_date.astype('string').eq(df.trade_date.astype('string'))
    return df.sort_values('timestamp',kind='stable').reset_index(drop=True)
