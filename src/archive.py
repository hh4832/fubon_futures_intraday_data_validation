from datetime import datetime
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
from zoneinfo import ZoneInfo


def git_info(root):
    def git(*args):
        return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    return {'git_commit':git('rev-parse','HEAD'), 'git_branch':git('branch','--show-current'),
            'git_status':git('status','--porcelain')}


def start_run(root, cfg, sdk_info, auth_info):
    git = git_info(root)
    if git['git_status']:
        raise RuntimeError('Formal runs require clean Git working tree for provenance')
    now = datetime.now(ZoneInfo('Asia/Taipei'))
    path = Path(cfg['drive_root']) / f"{now:%Y%m%d_%H%M%S}_{git['git_commit'][:12]}"
    path.mkdir(parents=True, exist_ok=False)
    for directory in ['raw','data','diagnostics']:
        (path/directory).mkdir()
    info = dict(git, run_timestamp_asia_taipei=now.isoformat(), repository=cfg['repository'],
        python_version=sys.version, python_executable=sys.executable, platform=platform.platform(),
        architecture=platform.machine(), fubon_sdk_version=sdk_info['version'],
        sdk_download_url=sdk_info['download_url'], sdk_binary_sha256=sdk_info.get('binary_sha256'),
        sdk_official_hash_verified=sdk_info.get('official_hash_verified',False),
        drive_output_dir=str(path), account_types=auth_info['account_types'],
        futopt_account=auth_info['futopt_account'], login_status=auth_info['login_status'],
        sessions_tested=cfg['sessions'], timeframes_tested=cfg['timeframes'],
        execution_status='IN_PROGRESS', packages={p:importlib.metadata.version(p) for p in
            ['pandas','numpy','pyarrow','tqdm','PyYAML']})
    (path/'config_snapshot.json').write_text(json.dumps(cfg,ensure_ascii=False,indent=2))
    write_info(path,info)
    return path,info


def write_info(path, info):
    (path/'run_info.txt').write_text('\n'.join(f'{k}={json.dumps(v,ensure_ascii=False)}' for k,v in info.items()),encoding='utf-8')


def finish_run(reader, info, status='COMPLETED'):
    from .diagnostics import write_table
    write_table(reader.run_dir,'api_requests',reader.attempts,
        ['endpoint','attempt','status_code','error_type','message','rate_limit_hit'])
    info.update(execution_status=status, api_request_count=reader.calls,
        api_error_count=sum(bool(x['error_type']) for x in reader.attempts),
        rate_limit_hit=any(x['rate_limit_hit'] for x in reader.attempts))
    write_info(reader.run_dir,info)
    # Verification reads everything back; run directory already writes directly to Drive.
    for path in reader.run_dir.rglob('*'):
        if path.is_file() and path.stat().st_size==0:
            raise RuntimeError(f'Empty archive file: {path.name}')
    print('Archive verified:',reader.run_dir)
