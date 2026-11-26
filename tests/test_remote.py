"""remote.py: the network checks, against simulated providers."""
import io
import json
import os
import time
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from unittest import mock

from helpers import Sandbox, load


def remote():
    return load("remote")  # fresh per test, after the sandbox sets the environment


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://example", code, "error", headers or {}, io.BytesIO(b""))


class ClaudeExtra(unittest.TestCase):
    PAYLOAD = {
        "spend": {"used": {"amount_minor": 2726, "currency": "USD", "exponent": 2},
                  "limit": {"amount_minor": 6000, "currency": "USD", "exponent": 2}, "enabled": True},
        "extra_usage": {"is_enabled": True, "monthly_limit": 6000, "used_credits": 2726.0,
                        "decimal_places": 2, "currency": "USD", "spend_limit_reached": False},
    }

    def sign_in(self, expires_ms=None):
        os.makedirs(os.environ["CLAUDE_CONFIG_DIR"], exist_ok=True)
        oauth = {"accessToken": "test-token"}
        if expires_ms is not None:
            oauth["expiresAt"] = expires_ms
        with open(os.path.join(os.environ["CLAUDE_CONFIG_DIR"], ".credentials.json"), "w") as fh:
            json.dump({"claudeAiOauth": oauth}, fh)

    def test_parse_both_shapes(self):
        r = remote()
        self.assertEqual(r.parse_extra(self.PAYLOAD)["used"], 27.26)
        only_extra = {"extra_usage": self.PAYLOAD["extra_usage"]}
        self.assertEqual(r.parse_extra(only_extra)["limit"], 60.0)
        with self.assertRaises(ValueError):
            r.parse_extra({})

    def test_no_sign_in_is_none(self):
        with Sandbox():
            self.assertIsNone(remote().claude_extra("/nonexistent/cache.json"))

    def test_success_is_cached_and_reused(self):
        with Sandbox() as box:
            self.sign_in()
            r = remote()
            cache = os.path.join(box.plugin_state, "claude-extra.json")
            body = io.BytesIO(json.dumps(self.PAYLOAD).encode())
            with mock.patch.object(r.urllib.request, "urlopen", return_value=mock.MagicMock(
                    __enter__=lambda s: body, __exit__=lambda *a: False)) as opener:
                first = r.claude_extra(cache)
                second = r.claude_extra(cache)
            self.assertEqual(first["used"], 27.26)
            self.assertEqual(opener.call_count, 1)  # second answer came from the cache
            self.assertEqual(second["used"], 27.26)

    def test_429_waits_before_asking_again(self):
        with Sandbox() as box:
            self.sign_in()
            r = remote()
            cache = os.path.join(box.plugin_state, "claude-extra.json")
            with mock.patch.object(r.urllib.request, "urlopen", side_effect=http_error(429, {"Retry-After": "120"})) as opener:
                first = r.claude_extra(cache)
                r.save_spend(cache, first)
                r.claude_extra(cache)
            self.assertIn("retrying at", first["error"])
            self.assertGreater(first["retryAt"], time.time() + 100)
            self.assertEqual(opener.call_count, 1)

    def test_waits_a_minute_after_omarchys_own_check(self):
        with Sandbox() as box:
            self.sign_in()
            box.write_record("claude", {"limits": [], "limitsFetchedAt": time.time() * 1000 - 20000})
            r = remote()
            cache = os.path.join(box.plugin_state, "claude-extra.json")
            r.save_spend(cache, {"ok": True, "used": 1, "fetchedAt": time.time() - 900})
            with mock.patch.object(r.urllib.request, "urlopen") as opener:
                result = r.claude_extra(cache)
            opener.assert_not_called()
            self.assertTrue(result.get("_deferred"))

    def test_lapsed_token_is_not_sent(self):
        with Sandbox() as box:
            self.sign_in(expires_ms=1000)
            r = remote()
            with mock.patch.object(r.urllib.request, "urlopen") as opener:
                result = r.claude_extra(os.path.join(box.plugin_state, "claude-extra.json"))
            opener.assert_not_called()
            self.assertIn("lapsed", result["error"])


class Freshness(unittest.TestCase):
    def test_answers_are_due_slightly_early(self):
        r = remote()
        self.assertEqual(r.still_fresh(30), 24)
        self.assertEqual(r.still_fresh(60), 48)
        self.assertEqual(r.still_fresh(300), 280)


