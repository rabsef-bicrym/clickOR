import json
import tempfile
import unittest

import yaml

from clickor.companions import (
    CompanionError,
    CompanionRule,
    cards_for_item,
    parse_companions,
)
from clickor.flat import expand_flat_to_playlist_entries, load_flat_config, FlatError
from clickor.model import (
    BumperItem,
    BumperPoolConfig,
    BumpersConfig,
    ChannelConfig,
    Item,
    PoolConfig,
    SolverConfig,
)
from clickor.verify import verify_yaml_against_config


def _rule(**kw):
    defaults = dict(
        pools=None,
        types=None,
        path_glob=None,
        scope="every_match",
        position="before",
        card_template=None,
        card_map={},
        card_type="other_video",
        include_in_guide=True,
    )
    defaults.update(kw)
    return CompanionRule(**defaults)


class TestParse(unittest.TestCase):
    def test_parse_minimal_template_rule(self):
        rules = parse_companions(
            [
                {
                    "match": {"pools": ["coronet"]},
                    "card": {"template": "/media/cards/{stem}.mp4"},
                }
            ]
        )
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].scope, "every_match")
        self.assertEqual(rules[0].position, "before")
        self.assertEqual(rules[0].card_type, "other_video")

    def test_parse_rejects_unconstrained_match(self):
        with self.assertRaises(CompanionError):
            parse_companions([{"match": {}, "card": {"template": "/x/{stem}.mp4"}}])

    def test_parse_rejects_cardless_rule(self):
        with self.assertRaises(CompanionError):
            parse_companions([{"match": {"pools": ["a"]}, "card": {}}])

    def test_parse_rejects_bad_scope(self):
        with self.assertRaises(CompanionError):
            parse_companions(
                [{"match": {"pools": ["a"]}, "scope": "sometimes", "card": {"template": "/x/{stem}.mp4"}}]
            )

    def test_none_means_no_rules(self):
        self.assertEqual(parse_companions(None), [])


class TestMatchingAndResolution(unittest.TestCase):
    def test_template_resolution_uses_stem(self):
        r = _rule(pools=frozenset({"coronet"}), card_template="/media/cards/{stem}.mp4")
        cards = cards_for_item(
            [r], path="/media/other/Shy Guy (1947).mkv", pool="coronet",
            media_type="other_video", is_block_start=False,
        )
        self.assertEqual([c.path for c in cards], ["/media/cards/Shy Guy (1947).mp4"])

    def test_map_takes_precedence_over_template(self):
        r = _rule(
            pools=frozenset({"p"}),
            card_template="/media/cards/{stem}.mp4",
            card_map={"/media/x/a.mkv": "/media/cards/special.mp4"},
        )
        cards = cards_for_item(
            [r], path="/media/x/a.mkv", pool="p", media_type="movie", is_block_start=False
        )
        self.assertEqual(cards[0].path, "/media/cards/special.mp4")

    def test_matched_without_card_raises(self):
        r = _rule(pools=frozenset({"p"}), card_map={"/media/x/other.mkv": "/c.mp4"})
        with self.assertRaises(CompanionError):
            cards_for_item(
                [r], path="/media/x/a.mkv", pool="p", media_type="movie", is_block_start=False
            )

    def test_block_start_scope_only_fires_at_block_start(self):
        r = _rule(pools=frozenset({"p"}), scope="block_start", card_template="/c/{stem}.mp4")
        at_start = cards_for_item(
            [r], path="/m/a.mkv", pool="p", media_type="movie", is_block_start=True
        )
        mid_block = cards_for_item(
            [r], path="/m/a.mkv", pool="p", media_type="movie", is_block_start=False
        )
        self.assertEqual(len(at_start), 1)
        self.assertEqual(len(mid_block), 0)

    def test_glob_and_type_matching(self):
        r = _rule(types=frozenset({"episode"}), path_glob="*/MST3K/*", card_template="/c/{stem}.mp4")
        hit = cards_for_item(
            [r], path="/media/MST3K/e1.mkv", pool=None, media_type="episode", is_block_start=False
        )
        wrong_type = cards_for_item(
            [r], path="/media/MST3K/e1.mkv", pool=None, media_type="movie", is_block_start=False
        )
        wrong_path = cards_for_item(
            [r], path="/media/Krtek/e1.mkv", pool=None, media_type="episode", is_block_start=False
        )
        self.assertEqual(len(hit), 1)
        self.assertEqual(len(wrong_type), 0)
        self.assertEqual(len(wrong_path), 0)


