#!/usr/bin/env python3
"""Register this checkout as a per-user Linux desktop application."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess


def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'


def main():
    project = Path(__file__).resolve().parents[1]
    python = project / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Run uv sync in the project folder before installing the launcher.')
    check = subprocess.run([str(python), '-c', 'import tkinter; import ptts; import trafilatura'], capture_output=True, text=True)
    if check.returncode:
        raise SystemExit('App dependencies unavailable. Install python3-tk and run uv sync.\n' + check.stderr)
    home = Path.home()
    data = Path(os.environ.get('XDG_DATA_HOME', home / '.local/share'))
    applications = data / 'applications'
    scalable = data / 'icons/hicolor/scalable/apps'
    raster = data / 'icons/hicolor/128x128/apps'
    scalable.mkdir(parents=True, exist_ok=True)
    raster.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / 'assets/runespeak.svg', scalable / 'runespeak-lite.svg')
    shutil.copyfile(project / 'runespeak/resources/icon.png', raster / 'runespeak-lite.png')
    bin_dir = home / '.local/bin'
    applications.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / 'runespeak-lite'
    launcher.write_text('#!/bin/sh\n'
                        'state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/runespeak-lite"\n'
                        'mkdir -p "$state_dir"\n'
                        f'cd {shlex.quote(str(project))} || exit 1\n'
                        f'exec {shlex.quote(str(python))} -m runespeak.app "$@" >>"$state_dir/app.log" 2>&1\n')
    launcher.chmod(0o755)
    desktop = applications / 'runespeak-lite.desktop'
    desktop.write_text('[Desktop Entry]\nVersion=1.0\nType=Application\n'
                       'Name=Runespeak Lite\nComment=Read webpages and selected text aloud locally\n'
                       f'Exec={desktop_quote(launcher)} %f\n'
                       'Icon=runespeak-lite\n'
                       'Terminal=false\nCategories=AudioVideo;Audio;\nMimeType=audio/x-wav;audio/wav;audio/vnd.wave;application/pdf;text/plain;text/markdown;application/msword;application/vnd.openxmlformats-officedocument.wordprocessingml.document;application/vnd.oasis.opendocument.text;image/png;image/jpeg;image/tiff;image/webp;\n'
                       'StartupNotify=true\nStartupWMClass=Runespeaklite\n')
    desktop.chmod(0o755)
    if shutil.which('desktop-file-validate'):
        subprocess.run(['desktop-file-validate', str(desktop)], check=True)
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', str(applications)], check=False)
    if shutil.which('gtk-update-icon-cache'):
        subprocess.run(['gtk-update-icon-cache', '-f', '-t', str(data / 'icons/hicolor')], check=False)
    if shutil.which('xdg-mime'):
        for mime in ('audio/x-wav', 'audio/wav', 'audio/vnd.wave'):
            current = subprocess.check_output(['xdg-mime', 'query', 'default', mime], text=True).strip()
            if not current:
                subprocess.run(['xdg-mime', 'default', desktop.name, mime], check=True)
    print(f'Installed {desktop}\nOpen Applications and search for Runespeak Lite. You can pin it to the dock.')


if __name__ == '__main__':
    main()
