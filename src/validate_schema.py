import json
import pandas as pd
from .normalize import NUMERIC

TOP = {'date', 'type', 'exchange', 'symbol', 'timeframe', 'data'}
BAR = {'open', 'high', 'low', 'close'}


def validate_schema(item, df=None):
    response = item['response']
    base = {k: item[k] for k in ['symbol', 'session', 'timeframe']}
    if not isinstance(response, dict) or not isinstance(response.get('data'), list):
        return dict(base, row_count=0, missing_required_fields='data/object', extra_fields='',
                    null_counts='{}', schema_status='FAIL', notes='request failed or invalid response')
    rows = response['data']
    raw = pd.DataFrame(rows) if all(isinstance(r, dict) for r in rows) else pd.DataFrame()
    missing = TOP - response.keys()
    bar_missing = BAR - set(raw.columns)
    timestamp_present = 'date' in raw or 'timestamp' in raw
    partial = {c: int(raw[c].isna().sum()) for c in BAR if c in raw}
    failed = bool(missing or bar_missing or not timestamp_present or any(partial.values())
                  or any(not isinstance(r, dict) for r in rows))
    metadata_mismatch = response.get('symbol') != item['symbol'] or str(response.get('timeframe')) != str(item['timeframe'])
    failed |= metadata_mismatch
    if df is not None and len(df):
        failed |= bool(df[['open', 'high', 'low', 'close', 'timestamp']].isna().any().any())
    return dict(base, row_count=len(rows), missing_required_fields=json.dumps(sorted(missing | bar_missing)),
                extra_fields=json.dumps(sorted(set(raw.columns) - BAR - {'date', 'timestamp', 'volume', 'average'})),
                null_counts=json.dumps(df[NUMERIC].isna().sum().to_dict() if df is not None else partial),
                dtypes=json.dumps(df.dtypes.astype(str).to_dict() if df is not None else {}),
                top_level_keys=json.dumps(list(response)),
                metadata=json.dumps({k: response.get(k) for k in ['date','type','exchange','market','symbol','timeframe']}),
                data_type=type(rows).__name__, schema_status='UNRESOLVED' if not rows else 'FAIL' if failed else 'PASS',
                notes='empty data' if not rows else 'request/response identity mismatch' if metadata_mismatch else 'volume/average are optional in official schema')


def documentation_comparison(item):
    r = item['response']
    if not isinstance(r, dict):
        r = {}
    rows = r.get('data', [])
    keys = set().union(*(x.keys() for x in rows if isinstance(x, dict))) if isinstance(rows, list) else set()
    records = []
    for scope, documented, observed in [('top', TOP, set(r)), ('bar', BAR | {'date','volume','average'}, keys)]:
        for field in sorted(documented | observed):
            records.append({'symbol':item['symbol'], 'session':item['session'], 'timeframe':item['timeframe'],
                'field':scope+'.'+field, 'documented':field in documented, 'observed':field in observed,
                'status':'MATCH' if field in documented and field in observed else 'OBSERVED_EXTRA' if field in observed else 'NOT_OBSERVED',
                'notes':'bar.date shown in official example; volume/average optional; empty responses do not establish absence'})
    return records
