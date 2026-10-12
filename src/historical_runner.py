"""Ordered, gated historical validation. No production warehouse or trading."""
import json
from pathlib import Path
from time import perf_counter
import pandas as pd
from tqdm.auto import tqdm
from .archive import start_run, write_info, finish_run
from .diagnostics import write_table
from .historical_client import (HistoricalFatalError, HistoricalReader, load_historical_config,
    read_canonical_documentation, session_calendar, recent_complete_dates)
from .historical_normalization import normalize_historical
from .historical_validation import evaluate, status_rollup
from .historical_overlap import compare_frames, compare_external
from .rollover_validation import rollover_mapping
from .validate_aggregation import compare_aggregation
from .normalize import normalize
from .discover_contracts import discover

STAGES = ['recent regular 1m','historical vs intraday overlap','concrete contract month',
          'single rollover window','afterhours','higher timeframe aggregation','cross-year samples']


def core_comparison_status(summary):
    if summary.empty or 'field' not in summary or 'status' not in summary:
        return 'UNRESOLVED'
    # Missing average definition does not erase exact OHLC evidence. Volume needs inspection.
    return status_rollup(summary.loc[summary.field.isin(['open','high','low','close','volume']),'status'])


class HistoricalValidationRun:
    def __init__(self,rest,path,cfg,info,calendar,reader=None):
        self.rest,self.path,self.cfg,self.info,self.calendar=rest,path,cfg,info,calendar
        self.reader=reader or HistoricalReader(rest,path,cfg)
        self.frames={};self.results={};self.stages=[];self.metrics=[]
        self.recent_dates=None;self.regular=None;self.afterhours=None
        self.overlap_status='UNRESOLVED';self.rollover_result={};self.concrete_status='UNRESOLVED'
        self.discovered_contracts=pd.DataFrame()

    def stage(self,number,status,notes):
        self.stages.append({'phase':number,'question':STAGES[number-1],'status':status,
            'evidence_type':'Observed fact' if status in {'PASS','PASS WITH CAVEATS','FAIL'} else 'Unresolved',
            'notes':notes})
        self.info['validation_stage']=number
        self.flush()

    def flush(self):
        self.info.update(validation_phase='historical', api_request_count=self.reader.calls,
            api_error_count=sum(bool(a['error_type']) for a in self.reader.attempts),
            rate_limit_hit=any(a['rate_limit_hit'] for a in self.reader.attempts))
        self.info['sessions_tested'] = sorted({a['params'].get('session','regular').lower() for a in self.reader.attempts})
        self.info['timeframes_tested'] = sorted({str(a['params']['timeframe']) for a in self.reader.attempts if 'timeframe' in a['params']})
        write_info(self.path,self.info)
        write_table(self.path,'historical_api_requests',self.reader.attempts)
        write_table(self.path,'historical_stages',self.stages,['phase','question','status','evidence_type','notes'])
        write_table(self.path,'historical_performance',self.metrics,['stage','row_count','elapsed_seconds'])
        for name,suffix in [('schema','schema_validation'),('ohlc','ohlc_validation'),
                            ('timestamp','timestamp_validation'),('session','session_validation'),('average','average_validation')]:
            records=[dict(request=k,**{f:v for f,v in r[name].items() if f!='request'}) for k,r in self.results.items()]
            write_table(self.path,'historical_'+suffix,records)
        daily=[dict(r['daily'].assign(request=k).to_dict('list')) for k,r in self.results.items()]
        tables=[pd.DataFrame(x) for x in daily]
        write_table(self.path,'historical_daily_coverage',pd.concat(tables,ignore_index=True).to_dict('records') if tables else [])
        average_tables=[]
        for request,frame in self.frames.items():
            if frame.empty:
                continue
            reset=frame.sort_values('timestamp').groupby(['session','trade_date'],dropna=False).agg(
                first_timestamp=('timestamp','first'),last_timestamp=('timestamp','last'),
                first_average=('average','first'),last_average=('average','last'),average_rows=('average','count')).reset_index()
            reset['previous_session_last_average']=reset.groupby('session').last_average.shift()
            reset['boundary_average_difference']=reset.first_average-reset.previous_session_last_average
            reset['request']=request
            reset['status']='UNRESOLVED'
            reset['evidence_type']='Observed fact'
            reset['notes']='boundary values only; reset semantics and per-bar/cumulative identity are not proven'
            average_tables.append(reset)
        write_table(self.path,'historical_average_reset',pd.concat(average_tables,ignore_index=True).to_dict('records') if average_tables else [])

    def request(self,name,contract,dates,session='regular',timeframe=1):
        t=perf_counter()
        item=self.reader.fetch(name,contract,dates,session,timeframe)
        self.info.update(product=self.cfg['product'],contractMonth=contract,session=session,timeframe=timeframe,
            from_date=min(dates),to_date=max(dates),historical_endpoint_callable='sdk.marketdata.rest_client.futopt.historical.candles')
        if item['response'] is None:
            write_table(self.path,name+'_request_failure',[{'status':'FAIL','evidence_type':'Observed fact','notes':'API request failed; no substituted data'}])
            self.flush()
            return None,'FAIL'
        try:
            df=normalize_historical(item,self.calendar)
            df.to_parquet(self.path/'data'/f'{name}.parquet',index=False)
            result=evaluate(item,df,self.calendar)
            result['invalid'].to_parquet(self.path/'diagnostics'/f'{name}_invalid_ohlc_rows.parquet',index=False)
            self.results[name]=result;self.frames[name]=df
            self.metrics.append({'stage':name,'row_count':len(df),'elapsed_seconds':perf_counter()-t})
            self.flush()
            return df,result['status']
        except HistoricalFatalError:
            write_table(self.path,name+'_fatal_failure',[{'status':'FAIL','evidence_type':'Observed fact','notes':'Unsafe schema/timestamp; stopped without repair'}])
            self.flush()
            raise

    def phase1(self):
        self.recent_dates=recent_complete_dates(self.calendar,'regular',delay_minutes=self.cfg['publication_safety_delay_minutes'])
        write_table(self.path,'historical_requested_sample',[{'trade_date':d,'session':'regular',
            'contract_query':'1!','evidence_type':'Documented fact','notes':'scheduled completed session; 60-minute publication safety delay is an explicit conservative inference'} for d in self.recent_dates])
        self.regular,status=self.request('phase1_recent_regular','1!',self.recent_dates)
        if status in {'PASS','PASS WITH CAVEATS'} and self.cfg['repeat_phase1']:
            repeated,repeat_status=self.request('phase1_recent_regular_repeat','1!',self.recent_dates)
            if repeated is None:return repeat_status
            detail,summary=compare_frames(self.regular,repeated,'first','repeat')
            write_table(self.path,'historical_repeatability',summary.to_dict('records'))
            detail.to_csv(self.path/'diagnostics'/'historical_repeatability_rows.csv',index=False)
            status=status_rollup([status,repeat_status,core_comparison_status(summary)])
        return status

    def phase2(self):
        # Contract symbol must come from discovery, not a guessed month-code translation.
        responses,contracts=discover(self.reader.intraday)
        self.discovered_contracts=contracts
        for response in responses.values():
            if response is None and self.reader.intraday.attempts and str(self.reader.intraday.attempts[-1]['status_code']) in {'401','403'}:
                raise HistoricalFatalError('Intraday authentication/permission failure during overlap')
        write_table(self.path,'historical_overlap_discovered_contracts',contracts.to_dict('records'))
        if contracts.empty:
            write_table(self.path,'historical_vs_intraday_overlap',[])
            write_table(self.path,'historical_vs_intraday_summary',[{'status':'UNRESOLVED','evidence_type':'Unresolved','notes':'No discovered actual contract for overlap'}])
            return 'UNRESOLVED'
        # Canonical query endpoints 1! may span contracts. Discovery maps the observed month via settlementDate only.
        latest=self.recent_dates[-1]
        months=self.regular.loc[self.regular.trade_date.eq(latest),'actual_contract_month'].dropna().unique()
        if len(months)!=1 or 'settlementDate' not in contracts:
            return self.no_overlap('Actual month or API settlementDate unavailable; no symbol inference')
        settlement=pd.to_datetime(contracts.settlementDate,errors='coerce').dt.strftime('%Y%m')
        eligible=contracts[settlement.eq(months[0]) & contracts.discovery_session.eq('REGULAR')]
        symbols=eligible.symbol.dropna().unique()
        if len(symbols)!=1:
            return self.no_overlap('No unique discovered month-to-symbol identity')
        symbol=symbols[0]
        response=self.reader.intraday.request('candles','overlap_intraday_regular_1m',symbol=symbol,timeframe='1')
        if str(self.reader.intraday.attempts[-1]['status_code']) in {'401','403'}:
            raise HistoricalFatalError('Intraday authentication/permission failure during overlap')
        if not isinstance(response,dict) or not isinstance(response.get('data'),list):
            return self.no_overlap('Intraday response unavailable')
        date=response.get('date')
        if date not in self.recent_dates:
            return self.no_overlap('Intraday date not in completed historical sample; run after session publication or before next intraday date reset')
        reference=normalize(response,symbol,'regular',1)
        if reference.timestamp.isna().any():
            raise HistoricalFatalError('Unsafe intraday timestamp in overlap')
        # Keep actual-month assignment explicit as metadata-derived inference, not API bar observation.
        reference['actual_contract_month']=str(months[0])
        reference.to_parquet(self.path/'data'/'overlap_intraday_regular_1m.parquet',index=False)
        historical=self.regular[self.regular.trade_date.eq(date) & self.regular.actual_contract_month.eq(months[0])]
        detail,summary=compare_frames(historical,reference)
        detail.to_csv(self.path/'diagnostics'/'historical_vs_intraday_overlap.csv',index=False)
        write_table(self.path,'historical_vs_intraday_summary',summary.to_dict('records'))
        self.info['overlap_actual_symbol']=symbol;self.info['overlap_trade_date']=date
        self.overlap_status=core_comparison_status(summary)
        return self.overlap_status

    def no_overlap(self,notes):
        write_table(self.path,'historical_vs_intraday_overlap',[])
        write_table(self.path,'historical_vs_intraday_summary',[{'status':'UNRESOLVED','evidence_type':'Unresolved','notes':notes}])
        return 'UNRESOLVED'

    def phase3(self):
        concrete=self.cfg['concrete_contract']
        dates=[d for d in self.recent_dates if concrete['expected_valid_from']<=d<=concrete['expected_valid_to']]
        contracts=self.discovered_contracts
        if not {'settlementDate','startDate','endDate'}.issubset(contracts):
            write_table(self.path,'concrete_contract_metadata',[{'status':'UNRESOLVED','evidence_type':'Unresolved','notes':'API listing/lifetime metadata unavailable'}])
            return 'UNRESOLVED'
        matching=contracts[pd.to_datetime(contracts.settlementDate,errors='coerce').dt.strftime('%Y%m').eq(concrete['contractMonth'])]
        lifetimes=matching[['symbol','startDate','endDate','settlementDate']].drop_duplicates()
        if len(lifetimes)!=1:
            return 'UNRESOLVED'
        lifetime=lifetimes.iloc[0]
        dates=[d for d in dates if str(lifetime.startDate)<=d<=str(lifetime.endDate)]
        write_table(self.path,'concrete_contract_metadata',[dict(lifetime, status='PASS',evidence_type='Observed fact',notes='range bounded by observed API lifetime')])
        if not dates:
            return 'UNRESOLVED'
        df,status=self.request('phase3_concrete',concrete['contractMonth'],dates)
        if df is None:return status
        # A concrete request must retain concrete month identity in every observed bar.
        if not df.actual_contract_month.eq(concrete['contractMonth']).all():return 'FAIL'
        continuous=self.regular[self.regular.trade_date.isin(dates)]
        detail,summary=compare_frames(continuous,df,'continuous','concrete')
        detail.to_csv(self.path/'diagnostics'/'concrete_vs_continuous.csv',index=False)
        write_table(self.path,'concrete_vs_continuous_summary',summary.to_dict('records'))
        self.concrete_status=status_rollup([status,core_comparison_status(summary)])
        return self.concrete_status

    def phase4(self):
        roll=self.cfg['rollover']
        self.info.update(roll_window=[roll['from'],roll['to']],old_contract=roll['old_contract'],new_contract=roll['new_contract'])
        dates=self.calendar.loc[self.calendar.trade_date.between(roll['from'],roll['to']),'trade_date'].tolist()
        if not dates or sum(d<roll['expiry_date'] for d in dates)<2 or sum(d>roll['expiry_date'] for d in dates)<2:
            return 'UNRESOLVED'
        responses={};statuses=[]
        for contract in tqdm(['1!',roll['old_contract'],roll['new_contract']],desc='rollover comparisons'):
            requested=dates if contract!=roll['old_contract'] else [d for d in dates if d<=roll['expiry_date']]
            df,status=self.request('phase4_roll_'+contract.replace('!','continuous'),contract,requested)
            if df is None:return status
            responses[contract]=df;statuses.append(status)
        mapping,result=rollover_mapping(responses['1!'],responses[roll['old_contract']],responses[roll['new_contract']],
                                       roll['old_contract'],roll['new_contract'],roll['expiry_date'])
        next_dates=self.calendar.loc[self.calendar.trade_date.gt(roll['expiry_date']),'trade_date']
        expected=next_dates.iloc[0] if len(next_dates) else None
        result['documented_expected_next_trade_date']=expected
        result['expiry_boundary_consistent_in_sample']=result.get('transition_trade_date')==expected if expected is not None else None
        if result.get('status')=='PASS WITH CAVEATS' and result['expiry_boundary_consistent_in_sample'] is False:
            result['status']='UNRESOLVED'
            result['notes'] += '; observed transition differs from documented expiry boundary'
        mapping.to_csv(self.path/'diagnostics'/'rollover_mapping.csv',index=False)
        write_table(self.path,'rollover_semantics',[result])
        self.rollover_result=result
        return status_rollup(statuses+[result['status']])

    def phase5(self):
        dates=recent_complete_dates(self.calendar,'afterhours',delay_minutes=self.cfg['publication_safety_delay_minutes'])
        self.afterhours,status=self.request('phase5_afterhours','1!',dates,session='afterhours')
        if self.afterhours is None:return status
        daily=self.results['phase5_afterhours']['daily']
        write_table(self.path,'historical_afterhours_validation',daily.to_dict('records'))
        # Repeat the same roll window for session synchronization; no interpretation of unrelated recent dates.
        roll=self.cfg['rollover'];dates=self.calendar.loc[self.calendar.trade_date.between(roll['from'],roll['to']),'trade_date'].tolist()
        frames={};statuses=[status]
        for contract in ['1!',roll['old_contract'],roll['new_contract']]:
            requested=dates if contract!=roll['old_contract'] else [d for d in dates if d<=roll['expiry_date']]
            frames[contract],state=self.request('phase5_night_roll_'+contract.replace('!','continuous'),contract,requested,'afterhours')
            if frames[contract] is None:return state
            statuses.append(state)
        mapping,result=rollover_mapping(frames['1!'],frames[roll['old_contract']],frames[roll['new_contract']],roll['old_contract'],roll['new_contract'],roll['expiry_date'])
        mapping.to_csv(self.path/'diagnostics'/'rollover_afterhours_mapping.csv',index=False)
        write_table(self.path,'rollover_afterhours_semantics',[result])
        write_table(self.path,'rollover_session_comparison',[{
            'regular_transition_trade_date':self.rollover_result.get('transition_trade_date'),
            'afterhours_transition_trade_date':result.get('transition_trade_date'),
            'status':'UNRESOLVED','evidence_type':'Inference',
            'notes':'matching trade dates supports consistency in one window; calendar timestamps need not coincide; general rule remains unresolved'}])
        return status_rollup(statuses+[result['status']])

    def phase6(self):
        rows=[];statuses=[]
        for session,one in [('regular',self.regular),('afterhours',self.afterhours)]:
            dates=sorted(one.trade_date.dropna().unique())
            for tf in tqdm([5,15,60],desc=f'higher timeframe {session}'):
                higher,status=self.request(f'phase6_{session}_{tf}m','1!',dates,session,tf)
                if higher is None:return status
                statuses.append(status)
                for date in dates:
                    a=one[one.trade_date.eq(date)];b=higher[higher.trade_date.eq(date)]
                    result,detail=compare_aggregation(a,b,tf,self.cfg['aggregation_price_atol'],self.cfg['aggregation_volume_atol'])
                    alignment=result.pop('status')
                    result['alignment_status']=alignment
                    result['status']='UNRESOLVED' if alignment=='UNRESOLVED_ALIGNMENT' else alignment
                    result.update(session=session,trade_date=date,timeframe=tf,evidence_type='Observed fact',
                        notes=result['notes']+'; resample per trade-date/session; documented start timestamp; no gap bridging')
                    rows.append(result)
                    detail.to_csv(self.path/'diagnostics'/f'historical_aggregation_{session}_{date}_{tf}m.csv',index=False)
                    statuses.append(result['status'])
        write_table(self.path,'historical_aggregation_validation',rows)
        return status_rollup(statuses)

    def phase7(self):
        rows=[];states=[]
        for sample in tqdm(self.cfg['cross_year_samples'],desc='yearly sample groups'):
            year=sample['year'];df,status=self.request(f'phase7_{year}','1!',sample['dates'])
            states.append(status)
            if df is None:
                rows.extend({'year':year,'trade_date':d,'session':'regular','row_count':0,'schema_status':'UNRESOLVED',
                    'ohlc_status':'UNRESOLVED','timestamp_status':'UNRESOLVED','gap_count':None,'duplicate_count':None,
                    'api_status':'FAIL','status':'FAIL','evidence_type':'Observed fact','notes':'fixed date not replaced after failure'} for d in sample['dates'])
                break
            result=self.results[f'phase7_{year}']
            for record in result['daily'].to_dict('records'):
                record.update(year=year,schema_status=result['schema']['schema_status'],ohlc_status=result['ohlc']['status'],
                    timestamp_status=result['timestamp']['status'],gap_count=record['gap_event_count'],
                    api_status='PASS',notes=record['notes']+'; ex-ante fixed dates; historical calendar not independently confirmed')
                rows.append(record)
            if status not in {'PASS','PASS WITH CAVEATS'}:break
        write_table(self.path,'cross_year_coverage',rows)
        return status_rollup(states)

    def run(self):
        for number in tqdm(range(1,8),desc='Historical validation phases'):
            if number>self.cfg['max_phase']:
                self.stage(number,'UNRESOLVED','NOT RUN: beyond configured max_phase; default minimal Phase 1')
                continue
            if any(x['status'] not in {'PASS','PASS WITH CAVEATS'} for x in self.stages):
                self.stage(number,'UNRESOLVED','BLOCKED: previous phase FAIL/UNRESOLVED; no skipped gates')
                continue
            status=getattr(self,f'phase{number}')()
            self.stage(number,status,'executed in fixed order; see per-request diagnostics')
        return self.stages

    def summary(self,fatal=False):
        from .historical_overlap import compare_external
        reference = None
        if self.cfg.get('external_reference_csv') and self.cfg.get('external_reference_source'):
            reference = pd.read_csv(self.cfg['external_reference_csv'])
            aware = reference.timestamp.astype('string').str.contains(r'(?:Z|[+-]\d{2}:?\d{2})$',na=False)
            if not aware.all():
                raise HistoricalFatalError('External reference timestamps must be timezone-aware')
            reference['timestamp'] = pd.to_datetime(reference.timestamp,utc=True,errors='raise').dt.tz_convert('Asia/Taipei')
        external=compare_external(self.regular,reference,self.cfg.get('external_reference_source'))
        write_table(self.path,'historical_external_validation',external.to_dict('records'))
        completed=[s for s in self.stages if not s['notes'].startswith(('NOT RUN','BLOCKED'))]
        status='FAIL' if fatal else status_rollup(s['status'] for s in self.stages)
        # Evidence insufficiency => retest, never automatic source promotion.
        decision='修改後再測'
        questions=[('A. Validation question','Documented fact','Can historical candles support a later, separately validated multi-year construction phase?'),
            ('B. API / environment','Observed fact',f"SDK {self.info['fubon_sdk_version']}; login {self.info['login_status']}; Git {self.info['git_commit']}"),
            ('C. Historical endpoint callable','Observed fact',self.info.get('historical_endpoint_callable','see historical_callable.txt')),
            ('D. Requested sample','Observed fact',str(self.recent_dates)),
            ('E. Historical coverage','Observed fact','historical_daily_coverage.csv; no replacement of bad sample dates'),
            ('F. Schema validation','Observed fact','historical_schema_validation.csv; per-bar actual month retained'),
            ('G. OHLC validation','Observed fact','historical_ohlc_validation.csv; no repair'),
            ('H. Timestamp / session validation','Inference','sourced scheduled calendar; no naive timestamps; no automatic +1 weekday'),
            ('I. Gap diagnostics','Unresolved','Observed gaps/reference missing slots cannot identify no-trade suppression vs provider omission'),
            ('J. Historical vs intraday overlap','Observed fact' if self.overlap_status!='UNRESOLVED' else 'Unresolved',self.overlap_status+'; provider internal consistency only'),
            ('K. Continuous vs concrete comparison','Observed fact' if self.concrete_status!='UNRESOLVED' else 'Unresolved',self.concrete_status),
            ('L. Rollover result','Inference' if self.rollover_result else 'Unresolved',json.dumps(self.rollover_result,ensure_ascii=False)),
            ('M. Average field result','Unresolved','documented historical per-bar average; empirical semantics/session reset not proven; intraday may use a different definition'),
            ('N. External validation status','Unresolved' if external.external_validation.iloc[0]=='NOT PERFORMED' else 'Observed fact',external.external_validation.iloc[0]),
            ('O. Major limitations','Unresolved','single rollover; 2!/3! not sampled; fixed June cross-year windows lack seasonality coverage; incomplete independent historical calendar; no production-series approval'),
            ('P. Opponent view','Inference','same-provider exact matches can reproduce shared omissions; five days and one rollover cannot establish 2017–2026 reliability'),
            ('Q. Final decision','Unresolved',f'{status} / {decision}')]
        lines=['# Historical Candles Validation','',f"Branch: {self.info['git_branch']}",f"Git commit: {self.info['git_commit']}",
               '',pd.DataFrame(self.stages).to_string(index=False) if self.stages else 'Formal run stopped before Phase 1.']
        for heading,kind,value in questions:lines += ['', '## '+heading,f'[{kind}] {value}']
        lines += ['','historical validation ≠ strategy validation. No back-adjustment or silent data repair performed.']
        text='\n'.join(lines)
        for ext in ['md','txt']:(self.path/f'validation_summary.{ext}').write_text(text,encoding='utf-8')
        self.info.update(historical_validation_status=status,research_decision=decision)
        return text


