#!/usr/bin/env python3
"""Build an Ubuntu/Debian x86-64 desktop package without root privileges."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version']
    deb_version = re.sub(r'a(\d+)$', r'~alpha\1', version)
    wheel = ROOT/'dist'/f'runespeak_lite-{version}-py3-none-any.whl'
    if not wheel.is_file():
        raise SystemExit('Run uv build first')
    with tempfile.TemporaryDirectory(prefix='runespeak-deb-') as temporary:
        root = Path(temporary)
        payload = root/'usr/share/runespeak-lite'
        payload.mkdir(parents=True)
        legal = root/'usr/share/doc/runespeak-lite'
        legal.mkdir(parents=True)
        shutil.copyfile(ROOT/'LICENSE', legal/'copyright')
        shutil.copyfile(ROOT/'NOTICE', legal/'NOTICE')
        shutil.copyfile(wheel, payload/wheel.name)
        shutil.copyfile(ROOT/'scripts/linux-launcher.py', payload/'launcher.py')
        shutil.copyfile(ROOT/'runespeak/resources/icon.png', payload/'icon.png')
        (payload/'app.json').write_text(json.dumps({'version': version, 'wheel': wheel.name}))
        subprocess.run(['uv','export','--frozen','--no-dev','--no-emit-project','--no-header','--no-annotate','--output-file',str(payload/'requirements.txt')], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        binary = root/'usr/bin/runespeak-lite'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/share/runespeak-lite/launcher.py "$@"\n')
        binary.chmod(0o755)
        desktop = root/'usr/share/applications/runespeak-lite.desktop'
        desktop.parent.mkdir(parents=True)
        desktop.write_text('[Desktop Entry]\nVersion=1.0\nType=Application\nName=Runespeak Lite\nComment=A speaking tome for weary adventurers\nExec=runespeak-lite %f\nIcon=runespeak-lite\nTerminal=false\nCategories=AudioVideo;Audio;\nStartupNotify=true\nStartupWMClass=Runespeaklite\nMimeType=audio/x-wav;audio/wav;application/pdf;text/plain;text/markdown;application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/vnd.oasis.opendocument.text;image/png;image/jpeg;\n')
        for location, source in [('scalable/apps/runespeak-lite.svg', ROOT/'assets/runespeak.svg'), ('128x128/apps/runespeak-lite.png', ROOT/'runespeak/resources/icon.png')]:
            icon = root/'usr/share/icons/hicolor'/location
            icon.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, icon)
        control = root/'DEBIAN/control'
        control.parent.mkdir()
        control.write_text(f'Package: runespeak-lite\nVersion: {deb_version}\nSection: sound\nPriority: optional\nArchitecture: amd64\nMaintainer: Runespeak Lite contributors\nDepends: python3 (>= 3.11), python3-tk, python3-venv, pipewire-bin | pulseaudio-utils | alsa-utils, libxcursor1, libxrandr2, libxinerama1, ca-certificates\nRecommends: poppler-utils, tesseract-ocr, tesseract-ocr-eng, libreoffice-writer\nDescription: Local Phonon speaking tome (alpha)\n Reads webpages and documents with local speech synthesis.\n First launch downloads locked Python dependencies into a user-owned runtime.\n Speech model files download on first use.\n')
        output = ROOT/'dist'/f'runespeak-lite_{version}_amd64.deb'
        subprocess.run(['dpkg-deb','--root-owner-group','--build',str(root),str(output)], check=True)
        print(output)


if __name__ == '__main__':
    main()
