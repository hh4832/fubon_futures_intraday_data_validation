"""Opt-in first-sample regular smoke after successful offline archive replay."""
from time import perf_counter
import traceback
from .archive import start_run, write_info, finish_run
from .diagnostics import write_table
from .fatal_diagnostics import redact_sensitive_text
from .historical_client import HistoricalFatalError, HistoricalReader, read_canonical_documentation, session_calendar
from .historical_normalization import normalize_historical
from .historical_validation import validate_historical_schema, validate_historical_ohlc
from .rollover_robustness import load_robustness_config, validate_scope, classification
from .rollover_robustness_analysis import analyze_session


def run_first_regular_smoke(rest,root,sdk_info,auth_info,replay_info,cfg=None):
    """Exactly three unchanged queries. This archive is never a 36-request formal run."""
    cfg=cfg or load_robustness_config()
    validate_scope(cfg)
    sample=cfg['rollover_samples'][0]
    if not (replay_info.get('replay_status')=='PASS' and replay_info.get('analysis_classification') in {'PASS','PASS WITH CAVEATS'}
            and replay_info.get('sample_id')==sample['sample_id'] and replay_info.get('session')=='regular' and replay_info.get('api_request_count')==0):
        raise HistoricalFatalError('First-sample regular smoke requires successful offline replay first')
    doc,doc_info=read_canonical_documentation(cfg)
    calendar=session_calendar(cfg);cal=calendar[calendar.sample_id.eq(sample['sample_id'])].copy()
    if cal.trade_date.tolist()!=sample['trade_dates']:
        raise HistoricalFatalError('Fixed sample calendar mismatch')
    path,info=start_run(root,cfg,sdk_info,auth_info)
    info.update(doc_info,validation_phase='rollover_robustness_smoke',validation_stage='first_sample_regular',
        sample_count=1,sample_id=sample['sample_id'],sessions_tested=['regular'],timeframe=1,
        continuous_contract='1!',expected_logical_request_count=3,logical_request_count=0,external_validation='NOT PERFORMED')
    write_info(path,info)
    (path/'diagnostics'/'official_historical_documentation.txt').write_bytes(doc)
    calendar.to_csv(path/'diagnostics'/'rollover_calendar_snapshot.csv',index=False)
    write_table(path,'rollover_sample_metadata',cfg['rollover_samples'])
    reader=None;checks=[];start=perf_counter()
    try:
        reader=HistoricalReader(rest,path,cfg);frames={};items=[]
        for query in ['1!',sample['old_contract'],sample['new_contract']]:
            info['logical_request_count']+=1
            name=f"{sample['sample_id']}_regular_{'1bang' if query=='1!' else query}"
            item=reader.fetch(name,query,[sample['window_from'],sample['window_to']],'regular',1)
            item['requested_dates']=sample['trade_dates'];items.append(item)
        for item in items:
            if item['response'] is None:
                raise HistoricalFatalError('Smoke request failed; do not proceed to formal run')
            frame=normalize_historical(item,cal);frame['sample_id']=sample['sample_id']
            frame.to_parquet(path/'data'/f"{item['name']}.parquet",index=False)
            schema=validate_historical_schema(item,frame);ohlc,invalid=validate_historical_ohlc(frame)
            invalid.to_parquet(path/'diagnostics'/f"{item['name']}_invalid_ohlc_rows.parquet",index=False)
            scoped=frame[['trade_date','timestamp']].merge(cal[['trade_date','regular_open','regular_close']],on='trade_date',how='left',validate='many_to_one')
            outside=scoped.regular_open.isna() | scoped.timestamp.lt(scoped.regular_open) | scoped.timestamp.gt(scoped.regular_close)
            conflict='provider_trade_date_matches_mapping' in frame and not frame.provider_trade_date_matches_mapping.fillna(False).all()
            quality='FAIL' if schema['schema_status']=='FAIL' or ohlc['status']=='FAIL' else 'UNRESOLVED' if outside.any() or conflict or frame.empty and item['params']['contractMonth']!=sample['old_contract'] else 'PASS'
            checks.append(dict(contract_query=item['params']['contractMonth'],schema_status=schema['schema_status'],ohlc_status=ohlc['status'],row_count=len(frame),status=quality))
            frames[item['params']['contractMonth']]=frame
        tables,quality=analyze_session(frames,dict(cfg,**sample),cal,'regular')
        quality=classification([quality,*[c['status'] for c in checks]])
        for name,table in tables.items():write_table(path,'smoke_'+name,table.to_dict('records'),list(table.columns))
        info.update(smoke_status=quality,analysis_elapsed_seconds=perf_counter()-start)
        finish_run(reader,info)
        return path,info
    except Exception as exc:
        (path/'diagnostics'/'fatal_traceback.txt').write_text(redact_sensitive_text(traceback.format_exc()),encoding='utf-8')
        info.update(execution_status='STOPPED',smoke_status='UNRESOLVED',fatal_error_type=type(exc).__name__,fatal_error_message=redact_sensitive_text(str(exc)))
        write_info(path,info)
        if reader is not None:finish_run(reader,info,status='STOPPED')
        raise HistoricalFatalError(f'First-sample regular smoke stopped; safe diagnostics in {path}') from exc
    finally:
        write_table(path,'smoke_input_validation',checks,['contract_query','schema_status','ohlc_status','row_count','status'])
