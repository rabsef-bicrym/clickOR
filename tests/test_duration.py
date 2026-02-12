import unittest

from clickor.duration import parse_hhmmss_to_seconds


class TestDuration(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_hhmmss_to_seconds("00:00:00"), 0)
        self.assertEqual(parse_hhmmss_to_seconds("00:00:59"), 59)
        self.assertEqual(parse_hhmmss_to_seconds("00:01:00"), 60)
        self.assertEqual(parse_hhmmss_to_seconds("01:00:00"), 3600)
        self.assertEqual(parse_hhmmss_to_seconds("10:11:12"), 10 * 3600 + 11 * 60 + 12)


if __name__ == "__main__":
    unittest.main()
