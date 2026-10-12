from src.validate_ohlc import validate_ohlc,average_diagnostics

def test_valid(bars):
    summary,invalid=validate_ohlc(bars)
    assert summary['status']=='PASS' and invalid.empty

def test_invalid(bars):
    bars.loc[0,'high']=bars.loc[0,'close']-1
    bars.loc[1,'low']=bars.loc[1,'open']+1
    bars.loc[2,'open']=-1
    bars.loc[3,'volume']=-1
    bars.loc[4,'average']=0
    summary,invalid=validate_ohlc(bars)
    assert len(invalid)==5 and summary['status']=='FAIL'
    assert summary['invalid_high']>=1 and summary['invalid_low']>=1
    assert summary['nonpositive_price']==1

def test_average_unclear(bars):
    bars.loc[0,'average']=9999
    d=average_diagnostics(bars)
    assert d['outside_bar_range']==1 and d['conclusion']=='unclear'
