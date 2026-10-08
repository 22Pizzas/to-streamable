"""Unofficial Streamable upload client.

Checked on 2026-10-08 against the live host, without uploading a video:

* POST https://api.streamable.com/upload with no credentials returns
  401 and the text "You must authenticate this request with your
  Streamable account using http basic auth".
* The same call with a bad user:pass returns 401 "Invalid credentials".
* GET https://api.streamable.com/videos/{shortcode} still returns JSON
  with status, percent, url, message, and files. status 2 means ready.

Streamable's published API docs say uploading is not supported. This
module follows the request shape used by remixz/to-streamable and
pystreamable: multipart field "file", optional form field "title",
HTTP Basic auth, and bare query flags "mute" and "noresize".
"""

import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote

import requests

from to_streamable.errors import (
    ApiChangedError,
    AuthError,
    NetworkError,
    ProcessingError,
    RejectedError,
)

API_ROOT = "https://api.streamable.com"
UPLOAD_URL = API_ROOT + "/upload"
VIDEO_URL = API_ROOT + "/videos/{shortcode}"
PUBLIC_URL = "https://streamable.com/{shortcode}"

# ShareX and the old Ruby client use these. 2 is ready, 3 is an error.
STATUS_READY = 2
STATUS_ERROR = 3

# Streamable's support page says free accounts are limited to 250 MB
# and about 10 minutes. Paid accounts can be larger, so this is a warning.
FREE_ACCOUNT_BYTES = 250 * 1024 * 1024

USER_AGENT = "to-streamable/1.0 (unofficial; Kubuntu CLI)"

# Fallback when the filename has no useful suffix. mimetypes covers the rest.
EXTRA_MIME = {
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".flv": "video/x-flv",
    ".ts": "video/mp2t",
    ".m2ts": "video/mp2t",
    ".mts": "video/mp2t",
    ".wmv": "video/x-ms-wmv",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".3gp": "video/3gpp",
}


def public_url(shortcode):
    return PUBLIC_URL.format(shortcode=shortcode)


def content_type_for(path):
    suffix = Path(path).suffix.lower()
    if suffix in EXTRA_MIME:
        return EXTRA_MIME[suffix]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _safe_filename(path):
    name = Path(path).name.replace("\\", "_")
    return name.replace('"', "_").replace("\r", "").replace("\n", "")


def _filename_disposition(name):
    """Content-Disposition filename, plus filename* when the name is not ASCII."""
    try:
        name.encode("ascii")
        ascii_name = name
    except UnicodeEncodeError:
        ascii_name = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    header = f'filename="{ascii_name}"'
    if name != ascii_name:
        header += "; filename*=UTF-8''" + quote(name)
    return header


def build_multipart(path, title=None):
    """Return (prefix, suffix, boundary, content_type, file_size).

    The file bytes sit between prefix and suffix so the upload can stream.
    """
    boundary = "----ToStreamable" + os.urandom(8).hex()
    filename = _safe_filename(path)
    file_type = content_type_for(path)
    chunks = []
    if title:
        title_bytes = title.encode("utf-8")
        # quote() keeps the disposition header on one line.
        chunks.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="title"\r\n'
                f"\r\n"
            ).encode("utf-8")
            + title_bytes
            + b"\r\n"
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; {_filename_disposition(filename)}\r\n'
            f"Content-Type: {file_type}\r\n"
            f"\r\n"
        ).encode("utf-8")
    )
    prefix = b"".join(chunks)
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return prefix, suffix, boundary, file_type, os.path.getsize(path)


class StreamingBody:
    """File-like multipart body. requests streams objects with read and len."""

    def __init__(self, fileobj, file_size, prefix, suffix, on_progress=None):
        self._file = fileobj
        self._file_size = file_size
        self._prefix = prefix
        self._suffix = suffix
        self._on_progress = on_progress
        self._prefix_left = prefix
        self._suffix_left = suffix
        self._file_sent = 0
        self._phase = 0

    def __len__(self):
        return len(self._prefix) + self._file_size + len(self._suffix)

    def read(self, amt=-1):
        if amt is None or amt < 0:
            amt = 64 * 1024
        out = bytearray()
        while len(out) < amt and self._phase < 3:
            need = amt - len(out)
            if self._phase == 0:
                chunk = self._prefix_left[:need]
                self._prefix_left = self._prefix_left[len(chunk):]
                out += chunk
                if not self._prefix_left:
                    self._phase = 1
            elif self._phase == 1:
                chunk = self._file.read(need)
                if chunk:
                    self._file_sent += len(chunk)
                    out += chunk
                    if self._on_progress:
                        self._on_progress(self._file_sent, self._file_size)
                if not chunk or self._file_sent >= self._file_size:
                    self._phase = 2
            else:
                chunk = self._suffix_left[:need]
                self._suffix_left = self._suffix_left[len(chunk):]
                out += chunk
                if not self._suffix_left:
                    self._phase = 3
        return bytes(out)


def upload_query(mute=False, no_resize=False):
    """Bare flags, matching remixz/to-streamable: ?mute&noresize."""
    flags = []
    if mute:
        flags.append("mute")
    if no_resize:
        flags.append("noresize")
    if not flags:
        return UPLOAD_URL
    return UPLOAD_URL + "?" + "&".join(flags)


def _snippet(text, limit=300):
    text = " ".join((text or "").split())
    if len(text) > limit:
        return text[:limit] + "..."
    return text or "(empty body)"


