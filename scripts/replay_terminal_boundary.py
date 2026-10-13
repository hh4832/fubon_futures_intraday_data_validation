"""Offline terminal-boundary replay; never imports SDK or calls a market reader."""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd
from src.diagnostics import write_table
from src.archive import git_info
from src.config import ROOT
from src.historical_client import session_calendar
from src.historical_normalization import map_afterhours_trade_dates
from src.rollover_robustness import validate_scope, classification
from src.rollover_robustness_analysis import analyze_session


def replay_terminal_boundary(archive,output):
    archive,output=Path(archive),Path(output)
    if output.resolve()==archive.resolve() or archive.resolve() in output.resolve().parents:
        raise ValueError('Never overwrite/source-nest the canonical archive')
    def source(name,folder):
        nested=archive/folder/name
        return nested if nested.is_file() else archive/name
    config_path=archive/'config_snapshot.json'
    cfg=json.loads(config_path.read_text());validate_scope(cfg)
    sample=next(s for s in cfg['rollover_samples'] if s['sample_id']=='202512_202601')
    calendar_path=source('rollover_calendar_snapshot.csv','diagnostics')
    cal=session_calendar(dict(cfg,calendar_file=str(calendar_path.resolve())))
    cal=cal[cal.sample_id.eq(sample['sample_id'])].copy()
    if cal.trade_date.tolist()!=sample['trade_dates']:
        raise ValueError('Canonical calendar does not match sample config')
    paths={q:source(f"202512_202601_afterhours_{'1bang' if q=='1!' else q}.parquet",'data') for q in ['1!','202512','202601']}
    before={q:pd.read_parquet(p) for q,p in paths.items()}
    after={q:map_afterhours_trade_dates(frame,cal) for q,frame in before.items()}
    derived=['trade_date','trade_date_evidence','terminal_boundary_bar','provider_trade_date_matches_mapping']
    for q in before:
        pd.testing.assert_frame_equal(before[q].drop(columns=derived,errors='ignore'),after[q].drop(columns=derived,errors='ignore'))
    local=dict(cfg,**sample)
    b,bquality=analyze_session(before,local,cal,'afterhours')
    a,quality=analyze_session(after,local,cal,'afterhours')
    pd.testing.assert_frame_equal(b['fields'],a['fields'])
    adjustment_cols=['segment','status','constant_offset_detected','ratio_adjustment_detected','median_price_difference','median_price_ratio']
    pd.testing.assert_frame_equal(b['adjustment'][adjustment_cols],a['adjustment'][adjustment_cols])
    transition_cols=['last_timestamp_old_contract_in_1bang','first_timestamp_new_contract_in_1bang','last_trade_date_old_contract','first_trade_date_new_contract','identity_transition_count','transition_type','raw_unadjusted_supported','provider_private_trigger','volume_rule_interpretation']
    pd.testing.assert_frame_equal(b['transition'][transition_cols],a['transition'][transition_cols])
    raw_paths=[]
    for q in ['1!','202601']:
        raw_path=source(f"202512_202601_afterhours_{'1bang' if q=='1!' else q}_attempt1.json",'raw')
        payload=json.loads(raw_path.read_text())['response']['data']
        raw=pd.DataFrame(payload)
        raw['timestamp']=pd.to_datetime(raw['date'],utc=True).dt.tz_convert('Asia/Taipei')
        raw_terminal=raw[raw.timestamp.isin(cal.afterhours_close)]
        saved_terminal=after[q][after[q].terminal_boundary_bar]
        if len(raw_terminal)!=len(saved_terminal):raise ValueError('Raw/parquet terminal row count differs')
        for field in ['timestamp','open','high','low','close','volume','average']:
            if raw_terminal.sort_values('timestamp')[field].tolist()!=saved_terminal.sort_values('timestamp')[field].tolist():
                raise ValueError('Raw/parquet terminal fields differ')
        raw_paths.append(raw_path)
    rows=[]
    for q,frame in after.items():
        selected=frame[frame.terminal_boundary_bar].copy()
        selected['contract_query']=q
        selected['previous_trade_date']=before[q].loc[selected.index,'trade_date']
        rows.append(selected)
    terminal=pd.concat(rows,ignore_index=True)
    reconciliation=[]
    for date in ['2025-12-18','2025-12-19']:
        for q in ['1!','202601']:
            old=before[q][before[q].trade_date.eq(date)].volume.sum(min_count=1)
            new=after[q][after[q].trade_date.eq(date)].volume.sum(min_count=1)
            delta=after[q][after[q].trade_date.eq(date) & after[q].terminal_boundary_bar].volume.sum()
            reconciliation.append(dict(trade_date=date,contract_query=q,volume_before=old,terminal_volume=delta,volume_after=new,reconciled=bool(new==old+delta)))
    checks=[]
    for q,frame in after.items():
        scoped=frame[['trade_date','timestamp']].merge(cal[['trade_date','afterhours_open','afterhours_close']],on='trade_date',how='left',validate='many_to_one')
        outside=scoped.afterhours_open.isna() | scoped.timestamp.lt(scoped.afterhours_open) | scoped.timestamp.gt(scoped.afterhours_close)
        outside|=scoped.timestamp.eq(scoped.afterhours_close) & ~frame.terminal_boundary_bar.to_numpy()
        checks.append(dict(contract_query=q,outside_session_rows=int(outside.sum()),rows=len(frame)))
    if any(c['outside_session_rows'] for c in checks):
        quality=classification([quality,'UNRESOLVED'])
    continuous_rows=len(after['1!']);session_rows=int(a['sessions'].row_count.sum())
    if session_rows!=continuous_rows:quality=classification([quality,'UNRESOLVED'])
    source_summary=source('rollover_robustness_summary.csv','diagnostics')
    aggregate=pd.read_csv(source_summary)
    oldaggregate=classification(aggregate.classification.tolist())
    regular_path=source('rollover_regular_transition_summary.csv','diagnostics')
    regular=pd.read_csv(regular_path)
    regular_status=regular.loc[regular.sample_id.eq(sample['sample_id']),'status'].iloc[0]
    sample_quality=classification([quality,regular_status])
    # Other five samples are archived results, not claimed as replayed here.
    index=aggregate.sample_id.eq(sample['sample_id'])
    aggregate.loc[index,'classification']=sample_quality
    aggregate.loc[index,'research_decision']='保留' if sample_quality in {'PASS','PASS WITH CAVEATS'} else '修改後再測'
    aggregate.loc[index,'major_anomaly']=sample_quality not in {'PASS','PASS WITH CAVEATS'}
    newaggregate=classification(aggregate.classification.tolist())
    output.mkdir(parents=True,exist_ok=False);(output/'diagnostics').mkdir()
    write_table(output,'terminal_boundary_replay',terminal.to_dict('records'),list(terminal.columns))
    write_table(output,'terminal_boundary_volume_reconciliation',reconciliation)
    write_table(output,'terminal_boundary_input_validation',checks)
    for name,table in a.items():write_table(output,'replay_'+name,table.to_dict('records'),list(table.columns))
    write_table(output,'terminal_boundary_aggregate_summary',aggregate.to_dict('records'),list(aggregate.columns))
    inputs=[config_path,calendar_path,source_summary,regular_path,*paths.values(),*raw_paths]
    info=dict(api_request_count=0,replay_phase='afterhours_terminal_boundary',source_archive=str(archive),
        source_hashes={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        before_classification=bquality,after_classification=sample_quality,afterhours_classification=quality,code_provenance=git_info(ROOT),aggregate_before=oldaggregate,aggregate_after=newaggregate,
        mapping_rows=continuous_rows,session_map_rows=session_rows,partial_session_switches=int(a['sessions'].partial_session_switch.sum()),
        caveat='Provider terminal timestamp attribution only; not proof of closing auction. Other five samples reused from archived diagnostics.')
    (output/'replay_info.json').write_text(json.dumps(info,ensure_ascii=False,indent=2))
    text=f'''# Afterhours terminal boundary replay

Source: 20261014_063242_a359c880b1d3; existing parquet/config/calendar only. API calls: 0.

202512→202601 afterhours: {bquality} → {quality}.
Aggregate: {oldaggregate} → {newaggregate}. Other five samples reuse archived classifications.
Research decision: {'保留' if newaggregate in {'PASS','PASS WITH CAVEATS'} else '修改後再測'}.

Exact scheduled afterhours close is mapped to the same night window's next regular trade_date and labelled terminal_boundary_bar=True. Normal bars remain [open,close). 05:01 remains outside. Regular policy unchanged.

Continuous mapping/session map rows: {continuous_rows}/{session_rows}; partial switches: {info['partial_session_switches']}.
Outside-session checks:\n{pd.DataFrame(checks).to_string(index=False)}

Volume reconciliation:\n{pd.DataFrame(reconciliation).to_string(index=False)}

Timestamp, identity, raw OHLCVA, exact-match counts, adjustment conclusions, rollover boundaries and rule interpretation were asserted unchanged. No fill, dropping or API retrieval. Source files remain untouched.

This maps historical provider terminal bars; it does not prove 05:00 means closing auction. Shared missing slots remain caveats; this is not full-history or external validation.
'''
    (output/'terminal_boundary_summary.md').write_text(text,encoding='utf-8')
    return output,info


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    _,info=replay_terminal_boundary(args.archive,args.output)
    print(json.dumps(info,ensure_ascii=False,indent=2))
