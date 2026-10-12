from src.validate_aggregation import aggregate,compare_aggregation

def test_ohlcv_aggregation(bars):
    agg=aggregate(bars,5,bars.timestamp.min())
    assert agg.iloc[0].open==100 and agg.iloc[0].high==106
    assert agg.iloc[0].low==99 and agg.iloc[0].close==105 and agg.iloc[0].volume==10

def test_consistent(bars):
    direct=aggregate(bars,15,bars.timestamp.min())
    direct['trade_date']=bars.trade_date.iloc[0]
    summary,detail=compare_aggregation(bars,direct,15)
    assert summary['status']=='PASS WITH CAVEATS'
    assert detail.equal_ohlcv.all()

def test_mismatch_unresolved(bars):
    direct=aggregate(bars,15,bars.timestamp.min())
    direct['trade_date']=bars.trade_date.iloc[0]
    direct['volume']+=100
    summary,_=compare_aggregation(bars,direct,15)
    assert summary['status']=='UNRESOLVED_ALIGNMENT'

def test_date_mismatch(bars):
    direct=aggregate(bars,5,bars.timestamp.min());direct['trade_date']='2026-10-09'
    assert compare_aggregation(bars,direct,5)[0]['status']=='UNRESOLVED_ALIGNMENT'

def test_partial_unresolved(bars):
    one=bars.iloc[:4];direct=aggregate(one,5,one.timestamp.min());direct['trade_date']=one.trade_date.iloc[0]
    assert compare_aggregation(one,direct,5)[0]['status']=='UNRESOLVED_ALIGNMENT'
