#!/usr/bin/env python3
"""Verify release checksums, packaged resources and source-bundle hygiene."""
import hashlib
import io
import subprocess
import json
import re
import tomllib
from pathlib import Path
import tarfile
import zipfile


def main():
    directory = Path('dist')
    sums = directory/'SHA256SUMS'
    if not sums.is_file():
        raise SystemExit('Build missing SHA256SUMS')
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root/'pyproject.toml').read_text())['project']['version']
    extension = json.loads((root/'firefox-extension/manifest.json').read_text())['version']
    deb_version = re.sub(r'a(\d+)$', r'~alpha\1', version)
    expected = {f'runespeak-lite_{version}_amd64.deb', f'runespeak_lite-{version}.tar.gz', f'runespeak_lite-{version}-py3-none-any.whl',
                f'runespeak-lite-{version}-linux-source.tar.gz', f'runespeak-firefox-{extension}-unsigned.zip'}
    listed = set()
    for line in sums.read_text().splitlines():
        digest, name = line.split('  ',1)
        if Path(name).name != name or name in listed:
            raise SystemExit('Invalid artifact name')
        listed.add(name)
        if hashlib.sha256((directory/name).read_bytes()).hexdigest() != digest:
            raise SystemExit('Checksum mismatch: '+name)
    if listed != expected:
        raise SystemExit('Unexpected or missing release assets')
    if {file.name for file in directory.iterdir() if file.is_file() and not file.name.startswith('.') and file.name != 'SHA256SUMS'} != listed:
        raise SystemExit('Unlisted release artifact')
    wheels = list(directory.glob('*.whl'))
    if len(wheels) != 1:
        raise SystemExit('Expected one Python wheel')
    with zipfile.ZipFile(wheels[0]) as wheel:
        for name in ('icon.png','voices.json','narrator.png','highlight.png','back10.png','forward10.png','navigate.png'):
            if 'runespeak/resources/'+name not in wheel.namelist():
                raise SystemExit('Missing wheel resource: '+name)
    bundles = list(directory.glob('*-linux-source.tar.gz'))
    if len(bundles) != 1:
        raise SystemExit('Expected one Linux source bundle')
    for archive in directory.glob('*.tar.gz'):
        with tarfile.open(archive) as package:
            for member in package.getmembers():
                path = Path(member.name)
                if path.is_absolute() or '..' in path.parts or member.issym() or member.islnk():
                    raise SystemExit('Unsafe archive path: '+member.name)
                if any(part in ('.git','.venv','.aws','.codex','.agents','__pycache__','models') for part in path.parts) or path.suffix in ('.wav','.mp3','.pcm','.pyc') or any(part.startswith('mech-interp') or part.startswith('.env') for part in path.parts):
                    raise SystemExit('Local artifact in release: '+member.name)
    deb = directory/f'runespeak-lite_{version}_amd64.deb'
    content = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', str(deb)])
    with tarfile.open(fileobj=io.BytesIO(content)) as package:
        names = {member.name.removeprefix('./'): member for member in package.getmembers()}
        for name in ('usr/bin/runespeak-lite', 'usr/share/applications/runespeak-lite.desktop',
                     'usr/share/runespeak-lite/launcher.py', 'usr/share/runespeak-lite/requirements.txt',
                     'usr/share/runespeak-lite/app.json', 'usr/share/icons/hicolor/128x128/apps/runespeak-lite.png'):
            if name not in names:
                raise SystemExit('Missing desktop package file: '+name)
        wheel_name = 'usr/share/runespeak-lite/'+wheels[0].name
        if package.extractfile(names[wheel_name]).read() != wheels[0].read_bytes():
            raise SystemExit('Desktop package wheel differs from release wheel')
        requirements = package.extractfile(names['usr/share/runespeak-lite/requirements.txt']).read()
        if b'--hash=sha256:' not in requirements:
            raise SystemExit('Missing locked dependency hashes')
        if names['usr/bin/runespeak-lite'].mode & 0o111 != 0o111:
            raise SystemExit('Desktop launcher is not executable')
    print('Checksums, desktop package, wheel resources and source archive hygiene passed')


if __name__ == '__main__':
    main()
