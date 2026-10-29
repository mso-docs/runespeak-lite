from pathlib import Path
import tempfile
import unittest
import wave
from runespeak.navigation import audio_slice, heading_targets, locate_time


class NavigationTests(unittest.TestCase):
    def test_time_crosses_sections_and_clamps_end(self):
        self.assertEqual(locate_time(12, [5, 10, 8]), (1, 7))
        self.assertEqual(locate_time(5, [5, 10]), (1, 0))
        self.assertEqual(locate_time(99, [5, 10]), (1, 10))

    def test_wav_seek_preserves_exact_remaining_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/'source.wav', Path(folder)/'seek.wav'
            data = b'\x01\x00'*1000 + b'\x02\x00'*1000 + b'\x03\x00'*1000
            with wave.open(str(source), 'wb') as audio:
                audio.setparams((1, 2, 1000, 0, 'NONE', 'not compressed'))
                audio.writeframes(data)
            self.assertEqual(audio_slice(source, target, 1, maximum=1), 1)
            with wave.open(str(target),'rb') as audio:
                self.assertEqual(audio.readframes(99999), b'\x02\x00'*1000)
            self.assertEqual(audio_slice(source, target, 2), 1)

    def test_explicit_headings_match_spoken_passages(self):
        source = 'Article.\n\n## Early life\nBiography.\n\n## Education\nUniversity.'
        targets = heading_targets(source, ['Article.', 'Early life Biography.', 'Education University.'])
        self.assertEqual([entry[0] for entry in targets], ['Early life', 'Education'])
        self.assertEqual([entry[2] for entry in targets], [1, 2])
        self.assertTrue(source[targets[1][1]:].lstrip().startswith('## Education'))

    def test_jump_keeps_earlier_and_later_words_visible(self):
        from runespeak.core import chunks
        from runespeak.navigation import reading_context
        full = 'Earlier words remain available. Middle passage starts here. Later words remain too.'
        display, audio, offset = reading_context(full, 'Middle passage starts here. Later words remain too.', chunks)
        self.assertEqual(' '.join(display), full)
        self.assertEqual(' '.join(audio), 'Middle passage starts here. Later words remain too.')
        self.assertGreater(offset, 0)
        self.assertTrue(display[offset].startswith('Middle'))
        back, _, before = reading_context(full, full, chunks)
        self.assertEqual(' '.join(back), full)
        self.assertEqual(before, 0)
