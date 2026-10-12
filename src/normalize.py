import pandas as pd

NUMERIC = ['open', 'high', 'low', 'close', 'volume', 'average']


def normalize(response, symbol, session, timeframe, sort=True):
    if not isinstance(response, dict) or not isinstance(response.get('data'), list):
        raise ValueError('Expected JSON object with list data')
    if any(not isinstance(row, dict) for row in response['data']):
        raise ValueError('Every candle must be an object')
    df = pd.DataFrame(response['data'])
    # Retain original fields and source order; duplicates never dropped.
    df['source_row'] = range(len(df))
    for field in ['symbol', 'session', 'timeframe', 'trade_date', 'timestamp', 'timestamp_tw', 'calendar_date', 'time']:
        if field in df:
            df['raw_' + field] = df[field]
    source = df.get('date', df.get('raw_timestamp', pd.Series(index=df.index, dtype='object')))
    df['timestamp_raw'] = source
    text = source.astype('string')
    aware = text.str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$', na=False)
    # Never silently interpret a naive timestamp as UTC or Asia/Taipei.
    df['timestamp_source_aware'] = aware
    parsed = pd.to_datetime(source.where(aware), errors='coerce', utc=True, format='mixed')
    df['timestamp'] = parsed.dt.tz_convert('Asia/Taipei')
    df['timestamp_tw'] = df['timestamp']
    for col in NUMERIC:
        if col in df:
            df['raw_' + col] = df[col]
        df[col] = pd.to_numeric(df.get(col, pd.Series(index=df.index, dtype='float64')), errors='coerce')
    df['symbol'] = symbol
    df['session'] = session
    df['timeframe'] = int(timeframe)
    df['trade_date'] = response.get('date')
    df['calendar_date'] = df.timestamp.dt.strftime('%Y-%m-%d')
    df['time'] = df.timestamp.dt.strftime('%H:%M:%S')
    return df.sort_values('timestamp', kind='stable', na_position='last').reset_index(drop=True) if sort else df