class OpenRouter(unittest.TestCase):
    def keys(self, box, **keys):
        with open(os.path.join(box.config, "omarchy", "agents", "openrouter.json"), "w") as fh:
            json.dump(keys, fh)

    def fake(self, down=False, management_refused=False):
        def get(path, key, kind=dict, *, payload=None):
            if down:
                raise urllib.error.URLError("down")
            if path.startswith("/keys"):
                if management_refused:
                    raise http_error(403)
                return [{"usage_daily": 1, "usage_weekly": 2, "usage_monthly": 3},
                        {"usage_daily": 0.5, "usage_weekly": 1, "usage_monthly": 1}] if path.endswith("=0") else []
            if path.startswith("/analytics"):
                raise ValueError("not available")
            if path == "/key":
                return {"usage_daily": 0.83, "usage_weekly": 3.2, "usage_monthly": 6.1,
                        "limit": 20, "limit_remaining": 12, "limit_reset": "monthly"}
            if path == "/credits" and key == "inference":
                return {"total_credits": 50, "total_usage": 37.6}
            raise http_error(403)
        return get

    def test_no_keys_is_none(self):
        with Sandbox() as box:
            self.assertIsNone(remote().openrouter_live(os.path.join(box.plugin_state, "or.json")))

    def test_management_key_sums_every_key(self):
        with Sandbox() as box:
            self.keys(box, apiKey="inference", managementKey="manager")
            r = remote()
            r.openrouter_get = self.fake()
            result = r.openrouter_live(os.path.join(box.plugin_state, "or.json"))
        self.assertEqual((result["today"], result["month"], result["scope"]), (1.5, 4.0, "account"))
        self.assertEqual(result["remaining"], 12.4)
        self.assertFalse(result["stale"])

    def test_refused_management_key_falls_back_to_one_key(self):
        with Sandbox() as box:
            self.keys(box, apiKey="inference", managementKey="manager")
            r = remote()
            r.openrouter_get = self.fake(management_refused=True)
            result = r.openrouter_live(os.path.join(box.plugin_state, "or.json"))
        self.assertEqual((result["today"], result["scope"], result["limit"]), (0.83, "key", 20))
        self.assertTrue(result["stale"])

    def test_slow_keys_answer_keeps_account_figures(self):
        with Sandbox() as box:
            self.keys(box, apiKey="inference", managementKey="manager")
            cache = os.path.join(box.plugin_state, "or.json")
            r = remote()
            r.openrouter_get = self.fake()
            good = r.openrouter_live(cache)
            good["fetchedAt"] -= 600
            r.save_spend(cache, good)
            fast = self.fake()

            def slow_keys(path, key, kind=dict, *, payload=None):
                if path.startswith("/keys"):
                    raise TimeoutError("slow")
                return fast(path, key, kind, payload=payload)
            r.openrouter_get = slow_keys
            result = r.openrouter_live(cache)
        self.assertEqual((result["today"], result["month"], result["scope"]), (1.5, 4.0, "account"))
        self.assertNotIn("limit", result)
        self.assertTrue(result["stale"])

    def test_outage_keeps_last_good_answer(self):
        with Sandbox() as box:
            self.keys(box, apiKey="inference", managementKey="manager")
            cache = os.path.join(box.plugin_state, "or.json")
            r = remote()
            r.openrouter_get = self.fake()
            good = r.openrouter_live(cache)
            good["fetchedAt"] -= 600
            r.save_spend(cache, good)
            r.openrouter_get = self.fake(down=True)
            result = r.openrouter_live(cache)
        self.assertTrue(result["ok"] and result["stale"])
        self.assertEqual(result["remaining"], 12.4)


class Gemini(unittest.TestCase):
    def test_project_totals(self):
        r = remote()

        def series(project, token, metric_filter, start, end, group_by):
            if "request_count" in metric_filter:
                return [({"response_code_class": "2xx"}, 30), ({"response_code_class": "4xx"}, 2)]
            if "output_token_count" in metric_filter:
                return [({"model": "flash"}, 100)]
            if "paid_tier_input" in metric_filter:
                # the same count reported once per limit: counted once
                return [({"model": "flash", "limit_name": "PerMinute"}, 250), ({"model": "flash", "limit_name": "PerUser"}, 250)]
            return []
        r.monitoring_series = series
        result = r.gemini_project("proj-abcdef", "token", datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc))
        self.assertEqual((result["requests"], result["errors"]), (32, 2))
        self.assertEqual(result["models"]["flash"], {"in": 250, "out": 100})

    def test_expired_google_sign_in(self):
        with Sandbox() as box:
            r = remote()
            r.gcloud_token = lambda: (None, "Google sign-in expired: run gcloud auth login")
            result = r.gemini_usage(["proj-abcdef"], os.path.join(box.plugin_state, "gemini.json"))
        self.assertFalse(result["ok"])
        self.assertIn("gcloud auth login", result["error"])


class CommandLine(unittest.TestCase):
    def test_missing_key_is_recorded(self):
        with Sandbox() as box:
            import subprocess, sys
            subprocess.run([sys.executable, "-I", os.path.join(os.path.dirname(os.path.dirname(__file__)), "remote.py"),
                            "openrouter"], check=True, env=dict(os.environ))
            self.assertTrue(box.read_cache("openrouter-live.json")["missing"])


if __name__ == "__main__":
    unittest.main()