class TestFlatCompanions(unittest.TestCase):
    def _load(self, obj):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(json.dumps(obj))
            p = f.name
        return load_flat_config(p)

    def test_flat_injects_before_and_after(self):
        cfg = self._load(
            {
                "mode": "flat",
                "channel_name": "X",
                "loop_short_to": 0,
                "items": [
                    {"type": "episode", "path": "/media/MST3K/e1.mkv"},
                    {"type": "bumper", "path": "/media/shorts/B.mp4"},
                ],
                "companions": [
                    {
                        "match": {"types": ["episode"]},
                        "position": "before",
                        "card": {"template": "/media/cards/{stem}.mp4", "include_in_guide": False},
                    }
                ],
            }
        )
        entries = expand_flat_to_playlist_entries(cfg, probe=lambda _p: 100.0)
        self.assertEqual(
            [e.path for e in entries],
            ["/media/cards/e1.mp4", "/media/MST3K/e1.mkv", "/media/shorts/B.mp4"],
        )
        self.assertFalse(entries[0].include_in_guide)

    def test_flat_card_not_looped(self):
        # The episode's card must appear once even when short-loop machinery runs.
        cfg = self._load(
            {
                "mode": "flat",
                "channel_name": "X",
                "loop_short_under": 15,
                "loop_short_to": 30,
                "items": [{"type": "bumper", "path": "/media/shorts/B.mp4"}],
                "companions": [
                    {
                        "match": {"types": ["bumper"]},
                        "card": {"template": "/media/cards/{stem}.mp4"},
                    }
                ],
            }
        )
        entries = expand_flat_to_playlist_entries(cfg, probe=lambda _p: 2.0)
        card_count = sum(1 for e in entries if e.path == "/media/cards/B.mp4")
        self.assertEqual(card_count, 1)
        self.assertEqual(entries[0].path, "/media/cards/B.mp4")

    def test_flat_rejects_block_start(self):
        with self.assertRaises(FlatError):
            self._load(
                {
                    "mode": "flat",
                    "channel_name": "X",
                    "items": [{"type": "episode", "path": "/m/e1.mkv"}],
                    "companions": [
                        {
                            "match": {"types": ["episode"]},
                            "scope": "block_start",
                            "card": {"template": "/c/{stem}.mp4"},
                        }
                    ],
                }
            )

    def test_flat_rejects_pool_match(self):
        with self.assertRaises(FlatError):
            self._load(
                {
                    "mode": "flat",
                    "channel_name": "X",
                    "items": [{"type": "episode", "path": "/m/e1.mkv"}],
                    "companions": [
                        {
                            "match": {"pools": ["p"]},
                            "card": {"template": "/c/{stem}.mp4"},
                        }
                    ],
                }
            )


class TestGenerateSplice(unittest.TestCase):
    def test_block_entries_wrap_items(self):
        from clickor.generate import block_entries_with_companions

        every = _rule(pools=frozenset({"shorts"}), card_template="/c/{stem}.mp4")
        opener = _rule(
            pools=frozenset({"shorts"}),
            scope="block_start",
            card_template="/idents/block.mp4",
        )
        items = [
            Item(
                path=f"/media/shorts/{n}.mkv",
                duration_s=600,
                pool="shorts",
                media_type="other_video",
                repeatable=False,
                repeat_cost_s=0,
                max_extra_uses=0,
            )
            for n in ("a", "b")
        ]
        entries = block_entries_with_companions([opener, every], items)
        self.assertEqual(
            [e.path for e in entries],
            [
                "/idents/block.mp4",  # block_start card, first item only
                "/c/a.mp4",
                "/media/shorts/a.mkv",
                "/c/b.mp4",
                "/media/shorts/b.mkv",
            ],
        )


