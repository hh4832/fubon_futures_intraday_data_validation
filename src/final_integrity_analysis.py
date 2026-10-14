"""Vectorized final integrity diagnostics. Scheduled absence never means zero trades."""
import json
import numpy as np
import pandas as pd
from .historical_validation import validate_historical_schema,validate_historical_ohlc,status_rollup
from .validate_aggregation import aggregate
from .final_sample_matrix import effective_calendar
from .continuous_rollover_analysis import PRICES,FIELDS


def identity_checks(frame,sample,query):
    month=frame.actual_contract_month.astype('string')
    valid=month.str.fullmatch(r'[0-9]{4}(0[1-9]|1[0-2])',na=False)
    expected=[sample['old_contract'],sample['new_contract']] if sample['window_type']=='june_rollover' else [sample['old_contract']]
    wrong=~month.isin(expected) if query=='1!' else ~month.eq(query).fillna(False)
    return month.isna(),~valid,wrong


def query_diagnostics(item,frame,sample,calendar):
    p=item['params'];query=p['contractMonth'];session=p['session']
    tags=dict(year=sample['year'],window_type=sample['window_type'],sample_id=sample['sample_id'],session=session,contract_query=query,role=item.get('role','primary'))
    schema=validate_historical_schema(item,frame)
    raw=pd.DataFrame(item['response'].get('data',[]))
    required={'timestamp','trade_date','calendar_date','open','high','low','close','volume','average','actual_contract_month','terminal_boundary_bar'}
    normalized_missing=required-set(frame)
    schema_row=dict(tags,row_count=len(frame),columns=json.dumps(list(frame)),dtype_signature=json.dumps(frame.dtypes.astype(str).to_dict()),raw_schema=json.dumps(raw.dtypes.astype(str).to_dict()),normalized_schema=json.dumps(sorted(frame.columns)),timezone=str(frame.timestamp.dt.tz),schema_status=schema['schema_status'],missing_required_columns=json.dumps(sorted(normalized_missing)),unexpected_columns=schema['extra_bar_fields'],status='FAIL' if normalized_missing else schema['schema_status'])
    ohlc,invalid=validate_historical_ohlc(frame)
    missing,badformat,wrong=identity_checks(frame,sample,query)
    original=frame.sort_values('source_row',kind='stable')
    duplicate=frame.timestamp.duplicated(keep=False)
    aware=bool(frame.timestamp_source_aware.all())
    impossible=frame.timestamp.isna() | frame.timestamp.dt.second.ne(0) | frame.timestamp.dt.microsecond.ne(0)
    ts=dict(tags,row_count=len(frame),timezone_aware=aware,timezone=str(frame.timestamp.dt.tz),chronological=bool(original.timestamp.is_monotonic_increasing),duplicate_rows=int(duplicate.sum()),impossible_timestamp_rows=int(impossible.sum()),calendar_date_preserved=bool(frame.calendar_date.eq(frame.timestamp.dt.strftime('%Y-%m-%d')).all()),status='UNRESOLVED' if frame.empty else 'FAIL' if not aware or duplicate.any() or impossible.any() or not original.timestamp.is_monotonic_increasing else 'PASS')
    scoped=frame[['timestamp','trade_date']].merge(calendar[['trade_date',session+'_open',session+'_close','afterhours_scheduled']],on='trade_date',how='left',validate='many_to_one')
    outside=scoped[session+'_open'].isna() | scoped.timestamp.lt(scoped[session+'_open']) | scoped.timestamp.gt(scoped[session+'_close'])
    outside|=~frame.trade_date.isin(item['requested_dates']).to_numpy()
    if session=='afterhours':
        outside|=scoped.afterhours_scheduled.ne('true') | (scoped.timestamp.eq(scoped.afterhours_close) & ~frame.terminal_boundary_bar.to_numpy())
    conflict=frame.get('provider_trade_date_matches_mapping',pd.Series(True,index=frame.index)).eq(False)
    sessions=[];counts=[];gaps=[];trace=[]
    for date in item['requested_dates']:
        cal=calendar[calendar.trade_date.eq(date)].iloc[0]
        f=frame[frame.trade_date.eq(date)]
        actual=f.actual_contract_month.dropna().unique().tolist()
        scheduled=bool(session=='regular' or cal.afterhours_scheduled=='true')
        start,end=cal[session+'_open'],cal[session+'_close']
        old_expiry=(session=='regular' and date==sample['expiry_date'] and (query==sample['old_contract'] or query=='1!' and actual==[sample['old_contract']]))
        if old_expiry:end=min(end,pd.Timestamp(date+'T13:30:00+08:00').tz_convert(start.tz))
        expired=query==sample['old_contract'] and sample['window_type']=='june_rollover' and date>sample['expiry_date']
        slots=pd.date_range(start,end,freq='min',inclusive='left') if scheduled and not expired else pd.DatetimeIndex([],tz='Asia/Taipei')
        absent=slots.difference(f.timestamp)
        events=int(absent.to_series().diff().dt.total_seconds().div(60).ne(1).sum())
        date_out=int(outside.to_numpy()[frame.trade_date.eq(date).fillna(False).to_numpy(dtype=bool)].sum())
        status='UNRESOLVED' if scheduled and not expired and f.empty else 'FAIL' if not scheduled and len(f) else 'UNRESOLVED' if date_out or conflict.loc[f.index].any() else 'PASS WITH CAVEATS' if len(absent) else 'PASS'
        sessions.append(dict(tags,trade_date=date,session_start=start,session_end=end,observed_first=f.timestamp.min(),observed_last=f.timestamp.max(),terminal_boundary_bar_count=int(f.terminal_boundary_bar.sum()),outside_session_rows=date_out,provider_trade_date_conflict=int(conflict.loc[f.index].sum()),row_count=len(f),scheduled=scheduled,status=status))
        counts.append(dict(tags,trade_date=date,row_count=len(f),expected_scheduled_slots=len(slots),observed_slot_ratio=len(f)/len(slots) if len(slots) else np.nan,first_timestamp=f.timestamp.min(),last_timestamp=f.timestamp.max(),special_terminal_bar=int(f.terminal_boundary_bar.sum()),special_1345_bar=int(f.timestamp.dt.strftime('%H:%M').eq('13:45').sum()),expiry_short_session=old_expiry,notes='scheduled reference only; terminal extra bars preserved; absence not confirmed loss'))
        gaps.append(dict(tags,trade_date=date,absence_type='scheduled-slot absence' if query=='1!' else 'concrete scheduled-slot absence',missing_slot_count=len(absent),gap_event_count=events,duplicate_rows=int(f.timestamp.duplicated(keep=False).sum()),status='PASS WITH CAVEATS' if len(absent) else 'PASS',notes='no-trade suppression vs provider omission UNRESOLVED; no fill or zero-volume inference'))
        trace.append(dict(tags,trade_date=date,unique_contract_count=len(actual),actual_contract_months=json.dumps(actual),partial_switch=len(actual)>1,unexpected_contract=int(wrong.loc[f.index].sum()),missing_contract_metadata_rows=int(missing.loc[f.index].sum()),invalid_month_format_rows=int(badformat.loc[f.index].sum()),status='FAIL' if (wrong.loc[f.index].any() or missing.loc[f.index].any() or badformat.loc[f.index].any()) else 'UNRESOLVED' if len(actual)>1 or f.empty and scheduled and not expired else 'PASS'))
    unmapped=int(frame.trade_date.isna().sum())
    unassigned=~frame.trade_date.isin(item['requested_dates'])
    if unassigned.any():
        sessions.append(dict(tags,trade_date=None,row_count=int(unassigned.sum()),terminal_boundary_bar_count=int(frame.loc[unassigned,'terminal_boundary_bar'].sum()),outside_session_rows=int((outside.to_numpy() & unassigned.to_numpy()).sum()),provider_trade_date_conflict=int(conflict.loc[unassigned].sum()),status='UNRESOLVED',notes='all unresolved source rows retained in parquet; no guessed trade date'))
    ohlc_row={**tags,**ohlc,'row_count':len(frame),'OHLC_invalid_rows':len(invalid)}
    if len(invalid):
        invalid=invalid.assign(**tags)
    core=[schema_row['status'],ts['status'],ohlc['status']]
    if query=='1!':core += [s['status'] for s in sessions]+[s['status'] for s in trace]
    else:core += ['FAIL' if missing.any() or badformat.any() or wrong.any() else 'UNRESOLVED' if outside.any() or conflict.any() else 'PASS']
    quality=status_rollup(core)
    return dict(schema=pd.DataFrame([schema_row]),timestamp=pd.DataFrame([ts]),session=pd.DataFrame(sessions),bar_count=pd.DataFrame(counts),missing=pd.DataFrame(gaps),ohlc=pd.DataFrame([ohlc_row]),traceability=pd.DataFrame(trace)),quality,invalid


