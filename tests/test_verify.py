import tempfile
import unittest

import yaml

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


class TestVerify(unittest.TestCase):
    def test_verify_happy_path(self):
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
                    "coronet": BumperPoolConfig(
                        name="coronet",
                        weight=1.0,
                        items=[
                            BumperItem(
                                path="/media/other_videos/coronet/I1.mkv",
                                duration_s=10 * 60,
                                media_type="other_video",
                            ),
                            BumperItem(
                                path="/media/other_videos/coronet/I2.mkv",
                                duration_s=10 * 60,
                                media_type="other_video",
                            ),
                        ],
                    )
                },
            ),
            pools={
                "p": PoolConfig(
                    name="p",
                    default_type="other_video",
                    sequential=False,
                    default_repeatable=False,
                    default_repeat_cost_s=30 * 60,
                    default_max_extra_uses=0,
                    dominant_block_threshold_s=24 * 60,
                    dominant_block_penalty_s=0,
                )
            },
            items=[
                Item(
                    path="/media/other_videos/x/A.mkv",
                    duration_s=10 * 60,
                    pool="p",
                    media_type="other_video",
                    repeatable=False,
                    repeat_cost_s=0,
                    max_extra_uses=0,
                ),
                Item(
                    path="/media/other_videos/x/B.mkv",
                    duration_s=10 * 60,
                    pool="p",
                    media_type="other_video",
                    repeatable=False,
                    repeat_cost_s=0,
                    max_extra_uses=0,
                ),
            ],
        )

        y = {
            "channel": {"name": "X", "number": 1, "group": "X"},
            "schedule": {"name": "X Schedule", "shuffle": False, "guide_mode": "include_all"},
            "playlist": {
                "name": "X Master",
                "group": "X",
                "items": [
                    {"path": "/media/other_videos/coronet/I1.mkv", "type": "other_video"},
                    {"path": "/media/other_videos/x/A.mkv", "type": "other_video"},
                    {"path": "/media/other_videos/coronet/I2.mkv", "type": "other_video"},
                    {"path": "/media/other_videos/x/B.mkv", "type": "other_video"},
                ],
            },
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.dump(y, f, sort_keys=False)
            path = f.name

        findings = verify_yaml_against_config(cfg, path)
        errors = [x for x in findings if x.level == "ERROR"]
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
