#!/usr/bin/env python3
"""Build a self-contained Runespeak Lite desktop bundle for the current OS.

    python3 packaging/build_bundle.py --output release [--smoke] [--tts-smoke]

macOS   -> "Runespeak Lite.app" and Runespeak-Lite-<version>-macos-<arch>.dmg (unsigned, ad-hoc signed)
Windows -> Runespeak-Lite-<version>-windows-x64-setup.exe (Inno Setup, per-user, when iscc is
           installed) and Runespeak-Lite-<version>-windows-x64-portable.zip

Each bundle embeds a relocatable CPython from python-build-standalone (Tk included), installs the
hash-checked runtime dependencies in packaging/requirements-bundle.txt as binary wheels only, and
copies the runespeak package into that interpreter's site-packages. No system Python, uv or
Homebrew is needed to run it. Phonon model files download on the first narration, as on Linux.
Linux keeps its .deb (scripts/build-deb.py); this script refuses to run there.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import time
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = 'Runespeak Lite'
FILE_NAME = 'Runespeak-Lite'
BUNDLE_ID = 'org.runespeak.lite'
PBS_REPO = 'astral-sh/python-build-standalone'
PYTHON_SERIES = '3.14'
REQUIREMENTS = ROOT / 'packaging' / 'requirements-bundle.txt'
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '*.pyo')
IMPORT_CHECK = ('import tkinter, ptts, trafilatura, tkinterdnd2, PIL, lxml, pypdfium2, sounddevice; '
                'from runespeak.app import App; from runespeak import winplay, worker, bridge; '
                'print("imports ok", tkinter.TkVersion)')


def log(message):
    print(f'[bundle] {message}', flush=True)


def run(args, **kwargs):
    log('$ ' + ' '.join(map(str, args)))
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def version():
    return tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']


def target():
    machine = platform.machine().lower()
    arch = {'amd64': 'x86_64', 'x86_64': 'x86_64', 'arm64': 'aarch64', 'aarch64': 'aarch64'}[machine]
    if sys.platform == 'darwin':
        return 'macos', arch, f'{arch}-apple-darwin'
    if sys.platform == 'win32':
        return 'windows', arch, f'{arch}-pc-windows-msvc'
    raise SystemExit('Linux uses the .deb package: python3 scripts/build-deb.py')


def _get(url, accept='application/octet-stream'):
    headers = {'Accept': accept, 'User-Agent': 'runespeak-lite-bundler'}
    token = os.environ.get('GITHUB_TOKEN')
    if token and 'api.github.com' in url:
        headers['Authorization'] = 'Bearer ' + token
    context = None
    try:  # python.org macOS builds ship without a CA bundle; use certifi when it is installed.
        import certifi
        import ssl
        context = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=120, context=context)


def find_python(triple, tag=None):
    """(asset name, download URL, release tag) of a stripped install_only CPython build."""
    url = f'https://api.github.com/repos/{PBS_REPO}/releases/' + (f'tags/{tag}' if tag else 'latest')
    with _get(url, 'application/vnd.github+json') as response:
        release = json.load(response)
    pattern = re.compile(rf'cpython-{re.escape(PYTHON_SERIES)}\.\d+\+\d+-{re.escape(triple)}-install_only_stripped\.tar\.gz')
    for asset in release['assets']:
        if pattern.fullmatch(asset['name']):
            return asset['name'], asset['browser_download_url'], release['tag_name']
    raise SystemExit(f'No CPython {PYTHON_SERIES} build for {triple} in {PBS_REPO} {release["tag_name"]}')


def download(url, dest, cache):
    cache.mkdir(parents=True, exist_ok=True)
    cached = cache / url.rsplit('/', 1)[1].replace('%2B', '+')
    if not cached.is_file():
        log(f'downloading {url}')
        with _get(url) as response, open(str(cached) + '.part', 'wb') as out:
            shutil.copyfileobj(response, out)
        os.replace(str(cached) + '.part', cached)
    shutil.copy2(cached, dest)
    return dest


def site_packages(python_root, os_name):
    if os_name == 'windows':
        return python_root / 'Lib' / 'site-packages'
    return python_root / 'lib' / f'python{PYTHON_SERIES}' / 'site-packages'


def install_runtime(python_root, os_name, work, cache, pbs_tag):
    name, url, tag = find_python(target()[2], pbs_tag)
    archive = download(url, work / name, cache)
    with tarfile.open(archive) as tar:
        tar.extractall(work / 'pbs', filter='data')
    shutil.move(str(work / 'pbs' / 'python'), python_root)
    py = python_root / ('python.exe' if os_name == 'windows' else 'bin/python3')
    run([py, '-m', 'pip', 'install', '--disable-pip-version-check', '--no-warn-script-location',
         '--only-binary=:all:', '--require-hashes', '-r', REQUIREMENTS])
    packages = site_packages(python_root, os_name)
    shutil.copytree(ROOT / 'runespeak', packages / 'runespeak', ignore=IGNORE)
    # Trim weight that is never used at runtime (keep tkinter: the whole UI is Tk).
    lib = python_root / 'Lib' if os_name == 'windows' else python_root / 'lib' / f'python{PYTHON_SERIES}'
    for junk in ('test', 'idlelib', 'turtledemo', 'ensurepip', 'lib2to3'):
        shutil.rmtree(lib / junk, ignore_errors=True)
    return py, {'cpython_asset': name, 'pbs_release': tag}


def compile_all(py, *roots):
    run([py, '-m', 'compileall', '-q', '-j', '0', *roots], stdout=subprocess.DEVNULL)


def smoke(py, env=None, tts=False):
    """Import everything the app needs; optionally synthesize one real sentence."""
    run([py, '-I', '-c', IMPORT_CHECK], env=env)
    if not tts:
        return
    with tempfile.TemporaryDirectory(prefix='runespeak-smoke-') as folder:
        job = Path(folder) / 'job.json'
        job.write_text(json.dumps({'directory': folder, 'voice': 'default',
                                   'chunks': ['Runespeak Lite is ready on this computer.']}))
        run([py, '-I', '-m', 'runespeak.worker', job], env=env)
        wav = Path(folder) / '00000.wav'
        if not wav.is_file() or wav.stat().st_size < 1000:
            raise SystemExit('TTS smoke test produced no audio')
        log(f'TTS smoke OK ({wav.stat().st_size} bytes)')


def mac_icon(work, dest):
    """SVG (or the 128 px PNG) -> .icns with stock macOS tools. Best effort."""
    try:
        png = work / 'icon-1024.png'
        try:
            run(['qlmanage', '-t', '-s', '1024', '-o', work, ROOT / 'assets' / 'runespeak.svg'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (work / 'runespeak.svg.png').rename(png)
        except (OSError, subprocess.CalledProcessError):
            shutil.copy2(ROOT / 'runespeak' / 'resources' / 'icon.png', png)
        iconset = work / 'icon.iconset'
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                suffix = '' if scale == 1 else '@2x'
                run(['sips', '-z', px, px, png, '--out', iconset / f'icon_{size}x{size}{suffix}.png'], stdout=subprocess.DEVNULL)
        run(['iconutil', '-c', 'icns', iconset, '-o', dest])
        return True
    except (OSError, subprocess.CalledProcessError) as error:
        log(f'WARNING: icon not generated ({error})')
        return False


MAC_LAUNCHER = '''#!/bin/bash
# Runespeak Lite launcher: embedded CPython with Tk. No system Python is used.
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONHOME="$CONTENTS/Resources/python"
export PYTHONNOUSERSITE=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
# Finder starts apps with a minimal PATH; add Homebrew/MacPorts for optional Poppler/Tesseract.
export PATH="/opt/homebrew/bin:/usr/local/bin:/opt/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
LOGDIR="$HOME/Library/Logs/runespeak-lite"; mkdir -p "$LOGDIR"
cd "$HOME" || exit 1
# macOS passes -psn_* when launched from Finder on old systems; drop it.
args=(); for a in "$@"; do case "$a" in -psn_*) ;; *) args+=("$a");; esac; done
"$CONTENTS/MacOS/runespeak-python" -m runespeak.app "${args[@]}" >>"$LOGDIR/launcher.log" 2>&1
status=$?
if [ $status -ne 0 ]; then
  detail="$(tail -n 12 "$LOGDIR/launcher.log" | tr '"' "'" )"
  /usr/bin/osascript -e "display alert \\"Runespeak Lite could not start\\" message \\"$detail\\n\\nFull log: $LOGDIR/launcher.log\\" as critical" >/dev/null 2>&1
fi
exit $status
'''

MAC_READ_CLI = '''#!/bin/bash
# Send copied text or a playback command to the open app, e.g. from a Shortcuts/Automator action:
#   "/Applications/Runespeak Lite.app/Contents/MacOS/runespeak-read" --clipboard
CONTENTS="$(cd "$(dirname "$0")/.." && pwd)"
export PYTHONHOME="$CONTENTS/Resources/python" PYTHONNOUSERSITE=1 PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
exec "$CONTENTS/MacOS/runespeak-python" -m runespeak.bridge "$@"
'''

MAC_README = '''Runespeak Lite {version} (unsigned alpha) for macOS ({arch})

1. Drag "Runespeak Lite" onto Applications.
2. First launch: this alpha is not notarized, so Gatekeeper blocks a double-click.
   In Applications, right-click (or Control-click) "Runespeak Lite", choose Open, then Open again.
   If macOS still refuses: System Settings > Privacy & Security > "Open Anyway".
3. The first narration downloads the Phonon 31M English model (internet needed once);
   after that narration runs locally.
4. Optional: scanned PDFs and images need Poppler and Tesseract (for example
   "brew install poppler tesseract"); DOC/RTF files need LibreOffice.

Preferences: ~/Library/Application Support/runespeak-lite
Logs:        ~/Library/Logs/runespeak-lite
Saved WAVs:  ~/Music/Runespeak
'''

DOCUMENT_TYPES = [
    {'CFBundleTypeName': 'WAV narration', 'CFBundleTypeRole': 'Viewer', 'LSHandlerRank': 'Alternate',
     'LSItemContentTypes': ['com.microsoft.waveform-audio']},
    {'CFBundleTypeName': 'Readable document', 'CFBundleTypeRole': 'Viewer', 'LSHandlerRank': 'Alternate',
     'LSItemContentTypes': ['public.plain-text', 'net.daringfireball.markdown', 'com.adobe.pdf',
                            'org.openxmlformats.wordprocessingml.document', 'com.microsoft.word.doc',
                            'org.oasis-open.opendocument.text', 'public.rtf', 'public.png', 'public.jpeg',
                            'public.tiff', 'com.microsoft.bmp', 'org.webmproject.webp']},
]


def build_macos(out, work, ver, arch, cache, pbs_tag, do_smoke, tts):
    app = work / f'{APP_NAME}.app'
    contents = app / 'Contents'
    resources = contents / 'Resources'
    macos = contents / 'MacOS'
    macos.mkdir(parents=True)
    resources.mkdir()
    python_root = resources / 'python'
    _, meta = install_runtime(python_root, 'macos', work, cache, pbs_tag)
    # Run the interpreter from Contents/MacOS so macOS treats the window as this app
    # (Dock name and icon). libpython and Tcl/Tk are found through @executable_path/../lib.
    shutil.copy2(python_root / 'bin' / f'python{PYTHON_SERIES}', macos / 'runespeak-python')
    os.symlink('Resources/python/lib', contents / 'lib')
    for name, body in (('runespeak-lite', MAC_LAUNCHER), ('runespeak-read', MAC_READ_CLI)):
        (macos / name).write_text(body)
        (macos / name).chmod(0o755)
    has_icon = mac_icon(work, resources / 'AppIcon.icns')
    info = {
        'CFBundleName': APP_NAME, 'CFBundleDisplayName': APP_NAME, 'CFBundleIdentifier': BUNDLE_ID,
        'CFBundleExecutable': 'runespeak-lite', 'CFBundlePackageType': 'APPL', 'CFBundleVersion': ver,
        'CFBundleShortVersionString': ver, 'LSMinimumSystemVersion': '11.0',
        'NSHighResolutionCapable': True, 'LSApplicationCategoryType': 'public.app-category.productivity',
        'CFBundleDocumentTypes': DOCUMENT_TYPES,
        'NSHumanReadableCopyright': 'Copyright 2026 mso-docs and Runespeak Lite contributors. Apache-2.0.',
    }
    if has_icon:
        info['CFBundleIconFile'] = 'AppIcon'
    with open(contents / 'Info.plist', 'wb') as handle:
        plistlib.dump(info, handle)
    for name in ('LICENSE', 'NOTICE', 'README.md'):
        shutil.copy2(ROOT / name, resources / name)
    py = macos / 'runespeak-python'
    env = dict(os.environ, PYTHONHOME=str(python_root), PYTHONNOUSERSITE='1')
    compile_all(py, python_root / 'lib')
    if do_smoke:
        smoke(py, env, tts)
    run(['codesign', '--force', '--deep', '--sign', '-', app])
    final_app = out / app.name
    if final_app.exists():
        shutil.rmtree(final_app)
    run(['ditto', app, final_app])
    stage = work / 'dmg'
    stage.mkdir()
    run(['ditto', app, stage / app.name])
    os.symlink('/Applications', stage / 'Applications')
    (stage / 'READ ME FIRST.txt').write_text(MAC_README.format(version=ver, arch=arch))
    dmg = out / f'{FILE_NAME}-{ver}-macos-{arch}.dmg'
    if dmg.exists():
        dmg.unlink()
    run(['hdiutil', 'create', '-volname', f'{APP_NAME} {ver}', '-srcfolder', stage, '-ov', '-format', 'UDZO', dmg])
    return [dmg], meta


INNO = r'''
#define AppName "Runespeak Lite"
[Setup]
AppId={{8F3D6C21-7B4A-4E59-A1C2-5D9E0B7F3A64}
AppName={#AppName}
AppVersion=__VERSION__
AppPublisher=mso-docs
DefaultDirName={localappdata}\Programs\Runespeak Lite
DefaultGroupName=Runespeak Lite
PrivilegesRequired=lowest
OutputDir=__OUTDIR__
OutputBaseFilename=Runespeak-Lite-__VERSION__-windows-x64-setup
SetupIconFile=__SRC__\runespeak.ico
UninstallDisplayIcon={app}\runespeak.ico
Compression=lzma2/max
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
DisableProgramGroupPage=yes
LicenseFile=__SRC__\LICENSE
[Files]
Source: "__SRC__\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\Runespeak Lite"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m runespeak.app"; WorkingDir: "{userdocs}"; IconFilename: "{app}\runespeak.ico"
Name: "{userdesktop}\Runespeak Lite"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m runespeak.app"; WorkingDir: "{userdocs}"; IconFilename: "{app}\runespeak.ico"; Tasks: desktopicon
[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m runespeak.app"; WorkingDir: "{userdocs}"; Description: "Launch Runespeak Lite"; Flags: nowait postinstall skipifsilent
'''

WIN_CMD = '''@echo off
rem Portable launcher for Runespeak Lite (no installer needed).
set PYTHONNOUSERSITE=1
set PYTHONUTF8=1
start "" "%~dp0python\\pythonw.exe" -I -m runespeak.app %*
'''

WIN_READ_CMD = '''@echo off
rem Send copied text or a playback command to the open app, e.g. from a keyboard shortcut:
rem   runespeak-read.cmd --clipboard
"%~dp0python\\python.exe" -I -m runespeak.bridge %*
'''


def build_windows(out, work, ver, arch, cache, pbs_tag, do_smoke, tts):
    root = work / APP_NAME
    py, meta = install_runtime(root / 'python', 'windows', work, cache, pbs_tag)
    compile_all(py, root / 'python' / 'Lib')
    (root / f'{APP_NAME}.cmd').write_text(WIN_CMD)
    (root / 'runespeak-read.cmd').write_text(WIN_READ_CMD)
    for name in ('LICENSE', 'NOTICE', 'README.md'):
        shutil.copy2(ROOT / name, root / name)
    run([py, '-I', '-c', 'import sys; from PIL import Image; '
         'Image.open(sys.argv[1]).save(sys.argv[2], sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128)])',
         ROOT / 'runespeak' / 'resources' / 'icon.png', root / 'runespeak.ico'])
    if do_smoke:
        smoke(py, None, tts)
    artifacts = []
    portable = out / f'{FILE_NAME}-{ver}-windows-x64-portable.zip'
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            archive.write(path, Path(APP_NAME) / path.relative_to(root))
    artifacts.append(portable)
    iscc = shutil.which('iscc') or next((str(p) for p in (
        Path(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')) / 'Inno Setup 6' / 'ISCC.exe',
        Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Inno Setup 6' / 'ISCC.exe') if p.is_file()), None)
    if iscc:
        script = work / 'installer.iss'
        script.write_text(INNO.replace('__VERSION__', ver).replace('__OUTDIR__', str(out)).replace('__SRC__', str(root)))
        run([iscc, '/Q', script])
        artifacts.append(out / f'{FILE_NAME}-{ver}-windows-x64-setup.exe')
    else:
        log('WARNING: Inno Setup (iscc) not found; only the portable .zip was built')
    return artifacts, meta


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1 << 20), b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', default='release', help='artifact folder (default: release)')
    parser.add_argument('--cache', default=str(ROOT / '.bundle-cache'), help='download cache folder')
    parser.add_argument('--pbs-tag', help='python-build-standalone release tag (default: latest)')
    parser.add_argument('--smoke', action='store_true', help='import-check the bundled app before packaging')
    parser.add_argument('--tts-smoke', action='store_true', help='also synthesize one sentence (downloads the model)')
    options = parser.parse_args()
    os_name, arch, _ = target()
    ver = version()
    out = Path(options.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with tempfile.TemporaryDirectory(prefix='runespeak-bundle-') as tmp:
        work = Path(tmp)
        build = build_macos if os_name == 'macos' else build_windows
        artifacts, meta = build(out, work, ver, arch, Path(options.cache), options.pbs_tag,
                                options.smoke or options.tts_smoke, options.tts_smoke)
    sums = out / f'SHA256SUMS-{os_name}-{arch}'
    sums.write_text(''.join(f'{sha256(path)}  {path.name}\n' for path in artifacts))
    manifest = dict(meta, version=ver, os=os_name, arch=arch, artifacts=[p.name for p in artifacts],
                    seconds=round(time.time() - started))
    (out / f'BUILD-MANIFEST-{os_name}-{arch}.json').write_text(json.dumps(manifest, indent=2) + '\n')
    for path in artifacts:
        log(f'built {path} ({path.stat().st_size / 1e6:.1f} MB)')


if __name__ == '__main__':
    main()
