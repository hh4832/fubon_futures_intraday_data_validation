"""Single-window rollover diagnostics. All matching is vectorized; no series construction."""
import json
import numpy as np
import pandas as pd
from .historical_overlap import FIELDS

PRICES = ['open','high','low','close']
ANOMALY_COLUMNS = ['query','timestamp','trade_date','anomaly_type','evidence_type','notes']


def contract_coverage(continuous,cfg,dates):
    c=continuous.sort_values('timestamp',kind='stable').copy()
    c['_old']=c.actual_contract_month.eq(cfg['old_contract']).fillna(False)
    c['_new']=c.actual_contract_month.eq(cfg['new_contract']).fillna(False)
    c['_other']=c.actual_contract_month.notna() & ~c.actual_contract_month.isin([cfg['old_contract'],cfg['new_contract']])
    c['_missing']=c.actual_contract_month.isna()
    grouped=c.groupby('trade_date',dropna=False)
    daily=grouped.agg(first_timestamp=('timestamp','min'),last_timestamp=('timestamp','max'),
        row_count=('timestamp','size'),unique_actual_contract_months=('actual_contract_month','nunique'),
        contains_202609=('_old','any'),contains_202610=('_new','any'),
        other_contract_month_present=('_other','any'),missing_actual_contract_month_rows=('_missing','sum')).reset_index()
    first=c.groupby('trade_date',dropna=False).head(1).set_index('trade_date').actual_contract_month
    last=c.groupby('trade_date',dropna=False).tail(1).set_index('trade_date').actual_contract_month
    daily['first_actual_contract_month']=daily.trade_date.map(first)
    daily['last_actual_contract_month']=daily.trade_date.map(last)
    months=grouped.actual_contract_month.unique().map(lambda values:json.dumps([str(x) for x in values if pd.notna(x)]))
    daily['actual_contract_months']=daily.trade_date.map(months)
    # Missing requested dates stay visible; no replacement dates or bars.
    daily=pd.DataFrame({'trade_date':dates}).merge(daily,on='trade_date',how='outer')
    for col in ['row_count','unique_actual_contract_months','missing_actual_contract_month_rows']:
        daily[col]=daily[col].fillna(0).astype(int)
    for col in ['contains_202609','contains_202610','other_contract_month_present']:
        daily[col]=daily[col].astype('boolean').fillna(False).astype(bool)
    daily['partial_day_switch']=daily.unique_actual_contract_months.gt(1)
    daily['status']=np.select([daily.other_contract_month_present | daily.missing_actual_contract_month_rows.gt(0),
        daily.row_count.eq(0),daily.partial_day_switch],['FAIL','UNRESOLVED','PASS WITH CAVEATS'],default='PASS')
    daily['evidence_type']='Observed fact'
    daily['notes']='observed per-bar identity; empty dates unresolved; no 300-bar requirement'
    return daily


