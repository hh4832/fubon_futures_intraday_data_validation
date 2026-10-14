"""As-of-only policy regression; no SDK, market results or network calls."""
from copy import deepcopy
import json
import pandas as pd
import pytest
from scripts.freeze_final_samples import freeze
from src.final_sample_matrix import FrozenSampleMatrix,load_final_config,PREVIOUS_SAMPLE_MATRIX_SHA256,POLICY_REVISION_REASON
from src.historical_client import HistoricalFatalError

AS_OF='2026-10-14T10:45:25+08:00'

@pytest.fixture
def matrix():
    return FrozenSampleMatrix(load_final_config(),now=AS_OF)

def late(matrix):
    return next(s for s in matrix.samples if s['year']==2026 and s['window_type']=='ordinary_late')

def test_historical_completed_windows_eligible(matrix):
    historical=[s for s in matrix.samples if s['year']<=2025]
    assert len(historical)==27
    assert all(s['eligibility']=='ELIGIBLE' and s['included_in_formal_run'] for s in historical)
    assert all(s['included_in_formal_run'] for s in matrix.samples if s['year']==2026 and s['window_type']!='ordinary_late')

def test_future_window_not_yet_eligible(matrix):
    sample=late(matrix)
    assert sample['eligibility']=='NOT_YET_ELIGIBLE'
    assert sample['exclusion_reason']=='FUTURE_WINDOW'
    assert sample['included_in_formal_run'] is False

def test_future_window_retained_in_written_matrix(matrix,tmp_path):
    (tmp_path/'diagnostics').mkdir();matrix.write(tmp_path)
    table=pd.read_csv(tmp_path/'diagnostics/predeclared_sample_matrix.csv')
    assert len(table)==30
    row=table[table.sample_id.eq(late(matrix)['sample_id'])].iloc[0]
    assert json.loads(row.planned_trade_dates)==late(matrix)['trade_dates']
    assert row.eligibility=='NOT_YET_ELIGIBLE' and not row.included_in_formal_run
    assert {'year','window_type','planned_trade_dates','eligibility','included_in_formal_run','exclusion_reason'}.issubset(table)

def test_future_excluded_from_counts_and_requests(matrix):
    assert len(matrix.plan)==106
    assert not any(e['sample_id']==late(matrix)['sample_id'] for e in matrix.plan)
    assert matrix.metadata['planned_total_trade_dates']==190
    assert matrix.metadata['eligible_trade_dates']==185
    assert matrix.metadata['future_trade_dates']==5
    assert matrix.metadata['planned_windows']==30
    assert matrix.metadata['eligible_windows']==29 and matrix.metadata['future_windows']==1

def test_original_dates_unchanged_no_fallback_substitution(matrix):
    original=load_final_config()['samples']
    assert matrix.cfg['samples']==original
    assert [s['planned_trade_dates'] for s in matrix.samples]==[s['trade_dates'] for s in original]
    assert late(matrix)['planned_trade_dates']==['2026-11-02','2026-11-03','2026-11-04','2026-11-05','2026-11-06']

def test_require_completed_ignores_excluded_future_window(matrix):
    matrix.require_completed()
    # Frozen scope does not silently add a newly completed window later.
    matrix.require_completed('2026-11-07T00:00:00+08:00')
    assert len(matrix.plan)==106 and not late(matrix)['included_in_formal_run']

def test_incomplete_included_window_still_blocks(matrix):
    last=matrix.included_calendar.regular_close.max()
    with pytest.raises(HistoricalFatalError,match='Included.*incomplete'):
        matrix.require_completed(last) # Strictly ended before as-of, not equal.

def test_all_eligible_complete_preflight_ready(tmp_path):
    _,info=freeze(tmp_path/'preflight',now=AS_OF)
    assert info['readiness']=='READY'
    assert info['primary_trade_dates']==185 and info['expected_logical_request_count']==106
    assert '2026 ordinary_late not yet eligible' in info['excluded_windows_note']

def test_policy_revision_changes_sha_and_same_asof_reproduces(matrix):
    assert matrix.sample_matrix_sha256!=PREVIOUS_SAMPLE_MATRIX_SHA256
    assert matrix.sample_matrix_sha256==FrozenSampleMatrix(load_final_config(),now=AS_OF).sample_matrix_sha256
    all_complete=FrozenSampleMatrix(load_final_config(),now='2026-11-07T00:00:00+08:00')
    assert all_complete.sample_matrix_sha256!=matrix.sample_matrix_sha256
    assert len(all_complete.plan)==108 and len(all_complete.eligible_samples)==30

def test_preflight_revision_has_zero_requests_and_provenance(tmp_path,monkeypatch):
    def forbidden(*args,**kwargs):pytest.fail('Preflight must never construct a market reader')
    monkeypatch.setattr('src.historical_client.HistoricalReader',forbidden)
    _,info=freeze(tmp_path/'preflight',now=AS_OF)
    assert info['api_request_count']==info['api_request_count_before_revision']==0
    assert info['sampling_policy_revision_reason']==POLICY_REVISION_REASON
    assert info['as_of_timestamp']==AS_OF
    assert info['previous_sample_matrix_sha256']==PREVIOUS_SAMPLE_MATRIX_SHA256

def test_naive_asof_rejected_before_requests():
    with pytest.raises(HistoricalFatalError,match='timezone-aware'):
        FrozenSampleMatrix(load_final_config(),now='2026-10-14')
