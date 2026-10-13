"""Independent rollover-only formal entrypoint; no other historical phases executed."""
from pathlib import Path
from time import perf_counter
import pandas as pd
import yaml
from tqdm.auto import tqdm
from .archive import start_run, write_info, finish_run
from .config import ROOT
from .diagnostics import write_table
from .historical_client import (HistoricalFatalError, HistoricalReader, load_historical_config,
    read_canonical_documentation, session_calendar)
from .historical_normalization import normalize_historical
from .historical_validation import validate_historical_schema, validate_historical_ohlc
from .normalize import normalize
from .continuous_rollover_analysis import analyze_rollover

FIXED_SCOPE = {'product':'TXF','session':'regular','timeframe':1,'sort':'asc',
    'fields':'open,high,low,close,volume,average','continuous_contract':'1!',
    'old_contract':'202609','new_contract':'202610','window_from':'2026-09-10',
    'window_to':'2026-09-22','expiry_date':'2026-09-16'}


def load_rollover_config(path=None):
    shared=load_historical_config()
    shared_keys=['timezone','sdk_version','sdk_download_url','sdk_official_sha256','repository',
        'drive_root','max_attempts','backoff_seconds','request_spacing_seconds','calendar_file',
        'canonical_intraday_run','canonical_documentation_sha256']
    cfg={key:shared[key] for key in shared_keys}
    with Path(path or ROOT/'config/continuous_rollover_validation.yaml').open() as f:
        cfg.update(yaml.safe_load(f))
    if any(cfg.get(key)!=value for key,value in FIXED_SCOPE.items()):
        raise ValueError('Rollover scope is fixed; do not change dates/contracts/session/timeframe after observing results')
    if cfg['minimum_adjustment_rows']<2 or not 0<=cfg['adjustment_offset_atol']<=1e-8 or not 0<=cfg['adjustment_ratio_atol']<=1e-10:
        raise ValueError('Adjustment diagnostics require multiple bars and predeclared numerical tolerances')
    cfg['sessions']=['regular'];cfg['timeframes']=[1]
    return cfg


def empty_rollover_frame(cfg):
    frame=normalize({'data':[]},cfg['product'],'regular',1)
    frame['actual_contract_month']=pd.Series(dtype='string')
    frame['response_contractMonth']=pd.Series(dtype='string')
    return frame


def rollover_summary(tables,info,cfg,quality,fatal=False):
    transition=tables['rollover_transition_summary'].iloc[0].to_dict()
    fields=tables['rollover_field_comparison']
    anomalies=tables['rollover_anomalies']
    def matching(names):
        rows=fields[fields.field.isin(names)]
        return rows.to_string(index=False)
    rows=[('A. Validation question','Unresolved','Describe one observed TXF 1! rollover; do not presume expiry/volume/OI trigger.'),
        ('B. Data window','Observed fact',f"{cfg['window_from']}..{cfg['window_to']}; regular / 1m only"),
        ('C. Contracts queried','Observed fact','1!, 202609, 202610; same from/to/fields/sort'),
        ('D. Observed rollover transition','Observed fact' if transition['transition_confirmed_in_sample'] else 'Unresolved',
            f"old last={transition['last_timestamp_old_contract_in_1bang']}; new first={transition['first_timestamp_new_contract_in_1bang']}; type={transition['transition_type']}"),
        ('E. Per-bar contract identity','Observed fact','Per-bar metadata and coverage preserved; missing/third months not guessed.'),
        ('F. 1! vs actual contract OHLC match','Observed fact',matching(['open','high','low','close'])),
        ('G. Volume match','Observed fact',matching(['volume'])),('H. Average match','Observed fact',matching(['average'])),
        ('I. Roll gap','Observed fact',f"points={transition['roll_gap_points']}; ratio={transition['roll_gap_return']}; transition gap, not market return"),
        ('J. Back-adjustment evidence','Inference',transition['adjustment_test_result']+'\n'+tables['rollover_adjustment_test'].to_string(index=False)),
        ('K. Expiry-rule consistency','Inference' if transition['transition_confirmed_in_sample'] else 'Unresolved',
            f"expiry reference={cfg['expiry_date']}; before/on/after={transition['transition_occurs_before_expiry_date']}/{transition['transition_occurs_on_expiry_date']}/{transition['transition_occurs_after_expiry_date']}; not a unique rule proof"),
        ('L. Volume-rule consistency','Inference',transition['volume_rule_interpretation']),
        ('M. Major anomalies','Observed fact',str(anomalies.anomaly_type.value_counts().to_dict())),
        ('N. Opponent view','Inference','One rollover cannot establish a permanent rule; expiry alignment may be coincidence; one volume crossover cannot prove volume-based selection. Same-provider comparisons are not external validation. Provider methods may have changed during 2017–2026. Night-session rollover remains untested.'),
        ('O. Final classification','Observed fact' if quality=='FAIL' else 'Unresolved' if quality=='UNRESOLVED' else 'Inference',quality),
        ('P. Research decision','Unresolved','修改後再測; formal methodology and broader stability not established. Do not promote to a multi-year backtest source.')]
    text=['# Continuous Contract Rollover Validation','',f"Git commit: {info['git_commit']}",f"Branch: {info['git_branch']}",
          '[Documented fact] Canonical provider documentation is preserved separately. Documented rollover claims are not empirical rule proof.']
    for heading,evidence,value in rows:text+=['','## '+heading,f'[{evidence}] {value}']
    text+=['','## Next-step boundary',
        '[Inference] Raw continuous price-level analysis may be considered only for this sampled window if identity/adjustment comparisons pass. ',
        '[Unresolved] Direct multi-year return calculation: not approved. A contract transition gap is not a tradable market return.',
        '[Unresolved] Next step: review this rollover and methodology evidence first; cross-year validation is a separate later task and is not executed here.']
    if fatal:text+=['','[Unresolved] Formal run stopped; inspect safe error/provenance diagnostics.']
    return '\n'.join(text)


def run_rollover(rest,root,sdk_info,auth_info,cfg=None):
    cfg=cfg or load_rollover_config()
    # Revalidate config if a caller supplied overrides; no hidden scope expansion.
    if any(cfg.get(key)!=value for key,value in FIXED_SCOPE.items()):
        raise HistoricalFatalError('Fixed rollover scope violated')
    doc,doc_info=read_canonical_documentation(cfg)
    calendar=session_calendar(cfg)
    dates=calendar.loc[calendar.trade_date.between(cfg['window_from'],cfg['window_to']),'trade_date'].tolist()
    if not dates or dates[0]!=cfg['window_from'] or dates[-1]!=cfg['window_to']:
        raise HistoricalFatalError('Verified reference calendar must cover the complete fixed window')
    path,info=start_run(root,cfg,sdk_info,auth_info)
    info.update(doc_info,validation_phase='rollover',validation_stage='continuous_contract_rollover',
        product='TXF',session='regular',timeframe=1,continuous_contract='1!',
        old_contract=cfg['old_contract'],new_contract=cfg['new_contract'],
        window_from=cfg['window_from'],window_to=cfg['window_to'],expiry_date=cfg['expiry_date'])
    write_info(path,info)
    (path/'diagnostics'/'official_historical_documentation.txt').write_bytes(doc)
    calendar[calendar.trade_date.between(cfg['window_from'],cfg['window_to'])].to_csv(path/'diagnostics'/'rollover_calendar_snapshot.csv',index=False)
    reader=None;frames={c:empty_rollover_frame(cfg) for c in ['1!',cfg['old_contract'],cfg['new_contract']]}
    checks=[];metrics=[];request_failed=False
    try:
        reader=HistoricalReader(rest,path,cfg)
        items=[]
        # Exactly three independent logical requests with identical range, including the expired month.
        for query in tqdm(['1!',cfg['old_contract'],cfg['new_contract']],desc='rollover API requests'):
            name='rollover_1bang' if query=='1!' else 'rollover_'+query
            item=reader.fetch(name,query,[cfg['window_from'],cfg['window_to']],'regular',1)
            item['requested_dates']=dates
            items.append(item)
        for item in items:
            query=item['params']['contractMonth'];start=perf_counter()
            if item['response'] is None:
                request_failed=True
                checks.append({'query':query,'schema_status':'UNRESOLVED','ohlc_status':'UNRESOLVED','api_status':'FAIL','notes':'request failed; no substitute or narrower window'})
                frames[query].to_parquet(path/'data'/f"{item['name']}.parquet",index=False)
                continue
            frame=normalize_historical(item,calendar)
            frames[query]=frame
            frame.to_parquet(path/'data'/f"{item['name']}.parquet",index=False)
            schema=validate_historical_schema(item,frame)
            ohlc,invalid=validate_historical_ohlc(frame)
            invalid.to_parquet(path/'diagnostics'/f"{item['name']}_invalid_ohlc_rows.parquet",index=False)
            outside=~frame.trade_date.isin(dates)
            checks.append({'query':query,'schema_status':schema['schema_status'],'ohlc_status':ohlc['status'],
                'api_status':'PASS','row_count':len(frame),'outside_fixed_window_rows':int(outside.sum()),
                'missing_required_fields':schema['missing_bar_fields'],'nonfinite_core_values':ohlc['nonfinite_core_values'],
                'notes':'same request window for all contracts; absence after old expiry observed, not filled'})
            metrics.append({'stage':item['name'],'row_count':len(frame),'elapsed_seconds':perf_counter()-start})
        start=perf_counter()
        tables,quality=analyze_rollover(frames,cfg,calendar,dates)
        metrics.append({'stage':'rollover vectorized analysis','row_count':sum(len(d) for d in frames.values()),'elapsed_seconds':perf_counter()-start})
        if any(c['schema_status']=='FAIL' or c['ohlc_status']=='FAIL' or c.get('outside_fixed_window_rows',0)>0 for c in checks):quality='FAIL'
        elif request_failed:quality='UNRESOLVED'
        for name,table in tables.items():write_table(path,name,table.to_dict('records'),list(table.columns))
        write_table(path,'rollover_input_validation',checks)
        write_table(path,'rollover_performance',metrics)
        summary=rollover_summary(tables,info,cfg,quality)
        for ext in ['md','txt']:(path/f'validation_summary.{ext}').write_text(summary,encoding='utf-8')
        info.update(rollover_validation_status=quality,research_decision='修改後再測',external_validation='NOT PERFORMED')
        write_table(path,'rollover_api_requests',reader.attempts)
        finish_run(reader,info)
        return path,summary
    except Exception as exc:
        # Preserve failed raw evidence and safe summary; never write arbitrary SDK messages/secrets.
        info.update(execution_status='STOPPED',fatal_error_type=type(exc).__name__,
            rollover_validation_status='FAIL',research_decision='修改後再測')
        write_table(path,'rollover_input_validation',checks)
        write_table(path,'rollover_performance',metrics)
        tables,_=analyze_rollover(frames,cfg,calendar,dates)
        for name,table in tables.items():write_table(path,name,table.to_dict('records'),list(table.columns))
        summary=rollover_summary(tables,info,cfg,'FAIL',fatal=True)
        for ext in ['md','txt']:(path/f'validation_summary.{ext}').write_text(summary,encoding='utf-8')
        if reader is not None:
            write_table(path,'rollover_api_requests',reader.attempts)
            finish_run(reader,info,status='STOPPED')
        else:write_info(path,info)
        raise HistoricalFatalError(f'Rollover run stopped ({type(exc).__name__}); safe diagnostics in {path}') from None
