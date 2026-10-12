"""Vectorized matching to observed raw contracts; never adjust prices."""
import numpy as np
import pandas as pd
from .historical_overlap import FIELDS


def rollover_mapping(continuous,old,new,old_contract,new_contract,expiry_date=None):
    keys=['timestamp']
    essential=['timestamp','trade_date','actual_contract_month']+FIELDS
    if continuous.empty:
        return pd.DataFrame(columns=essential), {'status':'UNRESOLVED','evidence_type':'Unresolved','notes':'no continuous bars'}
    if not old.actual_contract_month.eq(old_contract).all() or not new.actual_contract_month.eq(new_contract).all():
        return pd.DataFrame(columns=essential), {'status':'FAIL','evidence_type':'Observed fact','notes':'concrete contract response identity mismatch'}
    if any(df.timestamp.duplicated().any() for df in [continuous,old,new]):
        return pd.DataFrame(columns=essential), {'status':'FAIL','evidence_type':'Observed fact','notes':'duplicate timestamps; no repair'}
    mapping=continuous[essential].copy().sort_values('timestamp')
    for name,df in [('old',old),('new',new)]:
        subset=df[['timestamp']+FIELDS].rename(columns={f:f+'_'+name for f in FIELDS})
        mapping=mapping.merge(subset,on='timestamp',how='left',validate='one_to_one')
        prices=pd.concat([mapping[f].eq(mapping[f+'_'+name]) & mapping[f].notna() & mapping[f+'_'+name].notna() for f in FIELDS[:4]],axis=1).all(axis=1)
        volume=mapping.volume.eq(mapping['volume_'+name]) & mapping.volume.notna()
        mapping['prices_match_'+name]=prices
        mapping['volume_matches_'+name]=volume
        mapping['continuous_matches_'+name]=prices & volume
    old_match=mapping.continuous_matches_old
    new_match=mapping.continuous_matches_new
    ambiguous=old_match & new_match
    unique=old_match ^ new_match
    metadata=mapping.actual_contract_month.astype('string')
    inferred=np.select([old_match & ~new_match,new_match & ~old_match],[old_contract,new_contract],default='UNRESOLVED')
    mapping['continuous_contract']=metadata
    mapping['matched_old_contract']=old_contract
    mapping['matched_new_contract']=new_contract
    mapping['inferred_underlying_contract']=inferred
    mapping['metadata_match']=pd.Series(inferred,index=mapping.index,dtype='string').eq(metadata).fillna(False)
    # Both contracts matching is ambiguous even when metadata declares one.
    mapping['confidence']=np.where(unique & mapping.metadata_match,'matched raw OHLCV + metadata','UNRESOLVED')
    mapping['evidence_type']='Observed fact'
    mapping['notes']=np.where(ambiguous,'both raw contracts match; identity ambiguous',
        np.where(unique & ~mapping.metadata_match,'metadata/value mismatch','raw values retained; no adjustment applied'))
    mapping['roll_transition_flag']=metadata.ne(metadata.shift()) & metadata.notna() & metadata.shift().notna()
    transitions=mapping[mapping.roll_transition_flag]
    have_old=bool((unique & old_match & mapping.metadata_match).any())
    have_new=bool((unique & new_match & mapping.metadata_match).any())
    price_mismatch=~(mapping.prices_match_old | mapping.prices_match_new)
    complete_proof=have_old and have_new and len(transitions)==1 and bool((unique & mapping.metadata_match).all())
    result='raw concatenation / roll gap retained' if complete_proof else 'transformed / requires further inference' if price_mismatch.any() else 'UNRESOLVED'
    boundary=transitions.timestamp.iloc[0] if len(transitions)==1 else None
    previous_close=None;next_open=None
    if boundary is not None:
        before=mapping[mapping.timestamp<boundary]
        if not before.empty:
            previous_close=float(before.close.iloc[-1]);next_open=float(transitions.open.iloc[0])
    summary={'status':'PASS WITH CAVEATS' if complete_proof else 'UNRESOLVED',
        'evidence_type':'Inference' if complete_proof else 'Unresolved',
        'price_transformation_result':result,'first_transition_timestamp':str(boundary),
        'transition_trade_date':transitions.trade_date.iloc[0] if len(transitions)==1 else None,
        'transition_count':len(transitions),'uniquely_matched_rows':int(unique.sum()),
        'ambiguous_rows':int(ambiguous.sum()),'metadata_mismatch_rows':int((unique & ~mapping.metadata_match).sum()),
        'raw_roll_gap':next_open-previous_close if next_open is not None else None,
        'expiry_reference':expiry_date,'rollover_rule':'UNRESOLVED; one window cannot establish universal expiry vs volume trigger',
        'session_rollover_synchronization':'UNRESOLVED until same rollover window afterhours comparison',
        'back_adjustment':'supports no adjustment in sampled segments only' if complete_proof else 'UNRESOLVED',
        'notes':'no historical segment rewritten; transition timing limited to first observed bar; price gap includes market move'}
    return mapping,summary
