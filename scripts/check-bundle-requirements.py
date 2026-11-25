#!/usr/bin/env python3
"""Fail when packaging/requirements-bundle.txt no longer matches uv.lock.

    python3 scripts/check-bundle-requirements.py          # check (CI)
    python3 scripts/check-bundle-requirements.py --write  # regenerate after `uv lock`
"""
import argparse
import difflib
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / 'packaging' / 'requirements-bundle.txt'
EXPORT = ['uv', 'export', '--frozen', '--no-dev', '--no-emit-project', '--no-header', '--format', 'requirements-txt']


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--write', action='store_true', help='rewrite the bundle requirements from uv.lock')
    options = parser.parse_args()
    if not shutil.which('uv'):
        raise SystemExit('uv is required: https://docs.astral.sh/uv/')
    expected = subprocess.run(EXPORT, cwd=ROOT, check=True, capture_output=True, text=True).stdout
    if options.write:
        TARGET.write_text(expected)
        print(f'Wrote {TARGET.relative_to(ROOT)}')
        return
    current = TARGET.read_text() if TARGET.exists() else ''
    if current == expected:
        print('packaging/requirements-bundle.txt matches uv.lock')
        return
    sys.stdout.writelines(difflib.unified_diff(current.splitlines(True), expected.splitlines(True),
                                               'packaging/requirements-bundle.txt', 'uv export (from uv.lock)'))
    raise SystemExit('packaging/requirements-bundle.txt is out of date. Run: python3 scripts/check-bundle-requirements.py --write')


if __name__ == '__main__':
    main()
