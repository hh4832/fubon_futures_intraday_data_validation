"""Independent TAIFEX daily CSV adapter; incompatible definitions never FAIL."""
import hashlib
import io
import json
import csv
import re
import unicodedata
from datetime import date as calendar_date
from urllib.parse import urlencode,urlparse
from urllib.request import Request,urlopen
import numpy as np
import pandas as pd

SOURCE='https://www.taifex.com.tw/cht/3/futDataDown'
DEFINITION_SOURCE='https://www.taifex.com.tw/cht/3/futDailyMarketReport'

REQUIRED={'交易日期','契約','到期月份(週別)','最高價','最低價','交易時段'}
KEY_COLUMNS={'contract':'契約','contract_month':'到期月份(週別)','trade_date':'交易日期','session':'交易時段'}

def normalized_text(value):
    if value is None or pd.isna(value):return ''
    return ' '.join(unicodedata.normalize('NFKC',str(value)).split())

def canonicalize_official_daily(frame):
    """Only comparison keys are canonicalized; keep official source columns."""
    df=frame.copy();df.columns=[normalized_text(c) for c in df.columns]
    if df.columns.duplicated().any() or not REQUIRED.issubset(df):
        raise ValueError('Official CSV schema unrecognized or duplicate headers')
    def month(value):
        text=normalized_text(value)
        return text[:-2] if re.fullmatch(r'[0-9]{6}\.0',text) else text
    def date(value):
        text=normalized_text(value);match=re.fullmatch(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})',text)
        if not match:return text
        try:return calendar_date(*map(int,match.groups())).isoformat()
        except ValueError:return text
    df['canonical_contract']=df['契約'].map(normalized_text)
    df['canonical_contract_month']=df['到期月份(週別)'].map(month)
    df['canonical_trade_date']=df['交易日期'].map(date)
    df['canonical_session']=df['交易時段'].map(normalized_text).map({'一般':'regular','盤後':'afterhours'})
    return df


def parse_official_daily(raw):
    """Validate CSV row widths explicitly; pandas must not consume date as index."""
    for encoding in ['utf-8-sig','cp950']:
        try:
            records=list(csv.reader(io.StringIO(raw.decode(encoding))))
        except UnicodeError:continue
        if not records:continue
        header=[normalized_text(c) for c in records[0]]
        if not REQUIRED.issubset(header):continue
        rows=[row for row in records[1:] if row] # CSV blank lines, not market rows.
        widths=sorted({len(row) for row in rows})
        extra=max(widths+[len(header)])-len(header)
        if any(len(row)<len(header) or any(normalized_text(v) for v in row[len(header):]) for row in rows):
            raise ValueError('Unexpected nonempty CSV fields or truncated row; no silent column drop')
        columns=header+[f'trailing_empty_csv_field_{i+1}' for i in range(extra)]
        df=pd.DataFrame([row+['']*(len(columns)-len(row)) for row in rows],columns=columns)
        return canonicalize_official_daily(df),dict(encoding=encoding,header_field_count=len(header),data_field_counts=widths,
            trailing_empty_field_count=extra,raw_candidate_count=len(rows),csv_index_policy='explicit columns; no implicit index')
    raise ValueError('Official CSV schema unrecognized or unavailable historical date')


def select_official_row(official,date,year,session,month,metadata):
    diagnostic=dict(year=year,trade_date=date,session=session,expected_contract='TX',expected_contract_month=month,
        raw_candidate_count=len(official),post_contract_filter_count=0,post_month_filter_count=0,post_date_filter_count=0,
        post_session_filter_count=0,selected_row_count=0,status='UNRESOLVED',notes='')
    for key in KEY_COLUMNS:diagnostic['canonicalized_unique_'+key+'_values']='[]'
    try:df=canonicalize_official_daily(official)
    except ValueError:
        diagnostic['notes']='DOWNLOAD_UNAVAILABLE_OR_SCHEMA_ERROR; '+str(metadata.get('error_type',''))
        return None,diagnostic
    for key in KEY_COLUMNS:
        diagnostic['canonicalized_unique_'+key+'_values']=json.dumps(sorted(df['canonical_'+key].dropna().unique().tolist()),ensure_ascii=False)
    for key,value,label in [('contract','TX','contract'),('contract_month',month,'month'),('trade_date',date,'date'),('session',session,'session')]:
        df=df[df['canonical_'+key].eq(value).fillna(False)]
        diagnostic['post_'+label+'_filter_count']=len(df)
    diagnostic['selected_row_count']=len(df)
    if month is None:diagnostic['notes']='FUBON_CONTRACT_IDENTITY_MISSING_OR_AMBIGUOUS'
    elif len(df)==0:diagnostic['notes']='NO_EXACT_CANONICAL_KEY_MATCH'
    elif len(df)>1:diagnostic['notes']='AMBIGUOUS_OFFICIAL_ROWS; no first-row fallback'
    else:
        diagnostic.update(status='PASS',notes='Unique exact TX/month/trade_date/session row; daily spot check is not minute-level proof')
        return df.iloc[0],diagnostic
    return None,diagnostic


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
        df,parse_info=parse_official_daily(raw)
        metadata.update(parse_info)
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
    row,selection=select_official_row(official,date,year,session,month,metadata)
    def value(column):
        if row is None:return None
        return pd.to_numeric(normalized_text(row.get(column)),errors='coerce')
    records=[]
    records.append(compare_external_field(year,date,session,'contract_identifier',month,row['canonical_contract_month'] if row is not None else None,True,notes='Fubon TXF vs official TX; '+selection['notes']))
    records.append(compare_external_field(year,date,session,'official_trading_date',date if len(f) else None,row['canonical_trade_date'] if row is not None else None,True,notes='next regular trade_date; '+selection['notes']))
    for field,col in [('high','最高價'),('low','最低價')]:
        observed=f[field].max() if field=='high' else f[field].min()
        records.append(compare_external_field(year,date,session,field,observed,value(col),True,notes='same monthly contract, same official session, observed minute extrema vs official daily extrema; missing minima/maxima would remain contradiction'))
    for field,col in [('close','收盤價'),('volume','成交量')]:
        observed=f.close.iloc[-1] if field=='close' and len(f) else f.volume.sum(min_count=1) if field=='volume' else None
        records.append(compare_external_field(year,date,session,field,observed,value(col),False,notes='NOT COMPARABLE: daily final/settlement and spread/combination volume definitions not proven identical to minute candles; not treated as FAIL'))
    result=pd.DataFrame(records)
    result.attrs['selection_diagnostics']=selection
    return result
