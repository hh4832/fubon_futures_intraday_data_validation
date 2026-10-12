"""Exact, key-preserving comparisons. Internal provider consistency ≠ external validation."""
import numpy as np
import pandas as pd

FIELDS = ['open','high','low','close','volume','average']


def compare_frames(left,right,left_name='historical',right_name='intraday',require_contract=True):
    keys = ['timestamp']
    if require_contract:
        keys.append('actual_contract_month')
    columns = keys+FIELDS
    if left.empty or right.empty or any(c not in left or c not in right for c in columns):
        return pd.DataFrame(columns=keys),pd.DataFrame([{'field':f,'compared_rows':0,'exact_match_rows':0,
            'mismatch_rows':0,'match_rate':np.nan,'max_abs_difference':np.nan,'status':'UNRESOLVED',
            'evidence_type':'Unresolved','notes':'No exact contract/session/date overlap'} for f in FIELDS])
    if left.duplicated(keys).any() or right.duplicated(keys).any():
        return pd.DataFrame(columns=keys),pd.DataFrame([{'field':f,'compared_rows':0,'status':'FAIL',
            'evidence_type':'Observed fact','notes':'Duplicate comparison keys; no dropping or many-to-many join'} for f in FIELDS])
    detail = left[columns].merge(right[columns],on=keys,how='outer',suffixes=('_'+left_name,'_'+right_name),indicator=True,validate='one_to_one')
    summaries=[]
    paired=detail['_merge'].eq('both')
    unmatched=int((~paired).sum())
    for f in FIELDS:
        a,b=detail[f+'_'+left_name],detail[f+'_'+right_name]
        usable=paired & a.notna() & b.notna()
        diff=a-b
        equal=usable & a.eq(b) # prices exact; no implicit tolerance
        detail[f+'_diff']=diff
        detail[f+'_exact_match']=equal
        n=int(usable.sum()); mismatches=n-int(equal.sum())
        summaries.append({'field':f,'compared_rows':n,'exact_match_rows':int(equal.sum()),
            'mismatch_rows':mismatches,'match_rate':float(equal.sum()/n) if n else np.nan,
            'max_abs_difference':float(diff[usable].abs().max()) if n else np.nan,
            'unmatched_rows':unmatched,'null_or_unavailable_pairs':int((paired & ~usable).sum()),
            'status':'UNRESOLVED' if not n else 'FAIL' if mismatches and f in FIELDS[:4] else
                'PASS WITH CAVEATS' if mismatches or unmatched or int((paired & ~usable).sum()) else 'PASS',
            'evidence_type':'Observed fact' if n else 'Unresolved',
            'notes':'provider cross-endpoint consistency only; volume/average definitions require independent interpretation'})
    return detail,pd.DataFrame(summaries)


def compare_external(df,reference,source):
    if reference is None or not source:
        return pd.DataFrame([{'status':'UNRESOLVED','evidence_type':'Unresolved',
            'external_validation':'NOT PERFORMED','notes':'No verified external reference source configured'}])
    # Interface accepts independently sourced minute-level records with identical key schema.
    detail,summary=compare_frames(df,reference,'provider','external')
    summary['reference_source']=source
    summary['external_validation']='PERFORMED ON SUPPLIED REFERENCE ONLY'
    return summary
