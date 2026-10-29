from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from runespeak.saving import filename, save_directory, unused_name


class SavingTests(unittest.TestCase):
    def test_url_name_excludes_query_and_fragment(self):
        self.assertEqual(filename('https://distill.pub/2020/circuits/zoom-in/?token=secret#heading'), 'distill-pub-2020-circuits-zoom-in.wav')

    def test_numeric_page_keeps_course_context(self):
        self.assertEqual(filename('https://bluedot.org/courses/technical-ai-safety/4/2'), 'bluedot-org-courses-technical-ai-safety-4-2.wav')

    def test_pasted_text_name_is_safe_and_bounded(self):
        name = filename(text='A tale: of dragons / and stars?')
        self.assertEqual(name, 'a-tale-of-dragons-and-stars.wav')
        self.assertLessEqual(len(filename(text='a' * 1000)), 114)

    def test_collision_checks_audio_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'book.wav').touch()
            (directory / 'book-2.json').touch()
            self.assertEqual(unused_name(directory, 'book.wav'), 'book-3.wav')

    def test_uses_desktop_music_directory(self):
        with patch('runespeak.saving.shutil.which', return_value='/usr/bin/xdg-user-dir'), patch('runespeak.saving.subprocess.check_output', return_value='/tmp/My Music\n'):
            self.assertEqual(save_directory(), Path('/tmp/My Music/Runespeak'))

    def test_default_music_folder(self):
        with patch('runespeak.saving.shutil.which', return_value=None), patch('runespeak.saving.Path.home', return_value=Path('/tmp/home')):
            self.assertEqual(save_directory(), Path('/tmp/home/Music/Runespeak'))

    def test_local_document_filename_uses_original_stem(self):
        self.assertEqual(filename('file:///home/user/My%20Research.pdf', 'First paragraph'), 'my-research.wav')
