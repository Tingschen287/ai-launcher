import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def load(name, file_name):
    spec = importlib.util.spec_from_file_location(name, SRC / file_name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


host = load("host_deck", "host_deck.py")
importer = load("tabby_import", "tabby_import.py")


SAMPLE = """
groups:
  - id: g-dev
    name: dev
profiles:
  - type: ssh
    id: p-one
    name: Dev Box
    group: g-dev
    options:
      host: one.example.invalid
      user: linux
      port: 2222
      auth: password
  - type: ssh
    id: p-two
    name: 中文主机
    options:
      host: two.example.invalid
      privateKeys:
        - C:\\\\Users\\\\demo\\\\.ssh\\\\id_ed25519
"""


class TabbyImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.ssh = root / "ssh_config"
        self.conf = root / "hosts.toml"
        self.tabby = root / "config.yaml"
        self.tabby.write_text(SAMPLE, encoding="utf-8")
        os.environ["HOST_DECK_SECRETS_DIR"] = str(root / "secrets")
        host.SSH_CONFIG = str(self.ssh)
        host.CONF = str(self.conf)
        importer.deck.SSH_CONFIG = str(self.ssh)
        importer.deck.CONF = str(self.conf)
        importer.secrets._HAS_CACHE.clear()

    def tearDown(self):
        os.environ.pop("HOST_DECK_SECRETS_DIR", None)
        importer.secrets._HAS_CACHE.clear()

    def test_windows_path_maps_into_wsl(self):
        self.assertEqual(
            importer.windows_path_to_wsl(r"C:\Users\demo\.ssh\id_ed25519"),
            "/mnt/c/Users/demo/.ssh/id_ed25519",
        )

    def test_import_writes_ssh_and_meta_without_passwords_in_files(self):
        vault = {"ssh@one.example.invalid:2222/linux": "secret-from-tabby"}
        stats = importer.import_from_tabby(
            str(self.tabby), getter=vault.get)
        self.assertEqual(stats["imported"], 2)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["passwords"], 1)
        self.assertEqual(stats["password_missing"], 0)
        text = self.ssh.read_text(encoding="utf-8")
        self.assertIn("Host Dev-Box", text)
        self.assertIn("HostName one.example.invalid", text)
        self.assertIn("Port 2222", text)
        self.assertIn("# host-deck tabby: p-one", text)
        self.assertIn("/mnt/c/Users/demo/.ssh/id_ed25519", text)
        self.assertNotIn("secret-from-tabby", text)
        meta = self.conf.read_text(encoding="utf-8")
        self.assertIn("Dev Box", meta)
        self.assertIn("dev", meta)
        self.assertNotIn("secret-from-tabby", meta)
        self.assertEqual(host.secrets.get_password("Dev-Box"), "secret-from-tabby")

    def test_second_import_skips_existing_tabby_ids(self):
        importer.import_from_tabby(str(self.tabby), copy_passwords=False)
        stats = importer.import_from_tabby(str(self.tabby), copy_passwords=False)
        self.assertEqual(stats["imported"], 0)
        self.assertEqual(stats["skipped"], 2)
        self.assertEqual(self.ssh.read_text(encoding="utf-8").count("Host "), 2)
