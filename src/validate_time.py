import pandas as pd


def validate_time(df):
    # Original order survives sorting via source_row.
    original = df.sort_values('source_row') if 'source_row' in df else df
    t = original.timestamp
    valid = t.dropna()
    ordered = valid.sort_values().drop_duplicates()
    delta = ordered.diff().dt.total_seconds() / 60
    step = int(df.timeframe.iloc[0]) if len(df) else 1
    gaps = pd.DataFrame({'timestamp':ordered, 'gap_minutes':delta})
    gaps = gaps[gaps.gap_minutes > step].copy()
    gaps['classification'] = 'observed_gap'
    gaps['confirmed_missing_data'] = False
    ascending = valid.is_monotonic_increasing
    descending = valid.is_monotonic_decreasing
    duplicate = int(valid.duplicated().sum())
    invalid = int(t.isna().sum())
    summary = {'row_count':len(df), 'timezone':str(t.dt.tz), 'ascending':ascending,
        'descending':descending, 'non_monotonic':not (ascending or descending),
        'duplicate_timestamp':duplicate, 'invalid_or_naive_timestamp':invalid,
        'observed_gaps':len(gaps), 'confirmed_missing_data':'unconfirmed',
        'first_timestamp':str(ordered.min()), 'last_timestamp':str(ordered.max()),
        'status':'UNRESOLVED' if df.empty else 'FAIL' if invalid or duplicate else
        'PASS WITH CAVEATS' if len(gaps) or not ascending else 'PASS'}
    return summary, gaps
