import unittest
from runespeak.follow import passage_at


class FollowTests(unittest.TestCase):
    def test_section_boundaries_and_end(self):
        self.assertEqual(passage_at(0, [2, 3, 4]), 0)
        self.assertEqual(passage_at(1.9, [2, 3, 4]), 0)
        self.assertEqual(passage_at(2, [2, 3, 4]), 1)
        self.assertEqual(passage_at(5, [2, 3, 4]), 2)
        self.assertEqual(passage_at(9, [2, 3, 4]), 2)
        self.assertIsNone(passage_at(1, []))
