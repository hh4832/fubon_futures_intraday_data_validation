import pandas as pd
import pytest
from src.historical_client import HistoricalFatalError
from src.historical_normalization import normalize_historical


def calendar_fixture():
    records=[]
    for date,previous in [('2026-10-08','2026-10-07'),('2026-10-12','2026-10-08'),('2026-10-13','2026-10-12')]:
        records.append(dict(trade_date=date,regular_open=pd.Timestamp(date+'T08:45:00+08:00'),
            regular_close=pd.Timestamp(date+'T13:45:00+08:00'),
            afterhours_open=pd.Timestamp(previous+'T15:00:00+08:00'),
            afterhours_close=pd.Timestamp(previous+'T15:00:00+08:00').normalize()+pd.Timedelta(days=1,hours=5),
            calendar_source='https://example.test/synthetic-calendar'))
    return pd.DataFrame(records)


def item_fixture(times,session='regular',month='1!',actual='202610'):
    return {'name':'synthetic','params':{'product':'TXF','contractMonth':month,'session':session,
        'timeframe':'1','from':'2026-10-08','to':'2026-10-13','sort':'asc'},
        'requested_dates':['2026-10-08','2026-10-12','2026-10-13'],
        'response':{'product':'TXF','contractMonth':month,'exchange':'TAIFEX','session':session.upper(),
            'timeframe':'1','sort':'asc','data':[{'date':t,'contractMonth':actual,
                'open':'100','high':'102','low':'99','close':'101','volume':'2','average':'100.5','extra':'retained'} for t in times]}}


def test_historical_timezone_month_and_extra():
    item=item_fixture(['2026-10-12T00:45:00Z','2026-10-12T00:46:00Z'])
    df=normalize_historical(item,calendar_fixture())
    assert str(df.timestamp.dt.tz)=='Asia/Taipei'
    assert df.timestamp.iloc[0].hour==8
    assert df.trade_date.eq('2026-10-12').all()
    assert df.contract_query.eq('1!').all() and df.actual_contract_month.eq('202610').all()
    assert df.extra.eq('retained').all()
    assert df.open.iloc[0]==100 and df.raw_open.iloc[0]=='100'


def test_duplicates_retained():
    item=item_fixture(['2026-10-12T08:45:00+08:00']*2)
    df=normalize_historical(item,calendar_fixture())
    assert len(df)==2 and df.timestamp.duplicated().sum()==1


def test_cross_midnight_holiday_mapping():
    # Night before long holiday belongs to next scheduled trading day, not next calendar date.
    item=item_fixture(['2026-10-08T23:59:00+08:00','2026-10-09T00:00:00+08:00'],'afterhours')
    df=normalize_historical(item,calendar_fixture())
    assert df.trade_date.eq('2026-10-12').all()
    assert df.calendar_date.nunique()==2


def test_no_calendar_guess():
    item=item_fixture(['2026-10-13T15:00:00+08:00'],'afterhours')
    df=normalize_historical(item,calendar_fixture())
    assert df.trade_date.isna().all()


@pytest.mark.parametrize('value',['2026-10-12T08:45:00','bad',None])
def test_unsafe_timestamps_stop(value):
    with pytest.raises(HistoricalFatalError):normalize_historical(item_fixture([value]),calendar_fixture())
