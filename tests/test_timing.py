import unittest
from runespeak.timing import ListeningClock, format_time


class TimingTests(unittest.TestCase):
    def setUp(self):
        self.time = 100.0
        self.clock = ListeningClock(lambda: self.time)

    def test_pause_excludes_paused_time(self):
        self.clock.start(0, 30)
        self.time += 5
        self.clock.pause()
        self.time += 100
        self.assertEqual(self.clock.snapshot(), (5, 5))
        self.clock.resume()
        self.time += 3
        self.assertEqual(self.clock.snapshot(), (8, 8))

    def test_boundaries_waits_and_restart(self):
        self.clock.start(0, 10)
        self.time += 12
        self.clock.finish()
        self.time += 100
        self.assertEqual(self.clock.snapshot(), (10, 10))
        self.clock.start(10, 20)
        self.time += 4
        self.clock.reset()
        self.assertEqual(self.clock.snapshot(), (0, 14))
        self.clock.start(0, 10)
        self.time += 2
        self.assertEqual(self.clock.snapshot(), (2, 16))

    def test_formats_hours_and_clamps_negative(self):
        self.assertEqual(format_time(3661), '1:01:01')
        self.assertEqual(format_time(65), '1:05')
        self.assertEqual(format_time(-1), '0:00')