def build_mapping(continuous,old,new,cfg):
    mapping=continuous[['timestamp','trade_date','actual_contract_month','response_contractMonth']+FIELDS].copy()
    mapping=mapping.rename(columns={'actual_contract_month':'continuous_actual_contract_month',
                                   **{f:'continuous_'+f for f in FIELDS}})
    mapping['continuous_contract_query']=cfg['continuous_contract']
    mapping['continuous_duplicate_timestamp']=mapping.timestamp.duplicated(keep=False)
    for label,reference,expected in [('old',old,cfg['old_contract']),('new',new,cfg['new_contract'])]:
        duplicate=reference.timestamp.duplicated(keep=False)
        # Comparison-only projection excludes ambiguous keys. Original data and all bad rows are retained in archives.
        unique=reference.loc[~duplicate,['timestamp','actual_contract_month']+FIELDS].copy()
        unique=unique.rename(columns={'actual_contract_month':label+'_actual_contract_month',
                                     **{f:label+'_'+f for f in FIELDS}})
        unique[label+'_contract_available']=True
        mapping=mapping.merge(unique,on='timestamp',how='left',validate='many_to_one')
        mapping[label+'_contract_available']=mapping[label+'_contract_available'].eq(True)
        mapping[label+'_duplicate_timestamp']=mapping.timestamp.isin(reference.loc[duplicate,'timestamp'])
        identity=mapping[label+'_actual_contract_month'].eq(expected).fillna(False)
        usable=identity & mapping[label+'_contract_available'] & ~mapping.continuous_duplicate_timestamp
        for f in FIELDS:
            a,b=mapping['continuous_'+f],mapping[label+'_'+f]
            finite=np.isfinite(a.to_numpy(dtype=float)) & np.isfinite(b.to_numpy(dtype=float))
            mapping[label+'_'+f+'_comparable']=usable & finite
            mapping[label+'_'+f+'_exact_match']=usable & finite & a.eq(b)
            mapping[f+'_difference_vs_'+label]=a-b
        mapping['continuous_matches_'+label+'_ohlc']=mapping[[label+'_'+f+'_exact_match' for f in PRICES]].all(axis=1)
        mapping['continuous_matches_'+label+'_volume']=mapping[label+'_volume_exact_match']
        mapping['continuous_matches_'+label+'_average']=mapping[label+'_average_exact_match']
    is_old=mapping.continuous_actual_contract_month.eq(cfg['old_contract']).fillna(False)
    is_new=mapping.continuous_actual_contract_month.eq(cfg['new_contract']).fillna(False)
    mapping['expected_contract_available']=(is_old & mapping.old_contract_available & ~mapping.old_duplicate_timestamp) | (is_new & mapping.new_contract_available & ~mapping.new_duplicate_timestamp)
    for f in FIELDS:
        mapping['expected_'+f]=np.select([is_old,is_new],[mapping['old_'+f],mapping['new_'+f]],default=np.nan)
        mapping[f+'_comparable']=np.select([is_old,is_new],[mapping['old_'+f+'_comparable'],mapping['new_'+f+'_comparable']],default=False)
        mapping[f+'_exact_match']=np.select([is_old,is_new],[mapping['old_'+f+'_exact_match'],mapping['new_'+f+'_exact_match']],default=False)
        mapping[f+'_difference_vs_expected']=mapping['continuous_'+f]-mapping['expected_'+f]
    mapping['expected_ohlc_comparable']=mapping[[f+'_comparable' for f in PRICES]].all(axis=1)
    mapping['expected_ohlc_exact_match']=mapping[[f+'_exact_match' for f in PRICES]].all(axis=1)
    exact=mapping.expected_ohlc_exact_match & mapping.volume_exact_match
    mapping['matched_contract']=np.select([exact & is_old,exact & is_new],[cfg['old_contract'],cfg['new_contract']],default='UNRESOLVED')
    both=mapping.continuous_matches_old_ohlc & mapping.continuous_matches_new_ohlc & mapping.continuous_matches_old_volume & mapping.continuous_matches_new_volume
    mapping['match_confidence']=np.select([exact & both,exact],['metadata plus exact OHLCV; both contracts price-equivalent','metadata plus exact OHLCV'],default='UNRESOLVED')
    mapping['evidence_type']='Observed fact'
    mapping['notes']=np.where(both,'value-only identity ambiguous; per-bar metadata retained',
        np.where(~mapping.expected_contract_available,'expected reference timestamp unavailable/duplicate; no fill','expected contract selected from observed metadata; average separate'))
    return mapping.sort_values('timestamp',kind='stable').reset_index(drop=True)


