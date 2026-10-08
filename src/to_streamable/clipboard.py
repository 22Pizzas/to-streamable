"""Copy a URL to the clipboard on Kubuntu (Wayland or X11).

Order: wl-copy when WAYLAND_DISPLAY is set, then xclip, then xsel,
then pyperclip if that package is installed. Failure is not fatal.
"""

import os
import shutil
import subprocess


class ClipboardError(Exception):
    pass


def _run(argv, text):
    subprocess.run(
        argv,
        input=text.encode("utf-8"),
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def copy_text(text):
    """Copy text. Return the tool name used, or raise ClipboardError."""
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        _run(["wl-copy"], text)
        return "wl-copy"
    if os.environ.get("DISPLAY") and shutil.which("xclip"):
        _run(["xclip", "-selection", "clipboard"], text)
        return "xclip"
    if shutil.which("xclip"):
        _run(["xclip", "-selection", "clipboard"], text)
        return "xclip"
    if shutil.which("xsel"):
        _run(["xsel", "--clipboard", "--input"], text)
        return "xsel"
    try:
        import pyperclip
    except ImportError:
        pyperclip = None
    if pyperclip is not None:
        pyperclip.copy(text)
        return "pyperclip"
    raise ClipboardError(
        "Could not copy to the clipboard. Install xclip "
        "(sudo apt install xclip) or, on Wayland, wl-clipboard "
        "(sudo apt install wl-clipboard)."
    )
