import unittest

from clickor.tv import parse_sxxexx


class TestTVParsing(unittest.TestCase):
    def test_parse_sxxexx(self):
        eid = parse_sxxexx("/media/shows/Foo/Season 01/Foo - S01E02 - Bar.mkv")
        self.assertIsNotNone(eid)
        self.assertEqual(eid.season, 1)
        self.assertEqual(eid.episode, 2)

    def test_parse_sxxexx_none(self):
        self.assertIsNone(parse_sxxexx("/media/shows/Foo/Season 01/Foo - Episode 2.mkv"))


if __name__ == "__main__":
    unittest.main()
