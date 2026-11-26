"""Shared test helpers: load the plugin's scripts by path, in a sandbox."""
import contextlib
import importlib.util
import io
import json
import os
import runpy
import socket
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(name):
    """Import one of the plugin's scripts (read, remote, providers) as a module."""
    spec = importlib.util.spec_from_file_location("agent_stats_" + name, os.path.join(ROOT, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Sandbox:
    """Temporary XDG state and config folders, so nothing real is read or written."""

    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.dir.name, "state")
        self.config = os.path.join(self.dir.name, "config")
        self.usage = os.path.join(self.state, "omarchy", "agents", "usage")
        self.plugin_state = os.path.join(self.state, "omarchy-agent-stats")
        os.makedirs(self.usage)
        os.makedirs(os.path.join(self.config, "omarchy", "agents"))
        self.saved_env = {k: os.environ.get(k) for k in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "CLAUDE_CONFIG_DIR",
                                                         "OPENROUTER_API_KEY", "OPENROUTER_MANAGEMENT_KEY")}
        os.environ["XDG_STATE_HOME"] = self.state
        os.environ["XDG_CONFIG_HOME"] = self.config
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.dir.name, "claude")
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("OPENROUTER_MANAGEMENT_KEY", None)
        return self

    def __exit__(self, *exc):
        for key, value in self.saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.dir.cleanup()

    def write_record(self, agent_id, record):
        record = {"id": agent_id, "name": agent_id.title(), **record}
        with open(os.path.join(self.usage, agent_id + ".json"), "w") as fh:
            json.dump(record, fh)

    def write_cache(self, name, data):
        os.makedirs(self.plugin_state, exist_ok=True)
        with open(os.path.join(self.plugin_state, name), "w") as fh:
            json.dump(data, fh)

    def read_cache(self, name):
        with open(os.path.join(self.plugin_state, name)) as fh:
            return json.load(fh)


@contextlib.contextmanager
def no_network():
    """Make every outgoing connection fail."""
    def refuse(*args, **kwargs):
        raise OSError("network blocked in tests")
    saved = socket.socket.connect, socket.create_connection
    socket.socket.connect = refuse
    socket.create_connection = refuse
    try:
        yield
    finally:
        socket.socket.connect, socket.create_connection = saved


def run_read(*args):
    """Run read.py's main with these arguments; returns its JSON output."""
    saved = sys.argv
    sys.argv = ["read.py", *args]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_path(os.path.join(ROOT, "read.py"), run_name="__main__")
    finally:
        sys.argv = saved
    return json.loads(buf.getvalue())
