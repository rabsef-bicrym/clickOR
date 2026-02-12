import unittest

from clickor.duration import parse_hhmmss_to_seconds


class TestDurationFractional(unittest.TestCase):
    def test_parse_fractional_seconds(self):
        self.assertEqual(parse_hhmmss_to_seconds("0:07:23.456"), 7 * 60 + 23)


if __name__ == "__main__":
    unittest.main()

