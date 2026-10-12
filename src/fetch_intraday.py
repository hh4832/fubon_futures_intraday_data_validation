from tqdm.auto import tqdm


def fetch(reader, symbol, cfg):
    results = []
    combos = [(s, t) for s in cfg['sessions'] for t in cfg['timeframes']]
    for session, timeframe in tqdm(combos, desc='API requests'):
        params = {'symbol': symbol, 'timeframe': str(timeframe)}
        if session == 'afterhours':
            params['session'] = 'afterhours'
        response = reader.request('candles', f'{symbol}_{session}_{timeframe}m', **params)
        results.append({'symbol': symbol, 'session': session, 'timeframe': timeframe,
                        'response': response})
    return results
