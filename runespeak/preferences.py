"""Small per-user preferences, written only after explicit selection."""
import json

from .platforms import config_dir


def preferences_path():
    return config_dir() / 'preferences.json'


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
