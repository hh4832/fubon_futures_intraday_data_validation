"""Offline validator replay only. Never rewrite source archives or call market APIs."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import pandas as pd
from src.archive import git_info
from src.config import ROOT
from src.historical_client import HistoricalFatalError,session_calendar
from src.historical_validation import status_rollup
from src.final_integrity_analysis import internal_aggregation
from src.final_external import parse_official_daily,external_spot_check
from src.final_integrity_report import OUTPUT_NAMES,yearly_summary,global_summary,final_report


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def replay_archive(archive,output):
    archive=Path(archive).resolve();output=Path(output).resolve()
    if output==archive or archive in output.parents or output in archive.parents:
        raise ValueError('Replay output must be separate from immutable source archive')
    cfg=json.loads((archive/'config_snapshot.json').read_text())
    source_info={}
    for line in (archive/'run_info.txt').read_text().splitlines():
        if '=' in line:
            key,value=line.split('=',1);source_info[key]=json.loads(value)
    if source_info.get('execution_status')!='COMPLETED' or source_info.get('logical_request_count')!=source_info.get('expected_logical_request_count'):
        raise HistoricalFatalError('Source formal run must be completed; replay does not complete old requests')
    metadata=json.loads((archive/'diagnostics/sample_eligibility_metadata.json').read_text())
    calendar_path=archive/'diagnostics/final_calendar_snapshot.csv'
    calendar=session_calendar(dict(cfg,calendar_file=str(calendar_path)))
    samples=pd.read_csv(archive/'diagnostics/predeclared_sample_matrix.csv',dtype={'sample_id':str})
    for sample in cfg['samples']:
        row=samples[samples.sample_id.eq(sample['sample_id'])]
        if len(row)!=1 or json.loads(row.iloc[0].planned_trade_dates)!=sample['trade_dates']:
            raise HistoricalFatalError('Archived planned matrix/config mismatch; no substitution')
        cal=calendar[calendar.sample_id.eq(sample['sample_id'])]
        if cal.trade_date.tolist()!=sample['trade_dates']:
            raise HistoricalFatalError('Archived calendar/config dates mismatch; no substitution')
    tables={};files=[archive/'config_snapshot.json',archive/'run_info.txt',calendar_path,archive/'diagnostics/sample_eligibility_metadata.json',archive/'diagnostics/predeclared_sample_matrix.csv']
    for key,name in OUTPUT_NAMES.items():
        path=archive/'diagnostics'/f'{name}.csv'
        if path.exists():tables[key]=pd.read_csv(path);files.append(path)
        else:tables[key]=pd.DataFrame()
    selected=[s for s in cfg['samples'] if s['window_type']=='ordinary_early' and bool(samples.loc[samples.sample_id.eq(s['sample_id']),'included_in_formal_run'].iloc[0])]
    for sample in selected:
        date=sample['trade_dates'][0]
        files += [archive/'raw'/f'taifex_{date}.csv',archive/'diagnostics'/f'taifex_{date}_source.json']
        files += [archive/'data'/f"{sample['year']}_ordinary_early_{session}_1bang.parquet" for session in cfg['sessions']]
    hashes={str(p.relative_to(archive)):sha(p) for p in files}
    # Snapshot datetime serialization differs from the original CSV; retain its own hash.
    output.mkdir(parents=True,exist_ok=False);(output/'diagnostics').mkdir();(output/'data').mkdir()
    aggregation=[];external=[];selection=[]
    for sample in selected:
        date=sample['trade_dates'][0];raw_path=archive/'raw'/f'taifex_{date}.csv'
        source=json.loads((archive/'diagnostics'/f'taifex_{date}_source.json').read_text())
        if source.get('sha256')!=sha(raw_path):raise HistoricalFatalError('Archived official raw hash mismatch')
        official,parse_info=parse_official_daily(raw_path.read_bytes())
        source.update(parse_info,status='PASS')
        for session in cfg['sessions']:
            name=f"{sample['year']}_ordinary_early_{session}_1bang"
            frame=pd.read_parquet(archive/'data'/f'{name}.parquet')
            cal=calendar[calendar.sample_id.eq(sample['sample_id'])]
            tags=dict(year=sample['year'],window_type=sample['window_type'],sample_id=sample['sample_id'],session=session,role='primary')
            rows,outputs=internal_aggregation(frame,cal,session,tags);aggregation.append(rows)
            for timeframe,data in outputs:data.to_parquet(output/'data'/f"{sample['year']}_internal_{session}_{timeframe}m.parquet",index=False)
            comparison=external_spot_check(frame,date,sample['year'],session,official,source)
            external.append(comparison);selection.append(comparison.attrs['selection_diagnostics'])
    tables.update(aggregation=pd.concat(aggregation,ignore_index=True),external=pd.concat(external,ignore_index=True),external_selection=pd.DataFrame(selection))
    annotated=[dict(s,included_in_formal_run=bool(samples.loc[samples.sample_id.eq(s['sample_id']),'included_in_formal_run'].iloc[0])) for s in cfg['samples']]
    yearly=yearly_summary(tables,dict(cfg,samples=annotated),request_complete=True)
    acceptance=tables['aggregation'].status.isin(['PASS','PASS WITH CAVEATS']).all() and tables['external_selection'].selected_row_count.eq(1).all()
    comparable=tables['external'][tables['external'].definition_compatible]
    acceptance=bool(acceptance and len(comparable)==80 and comparable.status.eq('PASS').all())
    known=tables['external'][tables['external'].year.eq(2018) & tables['external'].session.eq('regular') & tables['external'].definition_compatible]
    known_pass=bool(len(known)==4 and known.status.eq('PASS').all() and known.loc[known.field.eq('high'),'taifex_value'].iloc[0]==10778 and known.loc[known.field.eq('low'),'taifex_value'].iloc[0]==10600)
    acceptance=acceptance and known_pass
    if any(sha(archive/name)!=value for name,value in hashes.items()):raise HistoricalFatalError('Source changed during replay')
    patch=subprocess.check_output(['git','diff','HEAD'],cwd=ROOT)
    code_files=['src/final_integrity_analysis.py','src/final_external.py','src/final_integrity_report.py','scripts/replay_final_validator_corrections.py']
    info=dict(metadata,validation_phase='final_validator_offline_replay',source_archive=str(archive),source_formal_run_id=archive.name,
        source_git_commit=source_info['git_commit'],source_sample_matrix_sha256=source_info['sample_matrix_sha256'],
        source_api_request_count=source_info['api_request_count'],api_request_count=0,replay_status='PASS' if acceptance else 'UNRESOLVED',
        known_2018_external_case='PASS' if known_pass else 'UNRESOLVED',input_sha256=hashes,replay_code=git_info(ROOT),code_diff_sha256=hashlib.sha256(patch).hexdigest(),replay_code_file_sha256={name:sha(ROOT/name) for name in code_files},
        recomputed_validator_classification=status_rollup(yearly.classification.tolist()),formal_rerun_required=True,canonical_promotion_allowed=False,
        notes='Old 2018 calendar retained for report replay. Corrected June 13 inputs absent; new commit/matrix/full formal rerun required. This is not a formal PASS.')
    report_info=dict(source_info,api_request_count=0,external_validation='OFFLINE REPLAY OF SAVED OFFICIAL RAW',git_commit=info['replay_code']['git_commit'],git_branch=info['replay_code']['git_branch'])
    summary,_=final_report(tables,yearly,report_info,'UNRESOLVED')
    summary='# Offline validator correction replay — NOT a formal run\n\n'+info['notes']+'\n\n'+summary
    for key in ['aggregation','external','external_selection']:
        tables[key].to_csv(output/'diagnostics'/f'{OUTPUT_NAMES[key]}.csv',index=False)
    yearly.to_csv(output/'diagnostics/cross_year_yearly_summary.csv',index=False)
    global_summary(tables,yearly,report_info).to_csv(output/'diagnostics/final_historical_integrity_summary.csv',index=False)
    (output/'validator_correction_replay_summary.md').write_text(summary,encoding='utf-8')
    (output/'replay_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    return output,info


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();_,info=replay_archive(args.archive,args.output)
    print(json.dumps({key:info[key] for key in ['replay_status','known_2018_external_case','api_request_count','formal_rerun_required','canonical_promotion_allowed']},indent=2))
