import pandas as pd
from tqdm.auto import tqdm


def discover(reader):
    responses = {}
    tables = []
    for session in tqdm(['REGULAR', 'AFTERHOURS'], desc='contract discovery'):
        params = dict(type='FUTURE', exchange='TAIFEX', session=session, contractType='I')
        products = reader.request('products', f'products_{session}', **params, status='N')
        responses[f'products_{session}'] = products
        data = products.get('data', []) if isinstance(products, dict) else []
        valid = [p for p in data if isinstance(p, dict) and p.get('symbol') == 'TXF'
                 and p.get('statusCode', 'N') == 'N']
        if not valid:
            continue
        tickers = reader.request('tickers', f'tickers_{session}', **params, product='TXF')
        responses[f'tickers_{session}'] = tickers
        rows = tickers.get('data', []) if isinstance(tickers, dict) else []
        table = pd.DataFrame([r for r in rows if isinstance(r, dict)])
        if not table.empty and 'symbol' in table:
            # Use response codes only; exclude aliases and calendar spreads.
            table = table[table.symbol.str.startswith('TXF', na=False)
                          & ~table.symbol.str.contains(r'[/!]', regex=True, na=False)].copy()
            table['discovery_session'] = session
            tables.append(table)
    return responses, pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()


def select_contract(contracts, symbol=None):
    if contracts.empty:
        raise RuntimeError('TXF discovery failed; no guessed contract fallback')
    if symbol:
        if symbol not in contracts.symbol.values:
            raise ValueError('selected_symbol must exist in API discovery')
        return symbol, 'user-selected discovered contract'
    candidates = contracts.drop_duplicates('symbol').copy()
    field = next((f for f in ['settlementDate', 'endDate'] if f in candidates), None)
    if field:
        dates = pd.to_datetime(candidates[field], errors='coerce')
        today = pd.Timestamp.now(tz='Asia/Taipei').tz_localize(None).normalize()
        eligible = candidates[dates >= today].assign(_expiry=dates[dates >= today])
        if not eligible.empty:
            return eligible.sort_values(['_expiry', 'symbol']).iloc[0]['symbol'], 'nearest unexpired API settlement/endDate; volume dominance unverified'
    if len(candidates) == 1:
        return candidates.iloc[0]['symbol'], 'only discovered contract; dominance unverified'
    raise RuntimeError('Cannot identify nearest contract from response; configure selected_symbol from discovered candidates')
