"""Sample/session diagnostics; no filling, adjusted series or return construction."""
import json
import numpy as np
import pandas as pd
from .continuous_rollover_analysis import (
    PRICES, contract_coverage, build_mapping, transition_summary, adjustment_test,
    field_summary, volume_and_daily, anomaly_rows,
)


def tagged(frame, cfg, session):
    frame = frame.copy()
    frame.insert(0, 'sample_id', cfg['sample_id'])
    frame['session'] = session
    frame['old_contract'] = cfg['old_contract']
    frame['new_contract'] = cfg['new_contract']
    return frame


def missing_minutes(frames, coverage, cfg, calendar, session):
    """Reference-slot absence is diagnostic; asymmetric active-bar absence blocks.

    An empty or mixed session does not establish inactive identity. Report it as
    unknown, never quietly move its gaps into the inactive bucket.
    """
    records = []
    continuous = frames['1!']
    for date in cfg['trade_dates']:
        cal = calendar[calendar.trade_date.eq(date)].iloc[0]
        cov = coverage[coverage.trade_date.eq(date)].iloc[0]
        actual = cov.first_actual_contract_month if cov.unique_actual_contract_months == 1 else None
        c = continuous[continuous.trade_date.eq(date)]
        for query, frame in frames.items():
            underlying = True if query == '1!' else query == actual if actual is not None else None
            start, end = cal[session+'_open'], cal[session+'_close']
            if session == 'regular' and date == cfg['expiry_date'] and (query == cfg['old_contract'] or query == '1!' and actual == cfg['old_contract']):
                end = min(end, pd.Timestamp(date+'T13:30:00+08:00').tz_convert(start.tz))
            expired = query == cfg['old_contract'] and date > cfg['expiry_date']
            slots = pd.date_range(start, end, freq='min', inclusive='left') if not expired else pd.DatetimeIndex([], tz='Asia/Taipei')
            observed = frame[frame.trade_date.eq(date)].timestamp
            absent = slots.difference(observed)
            deltas = absent.to_series().diff().dt.total_seconds().div(60)
            events = int(deltas.ne(1).sum())
            # Never construct missing bars. Only compare timestamps of observed active references.
            active_reference = frames[actual] if actual in frames else frame.iloc[:0]
            reference_ts = active_reference[active_reference.trade_date.eq(date)].timestamp
            asymmetric = len(pd.DatetimeIndex(reference_ts).difference(c.timestamp)) if query == '1!' else len(pd.DatetimeIndex(c.timestamp).difference(observed)) if underlying is True else 0
            status = 'UNRESOLVED' if underlying is None or underlying and (c.empty or asymmetric) else 'PASS WITH CAVEATS' if len(absent) else 'PASS'
            records.append(dict(trade_date=date, contract_query=query, is_continuous_underlying=underlying,
                missing_slot_count=len(absent), gap_event_count=events, active_asymmetric_missing_rows=asymmetric,
                reference_slot_count=len(slots), status=status, evidence_type='Observed fact / Inference',
                notes='scheduled slots only; synchronized absence may be no-trade suppression or provider omission, unconfirmed; inactive gaps do not grade 1!'))
    return pd.DataFrame(records)


