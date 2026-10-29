"""Predictable narration destinations and descriptive, filesystem-safe names."""
from datetime import datetime
from pathlib import Path
import re
import shutil
import subprocess
import unicodedata
from urllib.parse import unquote, urlparse


def save_directory():
    music = Path.home() / 'Music'
    if shutil.which('xdg-user-dir'):
        try:
            result = subprocess.check_output(['xdg-user-dir', 'MUSIC'], text=True, timeout=3).strip()
            if result and Path(result).is_absolute() and Path(result) != Path.home():
                music = Path(result)
        except (OSError, subprocess.SubprocessError):
            pass
    return music / 'Runespeak'


def slug(value):
    value = unicodedata.normalize('NFKC', value).lower()
    return re.sub(r'[^\w]+', '-', value, flags=re.UNICODE).replace('_', '-').strip('-')


def filename(source='', text='', fallback=''):
    parsed = urlparse(source)
    if parsed.scheme in ('http', 'https') and parsed.hostname:
        host = parsed.hostname.removeprefix('www.')
        path = unquote(parsed.path).strip('/')
        path = re.sub(r'\.(?:html?|php|aspx?)$', '', path, flags=re.I)
        base = slug(host + '-' + path)
    elif parsed.scheme == 'file':
        base = slug(Path(unquote(parsed.path)).stem)
    else:
        base = slug(' '.join(text.split()[:10])) or slug(fallback)
    base = base[:110].rstrip('-') or datetime.now().strftime('reading-%Y-%m-%d-%H%M%S')
    return base + '.wav'


def unused_name(directory, name):
    candidate = Path(name)
    number = 1
    while (directory / candidate).exists() or (directory / candidate.with_suffix('.json')).exists():
        number += 1
        candidate = Path(f'{Path(name).stem}-{number}.wav')
    return candidate.name
