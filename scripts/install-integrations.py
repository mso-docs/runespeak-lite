#!/usr/bin/env python3
"""Install per-user selection helpers, Firefox native host and optional GNOME shortcut."""
import argparse
import ast
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

HOST = 'org.runespeak.lite'
EXTENSION = 'runespeak-lite@local'
GNOME_PATH = '/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/runespeak-lite/'


def install_shortcut(command):
    if not shutil.which('gsettings'):
        raise SystemExit('gsettings is unavailable. Add a desktop shortcut manually for ' + str(command))
    schema = 'org.gnome.settings-daemon.plugins.media-keys'
    setting = subprocess.check_output(['gsettings', 'get', schema, 'custom-keybindings'], text=True).strip()
    entries = ast.literal_eval(setting.removeprefix('@as '))
    target = 'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:' + GNOME_PATH
    # Preserve existing shortcuts and avoid silently taking a custom binding.
    for entry in entries:
        if entry != GNOME_PATH:
            other = 'org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:' + entry
            binding = subprocess.check_output(['gsettings', 'get', other, 'binding'], text=True).strip().lower()
            if binding.strip("'") in ('<super><shift>r', '<shift><super>r'):
                raise SystemExit('Super+Shift+R is already assigned. Set a different shortcut manually.')
    for key, value in [('name', 'Runespeak — read copied text'), ('command', shlex.quote(str(command)) + ' --clipboard'), ('binding', '<Super><Shift>r')]:
        subprocess.run(['gsettings', 'set', target, key, json.dumps(value)], check=True)
    if GNOME_PATH not in entries:
        entries.append(GNOME_PATH)
        subprocess.run(['gsettings', 'set', schema, 'custom-keybindings', repr(entries)], check=True)
    print('GNOME shortcut: Ctrl+C, then Super+Shift+R reads copied text while Runespeak is open.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gnome-shortcut', action='store_true')
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    python = project / '.venv/bin/python'
    if not python.exists():
        raise SystemExit('Run uv sync first.')
    home = Path.home()
    binaries = home / '.local/bin'
    binaries.mkdir(parents=True, exist_ok=True)
    helper = binaries / 'runespeak-read'
    helper.write_text('#!/bin/sh\n' + f'cd {shlex.quote(str(project))} || exit 1\nexec {shlex.quote(str(python))} -m runespeak.bridge "$@"\n')
    helper.chmod(0o755)
    host = binaries / 'runespeak-firefox-host'
    host.write_text('#!/bin/sh\n' + f'exec {shlex.quote(str(helper))} --native-host "$@"\n')
    host.chmod(0o755)
    hosts = home / '.mozilla/native-messaging-hosts'
    hosts.mkdir(parents=True, exist_ok=True)
    manifest = hosts / (HOST + '.json')
    manifest.write_text(json.dumps({'name': HOST, 'description': 'Read selections in the running Runespeak app', 'path': str(host), 'type': 'stdio', 'allowed_extensions': [EXTENSION]}, indent=2) + '\n')
    print(f'Installed selection helper: {helper}\nInstalled Firefox native host: {manifest}')
    if args.gnome_shortcut:
        install_shortcut(helper)
    print(f'Firefox: about:debugging#/runtime/this-firefox → Load Temporary Add-on → {project / "firefox-extension/manifest.json"}')


if __name__ == '__main__':
    main()