def analyze_session(frames, cfg, calendar, session):
    continuous = frames['1!']
    coverage = contract_coverage(continuous, cfg, cfg['trade_dates']).rename(columns={
        'contains_202609':'contains_old_contract', 'contains_202610':'contains_new_contract'})
    mapping = build_mapping(continuous, frames[cfg['old_contract']], frames[cfg['new_contract']], cfg)
    terminal=continuous.get('terminal_boundary_bar',pd.Series(False,index=continuous.index))
    mapping['terminal_boundary_bar']=mapping.timestamp.isin(continuous.loc[terminal,'timestamp'])
    mapping['calendar_date'] = mapping.timestamp.dt.strftime('%Y-%m-%d')
    mapping['actual_contract_month'] = mapping.continuous_actual_contract_month
    previous = mapping.continuous_actual_contract_month.shift()
    mapping['transition_flag'] = previous.notna() & mapping.continuous_actual_contract_month.ne(previous).fillna(False)
    mapping['old_contract'] = cfg['old_contract']; mapping['new_contract'] = cfg['new_contract']
    mapping['partial_day_switch'] = mapping.trade_date.isin(coverage.loc[coverage.partial_day_switch, 'trade_date'])
    # Existing transition helper checks its regular_open column. A comparison-only
    # calendar view changes that anchor for night; archived calendar remains intact.
    view = calendar.copy()
    if session == 'afterhours':
        view['regular_open'] = view.afterhours_open
    transition = transition_summary(continuous, coverage, cfg, view)
    partial = bool(coverage.partial_day_switch.any())
    transition['full_session_switch'] = bool(transition['transition_confirmed_in_sample'] and not partial)
    transition['transition_type'] = ('full-day switch' if session == 'regular' else 'full-afterhours-session switch') if transition['full_session_switch'] else 'partial-session switch' if partial else 'UNRESOLVED'
    last, first = transition['last_timestamp_old_contract_in_1bang'], transition['first_timestamp_new_contract_in_1bang']
    transition['cross_midnight_switch'] = bool(partial and transition['transition_confirmed_in_sample'] and last and first and pd.Timestamp(last).date() != pd.Timestamp(first).date())
    transition['switch_calendar_date'] = str(pd.Timestamp(first).date()) if first else None
    adjustment = adjustment_test(mapping, transition, cfg, view)
    fields = field_summary(mapping)
    volumes, _ = volume_and_daily(frames, mapping, coverage, transition, cfg, cfg['trade_dates'])
    volumes['volume_leader'] = volumes.observed_minute_volume_leader
    single = coverage.set_index('trade_date').unique_actual_contract_months.eq(1)
    actual = volumes.trade_date.map(coverage.set_index('trade_date').first_actual_contract_month)
    known = volumes.volume_leader.ne('UNRESOLVED') & volumes.trade_date.map(single).fillna(False)
    volumes['continuous_matches_volume_leader'] = actual.eq(volumes.volume_leader).astype('boolean').where(known)
    missing = missing_minutes(frames, coverage, cfg, calendar, session)
    anomalies = anomaly_rows(frames, mapping, coverage, cfg).rename(columns={'query':'contract_query'})
    extra = []
    for query, frame in frames.items():
        if session=='afterhours':
            terminal=frame.get('terminal_boundary_bar',pd.Series(False,index=frame.index))
            if terminal.any():
                rows=frame.loc[terminal,['timestamp','trade_date']].copy()
                rows['contract_query']=query;rows['anomaly_type']='terminal_boundary_bar'
                rows['evidence_type']='Observed fact / Inference'
                rows['notes']='exact scheduled afterhours close retained and assigned; not proof of closing auction'
                extra.append(rows)
        special = frame[frame.timestamp.dt.strftime('%H:%M').eq('13:45')]
        if session == 'regular' and len(special):
            table = special[['timestamp','trade_date']].copy()
            table['contract_query'] = query; table['anomaly_type'] = 'special_13_45_bar'
            table['evidence_type'] = 'Observed fact'; table['notes'] = 'preserved terminal/special bar; not deleted or counted as missing'
            extra.append(table)
    if session == 'regular':
        extra.append(pd.DataFrame([dict(contract_query=cfg['old_contract'], timestamp=pd.Timestamp(cfg['expiry_date']+'T13:30:00+08:00'),
            trade_date=cfg['expiry_date'], anomaly_type='expiry_day_shortened_session', evidence_type='Documented fact',
            notes='expiring TX month closes 13:30; other month reference closes 13:45; bar counts are not fixed at 300')]))
    if extra:
        parts=[part for part in [anomalies,*extra] if not part.empty]
        anomalies=pd.concat(parts,ignore_index=True)
    sessions = coverage.merge(calendar[['trade_date',session+'_open',session+'_close']],on='trade_date',validate='one_to_one').rename(columns={
        session+'_open':'session_start', session+'_close':'session_end', 'actual_contract_months':'continuous_actual_contract_month',
        'unique_actual_contract_months':'unique_contract_count','partial_day_switch':'partial_session_switch'})
    sessions['session_start_calendar_date'] = sessions.session_start.dt.strftime('%Y-%m-%d')
    for label, month in [('old',cfg['old_contract']),('new',cfg['new_contract'])]:
        count = continuous[continuous.actual_contract_month.eq(month)].groupby('trade_date').size()
        sessions[label+'_contract_rows'] = sessions.trade_date.map(count).fillna(0).astype(int)
    structural = (continuous.actual_contract_month.isna().any() or
        not continuous.actual_contract_month.isin([cfg['old_contract'],cfg['new_contract']]).all() or
        any(f.timestamp.duplicated().any() for f in frames.values()) or
        any((~f.actual_contract_month.eq(q).fillna(False)).any() for q,f in frames.items() if q != '1!'))
    unknown_dates = any(f.trade_date.isna().any() or (~f.trade_date.isin(cfg['trade_dates'])).any() for f in frames.values())
    raw = bool(len(mapping) and mapping.expected_ohlc_exact_match.all() and adjustment.status.eq('PASS').all())
    active_break = bool(missing.loc[missing.is_continuous_underlying.eq(True), 'active_asymmetric_missing_rows'].gt(0).any())
    quality = 'FAIL' if structural else 'UNRESOLVED' if (unknown_dates or partial or active_break or not raw or coverage.row_count.eq(0).any() or not transition['transition_confirmed_in_sample'] or not mapping.volume_exact_match.all()) else 'PASS WITH CAVEATS'
    transition['raw_unadjusted_supported'] = raw
    transition['provider_private_trigger'] = 'Unresolved; private algorithm name is not an operational traceability gate'
    transition['status'] = quality
    sessions.loc[sessions.partial_session_switch | sessions.row_count.eq(0),'status'] = 'UNRESOLVED'
    bad_dates=mapping.loc[~mapping.expected_ohlc_exact_match | ~mapping.volume_exact_match,'trade_date']
    active_gap_dates=missing.loc[missing.is_continuous_underlying.eq(True) & missing.active_asymmetric_missing_rows.gt(0),'trade_date']
    sessions.loc[sessions.trade_date.isin(bad_dates) | sessions.trade_date.isin(active_gap_dates),'status']='UNRESOLVED'
    tables = dict(coverage=coverage,mapping=mapping,transition=pd.DataFrame([transition]),adjustment=adjustment,
        fields=fields,volume=volumes,missing=missing,anomalies=anomalies,sessions=sessions)
    return {key:tagged(value,cfg,session) for key,value in tables.items()}, quality


