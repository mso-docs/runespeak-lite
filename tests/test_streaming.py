from pathlib import Path
import unittest
from runespeak.streaming import SectionQueue


class StreamingTests(unittest.TestCase):
    def test_starts_without_waiting_for_all_sections(self):
        queue = SectionQueue(parts=[Path('first.wav')])
        self.assertFalse(queue.complete)
        self.assertEqual(queue.next(), Path('first.wav'))
        self.assertIsNone(queue.next())
        queue.parts.append(Path('second.wav'))
        self.assertEqual(queue.next(), Path('second.wav'))

    def test_stop_prevents_late_sections_starting(self):
        queue = SectionQueue(parts=[Path('first.wav')], active=False)
        queue.parts.append(Path('late.wav'))
        self.assertIsNone(queue.next())
        self.assertEqual(queue.cursor, 0)

    def test_restart_replays_without_duplicate_after_completion(self):
        queue = SectionQueue(parts=[Path('a.wav'), Path('b.wav')], complete=True)
        self.assertEqual([queue.next(), queue.next(), queue.next()], [Path('a.wav'), Path('b.wav'), None])
        queue.restart()
        self.assertEqual(queue.next(), Path('a.wav'))