def run_historical(rest,root,sdk_info,auth_info,cfg=None):
    cfg=cfg or load_historical_config()
    # Read canonical evidence and calendar before any market-data request.
    doc,doc_info=read_canonical_documentation(cfg)
    calendar=session_calendar(cfg)
    path,info=start_run(root,cfg,sdk_info,auth_info)
    info.update(doc_info,validation_phase='historical',validation_stage=0,
                historical_endpoint_callable='sdk.marketdata.rest_client.futopt.historical.candles')
    write_info(path,info)
    (path/'diagnostics'/'official_historical_documentation.txt').write_bytes(doc)
    calendar.to_csv(path/'diagnostics'/'historical_calendar_snapshot.csv',index=False)
    task=None
    try:
        task=HistoricalValidationRun(rest,path,cfg,info,calendar)
        task.run()
        text=task.summary()
        finish_run(task.reader,info,status='COMPLETED')
        return path,text
    except Exception as exc:
        # Do not put arbitrary client exception strings or credential data in logs.
        info.update(execution_status='STOPPED',fatal_error_type=type(exc).__name__,
                    historical_validation_status='FAIL',research_decision='修改後再測')
        if task is not None:
            task.summary(fatal=True)
            finish_run(task.reader,info,status='STOPPED')
        else:
            text='# Historical Candles Validation\n[Unresolved] Formal run stopped during client discovery.\nFAIL / 修改後再測\n'
            for ext in ['md','txt']:(path/f'validation_summary.{ext}').write_text(text)
            write_info(path,info)
        raise HistoricalFatalError(f'Historical formal run stopped ({type(exc).__name__}); inspect safe diagnostics in {path}') from None