def interpret_upload_response(response):
    """Turn an upload HTTP response into a shortcode, or raise a typed error."""
    status = response.status_code
    text = getattr(response, "text", "") or ""
    if 300 <= status < 400:
        raise ApiChangedError(
            f"The upload endpoint redirected (HTTP {status}). "
            "The unofficial API may have changed."
        )
    if status == 401:
        detail = _snippet(text)
        if "invalid credentials" in text.lower():
            raise AuthError(
                "Streamable rejected the username or password (HTTP 401). "
                "Check them with --setup or --auth user:pass."
            )
        raise AuthError(
            "Streamable requires HTTP Basic auth (HTTP 401). " + detail
        )
    if status == 429:
        raise RejectedError(
            "Streamable rate-limited this request (HTTP 429). "
            "Wait a few minutes and try again. " + _snippet(text)
        )
    if status == 413:
        raise RejectedError(
            "Streamable refused the upload as too large (HTTP 413). "
            "Free accounts are about 250 MB and 10 minutes."
        )

    data = _json_or_none(response)
    if data is None:
        if status >= 500:
            raise NetworkError(
                f"Streamable returned HTTP {status}. {_snippet(text)}"
            )
        raise ApiChangedError(
            "Streamable did not return JSON from the upload endpoint "
            f"(HTTP {status}, {_snippet(text)}). "
            "The unofficial upload API may have changed. "
            "The published API is read-only."
        )

    shortcode = data.get("shortcode")
    if shortcode:
        return data

    message = data.get("message") or data.get("error") or _snippet(str(data))
    lowered = str(message).lower()
    if status >= 500:
        raise NetworkError(f"Streamable returned HTTP {status}: {message}")
    if any(word in lowered for word in ("large", "size", "duration", "limit", "long", "mb")):
        raise RejectedError(f"Streamable refused the video: {message}")
    if status >= 400:
        raise RejectedError(f"Streamable refused the upload (HTTP {status}): {message}")
    raise ApiChangedError(
        "Upload response had no shortcode. "
        "The unofficial upload API may have changed. "
        f"Body: {_snippet(str(data))}"
    )


def _json_or_none(response):
    try:
        data = response.json()
    except ValueError:
        return None
    if isinstance(data, dict):
        return data
    return None


class Client:
    def __init__(self, username, password, session=None, timeout=(30, 120)):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

    def upload(self, path, title=None, mute=False, no_resize=False, on_progress=None):
        path = Path(path)
        if not path.is_file():
            raise RejectedError(f"Not a file: {path}")
        size = path.stat().st_size
        if size == 0:
            raise RejectedError(f"{path} is empty.")
        prefix, suffix, boundary, _file_type, file_size = build_multipart(path, title)
        url = upload_query(mute=mute, no_resize=no_resize)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
        try:
            with path.open("rb") as handle:
                body = StreamingBody(handle, file_size, prefix, suffix, on_progress)
                response = self.session.post(
                    url,
                    data=body,
                    headers=headers,
                    auth=(self.username, self.password),
                    timeout=self.timeout,
                    allow_redirects=False,
                )
        except requests.Timeout as exc:
            raise NetworkError(
                f"Timed out talking to Streamable while uploading {path.name}."
            ) from exc
        except requests.RequestException as exc:
            raise NetworkError(f"Network error during upload: {exc}") from exc

        data = interpret_upload_response(response)
        shortcode = data["shortcode"]
        return {
            "shortcode": shortcode,
            "url": public_url(shortcode),
            "status": data.get("status"),
            "bytes": size,
            "raw": data,
        }

    def video_status(self, shortcode):
        url = VIDEO_URL.format(shortcode=shortcode)
        try:
            response = self.session.get(
                url,
                auth=(self.username, self.password),
                timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise NetworkError(
                f"Timed out asking Streamable about {shortcode}."
            ) from exc
        except requests.RequestException as exc:
            raise NetworkError(f"Network error while checking status: {exc}") from exc

        if response.status_code == 401:
            raise AuthError("Streamable rejected credentials while checking status.")
        data = _json_or_none(response)
        if data is None or "status" not in data:
            raise ApiChangedError(
                "Status check did not return the expected JSON "
                f"(HTTP {response.status_code}, {_snippet(getattr(response, 'text', ''))}). "
                "The unofficial API may have changed."
            )
        return data

    def wait_until_ready(self, shortcode, timeout=600, interval=2, on_status=None):
        deadline = time.monotonic() + timeout
        last = None
        while True:
            last = self.video_status(shortcode)
            status = last.get("status")
            if on_status:
                on_status(last)
            if status == STATUS_READY:
                return last
            if isinstance(status, int) and status >= STATUS_ERROR:
                message = last.get("message") or "Streamable failed while processing the video."
                raise ProcessingError(str(message))
            if time.monotonic() >= deadline:
                raise ProcessingError(
                    f"Gave up after {timeout}s waiting for {public_url(shortcode)} "
                    f"to finish processing (last status {status}, "
                    f"{last.get('percent')}%)."
                )
            time.sleep(interval)


def warn_if_large(size, file=None):
    """Print a free-account size warning. Return True when it warned."""
    import sys

    if size <= FREE_ACCOUNT_BYTES:
        return False
    mb = size / (1024 * 1024)
    text = (
        f"Warning: {mb:.0f} MB is over the free-account limit of about 250 MB "
        "(and about 10 minutes). Streamable may reject this upload.\n"
    )
    (file or sys.stderr).write(text)
    return True
