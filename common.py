"""Small helpers shared by read.py, remote.py and providers.py."""
import json
import math
import os
import tempfile


def text(value, limit=48):
    return str(value)[:limit] if isinstance(value, (str, int, float)) else ""


def money(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and 0 <= parsed < 10**9 else None


def stamp_seconds(value):
    """A Unix time in seconds or milliseconds as seconds, or 0."""
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(number) or number <= 0:
        return 0.0
    return number / 1000 if number > 10**11 else number


def state_dir():
    """$XDG_STATE_HOME/omarchy-agent-stats, where this plugin keeps its files."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "omarchy-agent-stats")


def load_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_json(path, data):
    """Write atomically, owner-only."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass
