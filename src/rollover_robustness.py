"""Independent six-window regular/night validation runner; historical 1m only."""
from pathlib import Path
from time import perf_counter
import traceback
import pandas as pd
import yaml
from tqdm.auto import tqdm
from .archive import start_run, write_info, finish_run
from .config import ROOT
from .fatal_diagnostics import redact_sensitive_text
from .diagnostics import write_table
from .historical_client import (HistoricalFatalError, HistoricalReader, load_historical_config,
    read_canonical_documentation, session_calendar)
from .historical_normalization import normalize_historical
from .historical_validation import validate_historical_schema, validate_historical_ohlc
from .rollover_robustness_analysis import analyze_session, compare_sessions, summarize_sample

FIXED_SAMPLES = [
    ('202606','202607','2026-06-17','2026-06-11','2026-06-24'),
    ('202608','202609','2026-08-19','2026-08-13','2026-08-25'),
    ('202609','202610','2026-09-16','2026-09-10','2026-09-22'),
    ('202512','202601','2025-12-17','2025-12-11','2025-12-23'),
    ('202406','202407','2024-06-19','2024-06-13','2024-06-25'),
    ('202006','202007','2020-06-17','2020-06-11','2020-06-23'),
]
SCOPE = dict(product='TXF',sessions=['regular','afterhours'],timeframe=1,sort='asc',
    fields='open,high,low,close,volume,average',continuous_contract='1!')
OUTPUTS = {
    'rollover_regular_contract_coverage':('coverage','regular'),
    'rollover_regular_mapping':('mapping','regular'),
    'rollover_regular_transition_summary':('transition','regular'),
    'rollover_afterhours_mapping':('mapping','afterhours'),
    'rollover_afterhours_contract_coverage':('coverage','afterhours'),
    'rollover_afterhours_identity_transition':('transition','afterhours'),
    'rollover_session_map':('sessions',None),
    'rollover_robustness_field_comparison':('fields',None),
    'rollover_robustness_adjustment_test':('adjustment',None),
    'rollover_volume_rule_robustness':('volume',None),
    'rollover_session_anomalies':('anomalies',None),
    'rollover_missing_minutes_summary':('missing',None),
}


def validate_scope(cfg):
    if any(cfg.get(k)!=v for k,v in SCOPE.items()):
        raise HistoricalFatalError('Fixed robustness product/session/timeframe/fields scope violated')
    keys=['old_contract','new_contract','expiry_date','window_from','window_to']
    if [tuple(s[k] for k in keys) for s in cfg['rollover_samples']] != FIXED_SAMPLES:
        raise HistoricalFatalError('Six predeclared rollover samples must not be changed after observing results')
    if len({s['sample_id'] for s in cfg['rollover_samples']}) != 6:
        raise HistoricalFatalError('Six unique sample ids required')
    if cfg['minimum_adjustment_rows']<2 or not 0<=cfg['adjustment_offset_atol']<=1e-8 or not 0<=cfg['adjustment_ratio_atol']<=1e-10:
        raise HistoricalFatalError('Predeclared adjustment diagnostics require multiple bars and bounded tolerances')
    if not 1<=cfg['max_attempts']<=3:
        raise HistoricalFatalError('At most three bounded attempts per request')
    for s in cfg['rollover_samples']:
        dates=s['trade_dates']
        fixed_dates=pd.date_range(s['window_from'],s['window_to'],freq='B').strftime('%Y-%m-%d').tolist()
        fixed_dates=[d for d in fixed_dates if d!='2026-06-19']
        if dates!=fixed_dates or s['sample_id']!=s['old_contract']+'_'+s['new_contract']:
            raise HistoricalFatalError('Fixed sample identity/trading dates must not be replaced')
        if dates!=sorted(set(dates)) or dates[0]!=s['window_from'] or dates[-1]!=s['window_to'] or s['expiry_date'] not in dates:
            raise HistoricalFatalError('Invalid fixed trading-date matrix')
        if sum(d<s['expiry_date'] for d in dates)<4 or sum(d>s['expiry_date'] for d in dates)<4:
            raise HistoricalFatalError('Require four trading days each side of official expiry')
        if not s['expiry_source'].startswith('https://www.taifex.com.tw/'):
            raise HistoricalFatalError('Official verified expiry metadata required')


def load_robustness_config(path=None):
    shared=load_historical_config()
    cfg={k:shared[k] for k in ['timezone','sdk_version','sdk_download_url','sdk_official_sha256',
        'repository','drive_root','max_attempts','backoff_seconds','request_spacing_seconds',
        'canonical_intraday_run','canonical_documentation_sha256']}
    with Path(path or ROOT/'config/rollover_robustness_validation.yaml').open() as f:
        cfg.update(yaml.safe_load(f))
    cfg['timeframes']=[1]
    validate_scope(cfg)
    return cfg


def classification(values):
    return 'FAIL' if 'FAIL' in values else 'UNRESOLVED' if 'UNRESOLVED' in values else 'PASS WITH CAVEATS'


def robustness_summary(tables,info,status,stopped=False):
    summary=tables.get('rollover_robustness_summary',pd.DataFrame())
    complete=len(summary)==6 and status in ['PASS','PASS WITH CAVEATS'] and not stopped
    decision='保留' if complete else '修改後再測'
    def display(name):
        table=tables.get(name)
        return table.to_string(index=False) if table is not None and len(table) else 'Unresolved: not available / not executed'
    def all_true(column):
        return bool(len(summary)==6 and summary[column].eq(True).all()) if column in summary else False
    sections=[
        ('A. Validation question','Inference','Six fixed TXF rollover windows, regular + afterhours; can actual monthly identity and raw construction be reconstructed?'),
        ('B. Samples','Documented fact',display('rollover_sample_metadata')),
        ('C. Regular rollover results','Observed fact',display('rollover_regular_transition_summary')),
        ('D. Afterhours rollover results','Observed fact',display('rollover_afterhours_transition_summary')),
        ('E. Session-level transition map','Observed fact',display('rollover_session_map')),
        ('F. 1! vs actual contract OHLCVA','Observed fact',display('rollover_robustness_field_comparison')),
        ('G. Adjustment evidence','Inference',display('rollover_robustness_adjustment_test')),
        ('H. Roll-gap behavior','Observed fact',display('rollover_gap_summary')+'\nContract transition gap, not market overnight return; never used in performance.'),
        ('I. Volume-rule evidence','Inference',display('rollover_volume_rule_robustness')+'\nOnly observed minute sums. A counterexample rejects simple observed same-session volume-max selection, not complex volume/OI rules.'),
        ('J. Expiry relation','Observed fact / Inference',display('rollover_expiry_relation')),
        ('K. Missing-minute interpretation','Observed fact / Unresolved',display('rollover_missing_minutes_summary')+'\nScheduled slots are references, not confirmed lost bars. Inactive gaps never grade 1!. Asymmetric active gaps block. Shared no-trade suppression versus provider omission remains unconfirmed.'),
        ('L. Special-session anomalies','Observed fact',display('rollover_session_anomalies')),
        ('M. Cross-sample consistency','Observed fact',display('rollover_robustness_summary')),
        ('N. What is proven','Inference','Operationally traceable raw construction in all sampled windows.' if complete else 'No aggregate construction promotion; inspect observed tables and blockers.'),
        ('O. What remains unresolved','Unresolved','Private trigger name; untested months; provider changes outside samples; no-trade suppression vs omission; external validation NOT PERFORMED.'),
        ('P. Opponent view','Inference','1. Six rollovers do not cover every historical month.\n2. Current metadata convention may differ in other years.\n3. Expiry alignment is not proof of a universal trigger.\n4. Simple observed volume leader counterexamples do not exclude complex volume/OI rules.\n5. Same-provider exact matches are not independent external validation.\n6. Shared reference gaps may conceal provider omissions.\n7. Raw roll gaps cannot be treated as market returns or strategy profits.'),
        ('Q. Final classification','Inference' if complete else 'Unresolved',status),
        ('R. Research decision','Inference',decision+'; next stage Final Cross-Year Historical Integrity Validation is eligible for a separate task.' if complete else decision+'; do not advance while sampled blockers remain.'),
    ]
    text=['# Rollover Robustness + Night Session Validation',f"Git commit: {info['git_commit']}",f"Branch: {info['git_branch']}",
        'External validation: NOT PERFORMED','Scope: 36 sequential logical historical.candles requests; regular/afterhours 1m only.']
    for heading,evidence,value in sections:text.extend(['','## '+heading,f'[{evidence}] {value}'])
    rates={c:summary[c].tolist() if c in summary else 'UNRESOLVED' for c in ['ohlc_exact_match_rate','volume_exact_match_rate','average_exact_match_rate']}
    volume_count=int(summary.volume_rule_counterexamples.sum()) if 'volume_rule_counterexamples' in summary else 0
    expiry_table=tables.get('rollover_expiry_relation',pd.DataFrame())
    expiry_counts={c:int(expiry_table[c].eq(True).sum()) for c in ['regular_switch_at_next_regular','afterhours_switch_at_expiry_evening'] if c in expiry_table}
    questions=[
        ('1. 多個 rollover 的 per-bar actual month 是否可重建？',str(all_true('per_bar_identity_reconstructable'))+'; see mapping and schema evidence'),
        ('2. 所有 regular 是否 full-session switch？',str(all_true('regular_full_session_switch'))),
        ('3. afterhours 何時切換？',display('rollover_afterhours_transition_summary')),
        ('4. 是否發生 intraday partial switch？',str(summary.partial_switch_detected.tolist()) if 'partial_switch_detected' in summary else 'UNRESOLVED'),
        ('5. regular／afterhours boundary 是否一致？',str(all_true('regular_afterhours_consistent'))),
        ('6. OHLC 是否持续等於 actual contract？',str(rates['ohlc_exact_match_rate'])),
        ('7. volume 是否一致？',str(rates['volume_exact_match_rate'])),
        ('8. average 是否一致？',str(rates['average_exact_match_rate'])),
        ('9. 有無 back-adjustment evidence？',display('rollover_robustness_adjustment_test')),
        ('10. raw roll gap 是否保留？',str(all_true('roll_gap_retained'))),
        ('11. simple volume-leader rule 是否可排除？',f'可排除 simple observed same-session volume-max：{volume_count} 個反例；不能排除複雜 volume/OI 規則。' if volume_count else 'Unresolved: 未觀察到反例，不能因此證明 volume rule。'),
        ('12. expiry-boundary 是否跨 samples 穩定？',str(expiry_counts)+' / 6 samples; observed alignment is not universal proof.\n'+display('rollover_expiry_relation')),
        ('13. construction 是否 operationally traceable？',str(complete)),
        ('14. 可否進 final cross-year integrity validation？','可以，僅授權另行驗證；本次不執行。' if complete else '不可；sampled blockers 尚未排除。'),
        ('15. 還有 rollover blocker？','無 sampled construction blocker；未測月份仍未驗證。' if complete else '有；FAIL/UNRESOLVED、未完成樣本或停止執行，見 diagnostics。'),
    ]
    text.extend(['','## Final questions'])
    for q,a in questions:text.extend(['',q,a])
    if stopped:text.extend(['','[Unresolved] Execution stopped. Raw attempts retained; incomplete outputs are not PASS.'])
    return '\n'.join(text),decision


def assemble(groups,boundaries,expiries,summaries,cfg):
    tables={}
    for name,(key,session) in OUTPUTS.items():
        values=[group[key] for group in groups if session is None or group[key].session.eq(session).all()]
        tables[name]=pd.concat(values,ignore_index=True) if values else pd.DataFrame(columns=['sample_id','session','status','notes'])
    tables['rollover_afterhours_transition_summary']=pd.DataFrame(boundaries,columns=list(boundaries[0]) if boundaries else ['sample_id','status','notes'])
    tables['rollover_expiry_relation']=pd.DataFrame(expiries,columns=list(expiries[0]) if expiries else ['sample_id','status','notes'])
    tables['rollover_robustness_summary']=pd.DataFrame(summaries,columns=list(summaries[0]) if summaries else ['sample_id','classification'])
    gaps=[]
    for group in groups:
        row=group['transition'].iloc[0]
        gaps.append(dict(sample_id=row.sample_id,transition_session=row.session,
            old_last_timestamp=row.last_timestamp_old_contract_in_1bang,old_last_close=row.old_last_close,
            new_first_timestamp=row.first_timestamp_new_contract_in_1bang,new_first_open=row.new_first_open,
            roll_gap_points=row.roll_gap_points,roll_gap_return=row.roll_gap_return,status=row.status,
            notes='contract transition gap only; not market overnight return; no strategy calculation'))
    tables['rollover_gap_summary']=pd.DataFrame(gaps,columns=list(gaps[0]) if gaps else ['sample_id','transition_session','status','notes'])
    tables['rollover_sample_metadata']=pd.DataFrame(cfg['rollover_samples'])
    return tables


