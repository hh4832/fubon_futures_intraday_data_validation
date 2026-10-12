import numpy as np
import pandas as pd

FIELDS = ['open','high','low','close','volume']


def aggregate(df, timeframe, origin, label='left', closed='left'):
    result = (df.set_index('timestamp').sort_index().resample(f'{timeframe}min', origin=origin,
               label=label, closed=closed).agg(open=('open','first'),high=('high','max'),
               low=('low','min'),close=('close','last'),volume=('volume','sum'), volume_count=('volume','count'),
               minute_count=('open','count')).dropna(subset=['open']).reset_index())
    result.loc[result.volume_count == 0, 'volume'] = np.nan
    return result


def compare_aggregation(one, direct, timeframe, price_atol=1e-6, volume_atol=0):
    if one.empty or direct.empty or one.timestamp.isna().any() or direct.timestamp.isna().any():
        return {'status':'UNRESOLVED_ALIGNMENT','row_count':0,'notes':'empty or invalid timestamp'}, pd.DataFrame(columns=['timestamp']+[f'{x}_diff' for x in FIELDS])
    if one.timestamp.duplicated().any() or direct.timestamp.duplicated().any():
        return {'status':'UNRESOLVED_ALIGNMENT','row_count':0,'notes':'duplicate timestamps'}, pd.DataFrame()
    if one.trade_date.iloc[0] != direct.trade_date.iloc[0]:
        return {'status':'UNRESOLVED_ALIGNMENT','row_count':0,'notes':'different response trading dates'}, pd.DataFrame()
    candidates = []
    # Enumerate timestamp conventions; this is alignment diagnostics, not strategy optimization.
    for origin_name, origin in [('day','start_day'), ('session',one.timestamp.min())]:
        for closed in ['left','right']:
            for label in ['left','right']:
                agg = aggregate(one, timeframe, origin, label, closed)
                merged = direct[['timestamp']+FIELDS].merge(agg, on='timestamp', suffixes=('_api','_1m'), how='inner')
                if merged.empty:
                    continue
                complete = merged.minute_count.eq(timeframe)
                for field in FIELDS:
                    merged[field+'_diff'] = merged[field+'_api'] - merged[field+'_1m']
                checks = pd.DataFrame({field:np.isclose(merged[field+'_diff'],0,
                    atol=volume_atol if field=='volume' else price_atol,rtol=0) for field in FIELDS})
                merged['complete_bucket'] = complete
                merged['equal_ohlcv'] = checks.all(axis=1)
                # At least two complete buckets; exclude newest bucket due to sequential snapshots.
                stable = complete & (merged.timestamp < direct.timestamp.max())
                matches = int((stable & merged.equal_ohlcv).sum())
                mismatches = int((stable & ~merged.equal_ohlcv).sum())
                key = f'{origin_name}/{closed}/{label}'
                candidates.append((key, merged, matches, mismatches, int(stable.sum())))
    proven = [x for x in candidates if x[4]>=2 and x[3]==0]
    if not candidates:
        return {'status':'UNRESOLVED_ALIGNMENT','row_count':0,'notes':'no timestamp overlap'}, pd.DataFrame()
    best = max(candidates, key=lambda x:(x[2],-x[3],x[4]))
    # Multiple names producing identical grids are equivalent; distinct mappings remain unresolved.
    signatures = {tuple(zip(x[1].timestamp.astype(str), x[1].open_1m, x[1].close_1m)) for x in proven}
    resolved = len(signatures)==1 and bool(proven)
    if resolved:
        best = proven[0]
    key, detail, matches, mismatches, count = best
    return {'status':'PASS WITH CAVEATS' if resolved else 'UNRESOLVED_ALIGNMENT',
        'row_count':len(detail), 'candidate_alignment':key,'complete_stable_buckets':count,
        'equal_buckets':matches,'mismatch_buckets':mismatches,
        'api_only_timestamps':int((~direct.timestamp.isin(detail.timestamp)).sum()),
        'notes':'empirical consistency only; partial/gap buckets and latest bucket excluded; sequential snapshot drift possible'}, detail
