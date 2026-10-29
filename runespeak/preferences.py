"""Small per-user preferences, written only after explicit selection."""
import json
import os
from pathlib import Path


def preferences_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'runespeak-lite/preferences.json'


def load_preferences():
    try:
        value = json.loads(preferences_path().read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(values):
    path = preferences_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, indent=2)+'\n')
    temporary.replace(path)