def run_rollover_robustness(rest,root,sdk_info,auth_info,cfg=None):
    cfg=cfg or load_robustness_config()
    validate_scope(cfg)
    doc,doc_info=read_canonical_documentation(cfg)
    calendar=session_calendar(cfg)
    for sample in cfg['rollover_samples']:
        scoped=calendar[calendar.sample_id.eq(sample['sample_id'])]
        if scoped.trade_date.tolist()!=sample['trade_dates'] or not scoped.session_source.str.startswith('https://www.taifex.com.tw/').all():
            raise HistoricalFatalError('Reviewed calendar must exactly cover the fixed sample dates with official night sources')
    path,info=start_run(root,cfg,sdk_info,auth_info)
    info.update(doc_info,validation_phase='rollover_robustness',validation_stage='multi_rollover_regular_afterhours',
        sample_count=6,timeframe=1,continuous_contract='1!',sdk_version=sdk_info['version'],branch=info['git_branch'],logical_request_count=0,expected_logical_request_count=36,
        external_validation='NOT PERFORMED')
    write_info(path,info)
    (path/'diagnostics'/'official_historical_documentation.txt').write_bytes(doc)
    calendar.to_csv(path/'diagnostics'/'rollover_calendar_snapshot.csv',index=False)
    groups=[];boundaries=[];expiries=[];summaries=[];checks=[];metrics=[];reader=None
    def archive(status,stopped=False):
        tables=assemble(groups,boundaries,expiries,summaries,cfg)
        for name,table in tables.items():write_table(path,name,table.to_dict('records'),list(table.columns))
        write_table(path,'rollover_input_validation',checks,['sample_id','session','contract_query','schema_status','ohlc_status','status','notes'])
        write_table(path,'rollover_performance',metrics,['sample_id','session','row_count','elapsed_seconds'])
        summary,decision=robustness_summary(tables,info,status,stopped)
        for ext in ['md','txt']:(path/f'validation_summary.{ext}').write_text(summary,encoding='utf-8')
        info.update(rollover_robustness_status=status,research_decision=decision)
        write_info(path,info)
        if reader is not None:finish_run(reader,info,status='STOPPED' if stopped else 'COMPLETED')
        return summary
    try:
        reader=HistoricalReader(rest,path,cfg)
        for sample in tqdm(cfg['rollover_samples'],desc='rollover samples'):
            local=dict(cfg,**sample)
            cal=calendar[calendar.sample_id.eq(sample['sample_id'])].copy()
            results={};qualities=[]
            for session in cfg['sessions']:
                frames={}; input_quality=[];start=perf_counter();items=[]
                for query in ['1!',sample['old_contract'],sample['new_contract']]:
                    name=f"{sample['sample_id']}_{session}_{'1bang' if query=='1!' else query}"
                    info['logical_request_count']+=1
                    item=reader.fetch(name,query,[sample['window_from'],sample['window_to']],session,1)
                    item['requested_dates']=sample['trade_dates'];items.append(item)
                for item in items:
                    query=item['params']['contractMonth']
                    failed=item['response'] is None
                    source=dict(item,response={'data':[]}) if failed else item
                    frame=normalize_historical(source,cal)
                    frame['sample_id']=sample['sample_id']
                    frames[query]=frame
                    frame.to_parquet(path/'data'/f"{item['name']}.parquet",index=False)
                    schema=validate_historical_schema(source,frame)
                    ohlc,invalid=validate_historical_ohlc(frame)
                    invalid.to_parquet(path/'diagnostics'/f"{item['name']}_invalid_ohlc_rows.parquet",index=False)
                    scoped=frame[['trade_date','timestamp']].merge(cal[['trade_date',session+'_open',session+'_close']],on='trade_date',how='left',validate='many_to_one')
                    outside=scoped[session+'_open'].isna() | scoped.timestamp.lt(scoped[session+'_open']) | scoped.timestamp.gt(scoped[session+'_close'])
                    if session=='afterhours':outside|=scoped.timestamp.eq(scoped[session+'_close'])
                    provider_conflict='provider_trade_date_matches_mapping' in frame and not frame.provider_trade_date_matches_mapping.fillna(False).all()
                    q='UNRESOLVED' if failed else 'FAIL' if schema['schema_status']=='FAIL' or ohlc['status']=='FAIL' else 'UNRESOLVED' if outside.any() or provider_conflict or frame.empty and query!=sample['old_contract'] else 'PASS'
                    input_quality.append(q)
                    checks.append(dict(sample_id=sample['sample_id'],session=session,contract_query=query,
                        schema_status=schema['schema_status'],ohlc_status=ohlc['status'],status=q,row_count=len(frame),
                        outside_session_rows=int(outside.sum()),provider_trade_date_conflict=bool(provider_conflict),
                        notes='No substitution, repair or window change; raw and all source rows retained'))
                tables,quality=analyze_session(frames,local,cal,session)
                quality=classification([quality,*input_quality])
                tables['transition']['status']=quality
                results[session]=tables;groups.append(tables);qualities.append(quality)
                metrics.append(dict(sample_id=sample['sample_id'],session=session,row_count=sum(len(f) for f in frames.values()),elapsed_seconds=perf_counter()-start))
            boundary,expiry=compare_sessions(results,local)
            boundaries.append(boundary);expiries.append(expiry)
            quality=classification([*qualities,boundary['status']])
            summaries.append(summarize_sample(results,local,quality,boundary))
        status=classification([s['classification'] for s in summaries])
        summary=archive(status)
        return path,summary
    except Exception as exc:
        trace=redact_sensitive_text(traceback.format_exc())
        (path/'diagnostics'/'fatal_traceback.txt').write_text(trace,encoding='utf-8')
        info.update(execution_status='STOPPED',fatal_error_type=type(exc).__name__,
            fatal_error_message=redact_sensitive_text(str(exc)))
        archive('UNRESOLVED',stopped=True)
        raise HistoricalFatalError(f'Robustness run stopped ({type(exc).__name__}); safe diagnostics in {path}') from exc
