"""Final report gates: execution completion and evidence completion are separate."""
import numpy as np
import pandas as pd
from .historical_validation import status_rollup

OUTPUT_NAMES={
 'schema':'cross_year_schema_validation','timestamp':'cross_year_timestamp_validation',
 'session':'cross_year_session_validation','bar_count':'cross_year_bar_count',
 'missing':'cross_year_missing_minutes','ohlc':'cross_year_ohlc_validation',
 'traceability':'cross_year_contract_traceability','rollover_fields':'cross_year_rollover_field_comparison',
 'adjustment':'cross_year_adjustment_validation','roll_gap':'cross_year_roll_gap',
 'repeatability':'cross_year_repeatability','aggregation':'cross_year_aggregation_validation',
 'external':'external_taifex_spot_check','external_selection':'external_taifex_selection_diagnostics','availability':'historical_availability_boundary'}


def yearly_summary(tables,cfg,request_complete):
    rows=[]
    def section(name,year,continuous=False):
        df=tables.get(name,pd.DataFrame())
        if 'year' not in df:return pd.DataFrame()
        out=df[df.year.eq(year)]
        if 'role' in out and name!='repeatability':out=out[out.role.eq('primary')]
        if continuous and 'contract_query' in out:out=out[out.contract_query.eq('1!')]
        return out
    for year in range(2017,2027):
        samples=[s for s in cfg['samples'] if s['year']==year]
        included=[s for s in samples if s.get('included_in_formal_run',True)]
        late=next(s for s in samples if s['window_type']=='ordinary_late')
        statuses=[];row=dict(year=year,sampled_trade_dates=sum(len(s['trade_dates']) for s in included),
            planned_trade_dates=sum(len(s['trade_dates']) for s in samples),
            late_year_status='INCLUDED — see data-quality gates' if late.get('included_in_formal_run',True) else 'NOT_YET_ELIGIBLE',
            late_year_test_scope='INCLUDED' if late.get('included_in_formal_run',True) else 'NOT TESTED — FUTURE WINDOW')
        if year==2026:row['2026_late_year_status']=row['late_year_status']
        for key,label in [('schema','schema_pass'),('timestamp','timestamp_pass'),('session','session_pass'),('ohlc','ohlc_pass'),('traceability','contract_traceability_pass')]:
            df=section(key,year,continuous=key in {'session','traceability'})
            value=status_rollup(df.status.tolist()) if 'status' in df else 'UNRESOLVED'
            row[label]=value;statuses.append(value)
        sessions=section('session',year,True)
        row['regular_rows']=int(sessions.loc[sessions.session.eq('regular'),'row_count'].sum()) if len(sessions) else 0
        row['afterhours_rows']=int(sessions.loc[sessions.session.eq('afterhours'),'row_count'].sum()) if len(sessions) else 0
        ts=section('timestamp',year);row['duplicate_count']=int(ts.duplicate_rows.sum()) if len(ts) else 0
        row['outside_session_count']=int(sessions.outside_session_rows.sum()) if len(sessions) else 0
        fields=section('rollover_fields',year)
        prices=fields[fields.field.isin(['open','high','low','close'])] if len(fields) else pd.DataFrame()
        row['rollover_exact_match_rate']=float(prices.exact_match_rows.sum()/prices.continuous_rows.sum()) if len(prices) and prices.continuous_rows.sum() else np.nan
        if len(prices):statuses += ['UNRESOLVED' if prices.unavailable_rows.sum() or prices.mismatch_rows.sum() else 'PASS']
        else:statuses += ['UNRESOLVED']
        volume=fields[fields.field.eq('volume')] if len(fields) else fields
        if not len(volume) or volume.mismatch_rows.sum() or volume.unavailable_rows.sum():statuses += ['UNRESOLVED']
        adjustment=section('adjustment',year);statuses += [status_rollup(adjustment.status.tolist()) if len(adjustment) else 'UNRESOLVED']
        missing=section('missing',year)
        active=missing[missing.absence_type.eq('active asymmetric break')] if len(missing) else pd.DataFrame()
        row['active_asymmetric_missing_count']=int(active.missing_slot_count.sum()) if len(active) else 0
        if row['active_asymmetric_missing_count']:statuses += ['UNRESOLVED']
        repeat=section('repeatability',year)
        row['repeatability_status']=status_rollup(repeat.status.tolist()) if len(repeat) else 'UNRESOLVED' if year in cfg['repeatability_years'] else 'NOT SAMPLED (predeclared)'
        if year in cfg['repeatability_years']:statuses += [row['repeatability_status']]
        aggregation=section('aggregation',year);statuses += [status_rollup(aggregation.status.tolist()) if len(aggregation) else 'UNRESOLVED']
        external=section('external',year)
        compatible=external[external.definition_compatible.eq(True)] if len(external) else pd.DataFrame()
        row['external_validation_status']=status_rollup(compatible.status.tolist()) if len(compatible) else 'UNRESOLVED'
        prices_external=compatible[compatible.field.isin(['high','low'])] if len(compatible) else pd.DataFrame()
        required_external={(session,field) for session in cfg['sessions'] for field in ['contract_identifier','official_trading_date','high','low']}
        actual_external=set(zip(compatible.session,compatible.field)) if len(compatible) else set()
        if len(compatible)!=8 or actual_external!=required_external:
            row['external_validation_status']=status_rollup([row['external_validation_status'],'UNRESOLVED'])
        statuses += [row['external_validation_status']]
        if year==2017:
            available=tables.get('availability',pd.DataFrame())
            statuses += [status_rollup(available.status.tolist()) if len(available)==2 else 'UNRESOLVED']
        if not request_complete:statuses += ['UNRESOLVED']
        row['classification']=status_rollup(statuses)
        if row['classification']=='PASS':row['classification']='PASS WITH CAVEATS'
        row['validation_blocker']=row['classification'] in {'FAIL','UNRESOLVED'}
        def count(table,column):
            df=section(table,year)
            return int(pd.to_numeric(df[column],errors='coerce').sum()) if column in df else 0
        trace=section('traceability',year,True)
        anomaly_counts=dict(ohlc_invalid_count=count('ohlc','OHLC_invalid_rows'),duplicate_count=row['duplicate_count'],outside_session_count=count('session','outside_session_rows'),
            missing_contract_metadata_count=count('traceability','missing_contract_metadata_rows'),
            partial_session_switch_count=int(trace.partial_switch.sum()) if len(trace) else 0,
            rollover_ohlc_mismatch_count=int(prices.mismatch_rows.sum()) if len(prices) else 0,
            volume_mismatch_count=int(volume.mismatch_rows.sum()) if len(volume) else 0,
            active_asymmetric_missing_count=row['active_asymmetric_missing_count'],repeatability_mismatch_count=count('repeatability','mismatch_rows'),
            external_price_contradiction_count=int(prices_external.status.eq('FAIL').sum()) if len(prices_external) else 0)
        row.update(anomaly_counts)
        row['major_data_anomaly']=any(value>0 for value in anomaly_counts.values())
        row['major_anomaly']=row['major_data_anomaly'] # Legacy alias, no longer a blocker flag.
        rows.append(row)
    return pd.DataFrame(rows)


