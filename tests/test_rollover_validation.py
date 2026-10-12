import pandas as pd
from src.rollover_validation import rollover_mapping


def make(values,months):
    return pd.DataFrame({'timestamp':pd.date_range('2026-09-16 08:45',periods=4,freq='min',tz='Asia/Taipei'),
        'trade_date':['2026-09-16']*2+['2026-09-17']*2,'actual_contract_month':months,
        'open':values,'high':[x+1 for x in values],'low':[x-1 for x in values],
        'close':values,'volume':[10]*4,'average':values})


def test_raw_concatenation_roll_gap():
    old=make([100,101,102,103],['202609']*4)
    new=make([101,102,103,104],['202610']*4)
    continuous=make([100,101,103,104],['202609']*2+['202610']*2)
    mapping,summary=rollover_mapping(continuous,old,new,'202609','202610')
    assert summary['price_transformation_result']=='raw concatenation / roll gap retained'
    assert summary['raw_roll_gap']==2
    assert mapping.inferred_underlying_contract.tolist()==['202609','202609','202610','202610']
    assert mapping.roll_transition_flag.sum()==1


def test_transformation_not_back_adjustment_claim():
    old=make([100,101,102,103],['202609']*4)
    new=make([101,102,103,104],['202610']*4)
    continuous=make([102,103,103,104],['202609']*2+['202610']*2)
    _,summary=rollover_mapping(continuous,old,new,'202609','202610')
    assert summary['price_transformation_result']=='transformed / requires further inference'
    assert summary['status']=='UNRESOLVED'
    assert summary['back_adjustment']=='UNRESOLVED'


def test_identical_raw_contracts_ambiguous():
    old=make([100]*4,['202609']*4);new=make([100]*4,['202610']*4)
    continuous=make([100]*4,['202609']*2+['202610']*2)
    _,summary=rollover_mapping(continuous,old,new,'202609','202610')
    assert summary['ambiguous_rows']==4 and summary['status']=='UNRESOLVED'


def test_volume_mismatch_blocks_proof():
    old=make([100,101,102,103],['202609']*4);new=make([101,102,103,104],['202610']*4)
    continuous=make([100,101,103,104],['202609']*2+['202610']*2)
    continuous.volume+=1
    _,summary=rollover_mapping(continuous,old,new,'202609','202610')
    assert summary['status']=='UNRESOLVED'
