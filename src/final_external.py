"""Independent TAIFEX daily CSV adapter; incompatible definitions never FAIL."""
import hashlib
import io
import json
from urllib.parse import urlencode,urlparse
from urllib.request import Request,urlopen
import numpy as np
import pandas as pd

SOURCE='https://www.taifex.com.tw/cht/3/futDataDown'
DEFINITION_SOURCE='https://www.taifex.com.tw/cht/3/futDailyMarketReport'


def fetch_official_daily(date,path):
    """One bounded official download per frozen spot date. No third-party fallback."""
    parameters=dict(down_type='1',commodity_id='TX',commodity_id2='',queryStartDate=date.replace('-','/'),queryEndDate=date.replace('-','/'))
    raw_path=path/'raw'/f'taifex_{date}.csv'
    metadata=dict(source_url=SOURCE,definition_source=DEFINITION_SOURCE,parameters=parameters,status='UNRESOLVED')
    try:
        with urlopen(Request(SOURCE,data=urlencode(parameters).encode()),timeout=20) as response:
            if urlparse(response.geturl()).hostname!='www.taifex.com.tw':raise ValueError('Nonofficial redirect')
            raw=response.read(10_000_001)
        raw_path.write_bytes(raw)
        metadata['sha256']=hashlib.sha256(raw).hexdigest()
        if len(raw)>10_000_000:raise ValueError('Official single-day response oversized')
        for encoding in ['utf-8-sig','cp950']:
            try:
                df=pd.read_csv(io.StringIO(raw.decode(encoding)),dtype=str)
                df.columns=df.columns.str.strip()
                if {'交易日期','契約','到期月份(週別)','最高價','最低價','交易時段'}.issubset(df):break
            except (UnicodeError,pd.errors.ParserError):continue
        else:raise ValueError('Official CSV schema unrecognized or unavailable historical date')
        df=df.map(lambda value:value.strip() if isinstance(value,str) else value)
        metadata['status']='PASS'
        return df,metadata
    except Exception as exc:
        metadata['error_type']=type(exc).__name__
        return pd.DataFrame(),metadata
    finally:
        (path/'diagnostics'/f'taifex_{date}_source.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))


def compare_external_field(year,date,session,field,fubon,official,compatible,source_url=SOURCE,notes=''):
    known=official is not None and pd.notna(official) and fubon is not None and pd.notna(fubon)
    match=bool(fubon==official) if compatible and known else None
    numeric=isinstance(fubon,(int,float,np.number)) and isinstance(official,(int,float,np.number))
    return dict(year=year,trade_date=date,session=session,field=field,fubon_value=fubon,taifex_value=official,
        definition_compatible=compatible,difference=float(fubon-official) if compatible and known and numeric else np.nan,
        match=match,source_url=source_url,status='NOT COMPARABLE' if not compatible else 'UNRESOLVED' if not known else 'PASS' if match else 'FAIL',notes=notes)


def external_spot_check(frame,date,year,session,official,metadata):
    f=frame[frame.trade_date.eq(date)]
    months=f.actual_contract_month.dropna().unique()
    month=str(months[0]) if len(months)==1 else None
    selected=pd.DataFrame()
    if len(official) and month:
        market='一般' if session=='regular' else '盤後'
        selected=official[official['契約'].eq('TX') & official['到期月份(週別)'].eq(month) & official['交易時段'].eq(market)]
        normalized_date=selected['交易日期'].str.replace('/','-',regex=False)
        selected=selected[normalized_date.eq(date)]
    row=selected.iloc[0] if len(selected)==1 else None
    def value(column):
        if row is None:return None
        return pd.to_numeric(row.get(column),errors='coerce')
    records=[]
    records.append(compare_external_field(year,date,session,'contract_identifier',month,str(row['到期月份(週別)']) if row is not None else None,True,notes='Fubon TXF vs official TX; exact monthly identity, no spread/weekly substitute'))
    records.append(compare_external_field(year,date,session,'official_trading_date',date if len(f) else None,row['交易日期'].replace('/','-') if row is not None else None,True,notes='same regular trade_date; night belongs next regular date'))
    for field,col in [('high','最高價'),('low','最低價')]:
        observed=f[field].max() if field=='high' else f[field].min()
        records.append(compare_external_field(year,date,session,field,observed,value(col),True,notes='same monthly contract, same official session, observed minute extrema vs official daily extrema; missing minima/maxima would remain contradiction'))
    for field,col in [('close','收盤價'),('volume','成交量')]:
        observed=f.close.iloc[-1] if field=='close' and len(f) else f.volume.sum(min_count=1) if field=='volume' else None
        records.append(compare_external_field(year,date,session,field,observed,value(col),False,notes='NOT COMPARABLE: daily final/settlement and spread/combination volume definitions not proven identical to minute candles; not treated as FAIL'))
    return pd.DataFrame(records)