def global_summary(tables,yearly,info):
    def total(name,column,mask=None):
        f=tables.get(name,pd.DataFrame())
        if column not in f:return 0
        if mask is not None:f=mask(f)
        return int(pd.to_numeric(f[column],errors='coerce').sum())
    fields=tables.get('rollover_fields',pd.DataFrame())
    def mismatches(names):return int(fields.loc[fields.field.isin(names),'mismatch_rows'].sum()) if len(fields) else 0
    metrics={
        'years_tested':int(((yearly.regular_rows+yearly.afterhours_rows)>0).sum()),
        'trade_dates_tested':info.get('sample_trade_date_count',0),
        'continuous_rows':int(yearly.regular_rows.sum()+yearly.afterhours_rows.sum()),
        'schema_failures':int(tables.get('schema',pd.DataFrame()).get('status',pd.Series(dtype=str)).eq('FAIL').sum()),
        'OHLC_invalid_rows':total('ohlc','OHLC_invalid_rows'),
        'duplicate_rows':total('timestamp','duplicate_rows'),
        'outside_session_rows':total('session','outside_session_rows'),
        'missing_contract_month_rows':total('traceability','missing_contract_metadata_rows'),
        'partial_session_switches':total('traceability','partial_switch',lambda f:f[f.contract_query.eq('1!')]),
        'rollover_OHLC_mismatches':mismatches(['open','high','low','close']),
        'volume_mismatches':mismatches(['volume']),'average_mismatches':mismatches(['average']),
        'active_asymmetric_missing_rows':int(yearly.active_asymmetric_missing_count.sum()),
        'repeatability_mismatches':total('repeatability','mismatch_rows'),
        'external_spot_check_matches':int(tables.get('external',pd.DataFrame()).get('status',pd.Series(dtype=str)).eq('PASS').sum()),
        'external_spot_check_unresolved':int(tables.get('external',pd.DataFrame()).get('status',pd.Series(dtype=str)).eq('UNRESOLVED').sum())}
    return pd.DataFrame([dict(metric=k,value=v,status='Observed count; see yearly gates',evidence_type='Observed fact',interpretation='sampled scope only; zero does not establish unperformed checks passed') for k,v in metrics.items()])


