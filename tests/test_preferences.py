from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runespeak.preferences import load_preferences, save_preferences


class PreferenceTests(unittest.TestCase):
    def test_voice_persists_and_unknown_fields_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'settings/preferences.json'
            with patch('runespeak.preferences.preferences_path', return_value=path):
                self.assertEqual(load_preferences(), {})
                save_preferences({'voice': 'Easton', 'other': True})
                self.assertEqual(load_preferences(), {'voice': 'Easton', 'other': True})
                path.write_text('invalid')
                self.assertEqual(load_preferences(), {})
