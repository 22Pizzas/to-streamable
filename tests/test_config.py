import os
import stat
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from to_streamable.config import (
    credentials_from_env,
    load_saved,
    parse_auth_pair,
    resolve_credentials,
    save_credentials,
)
from to_streamable.errors import AuthError, UsageError


class ConfigTests(unittest.TestCase):
    def test_parse_auth_keeps_colons_in_password(self):
        self.assertEqual(parse_auth_pair("alice:se:cret"), ("alice", "se:cret"))

    def test_parse_auth_rejects_bad_pairs(self):
        with self.assertRaises(UsageError):
            parse_auth_pair("alice")
        with self.assertRaises(UsageError):
            parse_auth_pair(":secret")

    def test_save_is_mode_600_and_roundtrips(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "config"
            save_credentials("alice", "se:cret", path)
            mode = stat.S_IMODE(path.stat().st_mode)
            self.assertEqual(mode, 0o600)
            dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
            self.assertEqual(dir_mode & 0o077, 0)
            self.assertEqual(load_saved(path), ("alice", "se:cret"))

    def test_env_requires_both(self):
        old_user = os.environ.pop("STREAMABLE_USER", None)
        old_pass = os.environ.pop("STREAMABLE_PASS", None)
        try:
            os.environ["STREAMABLE_USER"] = "alice"
            with self.assertRaises(AuthError):
                credentials_from_env()
            os.environ["STREAMABLE_PASS"] = "secret"
            self.assertEqual(credentials_from_env(), ("alice", "secret"))
        finally:
            os.environ.pop("STREAMABLE_USER", None)
            os.environ.pop("STREAMABLE_PASS", None)
            if old_user is not None:
                os.environ["STREAMABLE_USER"] = old_user
            if old_pass is not None:
                os.environ["STREAMABLE_PASS"] = old_pass

    def test_auth_pair_beats_env_and_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            save_credentials("fromfile", "filepass", path)
            os.environ["STREAMABLE_USER"] = "fromenv"
            os.environ["STREAMABLE_PASS"] = "envpass"
            try:
                got = resolve_credentials("cli:clipass", path=path, prompt=False)
                self.assertEqual(got, ("cli", "clipass"))
                got = resolve_credentials(None, path=path, prompt=False)
                self.assertEqual(got, ("fromenv", "envpass"))
            finally:
                os.environ.pop("STREAMABLE_USER", None)
                os.environ.pop("STREAMABLE_PASS", None)
            self.assertEqual(
                resolve_credentials(None, path=path, prompt=False),
                ("fromfile", "filepass"),
            )


if __name__ == "__main__":
    unittest.main()
