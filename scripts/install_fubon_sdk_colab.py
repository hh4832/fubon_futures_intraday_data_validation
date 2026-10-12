"""Official-only SDK installation; no binary or guessed official hash in Git."""
import hashlib
import importlib
import importlib.metadata
import platform
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlparse
from urllib.request import urlopen
import zipfile


def installed_version():
    try:
        return importlib.metadata.version('fubon_neo')
    except importlib.metadata.PackageNotFoundError:
        return None


def install(version, url, official_sha256=None):
    print({'python': sys.version, 'executable': sys.executable,
           'os': platform.system(), 'architecture': platform.machine()})
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or not (3, 8) <= sys.version_info[:2] <= (3, 13):
        raise RuntimeError('Requires Linux x86_64 and Python 3.8–3.13')
    if installed_version() == version:
        importlib.import_module('fubon_neo')
        print('SDK already installed:', version)
        return {'version': version, 'download_url': url, 'binary_sha256': 'not downloaded'}
    if urlparse(url).hostname != 'www.fbs.com.tw' or urlparse(url).scheme != 'https':
        raise ValueError('Only official HTTPS SDK download accepted')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('Invalid SDK version')
    with tempfile.TemporaryDirectory(prefix='fubon_sdk_', dir='/tmp') as tmp:
        dest = Path(tmp) / 'sdk.zip'
        with urlopen(url, timeout=120) as r:
            if urlparse(r.geturl()).hostname != 'www.fbs.com.tw':
                raise RuntimeError('Non-official redirect rejected')
            dest.write_bytes(r.read())
        observed_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        if official_sha256 and observed_hash != official_sha256:
            raise RuntimeError('Official SHA256 mismatch')
        with zipfile.ZipFile(dest) as z:
            for entry in z.infolist():
                if not (Path(tmp) / entry.filename).resolve().is_relative_to(Path(tmp).resolve()):
                    raise RuntimeError('Unsafe SDK archive path')
            z.extractall(tmp)
        wheels = list(Path(tmp).rglob('fubon_neo-*.whl'))
        if len(wheels) != 1 or not wheels[0].name.startswith(f'fubon_neo-{version}-'):
            raise RuntimeError('Expected exactly one matching official SDK wheel')
        subprocess.run([sys.executable, '-m', 'pip', 'install', str(wheels[0])], check=True)
    importlib.invalidate_caches()
    importlib.import_module('fubon_neo')
    if installed_version() != version:
        raise RuntimeError('Installed SDK version mismatch')
    print('SDK installed:', installed_version())
    return {'version': version, 'download_url': url, 'binary_sha256': observed_hash,
            'official_hash_verified': bool(official_sha256)}


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.config import load_config
    c = load_config()
    install(c['sdk_version'], c['sdk_download_url'], c['sdk_official_sha256'])
