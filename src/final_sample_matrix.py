"""Immutable, source-backed final sampling plan; no market discovery requests."""
import copy
import hashlib
import json
from pathlib import Path
import pandas as pd
import yaml
from .config import ROOT
from .historical_client import HistoricalFatalError,load_historical_config,session_calendar


def load_final_config(path=None):
    shared=load_historical_config()
    cfg={k:shared[k] for k in ['timezone','sdk_version','sdk_download_url','sdk_official_sha256','repository','drive_root','max_attempts','backoff_seconds','request_spacing_seconds','canonical_intraday_run','canonical_documentation_sha256']}
    with Path(path or ROOT/'config/final_cross_year_validation.yaml').open() as f:cfg.update(yaml.safe_load(f))
    cfg['timeframes']=[1]
    return cfg


def calendar_path(cfg):
    p=Path(cfg['calendar_file'])
    return p if p.is_absolute() else ROOT/p


def effective_calendar(calendar,session):
    return calendar[calendar.afterhours_scheduled.eq('true')].copy() if session=='afterhours' else calendar.copy()


def build_query_plan(cfg):
    rows=[]
    def add(s,role='primary',contract=None):
        for session in cfg['sessions']:
            queries=[contract] if contract else ['1!',s['old_contract'],s['new_contract']] if s['window_type']=='june_rollover' else ['1!']
            for q in queries:
                dates=s['trade_dates']
                rows.append(dict(name=f"{s['year']}_{s['window_type']}_{session}_{'1bang' if q=='1!' else q}"+('_repeat' if role=='repeat' else ''),year=s['year'],window_type=s['window_type'],sample_id=s['sample_id'],session=session,contract_query=q,role=role,dates=dates,window_from=dates[0],window_to=dates[-1]))
    for s in cfg['samples']:add(s)
    for year in cfg['repeatability_years']:
        s=next(s for s in cfg['samples'] if s['year']==year and s['window_type']=='ordinary_early')
        add(s,'repeat','1!')
    for session in cfg['sessions']:
        dates=cfg['availability_dates'] if session=='regular' else cfg['availability_afterhours_dates']
        rows.append(dict(name=f'2017_availability_boundary_{session}_1bang',year=2017,window_type='availability_boundary',sample_id='2017_availability_boundary',session=session,contract_query='1!',role='availability',dates=dates,window_from=dates[0],window_to=dates[-1]))
    return rows


class FrozenSampleMatrix:
    def __init__(self,cfg):
        self.cfg=copy.deepcopy(cfg)
        if cfg['product']!='TXF' or cfg['sessions']!=['regular','afterhours'] or cfg['timeframe']!=1 or cfg['sort']!='asc' or cfg['fields']!='open,high,low,close,volume,average':
            raise HistoricalFatalError('Final validation scope must remain canonical TXF 1m')
        if not 1<=cfg['max_attempts']<=3 or cfg['repeatability_years']!=[2017,2021,2026]:
            raise HistoricalFatalError('Fixed repeatability years and bounded attempts required')
        if len(cfg['samples'])!=30 or {s['year'] for s in cfg['samples']}!=set(range(2017,2027)):
            raise HistoricalFatalError('Ten years, three fixed windows each required')
        for year in range(2017,2027):
            samples=[s for s in cfg['samples'] if s['year']==year]
            if len(samples)!=3 or {s['window_type'] for s in samples}!={'ordinary_early','june_rollover','ordinary_late'}:
                raise HistoricalFatalError('Exactly one of each predeclared window per year required')
        if len({s['sample_id'] for s in cfg['samples']})!=30:
            raise HistoricalFatalError('Distinct sample identities required')
        self.calendar=session_calendar(cfg)
        self.file_hash=hashlib.sha256(calendar_path(cfg).read_bytes()).hexdigest()
        if self.file_hash!=cfg['calendar_sha256']:
            raise HistoricalFatalError('Reviewed calendar hash changed; stop before market requests')
        for s in cfg['samples']:
            dates=s['trade_dates'];cal=self.calendar[self.calendar.sample_id.eq(s['sample_id'])]
            expected=9 if s['window_type']=='june_rollover' else 5
            if len(dates)!=expected or dates!=sorted(set(dates)) or cal.trade_date.tolist()!=dates or dates[0]!=s['window_from'] or dates[-1]!=s['window_to']:
                raise HistoricalFatalError('Sample/config/calendar mismatch; no date replacement')
            if s['window_type']=='june_rollover' and dates[4]!=s['expiry_date']:
                raise HistoricalFatalError('Rollover must contain expiry at fixed center')
            if not cal.calendar_source.str.startswith('https://www.taifex.com.tw/').all():
                raise HistoricalFatalError('Official calendar provenance required')
        self.plan=build_query_plan(cfg)
        self.sample_matrix_sha256=hashlib.sha256(json.dumps(dict(cfg=self.cfg,calendar_sha256=self.file_hash,plan=self.plan),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.config_hash=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()
    def verify(self,cfg):
        if hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()!=self.config_hash or hashlib.sha256(calendar_path(cfg).read_bytes()).hexdigest()!=self.file_hash:
            raise HistoricalFatalError('Sample matrix mutated; stop entire formal run, do not repair live')
    def require_completed(self,now=None):
        now=pd.Timestamp.now(tz='Asia/Taipei') if now is None else pd.Timestamp(now)
        if now.tzinfo is None:raise HistoricalFatalError('As-of must be timezone-aware')
        if (self.calendar.regular_close>=now).any() or (effective_calendar(self.calendar,'afterhours').afterhours_close>=now).any():
            raise HistoricalFatalError('Fixed matrix contains future/incomplete sessions; no replacement or partial final PASS')
    def write(self,path):
        rows=[dict(s,trade_dates=json.dumps(s['trade_dates'])) for s in self.cfg['samples']]
        pd.DataFrame(rows).to_csv(path/'diagnostics/predeclared_sample_matrix.csv',index=False)
        self.calendar.to_csv(path/'diagnostics/final_calendar_snapshot.csv',index=False)
        pd.DataFrame(self.plan).to_csv(path/'diagnostics/predeclared_query_plan.csv',index=False)
