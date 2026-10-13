"""Synthetic single-roll evidence tests; no credentials or SDK required."""
import numpy as np
import pandas as pd
import pytest
from src.continuous_rollover import load_rollover_config,empty_rollover_frame
from src.continuous_rollover_analysis import analyze_rollover


def synthetic_frames(intraday=False):
    dates=['2026-09-15','2026-09-16','2026-09-17','2026-09-18','2026-09-21','2026-09-22']
    timestamp=pd.DatetimeIndex([pd.Timestamp(d+'T08:45:00+08:00')+pd.Timedelta(minutes=m) for d in dates for m in range(4)]).tz_convert('Asia/Taipei')
    def frame(month,base):
        prices=base+np.arange(len(timestamp),dtype=float)
        return pd.DataFrame({'timestamp':timestamp,'trade_date':timestamp.strftime('%Y-%m-%d'),
            'actual_contract_month':month,'response_contractMonth':month,
            'open':prices,'high':prices+2,'low':prices-1,'close':prices+1,
            'volume':10.0,'average':prices+0.5})
    old=frame('202609',100);new=frame('202610',103)
    continuous=old.copy()
    after=continuous.trade_date.ge('2026-09-17')
    if intraday:
        after=continuous.trade_date.gt('2026-09-17') | (continuous.trade_date.eq('2026-09-17') & continuous.timestamp.dt.minute.ge(47))
    for col in ['open','high','low','close','volume','average','actual_contract_month']:
        continuous.loc[after,col]=new.loc[after,col]
    continuous['response_contractMonth']='1!'
    calendar=pd.DataFrame({'trade_date':dates,'regular_open':pd.DatetimeIndex([d+'T08:45:00+08:00' for d in dates]).tz_convert('Asia/Taipei')})
    return {'1!':continuous,'202609':old,'202610':new},calendar,dates


def test_observed_full_day_transition_and_exact_matches():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    transition=tables['rollover_transition_summary'].iloc[0]
    assert transition.first_trade_date_new_contract=='2026-09-17'
    assert transition.transition_type=='full-day switch'
    assert transition.transition_occurs_after_expiry_date
    assert status=='PASS WITH CAVEATS'
    assert tables['rollover_field_comparison'].match_rate.eq(1).all()
    assert tables['rollover_mapping'].matched_contract.isin(['202609','202610']).all()
    assert tables['rollover_adjustment_test'].exact_match_rows.eq(4).all()


def test_intraday_switch_and_anomaly():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames(intraday=True)
    tables,_=analyze_rollover(frames,cfg,calendar,dates)
    assert tables['rollover_transition_summary'].iloc[0].transition_type=='intraday switch'
    assert tables['rollover_anomalies'].anomaly_type.eq('partial_day_contract_switch').any()


def test_raw_gap_calculation():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    tables,_=analyze_rollover(frames,cfg,calendar,dates)
    row=tables['rollover_transition_summary'].iloc[0]
    old=frames['1!'].loc[frames['1!'].actual_contract_month.eq('202609')].iloc[-1].close
    new=frames['1!'].loc[frames['1!'].actual_contract_month.eq('202610')].iloc[0].open
    assert row.roll_gap_points==new-old and row.roll_gap_return==new/old-1
    assert row.roll_gap_retained_supported_in_sample


def test_exact_price_no_tolerance():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['1!'].loc[0,'close']+=1e-10
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert not tables['rollover_mapping'].iloc[0].expected_ohlc_exact_match
    assert tables['rollover_anomalies'].anomaly_type.eq('matched_contract_ohlc_mismatch').any()
    assert status=='UNRESOLVED'


def test_volume_mismatch_and_average_separate():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['1!'].loc[0,'volume']+=1
    frames['1!'].loc[1,'average']+=0.1
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert tables['rollover_field_comparison'].set_index('field').loc['volume','mismatch_rows']==1
    assert tables['rollover_field_comparison'].set_index('field').loc['average','mismatch_rows']==1
    assert tables['rollover_anomalies'].anomaly_type.eq('volume_mismatch').any()
    assert status=='UNRESOLVED'


