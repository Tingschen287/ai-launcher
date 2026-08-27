"""跨 deck 跳转（d 键）。"""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "src" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tui = _load("deck_tui")
launcher = _load("ai_launcher")
host = _load("host_deck")


class FakeTerm:
    def __init__(self, *keys):
        self.keys = iter(keys)

    def key(self):
        return "key", next(self.keys)


class PeerAvailableTests(unittest.TestCase):
    def test_existing_path_is_available(self):
        self.assertTrue(tui.peer_available("/bin/sh"))

    def test_missing_path_is_not_available(self):
        self.assertFalse(tui.peer_available("/nonexistent/deck/binary"))

    def test_empty_path_is_not_available(self):
        self.assertFalse(tui.peer_available(""))

    def test_tilde_is_expanded(self):
        with tempfile.NamedTemporaryFile(dir=os.path.expanduser("~")) as handle:
            self.assertTrue(tui.peer_available("~/" + os.path.basename(handle.name)))


class SwitchDeckTests(unittest.TestCase):
    def test_missing_peer_exits_instead_of_execing(self):
        with patch.object(tui.os, "execv") as execv:
            with self.assertRaises(SystemExit):
                tui.switch_deck("/nonexistent/deck", "P", "X_HANDOFF", False)
        execv.assert_not_called()

    def test_without_handoff_execs_peer_in_place(self):
        with patch.object(tui.os, "execv") as execv, \
                patch.object(tui.shutil, "which", return_value="/wt.exe") as which:
            tui.switch_deck("/bin/sh", "Host Deck", "HOST_DECK_HANDOFF", False)
        which.assert_not_called()          # 不在 WT 交接模式，压根不该找 wt.exe
        args = execv.call_args.args
        self.assertEqual(args[0], "/bin/bash")
        self.assertEqual(args[1][:2], ["bash", "-lc"])
        self.assertIn("/bin/sh", args[1][2])

    def test_handoff_opens_peer_profile_tab_and_exits(self):
        done = type("R", (), {"returncode": 0})()
        with patch.object(tui.os, "execv") as execv, \
                patch.object(tui.shutil, "which", return_value="/wt.exe"), \
                patch.object(tui.subprocess, "run", return_value=done) as run, \
                patch.object(tui.time, "sleep"):
            with self.assertRaises(SystemExit) as caught:
                tui.switch_deck("/bin/sh", "Host Deck", "HOST_DECK_HANDOFF", True)
        self.assertEqual(caught.exception.code, 0)
        execv.assert_not_called()          # 新页签接管了，本页签不再自己跑
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:5], ["/wt.exe", "-w", "0", "nt", "-p"])
        self.assertEqual(cmd[5], "Host Deck")
        self.assertIn("HOST_DECK_HANDOFF=1", cmd[-1])
        self.assertIn("/bin/sh", cmd[-1])

    def test_handoff_falls_back_to_exec_when_wt_fails(self):
        failed = type("R", (), {"returncode": 1})()
        with patch.object(tui.os, "execv") as execv, \
                patch.object(tui.shutil, "which", return_value="/wt.exe"), \
                patch.object(tui.subprocess, "run", return_value=failed):
            tui.switch_deck("/bin/sh", "Host Deck", "HOST_DECK_HANDOFF", True)
        execv.assert_called_once()

    def test_handoff_falls_back_to_exec_when_wt_missing(self):
        with patch.object(tui.os, "execv") as execv, \
                patch.object(tui.shutil, "which", return_value=None):
            tui.switch_deck("/bin/sh", "Host Deck", "HOST_DECK_HANDOFF", True)
        execv.assert_called_once()


class PeerWiringTests(unittest.TestCase):
    def test_each_deck_points_at_the_other(self):
        self.assertTrue(launcher.PEER_BIN.endswith("/host"))
        self.assertTrue(host.PEER_BIN.endswith("/ai"))
        self.assertEqual(launcher.PEER_HANDOFF_ENV, "HOST_DECK_HANDOFF")
        self.assertEqual(host.PEER_HANDOFF_ENV, "AI_LAUNCHER_HANDOFF")

    def test_switch_key_is_not_already_taken(self):
        # s 在 Agent 列表里是「纯 Shell」，所以切 deck 只能用 d。
        source = (ROOT / "src" / "ai_launcher.py").read_text(encoding="utf-8")
        self.assertIn('elif k == "s":\n            return "SHELL"', source)
        self.assertIn('elif k == "d" and can_switch:', source)


class AgentPickerSwitchTests(unittest.TestCase):
    AGENTS = [{"key": "x", "name": "X", "color": "#ffffff", "note": ""}]

    def test_d_returns_switch_when_peer_installed(self):
        with patch.object(launcher, "draw", return_value={"rows": {}}), \
                patch.object(launcher, "peer_available", return_value=True):
            result = launcher.pick_agent(FakeTerm("d"), self.AGENTS)
        self.assertEqual(result, "SWITCH")

    def test_d_is_inert_when_peer_missing(self):
        with patch.object(launcher, "draw", return_value={"rows": {}}), \
                patch.object(launcher, "peer_available", return_value=False):
            result = launcher.pick_agent(FakeTerm("d", "q"), self.AGENTS)
        self.assertIsNone(result)          # d 被忽略，继续等下一个键

    def test_footer_mentions_switch_only_when_peer_installed(self):
        with patch.object(launcher, "draw", return_value={"rows": {}}) as draw, \
                patch.object(launcher, "peer_available", return_value=True):
            launcher.pick_agent(FakeTerm("q"), self.AGENTS)
        self.assertIn("d 切到 Host", draw.call_args.args[4])
        with patch.object(launcher, "draw", return_value={"rows": {}}) as draw, \
                patch.object(launcher, "peer_available", return_value=False):
            launcher.pick_agent(FakeTerm("q"), self.AGENTS)
        self.assertNotIn("d 切到", draw.call_args.args[4])


if __name__ == "__main__":
    unittest.main()
