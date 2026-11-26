"""providers.py (keys and settings) and launch.sh (what a click opens)."""
import json
import os
import stat
import subprocess
import tempfile
import unittest

from helpers import ROOT, Sandbox, load


class Keys(unittest.TestCase):
    def test_saved_owner_only_and_other_fields_kept(self):
        with Sandbox() as box:
            p = load("providers")
            path = os.path.join(box.config, "omarchy", "agents", "zai.json")
            with open(path, "w") as fh:
                json.dump({"other": 1}, fh)
            p.save_field("zai", "apiKey", "testkey-1234567890abcd")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            with open(path) as fh:
                self.assertEqual(json.load(fh), {"other": 1, "apiKey": "testkey-1234567890abcd"})
            self.assertEqual(p.key_source("zai", "apiKey", "ZAI_API_KEY"), "saved (testke…abcd)")
            p.save_field("zai", "apiKey", "")
            with open(path) as fh:
                self.assertEqual(json.load(fh), {"other": 1})

    def test_key_validation(self):
        p = load("providers")
        self.assertFalse(p.valid_key("sk-or-v1 abc"))
        self.assertFalse(p.valid_key("short"))
        self.assertTrue(p.valid_key("sk-or-v1-abcdefgh"))


class DisplaySettings(unittest.TestCase):
    def test_every_choice_matches_the_manifest(self):
        p = load("providers")
        with open(os.path.join(ROOT, "manifest.json")) as fh:
            schema = {f["key"]: f for f in json.load(fh)["barWidget"]["schema"]}
        for key, _, choices in p.DISPLAY:
            self.assertIn(key, schema, key)
            field = schema[key]
            for _, value in choices:
                if field["type"] == "enum":
                    self.assertIn(value, field["options"], key)
                elif field["type"] == "boolean":
                    self.assertIsInstance(value, bool, key)
                elif field["type"] == "integer":
                    self.assertTrue(field.get("min", 0) <= value <= field.get("max", 10**9), key)

    def test_saves_through_omarchy_bar(self):
        with tempfile.TemporaryDirectory() as bin_dir:
            log = os.path.join(bin_dir, "calls")
            with open(os.path.join(bin_dir, "omarchy-bar"), "w") as fh:
                fh.write('#!/bin/bash\necho "$*" >> %s\n' % log)
            os.chmod(os.path.join(bin_dir, "omarchy-bar"), 0o755)
            old_path = os.environ["PATH"]
            os.environ["PATH"] = bin_dir + ":" + old_path
            try:
                p = load("providers")
                p.save_widget_setting("lowBalanceAt", 5, as_json=True)
                p.save_widget_setting("prepaidText", "Today")
            finally:
                os.environ["PATH"] = old_path
            with open(log) as fh:
                self.assertEqual(fh.read().splitlines(), [
                    "set io.github.andrewknown.agent-stats lowBalanceAt 5 --json",
                    "set io.github.andrewknown.agent-stats prepaidText Today"])


class Launch(unittest.TestCase):
    """launch.sh with stand-ins for every command it could start."""

    def run_launch(self, *args, cursor_auth=None):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = os.path.join(tmp, "bin")
            os.makedirs(bin_dir)
            for name in ("omarchy-launch-tui", "omarchy-agent", "xdg-open", "omarchy-shell", "cursor-agent", "claude"):
                with open(os.path.join(bin_dir, name), "w") as fh:
                    fh.write('#!/bin/bash\necho "[%s] $*"\n' % name)
                os.chmod(os.path.join(bin_dir, name), 0o755)
            with open(os.path.join(bin_dir, "omarchy-default-agent"), "w") as fh:
                fh.write("#!/bin/bash\necho real-default\n")
            os.chmod(os.path.join(bin_dir, "omarchy-default-agent"), 0o755)
            os.makedirs(os.path.join(tmp, "cfg", "cursor"))
            if cursor_auth is not None:
                with open(os.path.join(tmp, "cfg", "cursor", "auth.json"), "w") as fh:
                    json.dump(cursor_auth, fh)
            env = {"PATH": bin_dir + ":/usr/bin:/bin", "HOME": tmp, "XDG_CONFIG_HOME": os.path.join(tmp, "cfg"),
                   "XDG_RUNTIME_DIR": tmp}
            out = subprocess.run(["bash", os.path.join(ROOT, "launch.sh"), *args], env=env, cwd=tmp,
                                 capture_output=True, text=True, timeout=20)
            return out.stdout.strip()

    def test_agent_goes_through_omarchy(self):
        self.assertEqual(self.run_launch("claude"), "[omarchy-agent]")

    def test_cursor_with_file_sign_in_hides_the_display(self):
        out = self.run_launch("cursor-agent", cursor_auth={"accessToken": "x", "refreshToken": "y"})
        self.assertEqual(out, "[omarchy-launch-tui] --app-id=org.omarchy.agent env -u WAYLAND_DISPLAY -u DISPLAY cursor-agent --yolo --trust")

    def test_cursor_with_keyring_sign_in_launches_normally(self):
        self.assertEqual(self.run_launch("cursor-agent", cursor_auth={"storedIn": "os-secret-store"}), "[omarchy-agent]")

    def test_missing_cli_opens_its_page(self):
        self.assertEqual(self.run_launch("notinstalled", "https://openrouter.ai/activity"),
                         "[xdg-open] https://openrouter.ai/activity")

    def test_missing_cli_without_page_opens_agents_panel(self):
        self.assertEqual(self.run_launch("notinstalled", ""), "[omarchy-shell] omarchy.agents toggle")

    def test_unsafe_url_is_refused(self):
        self.assertEqual(self.run_launch("notinstalled", "https://x.com/a;rm"), "[omarchy-shell] omarchy.agents toggle")

    def test_bad_agent_name_does_nothing(self):
        self.assertEqual(self.run_launch("../etc"), "")


if __name__ == "__main__":
    unittest.main()
