import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "host_secrets.py"
SPEC = importlib.util.spec_from_file_location("host_secrets", SOURCE)
secrets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(secrets)


class HostSecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["HOST_DECK_SECRETS_DIR"] = self.tmp.name
        secrets._HAS_CACHE.clear()

    def tearDown(self):
        os.environ.pop("HOST_DECK_SECRETS_DIR", None)
        secrets._HAS_CACHE.clear()

    def test_store_get_delete_roundtrip(self):
        secrets.store_password("box-a", "secret-value-xyz")
        self.assertTrue(secrets.has_password("box-a"))
        self.assertEqual(secrets.get_password("box-a"), "secret-value-xyz")
        secrets.delete_password("box-a")
        self.assertFalse(secrets.has_password("box-a"))
        self.assertIsNone(secrets.get_password("box-a"))

    def test_files_are_owner_read_write_only(self):
        secrets.store_password("box-a", "secret-value-xyz")
        path = Path(self.tmp.name) / "box-a"
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")

    def test_askpass_prints_password_for_password_prompt(self):
        secrets.store_password("box-a", "secret-value-xyz")
        os.environ["HOST_DECK_ASKPASS_ALIAS"] = "box-a"
        try:
            from io import StringIO
            from unittest.mock import patch
            buf = StringIO()
            with patch.object(secrets.sys, "stdout", buf):
                code = secrets.run_askpass(["Password for linux@example:"])
            self.assertEqual(code, 0)
            self.assertEqual(buf.getvalue(), "secret-value-xyz\n")
        finally:
            os.environ.pop("HOST_DECK_ASKPASS_ALIAS", None)

    def test_askpass_refuses_host_key_prompt(self):
        secrets.store_password("box-a", "secret-value-xyz")
        os.environ["HOST_DECK_ASKPASS_ALIAS"] = "box-a"
        try:
            from io import StringIO
            from unittest.mock import patch
            buf = StringIO()
            with patch.object(secrets.sys, "stdout", buf):
                code = secrets.run_askpass([
                    "The authenticity of host example can't be established. "
                    "Are you sure you want to continue connecting (yes/no)?"
                ])
            self.assertEqual(code, 1)
            self.assertEqual(buf.getvalue(), "")
        finally:
            os.environ.pop("HOST_DECK_ASKPASS_ALIAS", None)
