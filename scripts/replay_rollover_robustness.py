"""Offline single-sample/session replay from an immutable failed archive.

python -m scripts.replay_rollover_robustness --archive PATH --output NEW_PATH
No SDK imports, authentication or market API requests; output is not a formal run.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import traceback
import pandas as pd
from src.archive import git_info
from src.diagnostics import write_table
from src.fatal_diagnostics import redact_sensitive_text
from src.historical_client import HistoricalFatalError, session_calendar
from src.historical_normalization import map_afterhours_trade_dates
from src.rollover_robustness import validate_scope
from src.rollover_robustness_analysis import analyze_session


def replay_archive(archive, output, sample_id='202606_202607', session='regular'):
    archive,output=Path(archive),Path(output)
    if output.resolve()==archive.resolve() or archive.resolve() in output.resolve().parents:
        raise ValueError('Replay output must be outside the immutable source archive')
    if session not in {'regular','afterhours'}:
        raise ValueError('Only existing regular/afterhours analysis may be replayed')
    cfg=json.loads((archive/'config_snapshot.json').read_text())
    validate_scope(cfg)
    sample=next((s for s in cfg['rollover_samples'] if s['sample_id']==sample_id),None)
    if sample is None:
        raise ValueError('Sample must exist in canonical config snapshot')
    metadata_path=archive/'diagnostics/rollover_sample_metadata.csv'
    metadata=pd.read_csv(metadata_path,dtype=str)
    row=metadata[metadata.sample_id.eq(sample_id)]
    keys=['old_contract','new_contract','expiry_date','window_from','window_to','expiry_source','calendar_source']
    if len(row)!=1 or any(row.iloc[0][k]!=sample[k] for k in keys) or ast.literal_eval(row.iloc[0].trade_dates)!=sample['trade_dates']:
        raise HistoricalFatalError('Archived sample metadata differs from config snapshot; do not substitute')
    calendar_path=archive/'diagnostics/rollover_calendar_snapshot.csv'
    calendar=session_calendar(dict(cfg,calendar_file=str(calendar_path.resolve())))
    cal=calendar[calendar.sample_id.eq(sample_id)].copy()
    if cal.trade_date.tolist()!=sample['trade_dates']:
        raise HistoricalFatalError('Archived calendar differs from fixed sample dates')
    files={'config':archive/'config_snapshot.json','calendar':calendar_path,'sample_metadata':metadata_path}
    for query in ['1!',sample['old_contract'],sample['new_contract']]:
        files[query]=archive/'data'/f"{sample_id}_{session}_{'1bang' if query=='1!' else query}.parquet"
    # Only read existing normalized data; no normalize, fill, trim or date inference.
    frames={q:pd.read_parquet(files[q]) for q in ['1!',sample['old_contract'],sample['new_contract']]}
    output.mkdir(parents=True,exist_ok=False)
    (output/'diagnostics').mkdir()
    info=dict(validation_phase='rollover_robustness_replay',source_archive=str(archive),
        sample_id=sample_id,session=session,api_request_count=0,execution_status='IN_PROGRESS',
        input_sha256={name:hashlib.sha256(p.read_bytes()).hexdigest() for name,p in files.items()},
        row_counts={q:len(f) for q,f in frames.items()},replay_code=git_info(Path(__file__).resolve().parents[1]))
    try:
        if session=='afterhours':
            frames={q:map_afterhours_trade_dates(frame,cal) for q,frame in frames.items()}
        tables,quality=analyze_session(frames,dict(cfg,**sample),cal,session)
        for name,table in tables.items():
            write_table(output,'replay_'+name,table.to_dict('records'),list(table.columns))
        info.update(execution_status='COMPLETED',replay_status='PASS',analysis_classification=quality)
    except Exception as exc:
        (output/'diagnostics'/'fatal_traceback.txt').write_text(redact_sensitive_text(traceback.format_exc()),encoding='utf-8')
        info.update(execution_status='STOPPED',replay_status='FAIL',fatal_error_type=type(exc).__name__,fatal_error_message=redact_sensitive_text(str(exc)))
        raise
    finally:
        (output/'replay_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2),encoding='utf-8')
    return output,info


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--sample-id',default='202606_202607')
    parser.add_argument('--session',choices=['regular','afterhours'],default='regular')
    args=parser.parse_args()
    _,info=replay_archive(args.archive,args.output,args.sample_id,args.session)
    print(json.dumps(info,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
