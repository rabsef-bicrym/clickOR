import json
import tempfile
import unittest

import yaml

from clickor.companions import parse_companions
from clickor.generate import bumper_entries_with_companions
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


def _write_yaml(items):
    obj = {
        "channel": {"name": "X", "number": 1, "group": "X"},
        "schedule": {"name": "X Schedule", "shuffle": False, "guide_mode": "include_all"},
        "playlist": {
            "name": "X",
            "group": "X",
            "items": [{"path": p, "type": "other_video"} for p in items],
        },
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(obj, f)
        return f.name


def _bumper(path):
    return BumperItem(path=path, duration_s=10 * 60, media_type="other_video")


def _cfg(bumpers, companions=(), slots=1):
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
            slots_per_break=slots,
            mixing_strategy="round_robin",
            pools={"coronet": BumperPoolConfig(name="coronet", weight=1.0, items=[_bumper(p) for p in bumpers])},
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
                path=f"/media/films/f{i}.mkv",
                duration_s=90 * 60,
                pool="films",
                media_type="movie",
                repeatable=False,
                repeat_cost_s=0,
                max_extra_uses=0,
            )
            for i in range(len(bumpers))
        ],
        companions=list(companions),
    )


SLATE_RULE = {
    "match": {"pools": ["coronet"]},
    "position": "before",
    "card": {"template": "/media/cards/{stem}.mp4", "include_in_guide": False},
}


class TestBumperCompanionSplice(unittest.TestCase):
    def test_bumper_gets_its_card_before_it(self):
        rules = parse_companions([SLATE_RULE])
        entries = bumper_entries_with_companions(rules, [("coronet", _bumper("/media/coronet/A.mkv"))])
        self.assertEqual([e.path for e in entries], ["/media/cards/A.mp4", "/media/coronet/A.mkv"])
        self.assertFalse(entries[0].include_in_guide)
        self.assertTrue(entries[1].include_in_guide)

    def test_rule_for_other_pool_leaves_bumper_bare(self):
        rules = parse_companions([{**SLATE_RULE, "match": {"pools": ["films"]}}])
        entries = bumper_entries_with_companions(rules, [("coronet", _bumper("/media/coronet/A.mkv"))])
        self.assertEqual([e.path for e in entries], ["/media/coronet/A.mkv"])


class TestVerifyBumperCards(unittest.TestCase):
    def setUp(self):
        self.bumpers = ["/media/coronet/A.mkv", "/media/coronet/B.mkv"]
        self.cfg = _cfg(self.bumpers, parse_companions([SLATE_RULE]))

    def test_accepts_slate_before_each_bumper(self):
        path = _write_yaml(
            [
                "/media/cards/A.mp4", "/media/coronet/A.mkv", "/media/films/f0.mkv",
                "/media/cards/B.mp4", "/media/coronet/B.mkv", "/media/films/f1.mkv",
            ]
        )
        errors = [f.message for f in verify_yaml_against_config(self.cfg, path) if f.level == "ERROR"]
        self.assertEqual(errors, [])

    def test_flags_bumper_missing_its_slate(self):
        path = _write_yaml(
            [
                "/media/cards/A.mp4", "/media/coronet/A.mkv", "/media/films/f0.mkv",
                "/media/coronet/B.mkv", "/media/films/f1.mkv",  # slate for B dropped
            ]
        )
        findings = verify_yaml_against_config(self.cfg, path)
        self.assertTrue(any("adjacency broken in bumper run" in f.message for f in findings))

    def test_slate_does_not_count_as_a_bumper_slot(self):
        # Two slates + one bumper in a one-slot break is still one bumper.
        path = _write_yaml(
            [
                "/media/cards/A.mp4", "/media/cards/A.mp4", "/media/coronet/A.mkv", "/media/films/f0.mkv",
                "/media/cards/B.mp4", "/media/coronet/B.mkv", "/media/films/f1.mkv",
            ]
        )
        findings = verify_yaml_against_config(self.cfg, path)
        msgs = [f.message for f in findings if f.level == "ERROR"]
        self.assertFalse(any("Bumper run must hold" in m for m in msgs), msgs)
        self.assertTrue(any("adjacency broken in bumper run" in m for m in msgs), msgs)


class TestVerifyExhaustPerDeal(unittest.TestCase):
    def _lineup(self, bumper_order):
        cfg = _cfg(["/media/coronet/A.mkv", "/media/coronet/B.mkv", "/media/coronet/C.mkv"])
        items = []
        for i, b in enumerate(bumper_order):
            items += [f"/media/coronet/{b}.mkv", f"/media/films/f{i % 3}.mkv"]
        # films repeat here; only read the bumper finding
        findings = verify_yaml_against_config(cfg, _write_yaml(items))
        return [f.message for f in findings if "Bumper repeats" in f.message]

    def test_fresh_deal_may_reuse_early(self):
        # Deal 1 = A B C, deal 2 = B C A: B returns after 2 uses. That is a shuffle, not a fault.
        self.assertEqual(self._lineup(["A", "B", "C", "B", "C", "A"]), [])

    def test_duplicate_inside_a_deal_fails(self):
        self.assertTrue(self._lineup(["A", "B", "A", "C", "B", "C"]))

    def test_back_to_back_across_deals_fails(self):
        self.assertTrue(self._lineup(["A", "B", "C", "C", "A", "B"]))


class TestEndToEndBumperCards(unittest.TestCase):
    def test_solved_lineup_has_a_slate_before_every_bumper(self):
        from clickor.config import load_config
        from clickor.generate import solve_to_yaml_obj
        from clickor.yaml_out import dump_yaml

        raw = json.load(open("examples/example-config.json"))
        pool_name = next(iter(raw["bumpers"]["pools"]))
        raw["companions"] = [{**SLATE_RULE, "match": {"pools": [pool_name]}}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(raw, f)
            cfg_path = f.name
        cfg = load_config(cfg_path)
        yaml_obj, _ = solve_to_yaml_obj(cfg, playlist_name="X", playlist_group="X", seed_override=7, time_limit_sec=5)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            out = f.name
        dump_yaml(yaml_obj, out)
        errors = [f"{x.level}: {x.message}" for x in verify_yaml_against_config(cfg, out) if x.level == "ERROR"]
        self.assertEqual(errors, [])
        items = yaml_obj["playlist"]["items"]
        bumper_paths = {b["path"] for b in raw["bumpers"]["pools"][pool_name]["items"]}
        n = 0
        for i, it in enumerate(items):
            if it["path"] in bumper_paths:
                n += 1
                self.assertTrue(items[i - 1]["path"].startswith("/media/cards/"), f"bumper at {i} has no slate")
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()
