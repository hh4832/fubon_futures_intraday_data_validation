def validate_session(df, session, cfg):
    dates = df.calendar_date.dropna().unique().tolist()
    valid = df.timestamp.dropna()
    start, end = cfg['session_expectations'][session]
    minute = valid.dt.hour * 60 + valid.dt.minute
    def mins(value):
        h,m = str(value).split(':')
        return int(h)*60+int(m)
    a,b = mins(start),mins(end)
    inside = minute.between(a,b) if a <= b else (minute >= a) | (minute <= b)
    outside = int((~inside).sum())
    return {'row_count':len(df), 'response_trade_date':df.trade_date.iloc[0] if len(df) else None,
        'calendar_dates':str(dates), 'cross_midnight':len(dates)>1,
        'first_timestamp':str(valid.min()), 'last_timestamp':str(valid.max()),
        'expected_session_rule':f'{start}–{end} Asia/Taipei; broad reference only',
        'outside_reference_window':outside, 'trade_date_attribution':'observed only; calendar/holiday mapping unconfirmed',
        'status':'UNRESOLVED' if not len(valid) else 'PASS WITH CAVEATS' if outside else 'PASS',
        'notes':'expiry/holiday exceptions and timestamp label convention require independent confirmation'}
