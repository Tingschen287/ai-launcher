import importlib.util
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "ai_launcher.py"
SPEC = importlib.util.spec_from_file_location("ai_launcher", SOURCE)
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class FakeTerm:
    def __init__(self, *keys):
        self.keys = iter(keys)

    def key(self):
        return "key", next(self.keys)


class LauncherTests(unittest.TestCase):
    def test_example_config_is_valid_and_has_unique_keys(self):
        with (ROOT / "config" / "agents.example.toml").open("rb") as handle:
            config = tomllib.load(handle)
        agents = config["agent"]
        keys = [agent["key"] for agent in agents]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(keys, ["cco", "ccs", "codex", "grok", "kimi"])

    def test_load_agents_accepts_example_config(self):
        old_config = launcher.CONF
        launcher.CONF = str(ROOT / "config" / "agents.example.toml")
        try:
            agents = launcher.load_agents()
        finally:
            launcher.CONF = old_config
        self.assertEqual(agents[0]["cmd"], "claude")
        self.assertEqual(agents[-1]["color"], "#047afe")

    def test_empty_key_does_not_crash_agent_picker(self):
        agents = [{"key": "x", "name": "X", "color": "#ffffff", "note": ""}]
        with patch.object(launcher, "draw", return_value={"rows": {}}):
            result = launcher.pick_agent(FakeTerm("", "q"), agents)
        self.assertIsNone(result)

    def test_empty_key_does_not_crash_directory_picker(self):
        with tempfile.TemporaryDirectory() as directory:
            item = {"path": directory, "ts": None}
            agent = {
                "key": "x",
                "name": "X",
                "color": "#ffffff",
                "default_dir": directory,
            }
            geometry = {"rows": {}, "bottom": 1, "left": 0}
            with patch.object(launcher, "dir_candidates", return_value=[item]), \
                    patch.object(launcher, "fill_git"), \
                    patch.object(launcher, "draw", return_value=geometry):
                result = launcher.pick_dir(FakeTerm("", "\r"), agent, allow_back=False)
            self.assertEqual(result, directory)

    def test_build_script_passes_arguments_to_native_cli(self):
        agent = {
            "cmd": "codex",
            "proxy": False,
            "path_prepend": [],
            "unset": [],
            "env": {},
        }
        script = launcher.build_script(agent, "/tmp/project")
        self.assertIn('codex "$@"', script)
        self.assertIn("cd /tmp/project", script)


if __name__ == "__main__":
    unittest.main()
