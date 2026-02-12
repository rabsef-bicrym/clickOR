import tempfile
import unittest

import yaml

from clickor.config import load_config
from clickor.generate import solve_to_yaml_obj
from clickor.verify import verify_yaml_against_config
from clickor.yaml_out import dump_yaml


class TestConfigAndSolver(unittest.TestCase):
    def test_solve_and_verify_small_example(self):
        cfg = load_config("examples/example-config.json")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            out_path = f.name

        # Keep the time limit small so the test stays fast.
        yaml_obj, _result = solve_to_yaml_obj(
            cfg,
            playlist_name="X",
            playlist_group="X",
            seed_override=123,
            time_limit_sec=5,
        )
        dump_yaml(yaml_obj, out_path)

        findings = verify_yaml_against_config(cfg, out_path)
        errors = [x for x in findings if x.level == "ERROR"]
        if errors:
            # Make failures easier to read.
            msg = "\n".join([f"{f.level}: {f.message}" for f in findings])
            self.fail(msg)

        # Also validate the YAML parses (smoke).
        with open(out_path) as f:
            y = yaml.safe_load(f)
        self.assertIsInstance(y, dict)
        self.assertIn("playlist", y)


if __name__ == "__main__":
    unittest.main()

