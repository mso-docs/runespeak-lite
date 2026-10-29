import tempfile
import unittest
import wave
from pathlib import Path

from runespeak.core import chunks, combine


class CoreTests(unittest.TestCase):
    def test_long_article_keeps_every_word_and_bounds_requests(self):
        text = ' '.join(['This is a sentence. Another sentence!'] * 300)
        result = chunks(text)
        self.assertTrue(all(0 < len(part) <= 500 for part in result))
        self.assertEqual(' '.join(result), text)

    def test_punctuation_at_limit_does_not_exceed_limit(self):
        result = chunks('a' * 500 + '. trailing words')
        self.assertTrue(all(len(part) <= 500 for part in result))
        self.assertEqual(''.join(result).replace(' ', ''), ('a' * 500 + '.trailingwords'))

    def test_empty_and_long_unbroken_text(self):
        self.assertEqual(chunks(' \n '), [])
        text = 'a' * 1201
        self.assertEqual(''.join(chunks(text)), text)
        self.assertTrue(all(len(part) <= 500 for part in chunks(text)))

    def test_wav_join_preserves_all_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            parts = []
            for index in range(3):
                path = Path(folder) / f'{index}.wav'
                with wave.open(str(path), 'wb') as output:
                    output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
                    output.writeframes(bytes([index, 0]) * 100)
                parts.append(path)
            joined = Path(folder) / 'joined.wav'
            combine(parts, joined)
            with wave.open(str(joined), 'rb') as source:
                self.assertEqual(source.getnframes(), 300)
                self.assertEqual(source.readframes(300), b''.join(bytes([i, 0]) * 100 for i in range(3)))


if __name__ == '__main__':
    unittest.main()


class ReadAlongChunkTests(unittest.TestCase):
    def test_each_normal_sentence_gets_its_own_passage(self):
        sentences = ['The challenges we face in understanding language models resemble those faced by biologists.', 'Living organisms are complex systems shaped by billions of years of evolution.', 'The basic principles are straightforward, but the mechanisms are complex.']
        self.assertEqual(chunks(' '.join(sentences)), sentences)

    def test_long_sentence_is_bounded_without_losing_words(self):
        text = ' '.join(['A lengthy explanation with technical details, supporting evidence and caveats'] * 30)
        pieces = chunks(text)
        self.assertTrue(all(len(piece) <= 180 for piece in pieces))
        self.assertEqual(' '.join(pieces), text)

    def test_common_abbreviations_do_not_become_separate_passages(self):
        text = 'Dr. Smith and Prof. Jones discuss the experimental evidence. Their conclusions are important.'
        pieces = chunks(text)
        self.assertEqual(pieces[0], 'Dr. Smith and Prof. Jones discuss the experimental evidence.')
        self.assertEqual(len(pieces), 2)

    def test_decimal_numbers_and_unicode_punctuation(self):
        text = 'The result was 3.14 and the measurement was reliable. “This is a complete sentence.” Another useful sentence follows.'
        pieces = chunks(text)
        self.assertIn('3.14', pieces[0])
        self.assertEqual(' '.join(pieces), text)