def transition_summary(continuous,coverage,cfg,calendar):
    c=continuous.sort_values('timestamp',kind='stable')
    month=c.actual_contract_month.astype('string')
    previous=month.shift()
    changes=month.notna() & previous.notna() & month.ne(previous)
    old_rows=c[month.eq(cfg['old_contract']).fillna(False)]
    new_rows=c[month.eq(cfg['new_contract']).fillna(False)]
    last_old=old_rows.iloc[-1] if len(old_rows) else None
    first_new=new_rows.iloc[0] if len(new_rows) else None
    valid_transition=(last_old is not None and first_new is not None and last_old.timestamp<first_new.timestamp
        and int(changes.sum())==1 and not c.timestamp.duplicated().any()
        and month.notna().all() and month.isin([cfg['old_contract'],cfg['new_contract']]).all())
    transition_date=str(first_new.trade_date) if first_new is not None else None
    gap=(first_new.timestamp-last_old.timestamp).total_seconds()/60 if first_new is not None and last_old is not None else None
    old_close=float(last_old.close) if last_old is not None else np.nan
    new_open=float(first_new.open) if first_new is not None else np.nan
    price_usable=bool(valid_transition and np.isfinite(old_close) and np.isfinite(new_open) and old_close>0)
    day=coverage[coverage.trade_date.eq(transition_date)] if transition_date else pd.DataFrame()
    first_observed_bar=bool(first_new is not None and not day.empty and first_new.timestamp==day.first_timestamp.iloc[0])
    cal=calendar[calendar.trade_date.eq(transition_date)] if transition_date else pd.DataFrame()
    scheduled_open=bool(first_new is not None and not cal.empty and first_new.timestamp==cal.regular_open.iloc[0])
    return {'last_timestamp_old_contract_in_1bang':str(last_old.timestamp) if last_old is not None else None,
        'last_trade_date_old_contract':str(last_old.trade_date) if last_old is not None else None,
        'first_timestamp_new_contract_in_1bang':str(first_new.timestamp) if first_new is not None else None,
        'first_trade_date_new_contract':transition_date,'transition_gap_minutes':gap,
        'transition_crosses_trade_date':bool(first_new is not None and last_old is not None and first_new.trade_date!=last_old.trade_date),
        'transition_occurs_on_expiry_date':transition_date==cfg['expiry_date'] if transition_date else None,
        'transition_occurs_after_expiry_date':transition_date>cfg['expiry_date'] if transition_date else None,
        'transition_occurs_before_expiry_date':transition_date<cfg['expiry_date'] if transition_date else None,
        'first_observed_bar_of_day':first_observed_bar,'matches_scheduled_regular_open':scheduled_open,
        'transition_type':'full-day switch' if valid_transition and first_observed_bar and not bool(day.partial_day_switch.iloc[0]) else 'intraday switch' if valid_transition else 'UNRESOLVED',
        'identity_transition_count':int(changes.sum()),'transition_confirmed_in_sample':bool(valid_transition),
        'old_last_close':old_close,'new_first_open':new_open,
        'roll_gap_points':new_open-old_close if price_usable else np.nan,
        'roll_gap_return':new_open/old_close-1 if price_usable else np.nan,
        'status':'PASS WITH CAVEATS' if valid_transition else 'UNRESOLVED',
        'evidence_type':'Observed fact','notes':'observed identity boundary only; timestamp gap can include closed sessions; contract transition gap is not market return or proof of rollover rule'}


