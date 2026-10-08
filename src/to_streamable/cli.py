"""Command-line interface for to-streamable."""

import argparse
import logging
import os
import sys
from pathlib import Path

from to_streamable import __version__
from to_streamable.clipboard import ClipboardError, copy_text
from to_streamable.client import Client, warn_if_large
from to_streamable.config import (
    log_path,
    prompt_credentials,
    resolve_credentials,
    save_credentials,
)
from to_streamable.errors import ApiChangedError, AuthError, StreamableError
from to_streamable.watch import move_uploaded, watch_folder

EPILOG = """
examples:
  to-streamable --setup
  to-streamable video.mp4
  to-streamable --mute --title "Desk clip" video.mp4
  to-streamable --wait --no-resize video.mp4
  to-streamable --auth user:pass video.mp4
  to-streamable --watch ~/Videos/inbox
  to-streamable --watch ~/Videos/inbox --delete

credentials, first match wins:
  --auth user:pass
  STREAMABLE_USER and STREAMABLE_PASS
  ~/.config/to-streamable/config   (mode 600, written by --setup)

watch mode uploads a video only after its size stops changing. Files
already in the folder are left alone unless you pass --existing.
Success moves the file into an uploaded/ subfolder, or deletes it
with --delete. A log is appended to ~/.config/to-streamable/upload.log.

The upload endpoint is not part of Streamable's published API, which
is read-only. If the response is unexpected, this tool stops and says
the API may have changed. Free accounts are about 250 MB and 10
minutes per video, and videos can disappear after about 90 days
without views.
""".strip()


