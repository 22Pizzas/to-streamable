"""Credentials in ~/.config/to-streamable/config, mode 600.

Resolution order:
  1. --auth user:pass
  2. STREAMABLE_USER and STREAMABLE_PASS
  3. the config file
  4. an interactive prompt, when stdin is a terminal
"""

import json
import os
import stat
from getpass import getpass
from pathlib import Path

from to_streamable.errors import AuthError, UsageError

CONFIG_NAME = "config"
ENV_USER = "STREAMABLE_USER"
ENV_PASS = "STREAMABLE_PASS"


def config_dir():
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "to-streamable"


def config_path():
    return config_dir() / CONFIG_NAME


def log_path():
    return config_dir() / "upload.log"


def parse_auth_pair(value):
    """Split user:pass on the first colon. Passwords may contain colons."""
    if not value or ":" not in value:
        raise UsageError("--auth must look like user:pass")
    username, password = value.split(":", 1)
    if not username or not password:
        raise UsageError("--auth must look like user:pass")
    return username, password


def load_saved(path=None):
    path = Path(path) if path else config_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthError(f"Could not read {path}: {exc}") from exc
    username = data.get("username") or ""
    password = data.get("password") or ""
    if not username or not password:
        raise AuthError(f"{path} is missing username or password. Run --setup.")
    return username, password


def save_credentials(username, password, path=None):
    """Write credentials atomically and force mode 600 on the file."""
    path = Path(path) if path else config_path()
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    payload = json.dumps(
        {"username": username, "password": password},
        indent=2,
    ) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise AuthError(f"Refusing to leave {path} readable by others (mode {mode:o})")
    return path


def credentials_from_env():
    username = os.environ.get(ENV_USER)
    password = os.environ.get(ENV_PASS)
    if username and password:
        return username, password
    if username or password:
        raise AuthError(
            f"Set both {ENV_USER} and {ENV_PASS}, or neither. "
            "One of them is set without the other."
        )
    return None


def prompt_credentials():
    print("Streamable credentials (saved to the config file, mode 600).")
    username = input("Username: ").strip()
    password = getpass("Password: ")
    if not username or not password:
        raise AuthError("Username and password are both required.")
    return username, password


def resolve_credentials(auth_pair=None, path=None, prompt=False):
    """Return (username, password) using the precedence documented above."""
    if auth_pair:
        return parse_auth_pair(auth_pair)
    from_env = credentials_from_env()
    if from_env:
        return from_env
    saved = load_saved(path)
    if saved:
        return saved
    if prompt:
        username, password = prompt_credentials()
        save_credentials(username, password, path)
        return username, password
    raise AuthError(
        "No Streamable credentials. Run `to-streamable --setup`, "
        f"pass --auth user:pass, or set {ENV_USER} and {ENV_PASS}."
    )
