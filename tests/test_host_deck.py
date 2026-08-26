import importlib.util
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "host_deck.py"
SPEC = importlib.util.spec_from_file_location("host_deck", SOURCE)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


class FakeTerm:
    def __init__(self, *keys):
        self.keys = iter(keys)

    def key(self):
        return "key", next(self.keys)


class EventTerm:
    def __init__(self, *events):
        self.events = iter(events)

    def key(self):
        return next(self.events)


class HostDeckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.ssh_config = root / "ssh_config"
        self.old = {
            "CONF": host.CONF,
            "HIST": host.HIST,
            "FAV": host.FAV,
            "SSH_CONFIG": host.SSH_CONFIG,
        }
        host.CONF = str(root / "hosts.toml")
        host.HIST = str(root / "history.tsv")
        host.FAV = str(root / "favorites.txt")
        host.SSH_CONFIG = str(self.ssh_config)
        os.environ["HOST_DECK_SKIP_SSH_G"] = "1"

    def tearDown(self):
        for key, value in self.old.items():
            setattr(host, key, value)
        os.environ.pop("HOST_DECK_SKIP_SSH_G", None)

    def write_ssh(self, text, path=None):
        target = Path(path or self.ssh_config)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def test_example_config_has_unique_aliases_and_no_secrets(self):
        example = ROOT / "config" / "hosts.example.toml"
        with example.open("rb") as handle:
            config = tomllib.load(handle)
        aliases = [item["alias"] for item in config["host"]]
        self.assertEqual(len(aliases), len(set(aliases)))
        blob = example.read_text(encoding="utf-8").lower()
        for banned in host.BANNED_KEYS:
            self.assertNotIn(f"{banned} =", blob)

    def test_bootstrap_config_has_no_host_entries(self):
        host.CONF = str(ROOT / "config" / "hosts.bootstrap.toml")
        cfg = host.load_config()
        self.assertEqual(cfg["wt_profile"], "SSH (WSL)")
        self.assertEqual(cfg["hosts"], {})

    def test_load_config_accepts_example(self):
        host.CONF = str(ROOT / "config" / "hosts.example.toml")
        cfg = host.load_config()
        self.assertEqual(cfg["wt_profile"], "SSH (WSL)")
        self.assertEqual(cfg["hosts"]["example-dev"]["group"], "dev")
        self.assertEqual(cfg["hosts"]["example-prod"]["color"], "#f87171")

    def test_load_config_rejects_credentials(self):
        Path(host.CONF).write_text(
            '[[host]]\nalias = "x"\npassword = "nope"\n', encoding="utf-8")
        with self.assertRaises(SystemExit):
            host.load_config()

    def test_discover_aliases_skips_patterns_and_follows_include(self):
        extra_dir = Path(self.tmp.name) / "extra"
        extra_dir.mkdir()
        (extra_dir / "more").write_text(
            "Host box-b box-c\nHost *.skip\nHost box-d\n", encoding="utf-8")
        self.write_ssh(
            "Host *\n    StrictHostKeyChecking accept-new\n"
            "Host box-a\n    HostName example.invalid\n"
            f"Include {extra_dir / 'more'}\n"
            "Host box-a\n"
        )
        aliases = host.discover_aliases(host.SSH_CONFIG)
        self.assertEqual(aliases, ["box-a", "box-b", "box-c", "box-d"])

    def test_missing_ssh_config_is_empty_not_an_error(self):
        self.assertEqual(host.discover_aliases(host.SSH_CONFIG), [])

    def test_summary_omits_identity_files(self):
        params = host.parse_ssh_g(
            "user demo\n"
            "hostname example.invalid\n"
            "port 2222\n"
            "identityfile /tmp/does-not-exist-key\n"
            "proxyjump jump-1\n"
        )
        summary = host.format_summary(params)
        self.assertEqual(summary, "demo@example.invalid:2222 via jump-1")
        self.assertNotIn("identity", summary)
        self.assertNotIn("does-not-exist-key", summary)

    def test_build_script_keeps_shell_and_passes_ssh_args(self):
        item = host.make_item("box-a")
        script = host.build_script(item, ["-v"])
        self.assertIn("ssh -v -- box-a", script)
        self.assertIn("exec bash -i", script)
        self.assertIn("ssh 退出码", script)

    def test_attach_uses_named_tmux_session(self):
        item = host.make_item("box-a", {"tmux_session": "dev", "remote_dir": "/opt/app"})
        argv = host.build_ssh_argv(item, [], attach=True)
        self.assertEqual(argv[:4], ["ssh", "-t", "--", "box-a"])
        self.assertIn("tmux new-session -A -s dev", argv[-1])
        self.assertIn("cd /opt/app", argv[-1])

    def test_attach_without_session_uses_tmux_attach_or_new(self):
        item = host.make_item("box-a")
        argv = host.build_ssh_argv(item, [], attach=True)
        self.assertEqual(argv[-1], "tmux attach-session || tmux new-session")

    def test_connect_mode_defaults_and_tab_is_clickable(self):
        self.write_ssh("Host box-a\n")
        items = host.build_items(host.default_config())
        geometry = {
            "rows": {10: 0},
            "tabs": [{"row": 5, "start": 20, "end": 28, "mode": "attach"}],
            "left": 0,
            "width": 40,
        }
        events = EventTerm(
            ("mouse", 5, 22, "click"),
            ("mouse", 10, 5, "click"),
        )
        with patch.object(host, "draw", return_value=geometry), \
                patch.object(host, "fill_summaries"):
            item, attach = host.pick_host(events, items, host.default_config())
        self.assertEqual(item["alias"], "box-a")
        self.assertTrue(attach)

    def test_picker_enter_defaults_to_connect(self):
        self.write_ssh("Host box-a\n")
        items = host.build_items(host.default_config())
        geometry = {"rows": {}, "tabs": [], "left": 0, "width": 40}
        with patch.object(host, "draw", return_value=geometry), \
                patch.object(host, "fill_summaries"):
            item, attach = host.pick_host(FakeTerm("\r"), items, host.default_config())
        self.assertEqual(item["alias"], "box-a")
        self.assertFalse(attach)

    def test_picker_hover_highlights_row(self):
        self.write_ssh("Host box-a\nHost box-b\n")
        items = host.build_items(host.default_config())
        geometry = {"rows": {10: 1}, "tabs": [], "left": 0, "width": 40}
        events = EventTerm(("mouse", 10, 5, "move"), ("key", "q"))
        with patch.object(host, "draw", return_value=geometry) as draw, \
                patch.object(host, "fill_summaries"):
            result = host.pick_host(events, items, host.default_config())
        self.assertIsNone(result)
        self.assertEqual(draw.call_args_list[1].args[3], 1)

    def test_hidden_host_is_omitted_from_menu(self):
        self.write_ssh("Host visible\nHost ghost\n")
        cfg = host.default_config()
        cfg["hosts"]["ghost"] = host.make_item("ghost", {"hidden": True})
        cfg["hosts"]["ghost"]["hidden"] = True
        items = host.build_items(cfg)
        self.assertEqual([item["alias"] for item in items], ["visible"])

    def test_direct_attach_cli_reaches_launch(self):
        argv = ["host", "--attach", "box-a"]
        item = host.make_item("box-a")
        cfg = host.default_config()
        with patch.object(host, "load_config", return_value=cfg), \
                patch.object(host.sys, "argv", argv), \
                patch.object(host, "find_item", return_value=item), \
                patch.object(host, "launch") as launch:
            host.main()
        launch.assert_called_once_with(item, [], True, cfg)

    def test_wt_handoff_sets_tab_title_and_generic_profile(self):
        item = host.make_item("box-a", {"name": "开发机", "group": "dev"})
        os.environ["HOST_DECK_HANDOFF"] = "1"
        try:
            with patch.object(host.shutil, "which", return_value="/mnt/c/wt.exe"), \
                    patch.object(host.subprocess, "run") as run, \
                    patch.object(host.time, "sleep"):
                run.return_value.returncode = 0
                ok = host.wt_handoff(item, ["-v"], attach=False, profile="SSH (WSL)")
            self.assertTrue(ok)
            cmd = run.call_args.args[0]
            self.assertIn("--title", cmd)
            self.assertEqual(cmd[cmd.index("--title") + 1], "开发机 · dev")
            self.assertEqual(cmd[cmd.index("-p") + 1], "SSH (WSL)")
            inner = cmd[-1]
            self.assertIn("--no-handoff", inner)
            self.assertIn("box-a", inner)
            self.assertIn("-- -v", inner)
        finally:
            os.environ.pop("HOST_DECK_HANDOFF", None)

    def test_menu_sections_put_favorites_first(self):
        items = [
            host.make_item("prod"),
            host.make_item("dev", {"group": "dev"}),
        ]
        items[0]["favorite"] = True
        sections = host.menu_sections(items)
        self.assertEqual(sections[0][0], "收藏")
        self.assertEqual(sections[0][1][0]["alias"], "prod")


if __name__ == "__main__":
    unittest.main()