def build_parser():
    parser = argparse.ArgumentParser(
        prog="to-streamable",
        description=(
            "Upload a video to Streamable and print the public URL. "
            "Uses the unofficial POST /upload endpoint (HTTP Basic auth, "
            "multipart field \"file\")."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "video",
        nargs="?",
        help="video file to upload (mp4, mov, mkv, webm, avi, ...)",
    )
    parser.add_argument("--mute", action="store_true", help="ask Streamable to mute the video")
    parser.add_argument(
        "--no-resize",
        action="store_true",
        help="ask Streamable not to resize the video",
    )
    parser.add_argument("--title", help="title stored on the video")
    parser.add_argument(
        "--auth",
        metavar="USER:PASS",
        help="Streamable username and password; overrides the environment and the config file",
    )
    parser.add_argument(
        "--watch",
        metavar="FOLDER",
        help="watch FOLDER for new videos and upload each one when it has finished writing",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="prompt for a username and password and save them (mode 600)",
    )
    parser.add_argument(
        "--wait",
        action="store_true",
        help="after upload, poll until Streamable finishes processing",
    )
    parser.add_argument(
        "--wait-timeout",
        type=float,
        default=600,
        metavar="SECONDS",
        help="give up on --wait after this many seconds (default 600)",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="in watch mode, delete the file after a successful upload",
    )
    parser.add_argument(
        "--uploaded-dir",
        metavar="FOLDER",
        help="where watch mode moves files (default: FOLDER/uploaded)",
    )
    parser.add_argument(
        "--existing",
        action="store_true",
        help="in watch mode, also upload videos already in the folder",
    )
    parser.add_argument(
        "--stable-seconds",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="watch mode: how long the file size must stay unchanged (default 2)",
    )
    parser.add_argument(
        "--no-clipboard",
        action="store_true",
        help="do not copy the URL to the clipboard",
    )
    parser.add_argument(
        "--log",
        metavar="FILE",
        help="watch-mode log file (default: ~/.config/to-streamable/upload.log)",
    )
    parser.add_argument("--quiet", action="store_true", help="print the URL only")
    parser.add_argument("--version", action="version", version=f"to-streamable {__version__}")
    return parser


def _stderr(quiet, message):
    if not quiet:
        print(message, file=sys.stderr)


def progress_callback(quiet):
    last_pct = {"value": -1}

    def on_progress(sent, total):
        if quiet or not total:
            return
        pct = int(sent * 100 / total)
        if pct == last_pct["value"] and sent < total:
            return
        last_pct["value"] = pct
        width = 28
        filled = int(width * sent / total)
        bar = "#" * filled + "-" * (width - filled)
        sys.stderr.write(
            f"\r[{bar}] {pct:3d}%  {sent / 1048576:.1f}/{total / 1048576:.1f} MB"
        )
        sys.stderr.flush()
        if sent >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return on_progress


def status_callback(quiet):
    def on_status(info):
        if quiet:
            return
        status = info.get("status")
        labels = {0: "receiving", 1: "processing", 2: "ready"}
        label = labels.get(status, f"status {status}")
        percent = info.get("percent")
        extra = f" {percent}%" if percent is not None else ""
        sys.stderr.write(f"\rStreamable: {label}{extra}   ")
        sys.stderr.flush()
        if status == 2:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return on_status


def deliver_url(url, quiet, use_clipboard):
    print(url)
    if not use_clipboard:
        return
    try:
        tool = copy_text(url)
    except ClipboardError as exc:
        print(f"Warning: {exc}", file=sys.stderr)
        return
    _stderr(quiet, f"Copied to the clipboard with {tool}.")


def upload_file(client, path, args):
    path = Path(path)
    if not path.is_file():
        raise StreamableError(f"No such file: {path}")
    warn_if_large(path.stat().st_size)
    _stderr(args.quiet, f"Uploading {path} ...")
    result = client.upload(
        path,
        title=args.title,
        mute=args.mute,
        no_resize=args.no_resize,
        on_progress=progress_callback(args.quiet),
    )
    if args.wait:
        _stderr(args.quiet, "Waiting for Streamable to finish processing ...")
        client.wait_until_ready(
            result["shortcode"],
            timeout=args.wait_timeout,
            on_status=status_callback(args.quiet),
        )
        _stderr(args.quiet, "Processing finished.")
    return result


def setup_watch_log(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("to-streamable")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return logger


def run_watch(args, username, password):
    folder = Path(args.watch).expanduser()
    if not folder.is_dir():
        raise StreamableError(f"Watch folder does not exist: {folder}")
    uploaded = (
        Path(args.uploaded_dir).expanduser()
        if args.uploaded_dir
        else folder / "uploaded"
    )
    logger = setup_watch_log(args.log or log_path())
    client = Client(username, password)
    logger.info("Watching %s", folder)
    if args.delete:
        logger.info("After a successful upload the file is deleted.")
    else:
        logger.info("After a successful upload the file is moved to %s", uploaded)

    def on_stable(path):
        logger.info("Uploading %s", path.name)
        try:
            result = upload_file(client, path, args)
        except (AuthError, ApiChangedError):
            logger.error("Stopping. Credentials or the upload API need attention.")
            raise
        except StreamableError as exc:
            logger.error("Failed %s: %s", path.name, exc)
            return
        deliver_url(result["url"], args.quiet, not args.no_clipboard)
        try:
            if args.delete:
                path.unlink()
                logger.info("Deleted %s after upload", path.name)
            else:
                dest = move_uploaded(path, uploaded)
                logger.info("Moved %s to %s", path.name, dest)
        except OSError as exc:
            logger.error(
                "Uploaded %s but could not tidy up %s: %s",
                result["url"],
                path,
                exc,
            )

    watch_folder(
        folder,
        on_stable,
        include_existing=args.existing,
        stable_seconds=args.stable_seconds,
    )


def run(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.stable_seconds < 0:
        raise StreamableError("--stable-seconds must be >= 0")
    if args.wait_timeout <= 0:
        raise StreamableError("--wait-timeout must be > 0")

    if args.setup:
        username, password = prompt_credentials()
        path = save_credentials(username, password)
        print(f"Saved credentials to {path} (mode 600).")
        print("They are checked on the next upload. --setup does not contact Streamable.")
        return 0

    if args.watch and args.video:
        raise StreamableError("Pass a video file or --watch, not both.")
    if not args.watch and not args.video:
        parser.print_help(sys.stderr)
        raise StreamableError("Pass a video file, or use --watch or --setup.")

    username, password = resolve_credentials(args.auth, prompt=sys.stdin.isatty())

    if args.watch:
        run_watch(args, username, password)
        return 0

    client = Client(username, password)
    result = upload_file(client, args.video, args)
    deliver_url(result["url"], args.quiet, not args.no_clipboard)
    return 0


def main(argv=None):
    try:
        code = run(argv)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        code = 130
    except StreamableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = exc.exit_code
    sys.exit(code)
