import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from to_streamable.cli import build_parser, run
from to_streamable.errors import AuthError


class CliTests(unittest.TestCase):
    def test_help_mentions_the_flags(self):
        help_text = build_parser().format_help()
        for flag in (
            "--mute",
            "--no-resize",
            "--title",
            "--auth",
            "--watch",
            "--setup",
            "--wait",
            "--no-clipboard",
        ):
            self.assertIn(flag, help_text)
        self.assertIn("unofficial", help_text.lower())

    def test_setup_writes_config(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            with mock.patch("to_streamable.cli.prompt_credentials", return_value=("ada", "pw")), \
                 mock.patch("to_streamable.cli.save_credentials", return_value=path) as save:
                code = run(["--setup"])
            self.assertEqual(code, 0)
            save.assert_called_once_with("ada", "pw")

    def test_upload_prints_url_and_copies(self):
        with TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"123456")
            result = {"shortcode": "abc12", "url": "https://streamable.com/abc12"}
            with mock.patch("to_streamable.cli.resolve_credentials", return_value=("ada", "pw")), \
                 mock.patch("to_streamable.cli.Client") as client_cls, \
                 mock.patch("to_streamable.cli.copy_text", return_value="xclip") as copy, \
                 mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                client_cls.return_value.upload.return_value = result
                code = run([str(video), "--no-resize", "--mute", "--title", "Hi"])
            self.assertEqual(code, 0)
            self.assertIn("https://streamable.com/abc12", stdout.getvalue())
            copy.assert_called_once_with("https://streamable.com/abc12")
            kwargs = client_cls.return_value.upload.call_args.kwargs
            self.assertTrue(kwargs["mute"])
            self.assertTrue(kwargs["no_resize"])
            self.assertEqual(kwargs["title"], "Hi")

    def test_missing_credentials_when_not_a_tty(self):
        with TemporaryDirectory() as tmp:
            video = Path(tmp) / "clip.mp4"
            video.write_bytes(b"1234")
            with mock.patch("to_streamable.cli.resolve_credentials", side_effect=AuthError("nope")):
                with self.assertRaises(AuthError):
                    run([str(video)])


if __name__ == "__main__":
    unittest.main()