def compare_sessions(results, cfg):
    regular, night = results['regular'], results['afterhours']
    r, n = regular['transition'].iloc[0], night['transition'].iloc[0]
    expiry_start = pd.Timestamp(cfg['expiry_date']+'T15:00:00+08:00')
    expiry_night = night['mapping'][night['mapping'].timestamp.between(expiry_start,expiry_start+pd.Timedelta(hours=14),inclusive='left')]
    first = expiry_night.iloc[0] if len(expiry_night) else None
    next_date = next((d for d in cfg['trade_dates'] if d>cfg['expiry_date']),None)
    next_r = regular['sessions'][regular['sessions'].trade_date.eq(next_date)]
    next_months = json.loads(next_r.continuous_actual_contract_month.iloc[0]) if len(next_r) and isinstance(next_r.continuous_actual_contract_month.iloc[0],str) else []
    joined = regular['sessions'][['trade_date','continuous_actual_contract_month','unique_contract_count']].merge(
        night['sessions'][['trade_date','continuous_actual_contract_month','unique_contract_count']],on='trade_date',suffixes=('_regular','_night'))
    comparable = joined.unique_contract_count_regular.eq(1) & joined.unique_contract_count_night.eq(1)
    consistent = bool(comparable.all() and joined.continuous_actual_contract_month_regular.eq(joined.continuous_actual_contract_month_night).all())
    row = dict(sample_id=cfg['sample_id'],expiry_date=cfg['expiry_date'],last_regular_old_timestamp=r.last_timestamp_old_contract_in_1bang,
        first_afterhours_timestamp=first.timestamp if first is not None else None,
        afterhours_contract_month=first.continuous_actual_contract_month if first is not None else None,
        afterhours_switch_date=n.first_trade_date_new_contract,afterhours_switch_calendar_date=n.switch_calendar_date,
        afterhours_switch_timestamp=n.first_timestamp_new_contract_in_1bang,next_regular_contract_month=next_months[0] if len(next_months)==1 else None,
        regular_afterhours_consistent=consistent,
        status='PASS WITH CAVEATS' if first is not None and r.full_session_switch and n.full_session_switch else 'UNRESOLVED',
        notes='compare same mapped trade_date; also retain calendar start date. Different boundaries are findings, not assumed provider errors.')
    def relation(date):
        return 'UNRESOLVED' if not date else 'before expiry' if date<cfg['expiry_date'] else 'on expiry' if date==cfg['expiry_date'] else 'after expiry'
    expiry = dict(sample_id=cfg['sample_id'],official_expiry_date=cfg['expiry_date'],expiry_source=cfg['expiry_source'],
        last_old_contract_regular_date=r.last_trade_date_old_contract,first_new_contract_regular_date=r.first_trade_date_new_contract,
        last_old_contract_afterhours_trade_date=n.last_trade_date_old_contract,first_new_contract_afterhours_trade_date=n.first_trade_date_new_contract,
        regular_switch_relation_to_expiry=relation(r.first_trade_date_new_contract),afterhours_switch_relation_to_expiry=relation(n.first_trade_date_new_contract),
        next_regular_trade_date=next_date,regular_switch_at_next_regular=r.first_trade_date_new_contract==next_date,
        afterhours_switch_at_expiry_evening=n.switch_calendar_date==cfg['expiry_date'])
    return row,expiry


