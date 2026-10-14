"""Offline preflight only: freeze sources/hash/plan before SDK or market requests."""
import argparse
import json
from pathlib import Path
from src.final_sample_matrix import FrozenSampleMatrix,load_final_config
from src.historical_client import HistoricalFatalError


def freeze(output,now=None):
    cfg=load_final_config();matrix=FrozenSampleMatrix(cfg)
    output=Path(output);output.mkdir(parents=True,exist_ok=False);(output/'diagnostics').mkdir()
    matrix.write(output)
    readiness='READY'
    try:matrix.require_completed(now)
    except HistoricalFatalError:readiness='UNRESOLVED: predetermined future/incomplete sessions; market requests forbidden'
    result=dict(sample_matrix_sha256=matrix.sample_matrix_sha256,primary_trade_dates=190,boundary_trade_dates=5,expected_logical_request_count=len(matrix.plan),api_request_count=0,readiness=readiness,earliest_all_sessions_completed=str(matrix.calendar.regular_close.max()))
    (output/'sample_freeze.json').write_text(json.dumps(result,indent=2))
    return matrix,result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();_,result=freeze(args.output);print(json.dumps(result,indent=2))