def _mk_cfg(companions):
    return ChannelConfig(
        channel={"name": "X", "number": 1, "group": "X"},
        schedule={"name": "X Schedule", "shuffle": False, "guide_mode": "include_all"},
        solver=SolverConfig(
            block_s=30 * 60,
            longform_consumes_block=True,
            allow_short_overflow_s=0,
            time_limit_sec=1,
            seed=1,
        ),
        bumpers=BumpersConfig(
            slots_per_break=1,
            mixing_strategy="round_robin",
            pools={
                "b": BumperPoolConfig(
                    name="b",
                    weight=1.0,
                    items=[BumperItem(path="/media/bump/1.mp4", duration_s=60, media_type="other_video")],
                )
            },
        ),
        pools={
            "shorts": PoolConfig(
                name="shorts",
                default_type="other_video",
                sequential=False,
                default_repeatable=False,
                default_repeat_cost_s=0,
                default_max_extra_uses=0,
                dominant_block_threshold_s=24 * 60,
                dominant_block_penalty_s=0,
            )
        },
        items=[
            Item(
                path="/media/shorts/a.mkv",
                duration_s=10 * 60,
                pool="shorts",
                media_type="other_video",
                repeatable=False,
                repeat_cost_s=0,
                max_extra_uses=0,
            ),
            Item(
                path="/media/shorts/b.mkv",
                duration_s=10 * 60,
                pool="shorts",
                media_type="other_video",
                repeatable=False,
                repeat_cost_s=0,
                max_extra_uses=0,
            ),
        ],
        companions=companions,
    )


