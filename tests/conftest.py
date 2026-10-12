from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
import pytest
from src.normalize import normalize

@pytest.fixture
def bars():
    times = pd.date_range('2026-10-12 08:45',periods=240,freq='min',tz='Asia/Taipei')
    response = {'date':'2026-10-12','type':'FUTURE','exchange':'TAIFEX','symbol':'SYNTHETIC',
        'timeframe':'1','data':[dict(date=t.isoformat(),open=100+i,high=102+i,low=99+i,
            close=101+i,volume=2,average=100.5+i) for i,t in enumerate(times)]}
    return normalize(response,'SYNTHETIC','regular',1)
