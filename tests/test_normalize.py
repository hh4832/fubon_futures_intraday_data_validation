import pandas as pd
from src.normalize import normalize

def test_timezone_numeric_sort_duplicates():
    rows = [{'date':t,'open':'100','high':'102','low':'99','close':'101','volume':'2','custom':'kept'}
            for t in ['2026-10-12T08:46:00+08:00','2026-10-12T08:45:00+08:00','2026-10-12T08:45:00+08:00']]
    df=normalize({'date':'2026-10-12','data':rows},'SYNTHETIC','regular',1)
    assert str(df.timestamp.dt.tz)=='Asia/Taipei'
    assert pd.api.types.is_numeric_dtype(df.open)
    assert df.timestamp.is_monotonic_increasing
    assert df.timestamp.duplicated().sum()==1
    assert df.source_row.tolist()==[1,2,0]
    assert df.custom.eq('kept').all()

def test_naive_timestamp_rejected():
    df=normalize({'data':[{'date':'2026-10-12T08:45:00'}]},'SYNTHETIC','regular',1)
    assert df.timestamp.isna().all()
    assert not df.timestamp_source_aware.any()

def test_empty_and_invalid_numeric():
    assert normalize({'data':[]},'SYNTHETIC','regular',1).empty
    df=normalize({'data':[{'date':'2026-10-12T08:45:00+08:00','open':'bad'}]},'SYNTHETIC','regular',1)
    assert df.open.isna().all() and df.raw_open.iloc[0]=='bad'
