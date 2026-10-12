from contextlib import contextmanager
import inspect
import json
import pandas as pd
from time import perf_counter
from urllib.request import urlopen

DOC_BASE = 'https://www.fbs.com.tw/TradeAPI/docs/market-data-future/http-api/'


def write_table(path, name, records, columns=None):
    df = pd.DataFrame(records)
    if df.empty:
        df = pd.DataFrame(columns=columns or ['status','notes'])
    df.to_csv(path/'diagnostics'/f'{name}.csv',index=False)
    return df


@contextmanager
def timed(metrics, stage, rows):
    start = perf_counter()
    try:
        yield
    finally:
        metrics.append({'stage':stage,'row_count':rows,'elapsed_seconds':perf_counter()-start})


def historical_capability(reader, symbol, cfg):
    method = reader.intraday.candles
    try:
        sig = inspect.signature(method)
        signature = str(sig)
        params = sig.parameters
    except (ValueError,TypeError):
        sig = None
        signature = 'unavailable'
        params = {}
    (reader.run_dir/'diagnostics'/'candles_callable.txt').write_text(
        signature+'\n'+(inspect.getdoc(method) or 'No docstring'),encoding='utf-8')
    docs = {}
    for name, url in [('intraday',DOC_BASE+'intraday/candles.txt'),
                      ('historical',DOC_BASE+'historical/candles/')]:
        try:
            with urlopen(url,timeout=30) as r:
                text = r.read().decode('utf-8')
            (reader.run_dir/'diagnostics'/f'official_{name}_documentation.txt').write_text(text)
            docs[name] = {'url':url,'retrieved':True,'sha256':__import__('hashlib').sha256(text.encode()).hexdigest()}
        except Exception:
            docs[name] = {'url':url,'retrieved':False}
    names = {'from','to','date','start_date','end_date'}
    explicit = sorted(names & params.keys())
    kwargs = any(p.kind == p.VAR_KEYWORD for p in params.values())
    unsupported = sig is not None and not kwargs and not explicit
    probe = None
    # One bounded date probe (no brute force). Acceptance alone cannot prove history.
    if cfg['historical_probe_enabled']:
        probe = reader.request('candles',f'{symbol}_historical_date_probe',symbol=symbol,timeframe='1',date='2020-01-02')
        attempt = reader.attempts[-1]
        # A TypeError alone may originate inside SDK; only signature binding proves rejection.
        if sig is not None and not kwargs:
            try:
                sig.bind(symbol=symbol,timeframe='1',date='2020-01-02')
            except TypeError:
                unsupported = unsupported or 'date' not in params
    observed_date = probe.get('date') if isinstance(probe,dict) else None
    actual_history = observed_date=='2020-01-02' and bool(probe.get('data')) if isinstance(probe,dict) else False
    capability = 'historical_date_observed_unverified' if actual_history else 'intraday_only' if unsupported else 'unresolved'
    record = {'endpoint':'intraday.candles','signature':signature,'explicit_date_parameters':str(explicit),
        'accepts_kwargs':kwargs,'probe_parameter':'date' if cfg['historical_probe_enabled'] else 'not run',
        'probe_requested_date':'2020-01-02','probe_response_date':observed_date,
        'historical_capability':capability,'status':'PASS WITH CAVEATS' if capability=='intraday_only' else 'UNRESOLVED',
        'separate_historical_endpoint_documented':True,
        'separate_historical_callable_present':bool(getattr(getattr(reader,'rest',None),'historical',None)),
        'separate_historical_api_tested':False,
        'notes':'SDK v2.4.0 docs advertise a separate historical endpoint; no multi-year source validation performed',
        'documentation_retrieval':json.dumps(docs)}
    if capability=='intraday_only':
        record['intraday_limit_statement']='Fubon futures intraday candles cannot currently be used directly as a historical multi-year backtest source based on validated API behavior.'
    write_table(reader.run_dir,'historical_capability',[record])
    return record


def write_summary(path,info,tables,history):
    lines = ['# 富邦期貨日內資料驗證', '', '本次不是策略回測。',
        f"Git commit: {info['git_commit']}",f"SDK: {info['fubon_sdk_version']}",
        f"登入: {info['login_status']}；futopt account: {info['futopt_account']}",
        f"契約: {info.get('contract_symbol','unresolved')}；選取依據: {info.get('selection_reason','unresolved')}",
        'products/tickers 完整 response：raw/；候選契約：diagnostics/discovered_contracts.csv。',
        '各次 API response 的 schema/日期/欄位見 schema_validation.csv；完整 K 線見 data/。']
    overall = []
    for name,table in tables.items():
        lines += ['', '## '+name, table.to_string(index=False)]
        col = 'schema_status' if 'schema_status' in table else 'status'
        if col in table:
            overall += table[col].tolist()
    lines += ['',f"Intraday 歷史查詢能力：{history['historical_capability']}",
        '獨立 historical endpoint：官方已有文件，本次未進行 API 測試；不能套用 intraday 結論至整個富邦 API。',
        '歷史當沖回測資料來源適用性：UNRESOLVED；需另案驗證 historical endpoint 與交易日、換月及成交量定義。',
        'average：unclear；僅靠 OHLC 與少量 snapshots 不足以證明 per-bar 或 cumulative VWAP。',
        'observed_gap 不等於 confirmed_missing_data；未補資料。',
        '反對者觀點：同一天 API snapshots 無法證明歷史完整性、流動性或多年回測可用性。']
    outcome = 'FAIL' if 'FAIL' in overall else 'UNRESOLVED' if any(s in {'UNRESOLVED','UNRESOLVED_ALIGNMENT'} for s in overall) else 'PASS WITH CAVEATS' if 'PASS WITH CAVEATS' in overall else 'PASS'
    lines += ['', '整體結論：'+outcome]
    text = '\n'.join(lines)
    for suffix in ['md','txt']:
        (path/f'validation_summary.{suffix}').write_text(text,encoding='utf-8')
    return text
