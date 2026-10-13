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
    df['terminal_boundary_bar'] = False
    if params['session']=='regular':
        df['trade_date'] = df.calendar_date
        df['trade_date_evidence'] = 'Inference: regular calendar date; verify against requested dates / session window'
    else:
        df = map_afterhours_trade_dates(df, calendar)
    if 'raw_trade_date' in df:
        df['provider_trade_date'] = df.raw_trade_date
        df['provider_trade_date_matches_mapping'] = df.provider_trade_date.astype('string').eq(df.trade_date.astype('string'))
    return df.sort_values('timestamp',kind='stable').reset_index(drop=True)


def map_afterhours_trade_dates(frame, calendar):
    """Normal [open, close); exact close is a labelled provider terminal bar.

    No timestamp/value edits. Shared/ambiguous boundaries fail rather than guess.
    This policy does not establish closing-auction semantics.
    """
    df=frame.copy()
    cal=calendar.sort_values('afterhours_open')
    starts=pd.DatetimeIndex(cal.afterhours_open).tz_convert('Asia/Taipei')
    ends=pd.DatetimeIndex(cal.afterhours_close).tz_convert('Asia/Taipei')
    idx=pd.IntervalIndex.from_arrays(starts,ends,closed='left')
    if idx.is_overlapping or ends.has_duplicates:
        raise HistoricalFatalError('Ambiguous night session calendar')
    timestamps=pd.DatetimeIndex(df.timestamp)
    positions=idx.get_indexer(timestamps) if len(df) else np.array([],dtype=int)
    terminal_positions=ends.get_indexer(timestamps) if len(df) else np.array([],dtype=int)
    terminal=terminal_positions>=0
    if ((positions>=0) & terminal & (positions!=terminal_positions)).any():
        raise HistoricalFatalError('Terminal boundary overlaps a different night session')
    positions[terminal]=terminal_positions[terminal]
    values=np.full(len(df),None,dtype=object)
    matched=positions>=0
    values[matched]=cal.trade_date.to_numpy()[positions[matched]]
    df['trade_date']=pd.Series(values,index=df.index,dtype='string')
    df['terminal_boundary_bar']=terminal
    df['trade_date_evidence']='Inference: sourced night windows; exact scheduled close mapped as provider terminal boundary, not proof of closing auction'
    if 'provider_trade_date' in df:
        df['provider_trade_date_matches_mapping']=df.provider_trade_date.astype('string').eq(df.trade_date)
    return df
