from types import SimpleNamespace
import pytest
from src.config import load_config
from src.fubon_client import MarketReader
from src.diagnostics import historical_capability
from src.fubon_auth import load_secrets, login


def test_ignored_date_is_not_history(tmp_path,monkeypatch):
    for directory in ['raw','diagnostics']:(tmp_path/directory).mkdir()
    class API:
        def candles(self,**params):return {'date':'2026-10-12','data':[{'date':'2026-10-12T08:45:00+08:00'}]}
    cfg=dict(load_config(),request_spacing_seconds=0)
    reader=MarketReader(SimpleNamespace(intraday=API()),tmp_path,cfg)
    def blocked(*args,**kwargs):raise OSError('offline synthetic test')
    monkeypatch.setattr('src.diagnostics.urlopen',blocked)
    record=historical_capability(reader,'SYNTHETIC',cfg)
    assert record['historical_capability']=='unresolved'
    assert reader.calls==1


def test_explicit_signature_no_dates(tmp_path,monkeypatch):
    for directory in ['raw','diagnostics']:(tmp_path/directory).mkdir()
    class API:
        def candles(self,symbol,timeframe='1',session=None):return {'data':[]}
    cfg=dict(load_config(),request_spacing_seconds=0)
    reader=MarketReader(SimpleNamespace(intraday=API()),tmp_path,cfg)
    def blocked(*args,**kwargs):raise OSError('offline synthetic test')
    monkeypatch.setattr('src.diagnostics.urlopen',blocked)
    assert historical_capability(reader,'SYNTHETIC',cfg)['historical_capability']=='intraday_only'


def test_read_allowlist(tmp_path):
    reader=MarketReader(SimpleNamespace(intraday=None),tmp_path,load_config())
    with pytest.raises(ValueError):reader.request('unauthorized','test')
    assert reader.calls==0


def test_login_failure_suppresses_secret(capsys):
    values=load_secrets(lambda name:'synthetic-secret')
    class SDK:
        def apikey_login(self,*args):raise ValueError('synthetic-secret')
    with pytest.raises(RuntimeError) as exc:login(values,SDK)
    assert 'synthetic-secret' not in str(exc.value)
    assert 'synthetic-secret' not in capsys.readouterr().out