def _write_yaml(items):
    obj = {
        "channel": {"name": "X", "number": 1, "group": "X"},
        "schedule": {"name": "X Schedule", "shuffle": False, "guide_mode": "include_all"},
        "playlist": {"name": "P", "group": "X", "items": [{"path": p} for p in items]},
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(obj, f)
        return f.name


class TestVerifyCompanions(unittest.TestCase):
    def setUp(self):
        self.rule = _rule(
            pools=frozenset({"shorts"}),
            card_template="/media/cards/{stem}.mp4",
        )

    def test_verify_accepts_correct_adjacency(self):
        cfg = _mk_cfg([self.rule])
        yaml_path = _write_yaml(
            [
                "/media/bump/1.mp4",
                "/media/cards/a.mp4",
                "/media/shorts/a.mkv",
                "/media/cards/b.mp4",
                "/media/shorts/b.mkv",
            ]
        )
        findings = verify_yaml_against_config(cfg, yaml_path)
        self.assertEqual([f.message for f in findings if f.level == "ERROR"], [])

    def test_verify_flags_missing_card(self):
        cfg = _mk_cfg([self.rule])
        yaml_path = _write_yaml(
            [
                "/media/bump/1.mp4",
                "/media/cards/a.mp4",
                "/media/shorts/a.mkv",
                "/media/shorts/b.mkv",  # card for b silently dropped
            ]
        )
        findings = verify_yaml_against_config(cfg, yaml_path)
        self.assertTrue(any("adjacency" in f.message.lower() for f in findings))

    def test_verify_flags_orphan_card(self):
        cfg = _mk_cfg([self.rule])
        yaml_path = _write_yaml(
            [
                "/media/bump/1.mp4",
                "/media/cards/a.mp4",
                "/media/cards/a.mp4",  # duplicated card
                "/media/shorts/a.mkv",
                "/media/cards/b.mp4",
                "/media/shorts/b.mkv",
            ]
        )
        findings = verify_yaml_against_config(cfg, yaml_path)
        self.assertTrue(any("adjacency" in f.message.lower() for f in findings))

    def test_verify_no_companions_is_unchanged(self):
        cfg = _mk_cfg([])
        yaml_path = _write_yaml(
            ["/media/bump/1.mp4", "/media/shorts/a.mkv", "/media/shorts/b.mkv"]
        )
        findings = verify_yaml_against_config(cfg, yaml_path)
        self.assertEqual([f.message for f in findings if f.level == "ERROR"], [])

    def test_cards_exempt_from_longform_rule(self):
        # A long movie plus its card in one block must not trip the
        # "long-form blocks contain exactly one item" rule.
        long_rule = _rule(pools=frozenset({"films"}), card_template="/media/cards/{stem}.mp4")
        cfg = ChannelConfig(
            channel={"name": "X", "number": 1, "group": "X"},
            schedule={"name": "X Schedule", "shuffle": False, "guide_mode": "include_all"},
            solver=SolverConfig(
                block_s=30 * 60,
                longform_consumes_block=True,
                allow_short_overflow_s=0,
                time_limit_sec=1,
                seed=1,
            ),
            bumpers=BumpersConfig(
                slots_per_break=1,
                mixing_strategy="round_robin",
                pools={
                    "b": BumperPoolConfig(
                        name="b",
                        weight=1.0,
                        items=[BumperItem(path="/media/bump/1.mp4", duration_s=60, media_type="other_video")],
                    )
                },
            ),
            pools={
                "films": PoolConfig(
                    name="films",
                    default_type="movie",
                    sequential=False,
                    default_repeatable=False,
                    default_repeat_cost_s=0,
                    default_max_extra_uses=0,
                    dominant_block_threshold_s=24 * 60,
                    dominant_block_penalty_s=0,
                )
            },
            items=[
                Item(
                    path="/media/films/long.mkv",
                    duration_s=90 * 60,
                    pool="films",
                    media_type="movie",
                    repeatable=False,
                    repeat_cost_s=0,
                    max_extra_uses=0,
                )
            ],
            companions=[long_rule],
        )
        yaml_path = _write_yaml(
            ["/media/bump/1.mp4", "/media/cards/long.mp4", "/media/films/long.mkv"]
        )
        findings = verify_yaml_against_config(cfg, yaml_path)
        self.assertEqual([f.message for f in findings if f.level == "ERROR"], [])


class TestEndToEnd(unittest.TestCase):
    def test_solve_splice_verify_roundtrip(self):
        # Real solver, real splice, real verify: cards for every 'shorts' item
        # must survive the whole pipeline and satisfy the adjacency check.
        import json

        from clickor.config import load_config
        from clickor.generate import solve_to_yaml_obj
        from clickor.yaml_out import dump_yaml

        raw = json.load(open("examples/example-config.json"))
        raw["companions"] = [
            {
                "match": {"pools": ["shorts"]},
                "position": "before",
                "card": {"template": "/media/cards/{stem}.mp4", "include_in_guide": False},
            }
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            cfg_path = f.name
        cfg = load_config(cfg_path)

        yaml_obj, _result = solve_to_yaml_obj(
            cfg,
            playlist_name="X",
            playlist_group="X",
            seed_override=123,
            time_limit_sec=5,
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            out_path = f.name
        dump_yaml(yaml_obj, out_path)

        findings = verify_yaml_against_config(cfg, out_path)
        errors = [x for x in findings if x.level == "ERROR"]
        if errors:
            self.fail("\n".join(f"{x.level}: {x.message}" for x in findings))

        items = yaml_obj["playlist"]["items"]
        card_paths = [i["path"] for i in items if i["path"].startswith("/media/cards/")]
        self.assertTrue(card_paths, "expected companion cards in the solved playlist")
        # Every card is immediately followed by its short.
        for idx, it in enumerate(items):
            if it["path"].startswith("/media/cards/"):
                nxt = items[idx + 1]["path"]
                stem = it["path"].rsplit("/", 1)[1].rsplit(".", 1)[0]
                self.assertTrue(
                    nxt.rsplit("/", 1)[1].startswith(stem),
                    f"card {it['path']} not followed by its item (next was {nxt})",
                )


if __name__ == "__main__":
    unittest.main()