def test_constant_additive_detection():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    old_segment=frames['1!'].actual_contract_month.eq('202609')
    for col in ['open','high','low','close']:frames['1!'].loc[old_segment,col]+=2
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    segment=tables['rollover_adjustment_test'].set_index('segment').loc['pre_roll']
    assert segment.constant_offset_detected and segment.median_price_difference==2
    assert not segment.ratio_adjustment_detected
    assert status=='UNRESOLVED'


def test_ratio_adjustment_detection():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    before=frames['1!'].actual_contract_month.eq('202609')
    for col in ['open','high','low','close']:frames['1!'].loc[before,col]*=1.02
    tables,_=analyze_rollover(frames,cfg,calendar,dates)
    segment=tables['rollover_adjustment_test'].set_index('segment').loc['pre_roll']
    assert segment.ratio_adjustment_detected
    assert segment.median_price_ratio==pytest.approx(1.02)
    assert not segment.constant_offset_detected


def test_no_adjustment_all_three_anchor_days():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    tables,_=analyze_rollover(frames,cfg,calendar,dates)
    assert tables['rollover_adjustment_test'].status.eq('PASS').all()
    assert not tables['rollover_adjustment_test'].constant_offset_detected.any()
    assert not tables['rollover_adjustment_test'].ratio_adjustment_detected.any()


def test_unexpected_third_contract():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['1!'].loc[0,'actual_contract_month']='202611'
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert status=='FAIL'
    assert tables['rollover_contract_coverage'].other_contract_month_present.any()
    assert tables['rollover_anomalies'].anomaly_type.eq('unexpected_contract_month').any()


def test_duplicate_keys_retained_without_join_explosion():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['202609']=pd.concat([frames['202609'],frames['202609'].iloc[[0]]],ignore_index=True)
    original_rows=len(frames['1!'])
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert len(tables['rollover_mapping'])==original_rows
    assert len(frames['202609'])==original_rows+1
    assert status=='FAIL' and tables['rollover_mapping'].iloc[0].old_duplicate_timestamp


def test_missing_expected_reference_no_fill():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['202609']=frames['202609'].iloc[1:].copy()
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert not tables['rollover_mapping'].iloc[0].old_contract_available
    assert np.isnan(tables['rollover_mapping'].iloc[0].old_open)
    assert status=='UNRESOLVED'


def test_expired_volume_unavailable_not_zero():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['202609']=frames['202609'][frames['202609'].trade_date.le('2026-09-16')].copy()
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    after=tables['rollover_volume_comparison'].trade_date.gt('2026-09-16')
    assert tables['rollover_volume_comparison'].loc[after,'old_contract_volume'].isna().all()
    assert status=='PASS WITH CAVEATS'


def test_empty_endpoint_and_missing_identity():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['202609']=empty_rollover_frame(cfg)
    _,status=analyze_rollover(frames,cfg,calendar,dates)
    assert status=='UNRESOLVED'
    frames['1!'].loc[0,'actual_contract_month']=pd.NA
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert status=='FAIL' and tables['rollover_anomalies'].anomaly_type.eq('missing_actual_contract_month').any()


def test_all_empty_writes_schemas():
    cfg=load_rollover_config();_,calendar,dates=synthetic_frames()
    frames={c:empty_rollover_frame(cfg) for c in ['1!','202609','202610']}
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    assert status=='UNRESOLVED' and len(tables)==9
    assert tables['rollover_mapping'].empty and len(tables['rollover_contract_coverage'])==len(dates)


def test_missing_roll_anchor_not_replaced():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['1!']=frames['1!'][frames['1!'].trade_date.ne('2026-09-18')].copy()
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    segment=tables['rollover_adjustment_test'].set_index('segment').loc['post_roll']
    assert segment.anchor_trade_date=='2026-09-18' and segment.compared_rows==0
    assert status=='UNRESOLVED'


def test_reversion_does_not_create_false_roll_return():
    cfg=load_rollover_config();frames,calendar,dates=synthetic_frames()
    frames['1!'].loc[len(frames['1!'])-1,'actual_contract_month']='202609'
    tables,status=analyze_rollover(frames,cfg,calendar,dates)
    row=tables['rollover_transition_summary'].iloc[0]
    assert not row.transition_confirmed_in_sample
    assert np.isnan(row.roll_gap_return) and status=='UNRESOLVED'
