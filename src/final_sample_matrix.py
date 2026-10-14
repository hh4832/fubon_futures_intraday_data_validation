"""Immutable, source-backed final sampling plan; no market discovery requests."""
import copy
import hashlib
import json
from pathlib import Path
import pandas as pd
import yaml
from .config import ROOT
from .historical_client import HistoricalFatalError,load_historical_config,session_calendar

POLICY_REVISION_REASON='Future predetermined current-year window detected before any market request.'
PREVIOUS_SAMPLE_MATRIX_SHA256='423ab3cb3fb9b6adfbe690516e0c8a35e8b381b71be099e1331b16815276d4f0'
SCOPE_LANGUAGE='Validated deterministic historical samples from 2017-06 through the latest completed eligible window as of the formal run timestamp.'

def as_of_timestamp(now=None):
    value=pd.Timestamp.now(tz='Asia/Taipei') if now is None else pd.Timestamp(now)
    if value.tzinfo is None:raise HistoricalFatalError('As-of must be timezone-aware')
    return value.tz_convert('Asia/Taipei')

def sessions_completed(calendar,sessions,as_of):
    return all((effective_calendar(calendar,session)[session+'_close']<as_of).all() for session in sessions)


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
    for s in cfg['samples']:
        if s.get('included_in_formal_run',True):add(s)
    for year in cfg['repeatability_years']:
        s=next(s for s in cfg['samples'] if s['year']==year and s['window_type']=='ordinary_early')
        if s.get('included_in_formal_run',True):add(s,'repeat','1!')
    for session in cfg['sessions']:
        dates=cfg['availability_dates'] if session=='regular' else cfg['availability_afterhours_dates']
        rows.append(dict(name=f'2017_availability_boundary_{session}_1bang',year=2017,window_type='availability_boundary',sample_id='2017_availability_boundary',session=session,contract_query='1!',role='availability',dates=dates,window_from=dates[0],window_to=dates[-1]))
    return rows


class FrozenSampleMatrix:
    def __init__(self,cfg,now=None):
        self.cfg=copy.deepcopy(cfg)
        self.as_of=as_of_timestamp(now)
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
        self.samples=[]
        for sample in self.cfg['samples']:
            cal=self.calendar[self.calendar.sample_id.eq(sample['sample_id'])]
            eligible=sessions_completed(cal,cfg['sessions'],self.as_of)
            self.samples.append(dict(sample,planned_trade_dates=list(sample['trade_dates']),
                eligibility='ELIGIBLE' if eligible else 'NOT_YET_ELIGIBLE',
                included_in_formal_run=eligible,exclusion_reason='' if eligible else 'FUTURE_WINDOW'))
        self.plan=build_query_plan(dict(self.cfg,samples=self.samples))
        self.metadata=dict(as_of_timestamp=self.as_of.isoformat(),sampling_policy='AS_OF_COMPLETED_WINDOWS_V2',
            sampling_policy_revision_reason=POLICY_REVISION_REASON,api_request_count_before_revision=0,
            previous_sample_matrix_sha256=PREVIOUS_SAMPLE_MATRIX_SHA256,
            planned_windows=len(self.samples),eligible_windows=len(self.eligible_samples),future_windows=len(self.samples)-len(self.eligible_samples),
            planned_total_trade_dates=sum(len(s['trade_dates']) for s in self.samples),
            eligible_trade_dates=sum(len(s['trade_dates']) for s in self.eligible_samples),
            future_trade_dates=sum(len(s['trade_dates']) for s in self.samples if not s['included_in_formal_run']),
            scope_language=SCOPE_LANGUAGE,
            excluded_windows_note='; '.join(f"{s['year']} {s['window_type']} not yet eligible as of run date. NOT TESTED — FUTURE WINDOW" for s in self.samples if not s['included_in_formal_run']))
        correction=cfg.get('validator_correction_provenance',{})
        if correction:
            self.metadata['eligibility_policy_previous_sample_matrix_sha256']=PREVIOUS_SAMPLE_MATRIX_SHA256
            self.metadata.update(correction)
        self.sample_matrix_sha256=hashlib.sha256(json.dumps(dict(cfg=self.cfg,calendar_sha256=self.file_hash,plan=self.plan,samples=self.samples,metadata=self.metadata),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        self.config_hash=hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()
    @property
    def eligible_samples(self):
        return [s for s in self.samples if s['included_in_formal_run']]
    @property
    def included_calendar(self):
        ids=[s['sample_id'] for s in self.eligible_samples]+['2017_availability_boundary']
        return self.calendar[self.calendar.sample_id.isin(ids)]
    def verify(self,cfg):
        if hashlib.sha256(json.dumps(cfg,sort_keys=True).encode()).hexdigest()!=self.config_hash or hashlib.sha256(calendar_path(cfg).read_bytes()).hexdigest()!=self.file_hash:
            raise HistoricalFatalError('Sample matrix mutated; stop entire formal run, do not repair live')
    def require_completed(self,now=None):
        now=self.as_of if now is None else as_of_timestamp(now)
        if not self.eligible_samples or not sessions_completed(self.included_calendar,self.cfg['sessions'],now):
            raise HistoricalFatalError('Included matrix contains future/incomplete sessions; market requests forbidden')
    def write(self,path):
        rows=[dict(s,trade_dates=json.dumps(s['trade_dates']),planned_trade_dates=json.dumps(s['planned_trade_dates'])) for s in self.samples]
        pd.DataFrame(rows).to_csv(path/'diagnostics/predeclared_sample_matrix.csv',index=False)
        self.calendar.to_csv(path/'diagnostics/final_calendar_snapshot.csv',index=False)
        pd.DataFrame(self.plan).to_csv(path/'diagnostics/predeclared_query_plan.csv',index=False)
        (path/'diagnostics/sample_eligibility_metadata.json').write_text(json.dumps(dict(self.metadata,sample_matrix_sha256=self.sample_matrix_sha256),ensure_ascii=False,indent=2))