def repeatability(first,second,tags):
    key=['timestamp','actual_contract_month']
    if first.timestamp.duplicated().any() or second.timestamp.duplicated().any():
        return dict(tags,same_row_set=False,same_timestamps=False,mismatch_rows=max(len(first),len(second)),status='UNRESOLVED',notes='duplicate keys; comparisons not silently deduplicated')
    compared=first[key+FIELDS].merge(second[key+FIELDS],on=key,how='outer',suffixes=('_first','_repeat'),indicator=True,validate='one_to_one')
    both=compared['_merge'].eq('both');same=both.copy()
    for field in FIELDS:same &= compared[field+'_first'].eq(compared[field+'_repeat'])
    mismatch=int((~same).sum())
    return dict(tags,first_rows=len(first),repeat_rows=len(second),same_row_set=bool(both.all() and len(first)>0),same_timestamps=bool(first.timestamp.equals(second.timestamp)),mismatch_rows=mismatch,status='PASS' if len(first) and mismatch==0 else 'UNRESOLVED',notes='exact timestamp/month/OHLCVA comparison; both raw responses preserved; NaN values are not exact proof')


def internal_aggregation(frame,calendar,session,tags):
    rows=[];outputs=[]
    # Fixed first scheduled requested session; incomplete input is not replaced.
    cal=effective_calendar(calendar,session).iloc[0]
    date=cal.trade_date;one=frame[frame.trade_date.eq(date)].copy()
    unique=one.actual_contract_month.nunique()==1 and not one.timestamp.duplicated().any()
    inside=(one.timestamp>=cal[session+'_open']) & (one.timestamp<=cal[session+'_close'])
    for timeframe in [5,15,60]:
        if one.empty or not unique or not inside.all():
            rows.append(dict(tags,trade_date=date,timeframe=timeframe,status='UNRESOLVED',notes='fixed first session empty/mixed/duplicate/outside; no substitute session'));continue
        output=aggregate(one,timeframe,cal[session+'_open'])
        repeated=aggregate(one,timeframe,cal[session+'_open'])
        labels=cal[session+'_open']+pd.to_timedelta(((one.timestamp-cal[session+'_open']).dt.total_seconds()//(timeframe*60))*timeframe,unit='min')
        expected=one.assign(bucket=labels).groupby('bucket',sort=True).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'),volume=('volume','sum')).reset_index().rename(columns={'bucket':'timestamp'})
        deterministic=output[['timestamp']+PRICES+['volume']].equals(expected) and output.equals(repeated)
        conserved=output.volume.sum()==one.volume.sum()
        output['trade_date']=date;output['session']=session;output['actual_contract_month']=one.actual_contract_month.iloc[0]
        output['terminal_only_bucket']=output.timestamp.eq(cal[session+'_close'])
        outputs.append((timeframe,output))
        rows.append(dict(tags,trade_date=date,timeframe=timeframe,input_rows=len(one),output_buckets=len(output),deterministic_bucket=deterministic,volume_sum_preserved=bool(conserved),cross_session_leakage=0,terminal_only_buckets=int(output.terminal_only_bucket.sum()),status='PASS WITH CAVEATS' if deterministic and conserved else 'UNRESOLVED',notes='internal only; observed bars aggregated with canonical session anchor; missing/partial buckets and terminal-only bucket not repaired; no provider higher-timeframe query'))
    return pd.DataFrame(rows),outputs


def availability(frame,calendar,session,documented):
    cal=effective_calendar(calendar,session)
    full=[]
    for date in cal.trade_date:
        f=frame[frame.trade_date.eq(date)];c=cal[cal.trade_date.eq(date)].iloc[0]
        if len(f) and f.timestamp.min()==c[session+'_open'] and f.timestamp.max()>=c[session+'_close']-pd.Timedelta(minutes=1):full.append(date)
    return dict(session=session,documented_availability=documented,earliest_successful_timestamp=str(frame.timestamp.min()) if len(frame) else None,first_full_session=full[0] if full else None,first_full_regular_session=full[0] if full and session=='regular' else None,first_full_afterhours_session=full[0] if full and session=='afterhours' else None,partial_first_day=bool(len(frame) and not full or len(frame) and full and frame.trade_date.iloc[0]!=full[0]),metadata_complete=bool(len(frame) and frame.actual_contract_month.notna().all()),status='PASS WITH CAVEATS' if full and frame.actual_contract_month.notna().all() else 'UNRESOLVED',notes='earliest observed in bounded frozen probes, not proof of exact provider global first minute; full refers to observed boundary coverage, not proof every missing slot is no-trade')