def summarize_sample(results, cfg, quality, boundary):
    fields = pd.concat([v['fields'] for v in results.values()],ignore_index=True)
    def rate(names):
        f=fields[fields.field.isin(names)]
        total=int(f.continuous_rows.sum())
        return float(f.exact_match_rows.sum()/total) if total else np.nan
    r,n=[results[s]['transition'].iloc[0] for s in ['regular','afterhours']]
    raw=bool(r.raw_unadjusted_supported and n.raw_unadjusted_supported)
    counterexamples=sum(int(t['volume'].simple_daily_max_rule_counterexample.sum()) for t in results.values())
    return dict(sample_id=cfg['sample_id'],old_contract=cfg['old_contract'],new_contract=cfg['new_contract'],expiry_date=cfg['expiry_date'],
        regular_switch_date=r.first_trade_date_new_contract,afterhours_switch_date=n.first_trade_date_new_contract,
        per_bar_identity_reconstructable=all(len(t['mapping'])>0 and not t['mapping'].continuous_actual_contract_month.isna().any() and t['mapping'].continuous_actual_contract_month.isin([cfg['old_contract'],cfg['new_contract']]).all() for t in results.values()),
        regular_full_session_switch=bool(r.full_session_switch),afterhours_full_session_switch=bool(n.full_session_switch),
        partial_switch_detected=any(t['sessions'].partial_session_switch.any() for t in results.values()),
        ohlc_exact_match_rate=rate(PRICES),volume_exact_match_rate=rate(['volume']),average_exact_match_rate=rate(['average']),
        raw_unadjusted_supported=raw,roll_gap_retained=bool(raw and r.transition_confirmed_in_sample and n.transition_confirmed_in_sample),
        simple_volume_rule_supported=False if counterexamples else None,
        simple_volume_rule_interpretation='counterexample observed' if counterexamples else 'UNRESOLVED: no counterexample does not prove rule',
        volume_rule_counterexamples=counterexamples,expiry_alignment=f'regular={r.first_trade_date_new_contract}; afterhours trade_date={n.first_trade_date_new_contract}',
        regular_afterhours_consistent=boundary['regular_afterhours_consistent'],major_anomaly=quality not in ['PASS','PASS WITH CAVEATS'],
        classification=quality,research_decision='保留' if quality in ['PASS','PASS WITH CAVEATS'] else '修改後再測')
