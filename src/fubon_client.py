import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo


class MarketReader:
    """Expose only a fixed allowlist of read-only intraday operations."""
    def __init__(self, restfutopt, run_dir, cfg):
        self.rest = restfutopt
        self.intraday = restfutopt.intraday
        self.run_dir = run_dir
        self.cfg = cfg
        self.attempts = []
        self.calls = 0

    def request(self, endpoint, name, **params):
        if endpoint not in {'products', 'tickers', 'candles'}:
            raise ValueError('Read operation not allowed')
        for attempt in range(1, self.cfg['max_attempts'] + 1):
            self.calls += 1
            start = datetime.now(ZoneInfo('Asia/Taipei')).isoformat()
            response = None
            error = None
            code = None
            try:
                response = getattr(self.intraday, endpoint)(**params)
            except Exception as exc:
                code = getattr(exc, 'status_code', None)
                # Do not propagate arbitrary SDK exception text (may contain credentials).
                error = {'error_type': type(exc).__name__, 'message': 'SDK market-data request failed',
                         'status_code': code}
            entry = {'endpoint': endpoint, 'params': params, 'attempt': attempt,
                     'status_code': code, 'error_type': error['error_type'] if error else '',
                     'message': error['message'] if error else '', 'rate_limit_hit': code == 429,
                     'request_timestamp': start}
            self.attempts.append(entry)
            raw_path = self.run_dir / 'raw' / f'{name}_attempt{attempt}.json'
            # SDK responses expected to be JSON; unsupported objects are explicit diagnostics.
            try:
                raw_path.write_text(json.dumps({'request': entry, 'response': response, 'error': error},
                                               ensure_ascii=False, indent=2), encoding='utf-8')
            except TypeError:
                raw_path.write_text(json.dumps({'request': entry, 'response_type': str(type(response)),
                    'error': {'message': 'Non-JSON response cannot be losslessly archived'}}, indent=2))
                response = None
                self.attempts[-1]['error_type'] = 'NonJSONResponse'
            time.sleep(self.cfg['request_spacing_seconds'])
            if error is None:
                return response
            if code != 429 or attempt == self.cfg['max_attempts']:
                return None
            time.sleep(self.cfg['backoff_seconds'] * 2 ** (attempt - 1))
