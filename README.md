# to-streamable

Command-line uploader for [Streamable](https://streamable.com), written for Kubuntu and other Linux desktops. Give it a video file and it prints the public URL. Point it at a folder and it uploads new videos after they finish writing.

```bash
to-streamable ~/Videos/clip.mp4
# https://streamable.com/abc12
```

This is a Python replacement for the old Node tool [remixz/to-streamable](https://github.com/remixz/to-streamable). It uses the same unofficial upload call that tool used, plus the ideas in ShareX and [pystreamable](https://github.com/jernejovc/pystreamable): HTTP Basic auth, a multipart field named `file`, and the bare query flags `mute` and `noresize`.

Python 3.9 or newer. The only required library is `requests`.

## The upload API is unofficial

Streamable's [published API](https://streamable-support.zendesk.com/hc/en-us/articles/35415672400916-API-Documentation) is read-only. It can fetch metadata and an embed code. The documentation says uploading, clipping, and editing are not supported, and that those actions have to be done on the website.

In practice, uploads still go through the endpoint the website and older unofficial clients use:

```http
POST https://api.streamable.com/upload
Authorization: Basic base64(username:password)
Content-Type: multipart/form-data; boundary=...
```

A success body is JSON and contains `shortcode`. The public page is:

```text
https://streamable.com/{shortcode}
```

Processing status is the documented call:

```http
GET https://api.streamable.com/videos/{shortcode}
```

That `GET` is part of the published API. The `POST` is not. Streamable can change or remove the upload endpoint without notice. This tool does not scrape the website, does not follow redirects, and does not try a second host when the response looks wrong. It stops and says the API may have changed.

### What was checked on 2026-10-08

These calls were made against the live host. No video was uploaded.

| Call | Result |
| --- | --- |
| `POST /upload` with no `Authorization` header | `401`, body `You must authenticate this request with your Streamable account using http basic auth` |
| `POST /upload` with a bad username and password | `401`, body `Invalid credentials` |
| `GET /videos/hn8hq` | `200` JSON with `status`, `percent`, `url`, `embed_code`, `message`, `files`, `thumbnail_url`, `title`, `source`, `audio_channels` |
| `GET /me` | `404` HTML. Older wrappers used this for an auth check. This tool does not. |

`status` values, from ShareX and the older Ruby client, and still consistent with that public video:

| `status` | Meaning |
| --- | --- |
| 0 | Streamable is still receiving the upload |
| 1 | Processing |
| 2 | Ready |
| 3 or higher | Processing failed. `message` usually says why |

`--wait` polls `GET /videos/{shortcode}` about every 2 seconds until status is 2, status is 3 or higher, or `--wait-timeout` seconds pass (default 600).

### Request this tool sends

The body is streamed. The file is not loaded into memory.

- Auth is HTTP Basic, username and password.
- The file part is named `file`. The filename is the local basename. Non-ASCII names also send `filename*` (RFC 5987).
- Content-Type is guessed from the extension (`video/mp4`, `video/x-matroska`, `video/webm`, and so on). Streamable sniffs the bytes anyway.
- `--title` adds a form field named `title`. If you omit it, Streamable names the video from the filename.
- `--mute` sends the bare query flag `mute`.
- `--no-resize` sends the bare query flag `noresize`.
- Both flags together are `POST /upload?mute&noresize`, with no `=true`. That is the shape remixz/to-streamable sent.
- `User-Agent` is `to-streamable/1.0 (unofficial; Kubuntu CLI)`.
- Redirects are refused. A 3xx is treated as a changed API, because following it would drop the upload body.
- Connect timeout is 30 seconds. The read timeout is 120 seconds between chunks, which is long enough for a slow link and short enough to notice a dead connection.

A response is accepted only when it is JSON and contains `shortcode`. Everything else is an error. See [Exit codes](#exit-codes).

### What a refusal looks like

| Response | What the tool does |
| --- | --- |
| `401` and text containing `invalid credentials` | Exit 3. The username or password was rejected. |
| `401` with the "you must authenticate" text | Exit 3. No usable Basic auth was sent. |
| `413` | Exit 6. The file is too large. |
| `429` | Exit 6. Too many attempts. Wait and try again. Empty or repeated login probes hit this. |
| `400` JSON whose message mentions size, duration, or a limit | Exit 6. Free-plan limits are the usual cause. |
| Other `4xx` JSON with a `message` | Exit 6, and the message is printed. |
| `5xx` | Exit 5. |
| HTML, a redirect, or JSON with no `shortcode` | Exit 4. The unofficial upload contract no longer matches. |
| Connection error or timeout | Exit 5. |

The password is never printed. Response bodies are trimmed to about 300 characters before they are shown.

## Account limits

Streamable's upload help says free accounts are limited to about **250 MB** and **10 minutes** per video. Videos on the free plan can disappear after a long stretch with no views. The figure people quote is about **90 days**. Check the current plan page before you rely on that, because Streamable changes it.

This tool warns on stderr when a file is larger than 250 MiB (250 × 1024 × 1024 bytes) and still uploads it. A paid account may accept the larger file. The warning is not a measurement of duration. A 10-minute cap is enforced by Streamable after it inspects the video, not by this program.

There is no delete command for the hosted video. `--delete` only removes the local file, and only in watch mode, after a successful upload. To take a video down, sign in at streamable.com and delete it from your video list.

## Install

The repository is private. `pip install` from a public URL will not work until you are authenticated to GitHub.

On Kubuntu, with [pipx](https://pipx.pypa.io/) and SSH already set up for GitHub (this is what `gh auth status` uses on this machine):

```bash
sudo apt install python3 pipx xclip
pipx ensurepath
pipx install 'git+ssh://git@github.com/22Pizzas/to-streamable.git'
```

Open a new terminal if `pipx ensurepath` changed your PATH. Then:

```bash
to-streamable --setup
to-streamable ~/Videos/clip.mp4
```

Upgrade later with:

```bash
pipx upgrade to-streamable
```

If pipx cannot see the private repo over SSH, install from a local checkout instead:

```bash
git clone git@github.com:22Pizzas/to-streamable.git
pipx install --force ~/to-streamable
```

Without pipx:

```bash
python3 -m pip install --user 'git+ssh://git@github.com/22Pizzas/to-streamable.git'
```

That script lands in `~/.local/bin`. That directory has to be on `PATH`.

### Clipboard tools

The URL is copied after a successful upload. The first tool that exists is used:

1. `wl-copy`, if `WAYLAND_DISPLAY` is set. Package: `wl-clipboard`.
2. `xclip` (`xclip -selection clipboard`), preferred when `DISPLAY` is set. Package: `xclip`.
3. `xsel --clipboard --input`.
4. Python `pyperclip`, only if that package is installed.

Kubuntu on X11 usually wants `xclip`. A Wayland session wants `wl-clipboard`. If none of these exist, the URL is still printed and a warning explains what to install. The upload itself is still a success. `--no-clipboard` skips the copy.

Optional extras, if you want them baked into the pipx environment:

```bash
pipx install 'git+ssh://git@github.com/22Pizzas/to-streamable.git[watch,clipboard]'
```

`watch` installs [watchdog](https://github.com/gorakhargosh/watchdog) so folder mode can use inotify. Without it, the folder is polled once a second. `clipboard` installs `pyperclip`, which is only used when the system clipboard tools are missing.

## Credentials

Four sources, first match wins:

1. `--auth user:pass` for this invocation only. The split is on the first colon, so a password may contain colons: `--auth 'alice:se:cret'`.
2. Both environment variables `STREAMABLE_USER` and `STREAMABLE_PASS`. Setting only one of them is an error.
3. The config file written by `--setup`.
4. An interactive prompt, when stdin is a terminal and nothing above was set. The answers are then saved.

```bash
to-streamable --setup
```

`--setup` asks for a username and a password. The password prompt does not echo. It writes JSON to:

```text
~/.config/to-streamable/config
```

If `XDG_CONFIG_HOME` is set, the file is `$XDG_CONFIG_HOME/to-streamable/config`. The directory is mode `700`. The file is mode `600`. The write is to a temporary file in that directory, then an atomic rename, so a crash mid-write does not leave a half-written config.

The file looks like this:

```json
{
  "username": "you@example.com",
  "password": "your-password"
}
```

The password is plain text. Mode `600` means only your user can read it. It is not stored in a keyring. Do not commit this file, do not paste it into a ticket, and do not pass `--auth` in a shell history you share. Putting the password in the environment is a bit better for a one-off script, because it never hits the config file, but it is still visible to other processes you run.

`--setup` does not contact Streamable. A probe with no file is treated as a failed login and the host answers `429` after a few of them. The username and password are checked on the next real upload. A wrong password exits 3 and leaves the local video where it was.

To replace a saved password, run `--setup` again. It overwrites the file.

## One-shot upload

```bash
to-streamable ~/Videos/clip.mp4
```

What you see:

- Stderr shows `Uploading ...`, then a progress bar in percent and megabytes. The bar updates when the percent changes, so it does not flood the terminal.
- Stdout's last line is only the URL, so another program can capture it.

```bash
url=$(to-streamable --quiet --no-clipboard ~/Videos/clip.mp4)
printf '%s\n' "$url"
```

`--quiet` hides the progress bar and the "copied to the clipboard" line. It does not hide errors.

Useful combinations:

```bash
to-streamable --mute --title "Desk clip" ~/Videos/clip.mp4
to-streamable --no-resize ~/Videos/clip.mp4
to-streamable --wait ~/Videos/clip.mp4
to-streamable --wait --wait-timeout 900 ~/Videos/clip.mp4
to-streamable --auth 'alice:s3cret' --no-clipboard ~/Videos/clip.mp4
```

`--mute` and `--no-resize` are requests to Streamable, not local ffmpeg filters. This program does not transcode. If Streamable ignores a flag, the URL is still returned.

`--wait` prints lines such as `Streamable: processing 40%` until status is 2, then prints the URL. If processing fails, the process exits 7 and the URL is not printed, even though the shortcode was assigned. If you need the URL before processing finishes, omit `--wait`. The page exists as soon as the upload returns a shortcode. It may still show a processing state in the browser.

Accepted extensions are not checked in one-shot mode. If the path is a real, non-empty file, it is uploaded. Streamable decides whether the bytes are a video.

## Watch a folder

```bash
to-streamable --watch ~/Videos/inbox
```

Leave that process running. It uploads new videos and keeps going until you press Ctrl+C (exit 130).

### Which files

A file is a candidate when all of these are true:

- It sits directly in the watched folder, not in a subfolder.
- The name does not start with a dot.
- The extension is one of: `mp4`, `mov`, `mkv`, `webm`, `avi`, `m4v`, `mpg`, `mpeg`, `wmv`, `flv`, `ts`, `m2ts`, `mts`, `3gp`, `ogv`. The check is case-insensitive.
- The name does not end in `.part`, `.partial`, `.crdownload`, `.tmp`, `.temp`, or `.download`. Browsers and download managers use those while a download is still open.

Files that were already in the folder when the process started are recorded by size and mtime and left alone. Pass `--existing` to upload those too.

### When a file is "finished"

The watcher remembers `(size, mtime)` and the time that pair was first seen. The file is uploaded only when:

- the size is greater than 0, and
- the size and mtime stay the same for `--stable-seconds` (default 2).

A download that is still growing resets the timer. Two seconds is enough for a finished copy onto a local disk. If you drop files from a slow network filesystem, raise it:

```bash
to-streamable --watch ~/Videos/inbox --stable-seconds 5
```

With watchdog installed, inotify wakes the loop as soon as the directory changes. The stability timer is the same either way. Without watchdog, the folder is listed about once a second (`--stable-seconds` still applies). One log line says that polling is in use.

Videos are uploaded one at a time. A second file that appears during an upload is picked up on the next pass.

### After a successful upload

The URL is printed and copied, the same way as a one-shot upload.

Then the local file is moved to `<folder>/uploaded/`. If that name already exists, the destination becomes `<stem>-YYYYmmdd-HHMMSS<suffix>`, and a counter is added if that also exists. The original is not overwritten.

```bash
to-streamable --watch ~/Videos/inbox --uploaded-dir ~/Videos/done
```

`--delete` removes the local file instead of moving it. This does not delete the Streamable video.

```bash
to-streamable --watch ~/Videos/inbox --delete
```

`--title`, `--mute`, `--no-resize`, and `--wait` apply to every file in the session. A fixed `--title` means every video in that run gets the same title.

### Failures while watching

A log line goes to stderr and is appended to:

```text
~/.config/to-streamable/upload.log
```

Override the path with `--log`. The log file is mode `600`. It contains timestamps, filenames, error text, and URLs. It does not contain the password.

| Failure | Watcher |
| --- | --- |
| Wrong password, or HTTP 401 | Stops. Fix credentials and start again. |
| Response is not the shortcode JSON (exit-4 class) | Stops. The upload API may have changed. |
| Network error, timeout, 429, file rejected, processing error | Logged. The watcher stays up. |
| Upload worked, but the move or delete failed | Logged. The video is already on Streamable. The local file is left in place. |

A file that failed is remembered by its size and mtime, so the same bytes are not uploaded in a loop. Replace the file, or move it out of the folder and back in, and the new mtime makes it eligible again. Starting the command again with `--existing` also retries files that are still sitting in the folder.

The `uploaded/` directory is not scanned. Moving a file there does not upload it a second time.

## All flags

```text
to-streamable [--mute] [--no-resize] [--title TEXT] [--auth USER:PASS]
              [--watch FOLDER] [--setup] [--wait] [--wait-timeout SECONDS]
              [--delete] [--uploaded-dir FOLDER] [--existing]
              [--stable-seconds SECONDS] [--no-clipboard] [--log FILE]
              [--quiet] [--version]
              [VIDEO]
```

| Flag | Effect |
| --- | --- |
| `VIDEO` | File to upload. Required unless you pass `--watch` or `--setup`. |
| `--mute` | Send the `mute` query flag. |
| `--no-resize` | Send the `noresize` query flag. |
| `--title TEXT` | Form field `title`. |
| `--auth USER:PASS` | Credentials for this run. Beats the environment and the config file. |
| `--watch FOLDER` | Run until Ctrl+C, uploading new videos in `FOLDER`. |
| `--setup` | Prompt, save credentials, exit. Ignores a video argument. |
| `--wait` | Poll until processing finishes before printing the URL. |
| `--wait-timeout SECONDS` | Give up on `--wait` after this many seconds. Default 600. Must be greater than 0. |
| `--delete` | Watch mode only. Delete the local file after a successful upload. |
| `--uploaded-dir FOLDER` | Watch mode destination. Default is `FOLDER/uploaded`. |
| `--existing` | Watch mode. Also upload videos already in the folder at startup. |
| `--stable-seconds SECONDS` | How long the size must stay unchanged. Default 2. Must be 0 or more. |
| `--no-clipboard` | Do not copy the URL. |
| `--log FILE` | Watch-mode log path. |
| `--quiet` | URL on stdout, errors on stderr, no progress bar. |
| `--version` | Print `to-streamable 1.0.0` and exit. |
| `-h`, `--help` | Help, including the examples at the bottom. |

`--watch` and a video path together are an error. You are either uploading one file or watching a folder.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Upload succeeded, or `--setup` saved the config. In watch mode, 0 is not used while the loop is running. Ctrl+C is 130. |
| 1 | Local problem: missing file, empty file, bad `--stable-seconds`, both a video and `--watch`, and similar. |
| 2 | argparse rejected the command line, or `--auth` was not `user:pass`. |
| 3 | No credentials, only one of the two environment variables, unreadable config, or Streamable returned 401. |
| 4 | The upload or status response does not match the contract above. |
| 5 | DNS, connection, timeout, or HTTP 5xx. |
| 6 | Streamable refused the video or rate-limited the request. |
| 7 | Processing failed, or `--wait` timed out. |
| 130 | Ctrl+C. |

A script can branch on these. For example, exit 3 means fix the login, exit 4 means stop and read the message before retrying, exit 6 means the file or the rate limit is the problem.

## Layout

```text
pyproject.toml          package metadata and the to-streamable script entry
src/to_streamable/
  cli.py                argparse, one-shot flow, watch-mode logging
  client.py             multipart body, upload, status poll, error classification
  config.py             credential order, mode 600 save
  clipboard.py          wl-copy, xclip, xsel, pyperclip
  watch.py              extension filter, stability, move-aside, poll or inotify
  errors.py             exception types and exit codes
tests/                  unittest, no network
```

`python3 -m to_streamable` works from a checkout when `PYTHONPATH=src`.

## Development

```bash
git clone git@github.com:22Pizzas/to-streamable.git
cd to-streamable
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The tests build the multipart body, check that `requests` keeps it as a stream and sets `Content-Length`, classify 401 / 413 / 429 / HTML / missing-shortcode responses, round-trip the config file mode, and walk the watcher's stability rules. They do not contact Streamable. Do not add a test that posts a real file. The host rate-limits failed attempts.

To try a local edit without reinstalling:

```bash
PYTHONPATH=src python3 -m to_streamable --help
```

To replace the pipx copy with the checkout:

```bash
pipx install --force ~/path/to/to-streamable
```

## Security notes

- The config file holds the Streamable password in plain text, mode `600`.
- `--auth` puts the password in the process list and in shell history.
- The upload log can contain filenames and Streamable URLs. It is mode `600`.
- Nothing in this repository stores your password. `~/.config/to-streamable/` is outside the git tree on purpose.
- The tool sends the password on every upload and every status poll as HTTP Basic over HTTPS. It does not keep a session cookie.

## What this does not do

- It does not delete, rename, or unlist a video that is already on Streamable.
- It does not import a video from a URL (`/import` exists on older wrappers and is not used here).
- It does not trim, crop, mute, or resize locally. `--mute` and `--no-resize` are flags on the upload request.
- It does not retry a failed one-shot upload.
- It does not watch subfolders.
- It does not keep going after the upload endpoint stops returning shortcode JSON.

## License

MIT. See [LICENSE](LICENSE).
