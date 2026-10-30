#!/usr/bin/env python3
"""Assemble alpha assets from an allowlist, never the whole workspace."""
import argparse
import gzip
import hashlib
import io
import json
import re
from pathlib import Path
import tarfile
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ('runespeak', 'scripts', 'assets', 'firefox-extension', 'tests', 'docs',
           '.github', 'README.md', 'LICENSE', 'NOTICE', 'pyproject.toml', 'uv.lock', '.gitignore')


def files():
    for name in ALLOWED:
        path = ROOT/name
        candidates = path.rglob('*') if path.is_dir() else [path]
        for file in sorted(candidates):
            if file.is_file() and not file.is_symlink() and '__pycache__' not in file.parts and file.suffix not in ('.pyc', '.wav', '.mp3', '.ogg', '.flac', '.pcm'):
                yield file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT/'dist')
    args = parser.parse_args()
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    filename = f'runespeak-lite-{version}-linux-source.tar.gz'
    with (output/filename).open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode='w') as archive:
        for file in files():
            data = file.read_bytes()
            entry = tarfile.TarInfo(f'runespeak-lite-{version}/'+file.relative_to(ROOT).as_posix())
            entry.size = len(data)
            entry.mode = 0o755 if file.parent.name == 'scripts' and file.suffix == '.py' else 0o644
            archive.addfile(entry, io.BytesIO(data))
    extension_version = json.loads((ROOT/'firefox-extension/manifest.json').read_text())['version']
    with zipfile.ZipFile(output/f'runespeak-firefox-{extension_version}-unsigned.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ('LICENSE', 'NOTICE'):
            entry = zipfile.ZipInfo(name, date_time=(2020,1,1,0,0,0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (ROOT/name).read_bytes())
        for file in sorted((ROOT/'firefox-extension').iterdir()):
            if file.is_file():
                entry = zipfile.ZipInfo(file.name, date_time=(2020,1,1,0,0,0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(entry, file.read_bytes())
    deb_version = re.sub(r'a(\d+)$', r'~alpha\1', version)
    expected = {f'runespeak-lite_{version}_amd64.deb', filename, f'runespeak-firefox-{extension_version}-unsigned.zip',
                f'runespeak_lite-{version}.tar.gz', f'runespeak_lite-{version}-py3-none-any.whl'}
    artifacts = sorted(file for file in output.iterdir() if file.is_file() and not file.name.startswith('.') and file.name != 'SHA256SUMS')
    if {file.name for file in artifacts} != expected:
        raise SystemExit('Expected exactly the current wheel, sdist, source bundle and extension; use a clean output directory')
    (output/'SHA256SUMS').write_text(''.join(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.name+'\n' for file in artifacts))
    print('\n'.join(file.name for file in artifacts))


if __name__ == '__main__':
    main()
