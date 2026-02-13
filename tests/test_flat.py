import json
import tempfile
import unittest

from clickor.flat import (
    FlatError,
    build_lineup_config_for_db,
    expand_flat_to_playlist_entries,
    load_flat_config,
)


class TestFlat(unittest.TestCase):
    def test_load_flat_config_defaults(self):
        obj = {
            "mode": "flat",
            "channel_name": "Classics",
            "items": [{"type": "feature", "path": "/media/movies/A.mp4"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)
        self.assertEqual(cfg.channel_name, "Classics")
        self.assertEqual(cfg.playlist_name, "Classics Playlist")
        self.assertEqual(cfg.playlist_group, "Classics")
        self.assertEqual(cfg.schedule_name, "Classics Schedule")
        self.assertEqual(cfg.short_loop.under_s, 15)
        self.assertEqual(cfg.short_loop.loop_to_s, 30)

    def test_expand_explicit_loop_to(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "loop_short_to": 0,  # disable auto loop, only explicit loop_to
            "items": [{"type": "bumper", "path": "/media/shorts/B.mp4", "loop_to": 30}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)
        entries = expand_flat_to_playlist_entries(cfg, probe=lambda _p: 2.0)
        self.assertEqual(len(entries), 15)
        self.assertTrue(all(e.path == "/media/shorts/B.mp4" for e in entries))
        self.assertTrue(all(e.media_type == "other_video" for e in entries))
        self.assertEqual(entries[0].include_in_guide, True)
        self.assertTrue(all(not e.include_in_guide for e in entries[1:]))

    def test_expand_auto_loop_short(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "loop_short_under": 15,
            "loop_short_to": 30,
            "items": [
                {"type": "feature", "path": "/media/movies/A.mp4"},
                {"type": "bumper", "path": "/media/shorts/B.mp4"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)

        def _probe(path: str) -> float:
            if path.endswith("A.mp4"):
                return 7200.0
            return 10.0

        entries = expand_flat_to_playlist_entries(cfg, probe=_probe)
        # A is long => 1, B is short => ceil(30/10)=3
        self.assertEqual([(e.path, e.media_type) for e in entries], [
            ("/media/movies/A.mp4", "movie"),
            ("/media/shorts/B.mp4", "other_video"),
            ("/media/shorts/B.mp4", "other_video"),
            ("/media/shorts/B.mp4", "other_video"),
        ])
        self.assertEqual([e.include_in_guide for e in entries], [True, True, False, False])

    def test_expand_auto_loop_disabled(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "loop_short_under": 15,
            "loop_short_to": 0,
            "items": [{"type": "bumper", "path": "/media/shorts/B.mp4"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)
        entries = expand_flat_to_playlist_entries(cfg, probe=lambda _p: 2.0)
        self.assertEqual(len(entries), 1)

    def test_expand_auto_loop_item_opt_out(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "loop_short_under": 15,
            "loop_short_to": 30,
            "items": [
                {"type": "bumper", "path": "/media/cards/title-card.mp4", "auto_loop": False},
                {"type": "other_video", "path": "/media/other_videos/short-film.mp4"},
            ],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)
        entries = expand_flat_to_playlist_entries(cfg, probe=lambda _p: 10.0)
        self.assertEqual([(e.path, e.media_type) for e in entries], [
            ("/media/cards/title-card.mp4", "other_video"),
            ("/media/other_videos/short-film.mp4", "other_video"),
            ("/media/other_videos/short-film.mp4", "other_video"),
            ("/media/other_videos/short-film.mp4", "other_video"),
        ])
        self.assertEqual([e.include_in_guide for e in entries], [True, True, False, False])

    def test_unknown_item_type_is_error(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "items": [{"type": "wat", "path": "/x.mp4"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        with self.assertRaises(FlatError):
            _ = load_flat_config(p)

    def test_build_lineup_config_for_db(self):
        obj = {
            "mode": "flat",
            "channel_name": "X",
            "channel_number": 42,
            "items": [{"type": "feature", "path": "/media/movies/A.mp4"}],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name

        cfg = load_flat_config(p)
        lineup = build_lineup_config_for_db(cfg, items=[{"path": "/media/movies/A.mp4", "type": "movie"}])
        self.assertEqual(lineup["channel"]["name"], "X")
        self.assertEqual(lineup["channel"]["number"], 42)
        self.assertEqual(lineup["playlist"]["name"], "X Playlist")


if __name__ == "__main__":
    unittest.main()
