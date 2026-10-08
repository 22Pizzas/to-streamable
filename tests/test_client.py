import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import requests

from to_streamable.client import (
    Client,
    StreamingBody,
    build_multipart,
    interpret_upload_response,
    upload_query,
)
from to_streamable.errors import ApiChangedError, AuthError, RejectedError


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text or payload is None else str(payload)
        self.headers = {"content-type": "application/json" if payload is not None else "text/html"}

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class InterpretTests(unittest.TestCase):
    def test_missing_auth_message(self):
        response = FakeResponse(
            401,
            text="You must authenticate this request with your Streamable account using http basic auth",
        )
        with self.assertRaises(AuthError):
            interpret_upload_response(response)

    def test_invalid_credentials(self):
        response = FakeResponse(401, text="Invalid credentials")
        with self.assertRaises(AuthError) as caught:
            interpret_upload_response(response)
        self.assertIn("username or password", str(caught.exception))

    def test_rate_limit(self):
        response = FakeResponse(429, text="Too many attempts. Please try again later.")
        with self.assertRaises(RejectedError):
            interpret_upload_response(response)

    def test_html_is_api_changed(self):
        response = FakeResponse(200, text="<!DOCTYPE html><html>moved</html>")
        with self.assertRaises(ApiChangedError):
            interpret_upload_response(response)

    def test_json_without_shortcode(self):
        response = FakeResponse(200, payload={"status": 1})
        with self.assertRaises(ApiChangedError):
            interpret_upload_response(response)

    def test_size_error_is_rejected(self):
        response = FakeResponse(400, payload={"message": "Video exceeds the 250MB size limit"})
        with self.assertRaises(RejectedError):
            interpret_upload_response(response)

    def test_shortcode(self):
        response = FakeResponse(200, payload={"shortcode": "abc12", "status": 1})
        data = interpret_upload_response(response)
        self.assertEqual(data["shortcode"], "abc12")


class BodyTests(unittest.TestCase):
    def test_streaming_body_matches_parts(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            payload = b"\x00\x01video-bytes\xff" * 50
            path.write_bytes(payload)
            prefix, suffix, boundary, _ctype, size = build_multipart(path, title="Desk")
            self.assertIn(b'name="title"', prefix)
            self.assertIn(b'name="file"', prefix)
            self.assertEqual(size, len(payload))
            with path.open("rb") as handle:
                body = StreamingBody(handle, size, prefix, suffix)
                blob = b""
                while True:
                    chunk = body.read(17)
                    if not chunk:
                        break
                    blob += chunk
            self.assertEqual(blob, prefix + payload + suffix)
            self.assertEqual(len(body), len(blob))
            self.assertIn(boundary.encode(), prefix)

    def test_requests_keeps_body_and_sets_length(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mp4"
            path.write_bytes(b"abc123")
            prefix, suffix, boundary, _ctype, size = build_multipart(path)
            with path.open("rb") as handle:
                body = StreamingBody(handle, size, prefix, suffix)
                prepared = requests.Request(
                    "POST",
                    upload_query(mute=True, no_resize=True),
                    data=body,
                    headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
                    auth=("alice", "secret"),
                ).prepare()
            self.assertIs(prepared.body, body)
            self.assertEqual(int(prepared.headers["Content-Length"]), len(body))
            self.assertIn("mute", prepared.url)
            self.assertIn("noresize", prepared.url)
            self.assertTrue(prepared.headers["Authorization"].startswith("Basic "))

    def test_query_flags(self):
        self.assertTrue(upload_query().endswith("/upload"))
        self.assertTrue(upload_query(mute=True).endswith("/upload?mute"))
        self.assertTrue(upload_query(mute=True, no_resize=True).endswith("/upload?mute&noresize"))


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.headers = {}
        self.calls = []

    def post(self, url, data=None, headers=None, auth=None, timeout=None, allow_redirects=True):
        self.calls.append(
            {
                "url": url,
                "body": data.read(),
                "headers": headers,
                "auth": auth,
                "timeout": timeout,
                "allow_redirects": allow_redirects,
            }
        )
        return self.response

    def get(self, url, auth=None, timeout=None):
        self.calls.append({"url": url, "auth": auth})
        return self.response


class ClientTests(unittest.TestCase):
    def test_upload_sends_basic_auth_and_file(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "clip.mkv"
            path.write_bytes(b"mkv-bytes")
            session = FakeSession(FakeResponse(200, payload={"shortcode": "zz91", "status": 1}))
            client = Client("alice", "pw", session=session)
            result = client.upload(path, title="Hi", mute=True)
        self.assertEqual(result["url"], "https://streamable.com/zz91")
        call = session.calls[0]
        self.assertEqual(call["auth"], ("alice", "pw"))
        self.assertIn("mute", call["url"])
        self.assertIn(b'name="file"', call["body"])
        self.assertIn(b"mkv-bytes", call["body"])
        self.assertIn(b"Hi", call["body"])
        self.assertIn("video/x-matroska", call["body"].decode("utf-8", "replace"))

    def test_wait_until_ready(self):
        statuses = [
            FakeResponse(200, payload={"status": 1, "percent": 40}),
            FakeResponse(200, payload={"status": 2, "percent": 100, "url": "streamable.com/zz91"}),
        ]

        class SeqSession(FakeSession):
            def get(self, url, auth=None, timeout=None):
                self.calls.append(url)
                return statuses.pop(0)

        client = Client("alice", "pw", session=SeqSession(None))
        seen = []
        info = client.wait_until_ready("zz91", timeout=30, interval=0, on_status=seen.append)
        self.assertEqual(info["status"], 2)
        self.assertEqual(len(seen), 2)


if __name__ == "__main__":
    unittest.main()
