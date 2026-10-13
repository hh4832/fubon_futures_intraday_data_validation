"""Only an exact scheduled night close is accepted; no widening or price changes."""
import pandas as pd
import pytest
from src.historical_normalization import normalize_historical,map_afterhours_trade_dates
from src.historical_client import HistoricalFatalError
from test_historical_normalization import calendar_fixture,item_fixture


def test_terminal_close_next_regular_trade_date_and_0501_outside():
    times=['2026-10-08T15:00:00+08:00','2026-10-09T04:59:00+08:00','2026-10-09T05:00:00+08:00','2026-10-09T05:01:00+08:00']
    df=normalize_historical(item_fixture(times,'afterhours'),calendar_fixture())
    assert df.trade_date.iloc[:3].eq('2026-10-12').all()
    assert pd.isna(df.trade_date.iloc[3])
    assert df.terminal_boundary_bar.tolist()==[False,False,True,False]
    assert df.timestamp_raw.tolist()==times
    assert df.volume.eq(2).all() and df.raw_open.eq('100').all()
    assert df.calendar_date.iloc[2]=='2026-10-09' # holiday; not guessed as trade date


def test_terminal_mapping_preserves_all_non_derived_fields_and_duplicates():
    item=item_fixture(['2026-10-09T05:00:00+08:00']*2,'afterhours')
    df=normalize_historical(item,calendar_fixture())
    after=map_afterhours_trade_dates(df,calendar_fixture())
    pd.testing.assert_frame_equal(df,after)
    assert len(after)==2 and after.timestamp.duplicated().sum()==1
    assert after.terminal_boundary_bar.all()


def test_regular_does_not_receive_afterhours_terminal_policy():
    df=normalize_historical(item_fixture(['2026-10-09T05:00:00+08:00'],'regular'),calendar_fixture())
    assert not df.terminal_boundary_bar.any()
    assert df.trade_date.iloc[0]=='2026-10-09' # existing regular convention unchanged


def test_unknown_close_not_accepted_and_before_open_outside():
    df=normalize_historical(item_fixture(['2026-10-08T14:59:00+08:00','2026-10-14T05:00:00+08:00'],'afterhours'),calendar_fixture())
    assert df.trade_date.isna().all() and not df.terminal_boundary_bar.any()


def test_shared_terminal_open_boundary_refuses_ambiguous_attribution():
    cal=calendar_fixture()
    cal.loc[1,'afterhours_open']=cal.loc[0,'afterhours_close']
    with pytest.raises(HistoricalFatalError,match='different night session'):
        normalize_historical(item_fixture(['2026-10-08T05:00:00+08:00'],'afterhours'),cal)