def adjustment_test(mapping,transition,cfg,calendar):
    records=[]
    date=transition.get('first_trade_date_new_contract')
    scheduled=calendar.trade_date.sort_values()
    before=scheduled[scheduled<date] if date else pd.Series(dtype=str)
    after=scheduled[scheduled>date] if date else pd.Series(dtype=str)
    anchors=[('pre_roll',before.iloc[-1] if len(before) else None),('roll_day',date),('post_roll',after.iloc[0] if len(after) else None)]
    for segment,anchor in anchors:
        rows=mapping[mapping.trade_date.eq(anchor)].copy() if anchor else mapping.iloc[:0].copy()
        if segment=='pre_roll':rows=rows[rows.continuous_actual_contract_month.eq(cfg['old_contract']).fillna(False)]
        else:rows=rows[rows.continuous_actual_contract_month.eq(cfg['new_contract']).fillna(False)]
        comparable=rows.expected_ohlc_comparable
        a=rows.loc[comparable,['continuous_'+f for f in PRICES]].to_numpy(dtype=float)
        b=rows.loc[comparable,['expected_'+f for f in PRICES]].to_numpy(dtype=float)
        diff=(a-b).ravel();ratio=(a/b).ravel() if b.size and (b>0).all() else np.array([])
        n=len(a);exact_rows=int(rows.loc[comparable,'expected_ohlc_exact_match'].sum())
        complete=n==len(rows) and n>=cfg['minimum_adjustment_rows']
        raw=complete and exact_rows==n
        median=float(np.median(diff)) if diff.size else np.nan
        std=float(np.std(diff)) if diff.size else np.nan
        median_ratio=float(np.median(ratio)) if ratio.size else np.nan
        additive=bool(complete and not raw and np.all(np.isclose(diff,median,atol=cfg['adjustment_offset_atol'],rtol=0)))
        proportional=bool(complete and not raw and ratio.size and np.all(np.isclose(ratio,median_ratio,atol=cfg['adjustment_ratio_atol'],rtol=0)))
        interpretation=('raw / unadjusted prices in this sampled segment' if raw else
            'constant offset and proportional models both fit; transformation mechanism ambiguous' if additive and proportional else
            'consistent with an additive adjusted segment; direction/methodology unproven' if additive else
            'consistent with a proportional adjusted segment; methodology unproven' if proportional else
            'unexplained differences or insufficient/missing reference bars; no adjustment inferred')
        records.append({'segment':segment,'anchor_trade_date':anchor,'actual_contract_month':cfg['old_contract'] if segment=='pre_roll' else cfg['new_contract'],
            'expected_rows':len(rows),'compared_rows':n,'exact_match_rows':exact_rows,
            'constant_offset_detected':additive,'median_price_difference':median,'std_price_difference':std,
            'ratio_adjustment_detected':proportional,'median_price_ratio':median_ratio,
            'complete_segment_comparison':complete,'status':'PASS' if raw else 'UNRESOLVED',
            'evidence_type':'Inference' if raw or additive or proportional else 'Unresolved','interpretation':interpretation})
    return pd.DataFrame(records)


def field_summary(mapping):
    rows=[]
    for f in FIELDS:
        comparable=mapping[f+'_comparable'];exact=mapping[f+'_exact_match']
        n=int(comparable.sum());mismatch=int((comparable & ~exact).sum())
        unavailable=len(mapping)-n
        rows.append({'field':f,'continuous_rows':len(mapping),'compared_rows':n,
            'exact_match_rows':int(exact.sum()),'mismatch_rows':mismatch,'unavailable_rows':unavailable,
            'match_rate':float(exact.sum()/n) if n else np.nan,
            'max_abs_difference':float(mapping.loc[comparable,f+'_difference_vs_expected'].abs().max()) if n else np.nan,
            'status':'UNRESOLVED' if not n or unavailable else 'PASS WITH CAVEATS' if mismatch else 'PASS',
            'evidence_type':'Observed fact' if n else 'Unresolved','notes':'exact comparison against contract specified by per-bar metadata; average evaluated separately'})
    return pd.DataFrame(rows)


