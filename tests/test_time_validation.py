import pandas as pd
from src.validate_time import validate_time

def test_gaps_not_missing(bars):
    summary,gaps=validate_time(bars.drop(index=[2,3]))
    assert summary['observed_gaps']==1
    assert not gaps.confirmed_missing_data.any()
    assert summary['confirmed_missing_data']=='unconfirmed'

def test_duplicates(bars):
    summary,_=validate_time(pd.concat([bars,bars.iloc[[0]]]))
    assert summary['duplicate_timestamp']==1 and summary['status']=='FAIL'

def test_original_sequence(bars):
    bars['source_row']=bars.source_row.astype(float)
    bars.loc[0,'source_row']=5.5
    summary,_=validate_time(bars)
    assert summary['non_monotonic']

def test_descending_valid(bars):
    bars['source_row']=bars.source_row.iloc[::-1].to_numpy()
    summary,_=validate_time(bars)
    assert summary['descending'] and not summary['non_monotonic']
