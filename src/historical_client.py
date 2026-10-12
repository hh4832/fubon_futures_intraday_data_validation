"""Historical read adapter reusing MarketReader retry/raw archives unchanged."""
import hashlib
import inspect
from pathlib import Path
from types import SimpleNamespace
import pandas as pd
import yaml
from .config import ROOT, load_config
from .fubon_client import MarketReader


class HistoricalFatalError(RuntimeError):
    """Unsafe schema/time/auth/provenance failure: stop formal run."""


def load_historical_config(path=None):
    cfg = load_config()
    with Path(path or ROOT/'config/historical_validation.yaml').open() as f:
        historical = yaml.safe_load(f)
    cfg.update(historical)
    if cfg['product'] != 'TXF' or str(cfg['contractMonth']) != '1!' or cfg['session'] != 'regular' or int(cfg['timeframe']) != 1:
        raise ValueError('Phase 1 must remain TXF / 1! / regular / 1m')
    if not 1 <= int(cfg['max_phase']) <= 7 or cfg['recent_session_count'] != 5:
        raise ValueError('max_phase 1..7; exactly five recent completed sessions')
    for sample in cfg['cross_year_samples']:
        dates = pd.to_datetime(sample['dates'],errors='raise')
        if not 3 <= len(dates) <= 5 or len(dates.unique()) != len(dates) or not (dates.year == sample['year']).all():
            raise ValueError('Fixed yearly matrix must contain 3–5 distinct dates in the declared year')
    return cfg


def read_canonical_documentation(cfg):
    path = Path(cfg['drive_root'])/cfg['canonical_intraday_run']/'diagnostics/official_historical_documentation.txt'
    if not path.is_file():
        raise HistoricalFatalError('Canonical Historical documentation missing; configure canonical_intraday_run; no guessed fallback')
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if cfg.get('canonical_documentation_sha256') and sha != cfg['canonical_documentation_sha256']:
        raise HistoricalFatalError('Canonical documentation hash changed; review before use')
    text = raw.decode('utf-8')
    if not all(term in text for term in ['historical/candles', 'contractMonth', 'timeframe']):
        raise HistoricalFatalError('Canonical documentation not recognized')
    return raw, {'documentation_sha256':sha,'documentation_source_run':cfg['canonical_intraday_run']}


def session_calendar(cfg):
    p = Path(cfg['calendar_file'])
    if not p.is_absolute():
        p = ROOT/p
    df = pd.read_csv(p,dtype=str)
    required = {'trade_date','regular_open','regular_close','afterhours_open','afterhours_close','calendar_source'}
    if not required.issubset(df) or df.trade_date.duplicated().any():
        raise HistoricalFatalError('Invalid or duplicate session calendar')
    for field in ['regular_open','regular_close','afterhours_open','afterhours_close']:
        df[field] = pd.to_datetime(df[field],utc=True,errors='raise').dt.tz_convert('Asia/Taipei')
    if not (df.regular_close > df.regular_open).all() or not (df.afterhours_close > df.afterhours_open).all():
        raise HistoricalFatalError('Invalid session calendar windows')
    df = df.sort_values('trade_date').reset_index(drop=True)
    if not df.calendar_source.str.startswith('https://').all():
        raise HistoricalFatalError('Calendar provenance required')
    return df


def recent_complete_dates(calendar, session, now=None, count=5, delay_minutes=60):
    now = pd.Timestamp.now(tz='Asia/Taipei') if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        raise HistoricalFatalError('as-of timestamp must be timezone-aware')
    now = now.tz_convert('Asia/Taipei')
    if now.strftime('%Y-%m-%d') > calendar.trade_date.max():
        raise HistoricalFatalError('Scheduled calendar exhausted; extend verified calendar before formal run')
    ends = calendar[session+'_close']+pd.Timedelta(minutes=delay_minutes)
    chosen = calendar.loc[ends <= now,'trade_date'].tail(count).tolist()
    if len(chosen) != count:
        raise HistoricalFatalError('Calendar does not contain five complete sessions')
    return chosen


def inspect_historical(rest, run_dir, sdk_version):
    obj = getattr(rest,'historical',None)
    method = getattr(obj,'candles',None)
    if not callable(method):
        raise HistoricalFatalError('sdk.marketdata.rest_client.futopt.historical.candles is unavailable')
    try:
        sig = str(inspect.signature(method))
    except (ValueError,TypeError):
        sig = 'unavailable'
    # Bypass custom repr which could expose client configuration / authorization.
    safe_repr = object.__repr__(obj)
    text = '\n'.join([f'Python object repr (base object only): {safe_repr}',
        'callable path: sdk.marketdata.rest_client.futopt.historical.candles',
        f'implementation: {type(obj).__module__}.{type(obj).__qualname__}.candles',
        f'inspect.signature: {sig}',f'SDK version: {sdk_version}',
        'accepted parameters: documented product/contractMonth/from/to/timeframe/session/fields/sort',
        'kwargs acceptance does not establish behavior; all responses retained'])
    (run_dir/'diagnostics'/'historical_callable.txt').write_text(text,encoding='utf-8')
    return method,text


class HistoricalReader:
    """Route only historical.candles through existing sequential request framework."""
    def __init__(self, rest, run_dir, cfg):
        inspect_historical(rest,run_dir,cfg['sdk_version'])
        self.cfg,self.run_dir = cfg,run_dir
        self.reader = MarketReader(SimpleNamespace(intraday=rest.historical),run_dir,cfg)
        self.intraday = MarketReader(rest,run_dir,cfg)

    def fetch(self, name, contract, dates, session='regular', timeframe=1):
        dates = sorted(set(dates))
        start,end = pd.Timestamp(dates[0]),pd.Timestamp(dates[-1])
        if end >= start+pd.DateOffset(years=1) or end < start:
            raise ValueError('Historical query interval must be less than one year')
        if str(contract) not in {'1!','2!','3!'} and not (len(str(contract))==6 and str(contract).isdigit()):
            raise ValueError('Monthly contract must use documented yyyyMM or 1!/2!/3!')
        if int(timeframe) not in {1,5,15,60} or session not in {'regular','afterhours'}:
            raise ValueError('Unsupported validation scope')
        params = {'product':self.cfg['product'],'contractMonth':str(contract),'session':session,
                  'timeframe':str(timeframe),'from':dates[0],'to':dates[-1],
                  'fields':self.cfg['fields'],'sort':self.cfg['sort']}
        response = self.reader.request('candles',name,**params)
        response_status = response.get('statusCode',response.get('status_code')) if isinstance(response,dict) else None
        if str(self.reader.attempts[-1]['status_code']) in {'401','403'} or str(response_status) in {'401','403'}:
            raise HistoricalFatalError('Historical API authentication/permission failure; stopped')
        return {'name':name,'params':params,'requested_dates':dates,'response':response,
                'api_status':'PASS' if response is not None else 'FAIL'}

    @property
    def attempts(self):
        return [dict(a, endpoint='historical.'+a['endpoint']) for a in self.reader.attempts] + [dict(a, endpoint='intraday.'+a['endpoint']) for a in self.intraday.attempts]

    @property
    def calls(self):
        return self.reader.calls+self.intraday.calls