def volume_and_daily(frames,mapping,coverage,transition,cfg,dates):
    volume=pd.DataFrame({'trade_date':dates})
    for label,df in [('old',frames[cfg['old_contract']]),('new',frames[cfg['new_contract']])]:
        totals=df.groupby('trade_date').volume.sum(min_count=1)
        volume[label+'_contract_volume']=volume.trade_date.map(totals)
    volume['new_minus_old']=volume.new_contract_volume-volume.old_contract_volume
    volume['new_over_old']=volume.new_contract_volume/volume.old_contract_volume.where(volume.old_contract_volume>0)
    metadata=coverage.set_index('trade_date')
    volume['continuous_actual_contract_month']=volume.trade_date.map(metadata.actual_contract_months)
    previous=volume.new_minus_old.shift()
    known=volume.new_minus_old.notna() & previous.notna()
    volume['observed_volume_leadership_crossover']=known & volume.new_minus_old.gt(0) & previous.le(0)
    winner=np.select([volume.new_minus_old.gt(0),volume.new_minus_old.lt(0)],[cfg['new_contract'],cfg['old_contract']],default='UNRESOLVED')
    volume['observed_minute_volume_leader']=winner
    single=coverage.set_index('trade_date').unique_actual_contract_months.eq(1)
    actual=volume.trade_date.map(metadata.first_actual_contract_month).astype('string')
    comparable=volume.new_minus_old.notna() & volume.new_minus_old.ne(0) & volume.trade_date.map(single).astype('boolean').fillna(False)
    volume['simple_daily_max_rule_counterexample']=comparable & actual.ne(volume.observed_minute_volume_leader.astype('string')).fillna(False)
    volume['evidence_type']='Observed fact'
    volume['notes']='observed minute-candle sums only; not external full-day TAIFEX volume; unavailable expired-contract volume stays NaN'
    crossover_dates=volume.loc[volume.observed_volume_leadership_crossover,'trade_date'].tolist()
    transition['observed_volume_crossover_dates']=json.dumps(crossover_dates)
    transition['transition_matches_observed_volume_crossover']=transition.get('first_trade_date_new_contract') in crossover_dates if crossover_dates else None
    transition['simple_daily_max_rule_counterexamples']=int(volume.simple_daily_max_rule_counterexample.sum())
    transition['volume_rule_interpretation']=('inconsistent with simple same-day maximum observed minute-volume selection' if volume.simple_daily_max_rule_counterexample.any() else
        'consistent with one observed volume crossover; not proof of a provider volume rule' if transition.get('first_trade_date_new_contract') in crossover_dates else
        'UNRESOLVED: crossover not observed/aligned in this fixed window; missing totals do not establish a leadership change')
    daily=coverage[['trade_date','actual_contract_months','row_count','partial_day_switch','status','notes']].rename(
        columns={'actual_contract_months':'continuous_actual_contract_month','row_count':'continuous_row_count'}).copy()
    for label,contract in [('old',cfg['old_contract']),('new',cfg['new_contract'])]:
        counts=frames[contract].groupby('trade_date').size()
        daily[label+'_contract_row_count']=daily.trade_date.map(counts).fillna(0).astype(int)
        totals=frames[contract].groupby('trade_date').volume.sum(min_count=1)
        daily[label+'_daily_volume']=daily.trade_date.map(totals)
    matches=mapping.groupby('trade_date').expected_ohlc_exact_match.all()
    daily['continuous_matches_expected_contract']=daily.trade_date.map(matches).astype('boolean')
    daily['roll_transition_day']=daily.trade_date.eq(transition.get('first_trade_date_new_contract'))
    bad=mapping.assign(_bad=mapping.expected_ohlc_comparable & ~mapping.expected_ohlc_exact_match).groupby('trade_date')._bad.any()
    missing=mapping.assign(_missing=~mapping.expected_contract_available).groupby('trade_date')._missing.any()
    daily.loc[daily.trade_date.map(bad).astype('boolean').fillna(False) | daily.trade_date.map(missing).astype('boolean').fillna(False),'status']='UNRESOLVED'
    daily['evidence_type']='Observed fact'
    return volume,daily


