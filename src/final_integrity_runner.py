"""Final stratified historical-only runner. Every gate is evidence-driven."""
from pathlib import Path
import json
import subprocess
import traceback
import pandas as pd
from tqdm.auto import tqdm
from .archive import start_run,write_info,finish_run
from .config import ROOT
from .diagnostics import write_table
from .fatal_diagnostics import redact_sensitive_text
from .historical_client import HistoricalReader,HistoricalFatalError,read_canonical_documentation
from .historical_normalization import normalize_historical
from .historical_validation import status_rollup
from .rollover_robustness_analysis import analyze_session
from .final_sample_matrix import load_final_config,FrozenSampleMatrix,effective_calendar
from .final_integrity_analysis import query_diagnostics,repeatability,internal_aggregation,availability,identity_checks
from .final_external import fetch_official_daily,external_spot_check
from .final_integrity_report import OUTPUT_NAMES,yearly_summary,global_summary,final_report


def run_final_integrity(rest,root,sdk_info,auth_info,cfg=None,now=None,external_fetcher=None):
    cfg=cfg or load_final_config();root=Path(root)
    matrix=FrozenSampleMatrix(cfg,now=now)
    subprocess.run(['git','merge-base','--is-ancestor',cfg['baseline_commit'],'HEAD'],cwd=root,check=True,capture_output=True)
    matrix.require_completed() # Only included windows; excluded future dates remain recorded.
    doc,doc_info=read_canonical_documentation(cfg)
    path,info=start_run(root,cfg,sdk_info,auth_info)
    matrix.write(path)
    (path/'diagnostics/official_historical_documentation.txt').write_bytes(doc)
    info.update(doc_info,validation_phase='final_cross_year_historical_integrity',validation_stage='2017_2026_stratified_validation',
        sample_matrix_sha256=matrix.sample_matrix_sha256,years_tested=[],sample_trade_date_count=matrix.metadata['eligible_trade_dates'],availability_trade_date_count=5,
        expected_logical_request_count=len(matrix.plan),logical_request_count=0,retry_count=0,
        external_validation='NOT PERFORMED',final_integrity_status='UNRESOLVED',research_decision='修改後再測')
    info.update(matrix.metadata)
    write_info(path,info)
    groups={name:[] for name in OUTPUT_NAMES};frames={};items={};invalid=[];qualities=[];reader=None
    def collect(name,table):groups[name].append(table)
    def tags(entry):return {k:entry[k] for k in ['year','window_type','sample_id','session','role']}
    def sample_for(entry):
        if entry['role']=='availability':
            return dict(sample_id='2017_availability_boundary',year=2017,window_type='availability_boundary',old_contract='201706',new_contract=None,expiry_date='2017-06-21',trade_dates=cfg['availability_dates'])
        return next(s for s in cfg['samples'] if s['sample_id']==entry['sample_id'])
    def calendar_for(entry):return matrix.calendar[matrix.calendar.sample_id.eq(entry['sample_id'])].copy()
    def request(entry):
        matrix.verify(cfg)
        info['logical_request_count']+=1
        write_info(path,info)
        item=reader.fetch(entry['name'],entry['contract_query'],[entry['window_from'],entry['window_to']],entry['session'],1)
        item['requested_dates']=entry['dates'];item['role']=entry['role']
        return item
    def normalize_entry(entry,item):
        cal=calendar_for(entry)
        failed=item['response'] is None
        source=dict(item,response={'data':[]}) if failed else item
        f=normalize_historical(source,effective_calendar(cal,entry['session']))
        f['year']=entry['year'];f['window_type']=entry['window_type']
        f['source_run']=path.name;f['git_commit']=info['git_commit']
        previous=f.actual_contract_month.shift()
        f['rollover_flag']=previous.notna() & f.actual_contract_month.ne(previous).fillna(False)
        f.to_parquet(path/'data'/f"{entry['name']}.parquet",index=False)
        tables,quality,bad=query_diagnostics(dict(source,name=entry['name']),f,sample_for(entry),cal)
        for key,value in tables.items():collect(key,value)
        if len(bad):invalid.append(bad)
        if failed:quality='UNRESOLVED'
        qualities.append(quality);frames[entry['name']]=f
        return f
    def flush(stopped=False):
        tables={key:pd.concat(values,ignore_index=True) if values else pd.DataFrame() for key,values in groups.items()}
        complete=info['logical_request_count']==info['expected_logical_request_count'] and not stopped
        yearly=yearly_summary(tables,dict(cfg,samples=matrix.samples),complete)
        status=status_rollup([*yearly.classification.tolist(),*qualities])
        if not complete and status!='FAIL':status='UNRESOLVED'
        summary,decision=final_report(tables,yearly,info,status)
        for key,name in OUTPUT_NAMES.items():
            table=tables[key]
            write_table(path,name,table.to_dict('records'),list(table.columns) if len(table.columns) else ['status','notes'])
        write_table(path,'cross_year_yearly_summary',yearly.to_dict('records'),list(yearly.columns))
        global_table=global_summary(tables,yearly,info)
        write_table(path,'final_historical_integrity_summary',global_table.to_dict('records'))
        bad=pd.concat(invalid,ignore_index=True) if invalid else pd.DataFrame(columns=['timestamp','trade_date','session','open','high','low','close','volume','actual_contract_month','year','window_type','contract_query'])
        bad.to_parquet(path/'diagnostics/cross_year_invalid_ohlc_rows.parquet',index=False)
        for ext in ['md','txt']:(path/f'validation_summary.{ext}').write_text(summary,encoding='utf-8')
        info.update(final_integrity_status=status,research_decision=decision,
            years_tested=sorted(set(int(e['year']) for e in matrix.plan if e['name'] in frames and len(frames[e['name']]))))
        if reader is not None:
            info['retry_count']=max(0,reader.calls-info['logical_request_count'])
            finish_run(reader,info,status='STOPPED' if stopped else 'COMPLETED')
        else:write_info(path,info)
        return summary
    try:
        reader=HistoricalReader(rest,path,cfg)
        # Phase 3: primary+bounded boundary raw retrieval first, all sequential.
        primary=[e for e in matrix.plan if e['role']!='repeat']
        for entry in tqdm(primary,desc='final request groups'):items[entry['name']]=request(entry)
        # Phases 4/5: no data repair; basic diagnostics on every query.
        for entry in primary:normalize_entry(entry,items[entry['name']])
        # Phase 6: annual rollover confirmation; scheduled-closed nights exempt, not replaced.
        for sample in matrix.eligible_samples:
            if sample['window_type']!='june_rollover':continue
            cal=matrix.calendar[matrix.calendar.sample_id.eq(sample['sample_id'])].copy()
            for session in cfg['sessions']:
                entry=next(e for e in primary if e['sample_id']==sample['sample_id'] and e['session']==session and e['contract_query']=='1!')
                queries=['1!',sample['old_contract'],sample['new_contract']]
                reference={q:frames[next(e['name'] for e in primary if e['sample_id']==sample['sample_id'] and e['session']==session and e['contract_query']==q)] for q in queries}
                analysis_calendar=effective_calendar(cal,session)
                local=dict(cfg,**sample,continuous_contract='1!')
                local['trade_dates']=analysis_calendar.trade_date.tolist()
                for query,frame in reference.items():
                    invalid_identity=identity_checks(frame,sample,query)
                    if any(mask.any() for mask in invalid_identity):
                        raise HistoricalFatalError(f"Invalid contract identity blocks rollover analysis: {sample['sample_id']} / {session} / {query}; retained in diagnostics")
                tables,quality=analyze_session(reference,local,analysis_calendar,session)
                qualities.append(quality)
                for source,key in [('fields','rollover_fields'),('adjustment','adjustment')]:
                    table=tables[source].copy();table['year']=sample['year'];table['window_type']='june_rollover';collect(key,table)
                t=tables['transition'].iloc[0]
                collect('roll_gap',pd.DataFrame([dict(year=sample['year'],session=session,old_contract=sample['old_contract'],new_contract=sample['new_contract'],old_last_timestamp=t.last_timestamp_old_contract_in_1bang,new_first_timestamp=t.first_timestamp_new_contract_in_1bang,old_last_close=t.old_last_close,new_first_open=t.new_first_open,gap_points=t.roll_gap_points,gap_ratio=t.roll_gap_return,label='contract_transition_gap',status=quality,notes='not market_return; never used for strategy performance')]))
                missing=tables['missing'].copy();missing['year']=sample['year'];missing['window_type']='june_rollover';missing['role']='primary';missing['contract_query']=missing.contract_query.astype(str)
                missing['absence_type']='inactive-contract absence'
                active=missing.is_continuous_underlying.eq(True)
                missing.loc[active,'absence_type']='continuous/reference shared absence'
                missing.loc[active & missing.active_asymmetric_missing_rows.gt(0),'absence_type']='active asymmetric break'
                missing.loc[missing.absence_type.eq('active asymmetric break'),'missing_slot_count']=missing.loc[missing.absence_type.eq('active asymmetric break'),'active_asymmetric_missing_rows']
                collect('missing',missing)
                tables['mapping'].to_parquet(path/'diagnostics'/f"{sample['year']}_rollover_{session}_mapping.parquet",index=False)
        # Phase 7: only the six predeclared repeat queries.
        for entry in [e for e in matrix.plan if e['role']=='repeat']:
            item=request(entry);f=normalize_entry(entry,item)
            first=frames[entry['name'].removesuffix('_repeat')]
            result=repeatability(first,f,tags(entry));collect('repeatability',pd.DataFrame([result]));qualities.append(result['status'])
        # Phase 8: internal aggregation, no provider higher-timeframe requests.
        for sample in matrix.eligible_samples:
            if sample['window_type']!='ordinary_early':continue
            cal=matrix.calendar[matrix.calendar.sample_id.eq(sample['sample_id'])]
            for session in cfg['sessions']:
                entry=next(e for e in primary if e['sample_id']==sample['sample_id'] and e['session']==session)
                table,outputs=internal_aggregation(frames[entry['name']],cal,session,tags(entry))
                collect('aggregation',table);qualities.append(status_rollup(table.status.tolist()))
                for timeframe,data in outputs:data.to_parquet(path/'data'/f"{sample['year']}_internal_{session}_{timeframe}m.parquet",index=False)
        # Phase 9: independent official CSVs; absence never becomes a same-provider substitute.
        external_fetcher=external_fetcher or fetch_official_daily
        info['external_validation']='ATTEMPTED; inspect independent TAIFEX diagnostics';write_info(path,info)
        for sample in matrix.eligible_samples:
            if sample['window_type']!='ordinary_early':continue
            matrix.verify(cfg);date=sample['trade_dates'][0]
            official,metadata=external_fetcher(date,path)
            for session in cfg['sessions']:
                name=f"{sample['year']}_ordinary_early_{session}_1bang"
                comparison=external_spot_check(frames[name],date,sample['year'],session,official,metadata)
                collect('external',comparison)
        for session in cfg['sessions']:
            entry=next(e for e in primary if e['role']=='availability' and e['session']==session)
            collect('availability',pd.DataFrame([availability(frames[entry['name']],calendar_for(entry),session,cfg['documented_availability'])]))
        matrix.verify(cfg)
        summary=flush()
        return path,summary
    except Exception as exc:
        (path/'diagnostics/fatal_traceback.txt').write_text(redact_sensitive_text(traceback.format_exc()),encoding='utf-8')
        info.update(execution_status='STOPPED',fatal_error_type=type(exc).__name__,fatal_error_message=redact_sensitive_text(str(exc)))
        flush(stopped=True)
        raise HistoricalFatalError(f'Final integrity validation stopped ({type(exc).__name__}); safe archive in {path}') from exc