def final_report(tables,yearly,info,status):
    passed=status in {'PASS','PASS WITH CAVEATS'}
    decision='保留' if passed else '修改後再測'
    headings=['Validation question','Provenance','Predeclared sampling design','Historical availability','Schema stability','Timestamp / timezone','Session mapping','Bar counts / special boundaries','Missing-minute diagnostics','OHLC integrity','ContractMonth traceability','Annual rollover confirmation','Back-adjustment evidence','Roll-gap behavior','Repeatability','1m aggregation','TAIFEX external validation','Year-by-year summary','Structural changes by year','Remaining unresolved issues','Opponent view','Final classification','Research decision','Canonical backtest-source recommendation']
    mapping={3:'availability',4:'schema',5:'timestamp',6:'session',7:'bar_count',8:'missing',9:'ohlc',10:'traceability',11:'rollover_fields',12:'adjustment',13:'roll_gap',14:'repeatability',15:'aggregation',16:'external'}
    text=['# Final Cross-Year Historical Integrity Validation',f"Git: {info['git_commit']}; branch: {info['git_branch']}",
        info.get('scope_language','Deterministic sampled historical validation; not a full calendar year claim.') if passed else 'Validation of eligible deterministic historical samples is not yet complete.',
        'As-of timestamp: '+str(info.get('as_of_timestamp','NOT RECORDED')),
        info.get('excluded_windows_note','')]
    for i,title in enumerate(headings):
        text+=['',f'## {chr(65+i)}. {title}']
        if i in mapping:
            f=tables.get(mapping[i],pd.DataFrame())
            text+=['[Observed fact / Unresolved] '+(f.to_string(index=False) if len(f) else 'NOT PERFORMED; no PASS inferred')]
        elif i==0:text+=['[Inference] Assess sampled TXF historical 1m data-source integrity, not strategies or full-history proof.']
        elif i in [1,2]:text += [str({k:info.get(k) for k in ['python_version','fubon_sdk_version','sample_matrix_sha256','as_of_timestamp','planned_windows','eligible_windows','future_windows','planned_total_trade_dates','eligible_trade_dates','future_trade_dates','sampling_policy_revision_reason','api_request_count_before_revision','sample_trade_date_count','expected_logical_request_count','logical_request_count','external_validation']})]
        elif i==17:text += [yearly.to_string(index=False)]
        elif i==18:text += ['[Inference] Compare raw_schema/dtype_signature and anomaly counts per year before attributing any change to provider methodology. Normalization success does not prove raw format unchanged.']
        elif i==19:text += ['[Unresolved] Shared omissions vs no-trade suppression; private trigger name; untested dates; daily close/volume definitions; failed or unavailable external checks.']
        elif i==20:text += ['[Inference] 1. Sampling is not every historical day. 2. Same-provider exact matches may share errors. 3. External daily checks cannot prove each minute. 4. Shared absences may be omissions. 5. Untested-month methodology may differ. 6. Future revisions/backfills possible. 7. Deterministic selection is not exhaustive. 8. Non-3.11 runtime remains a provenance caveat.']
        elif i==21:text += [status]
        elif i==22:text += [decision,'Historical data-source validation phase completed.' if passed else 'Data-source validation not completed; blockers prevent promotion.']
        elif i==23:text += ['[Inference] '+('Eligible as sampled-validated canonical raw TXF 1m source.' if passed else 'Canonical promotion NOT APPROVED.')+' Preserve timestamp/calendar_date/trade_date/session/OHLCVA/actual_contract_month/terminal_boundary_bar/rollover_flag/source_run/git_commit. Never compute returns across contractMonth; no silent filling/back-adjustment or shared-missing volume=0. Aggregate 5/15/60m from 1m using session anchors; retain missing/partial/terminal buckets. Data PASS does not imply strategy efficacy.']
    questions=['2017-06～2026是否穩定可取得','normalized schema跨年份是否穩定','timezone是否穩定','regular mapping是否穩定','afterhours mapping是否穩定','05:00 terminal是否安全','OHLC integrity是否通過','是否有duplicate','是否有active asymmetric missing','contractMonth是否可靠','annual rollover是否full-session raw','是否有back-adjustment','query是否reproducible','aggregation是否deterministic','external是否支持可信度','2017 availability為何','是否单一年貢獻多數anomalies','是否有structural format change','是否可作canonical backtest source','正式dataset restrictions']
    key=[None,'schema','timestamp','session','session','session','ohlc','timestamp','missing','traceability','rollover_fields','adjustment','repeatability','aggregation','external','availability',None,'schema',None,None]
    text+=['','## Final questions']
    for i,q in enumerate(questions):
        if i==18:answer='可以，僅在報告的sampled證據與restrictions內。' if passed else '不可；final gates未全部通過。'
        elif i==19:answer='保留完整provenance与月份identity；禁止跨月return／silent fill／silent adjustment；用1m session-anchor aggregation。'
        elif i in [0,16]:answer=yearly.to_string(index=False)
        else:
            f=tables.get(key[i],pd.DataFrame())
            answer=f.to_string(index=False) if len(f) else 'Unresolved: NOT PERFORMED'
        text += ['',f'{i+1}. {q}？',answer]
    return '\n'.join(text),decision