def anomaly_rows(frames,mapping,coverage,cfg):
    groups=[]
    def record(df,mask,query,kind,notes):
        selected=df.loc[mask,['timestamp','trade_date']].copy()
        if not selected.empty:
            selected['query']=query;selected['anomaly_type']=kind
            selected['evidence_type']='Observed fact';selected['notes']=notes
            groups.append(selected)
    allowed=[cfg['old_contract'],cfg['new_contract']]
    for query,df in frames.items():
        record(df,df.timestamp.duplicated(keep=False),query,'duplicate_timestamp','all duplicate source bars retained; ambiguous references excluded only from comparison projection')
        record(df,df.actual_contract_month.isna(),query,'missing_actual_contract_month','no identity guessed')
        wrong=df.actual_contract_month.notna() & (~df.actual_contract_month.isin(allowed) if query=='1!' else df.actual_contract_month.ne(query))
        record(df,wrong,query,'unexpected_contract_month','query/actual contract inconsistency; no substitution')
        delta=df.sort_values('timestamp').groupby('trade_date').timestamp.diff().dt.total_seconds()/60
        record(df,delta.gt(1),query,'missing_timestamp','observed intraday gap only; no-trade suppression vs provider omission unconfirmed')
    partial=coverage.loc[coverage.partial_day_switch,'trade_date']
    record(mapping,mapping.trade_date.isin(partial),'1!','partial_day_contract_switch','observed multi-contract day; not automatically a data error')
    record(mapping,mapping.expected_ohlc_comparable & ~mapping.expected_ohlc_exact_match,'1!','matched_contract_ohlc_mismatch','exact raw OHLC mismatch; see adjustment test')
    record(mapping,mapping.volume_comparable & ~mapping.volume_exact_match,'1!','volume_mismatch','exact volume mismatch; no volume correction')
    record(mapping,~mapping.expected_contract_available,'1!','missing_timestamp','expected reference timestamp unavailable or duplicate; no fill')
    record(mapping,mapping.expected_ohlc_comparable & ~mapping.expected_ohlc_exact_match,'1!','unexplained_price_adjustment','formal transformation unknown; offset/ratio hypotheses are diagnostics only')
    # Reference bars absent from 1! are visible when that day has one observed declared month.
    for label,query in [('old',cfg['old_contract']),('new',cfg['new_contract'])]:
        days=coverage.loc[coverage.unique_actual_contract_months.eq(1) & coverage.first_actual_contract_month.eq(query),'trade_date']
        df=frames[query]
        record(df,df.trade_date.isin(days) & ~df.timestamp.isin(mapping.timestamp),'1!','missing_timestamp','reference timestamp absent from 1! on an observed single-contract day; absence cause unresolved')
    return pd.concat(groups,ignore_index=True)[ANOMALY_COLUMNS] if groups else pd.DataFrame(columns=ANOMALY_COLUMNS)


def analyze_rollover(frames,cfg,calendar,dates):
    continuous=frames[cfg['continuous_contract']]
    coverage=contract_coverage(continuous,cfg,dates)
    mapping=build_mapping(continuous,frames[cfg['old_contract']],frames[cfg['new_contract']],cfg)
    transition=transition_summary(continuous,coverage,cfg,calendar)
    adjustment=adjustment_test(mapping,transition,cfg,calendar)
    volumes,daily=volume_and_daily(frames,mapping,coverage,transition,cfg,dates)
    fields=field_summary(mapping)
    anomalies=anomaly_rows(frames,mapping,coverage,cfg)
    price_columns=['timestamp','continuous_actual_contract_month']+['continuous_'+f for f in PRICES]+[
        label+'_'+f for label in ['old','new'] for f in PRICES]+['continuous_matches_old_ohlc','continuous_matches_new_ohlc','close_difference_vs_old','close_difference_vs_new']
    price=mapping[price_columns].rename(columns={'continuous_actual_contract_month':'continuous_contract_month',
        'continuous_matches_old_ohlc':'match_old_ohlc','continuous_matches_new_ohlc':'match_new_ohlc',
        'close_difference_vs_old':'price_difference_vs_old','close_difference_vs_new':'price_difference_vs_new'})
    price['difference_definition']='price_difference_vs_old/new = close difference; OHLC exact flags use all four price fields'
    structural=anomalies.anomaly_type.isin(['unexpected_contract_month','duplicate_timestamp','missing_actual_contract_month']).any()
    raw_supported=(transition['transition_confirmed_in_sample'] and adjustment.status.eq('PASS').all()
        and mapping.expected_ohlc_exact_match.all() and mapping.volume_exact_match.all()
        and not coverage.row_count.eq(0).any())
    transition['adjustment_test_result']='raw / unadjusted concatenation supported in sampled window' if raw_supported else 'UNRESOLVED; see segment offset/ratio diagnostics'
    transition['roll_gap_retained_supported_in_sample']=bool(raw_supported)
    transition['provider_rollover_rule']='UNRESOLVED: expiry/volume/OI rules not uniquely identified from one window'
    quality='FAIL' if structural else 'PASS WITH CAVEATS' if raw_supported else 'UNRESOLVED'
    return {'rollover_contract_coverage':coverage,'rollover_mapping':mapping,
        'rollover_transition_summary':pd.DataFrame([transition]),'rollover_price_comparison':price,
        'rollover_volume_comparison':volumes,'rollover_adjustment_test':adjustment,
        'rollover_daily_summary':daily,'rollover_anomalies':anomalies,'rollover_field_comparison':fields},quality
