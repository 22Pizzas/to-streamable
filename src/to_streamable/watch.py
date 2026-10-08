"""Watch a folder and upload videos once they have finished writing.

watchdog is optional. Without it, the folder is polled. Either way a file
is uploaded only after its size stays the same for `stable_seconds`.

A file is handled once per (size, mtime). Replacing it, or moving it out
and back in, changes that signature and the new bytes are uploaded.
"""

import logging
import queue
import shutil
import time
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".m4v",
    ".mpg",
    ".mpeg",
    ".wmv",
    ".flv",
    ".ts",
    ".m2ts",
    ".mts",
    ".3gp",
    ".ogv",
}

# Browser and downloader leftovers. These are not finished videos.
PARTIAL_SUFFIXES = (
    ".part",
    ".partial",
    ".crdownload",
    ".tmp",
    ".temp",
    ".download",
)


def is_video_file(path):
    path = Path(path)
    name = path.name
    if not name or name.startswith("."):
        return False
    lower = name.lower()
    if lower.endswith(PARTIAL_SUFFIXES):
        return False
    return path.suffix.lower() in VIDEO_EXTENSIONS


def file_signature(path):
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def is_stable(path, previous, now, stable_seconds):
    """Return (stable, signature_state).

    `previous` is `(signature, since)` or None. The timer resets when the
    size or mtime changes, and when the file is still empty.
    """
    try:
        signature = file_signature(path)
    except OSError:
        return False, None
    if signature[0] <= 0:
        return False, None
    if previous and previous[0] == signature and (now - previous[1]) >= stable_seconds:
        return True, previous
    if previous and previous[0] == signature:
        return False, previous
    return False, (signature, now)


def scan_videos(folder):
    folder = Path(folder)
    found = []
    try:
        entries = list(folder.iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if is_file and is_video_file(entry):
            found.append(entry)
    return found


def snapshot_signatures(folder):
    """Map resolved path -> signature for videos currently in the folder."""
    found = {}
    for path in scan_videos(folder):
        try:
            resolved = path.resolve()
            found[str(resolved)] = file_signature(resolved)
        except OSError:
            continue
    return found


def destination_for(name, directory):
    directory = Path(directory)
    candidate = directory / name
    if not candidate.exists():
        return candidate
    stamp = time.strftime("%Y%m%d-%H%M%S")
    stem = Path(name).stem
    suffix = Path(name).suffix
    candidate = directory / f"{stem}-{stamp}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{stamp}-{counter}{suffix}"
        counter += 1
    return candidate


def move_uploaded(path, uploaded_dir):
    uploaded_dir = Path(uploaded_dir)
    uploaded_dir.mkdir(parents=True, exist_ok=True)
    dest = destination_for(Path(path).name, uploaded_dir)
    shutil.move(str(path), str(dest))
    return dest


def process_cycle(folder, pending, handled, now, stable_seconds, on_stable):
    """One pass over the folder. Upload each video whose size has settled.

    `handled` maps a resolved path to the signature last given to
    `on_stable`. The same bytes are not uploaded again.
    """
    folder = Path(folder)
    for path in scan_videos(folder):
        try:
            resolved = path.resolve()
            signature = file_signature(resolved)
        except OSError:
            continue
        if resolved.parent != folder.resolve():
            continue
        key = str(resolved)
        if handled.get(key) == signature:
            pending.pop(key, None)
            continue
        pending.setdefault(key, None)

    ready = []
    for key, previous in list(pending.items()):
        path = Path(key)
        if not path.is_file() or not is_video_file(path):
            pending.pop(key, None)
            continue
        try:
            if path.resolve().parent != folder.resolve():
                pending.pop(key, None)
                continue
            signature = file_signature(path)
        except OSError:
            pending.pop(key, None)
            continue
        if handled.get(key) == signature:
            pending.pop(key, None)
            continue
        stable, state = is_stable(path, previous, now, stable_seconds)
        if stable:
            ready.append((path, signature))
            pending.pop(key, None)
        else:
            pending[key] = state

    for path, signature in ready:
        on_stable(path)
        key = str(path)
        try:
            if path.is_file():
                handled[key] = file_signature(path)
            else:
                handled[key] = signature
        except OSError:
            handled[key] = signature
    return pending, handled


class _ChangeWaiter:
    """Sleep until the folder changes, or until timeout. Polls if watchdog is absent."""

    def __init__(self, folder):
        self.folder = str(folder)
        self._observer = None
        self._events = None
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            logging.getLogger("to-streamable").info(
                "watchdog is not installed; polling the folder. "
                "Install the watch extra for inotify: pip install 'to-streamable[watch]'"
            )
            return

        events = queue.Queue()

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                events.put(event.src_path)

        observer = Observer()
        observer.schedule(Handler(), self.folder, recursive=False)
        observer.start()
        self._observer = observer
        self._events = events

    def wait(self, timeout):
        if self._events is None:
            time.sleep(timeout)
            return
        try:
            self._events.get(timeout=timeout)
        except queue.Empty:
            return
        while True:
            try:
                self._events.get_nowait()
            except queue.Empty:
                return

    def close(self):
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=5)


def watch_folder(
    folder,
    on_stable,
    include_existing=False,
    stable_seconds=2.0,
    poll_interval=1.0,
    monotonic=time.monotonic,
    sleeper=None,
):
    """Call on_stable(path) once a video's size stops changing.

    Files present at startup are skipped unless include_existing is set.
    `sleeper` replaces the waiter in tests: it is called with the poll
    interval and may raise to stop the loop.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"Watch folder does not exist: {folder}")

    pending = {}
    handled = {} if include_existing else snapshot_signatures(folder)
    waiter = None if sleeper else _ChangeWaiter(folder)

    def pause(seconds):
        if sleeper:
            sleeper(seconds)
        else:
            waiter.wait(seconds)

    try:
        while True:
            process_cycle(
                folder,
                pending,
                handled,
                monotonic(),
                stable_seconds,
                on_stable,
            )
            pause(poll_interval)
    finally:
        if waiter is not None:
            waiter.close()
