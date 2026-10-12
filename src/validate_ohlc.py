import pandas as pd


def validate_ohlc(df):
    flags = pd.DataFrame(index=df.index)
    flags['missing_price'] = df[['open','high','low','close']].isna().any(axis=1)
    flags['nonpositive_price'] = df[['open','high','low','close']].le(0).any(axis=1)
    flags['invalid_high'] = df.high < df[['open','close','low']].max(axis=1)
    flags['invalid_low'] = df.low > df[['open','close','high']].min(axis=1)
    flags['negative_volume'] = df.volume.lt(0)
    flags['nonpositive_average'] = df.average.notna() & df.average.le(0)
    invalid = flags.any(axis=1)
    summary = {c: int(flags[c].sum()) for c in flags}
    summary.update(row_count=len(df), invalid_rows=int(invalid.sum()),
        missing_volume=int(df.volume.isna().sum()),
        status='UNRESOLVED' if df.empty else 'FAIL' if invalid.any() else 'PASS WITH CAVEATS' if df.volume.isna().any() else 'PASS')
    return summary, df.loc[invalid].join(flags.loc[invalid])


def average_diagnostics(df):
    available = df.average.notna()
    outside = available & ((df.average < df.low) | (df.average > df.high))
    ordered = df.sort_values('timestamp')
    implied = (ordered.average * ordered.volume.cumsum()).diff() / ordered.volume.replace(0, float('nan'))
    return {'row_count':len(df), 'average_rows':int(available.sum()),
            'outside_bar_range':int(outside.sum()),
            'cumulative_implied_outside_range':int(((implied < ordered.low) | (implied > ordered.high)).sum()),
            'conclusion':'unclear', 'status':'UNRESOLVED',
            'notes':'OHLC cannot establish VWAP identity; implied values assume complete nonzero-volume sequence; not proof'}
