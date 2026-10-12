from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

def load_config(path=None):
    with Path(path or ROOT / 'config/validation_config.yaml').open() as f:
        cfg = yaml.safe_load(f)
    if cfg['timezone'] != 'Asia/Taipei':
        raise ValueError('timezone must be Asia/Taipei')
    if cfg['sessions'] != ['regular', 'afterhours'] or cfg['timeframes'] != [1, 5, 15, 60]:
        raise ValueError('phase-one request scope must remain small and fixed')
    if not 1 <= cfg['max_attempts'] <= 3:
        raise ValueError('max_attempts must be 1..3')
    return cfg
