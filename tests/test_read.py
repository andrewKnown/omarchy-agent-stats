"""read.py: Omarchy's records and the cached network results into bar data."""
import time
import unittest
from datetime import datetime

from helpers import Sandbox, load, no_network, run_read

read = load("read")


class TrackSpend(unittest.TestCase):
    """Spend today and this month from snapshots of an all-time total."""

    def test_first_sight_is_partial(self):
        e = {}
        self.assertEqual(read.track_spend(e, 10.0, datetime(2026, 11, 29, 9)),
                         (0.0, 0.0, "2026-11-29T09:00", "2026-11-29T09:00"))

    def test_same_day_accumulates(self):
        e = {}
        read.track_spend(e, 10.0, datetime(2026, 11, 29, 9))
        self.assertEqual(read.track_spend(e, 11.5, datetime(2026, 11, 29, 22))[:2], (1.5, 1.5))

    def test_next_day_carries_over_whole(self):
        e = {}
        read.track_spend(e, 10.0, datetime(2026, 11, 29, 9))
        read.track_spend(e, 11.5, datetime(2026, 11, 29, 22))
        self.assertEqual(read.track_spend(e, 12.0, datetime(2026, 11, 30, 8)), (0.5, 2.0, "", "2026-11-29T09:00"))

    def test_new_month_carries_over(self):
        e = {}
        read.track_spend(e, 10.0, datetime(2026, 11, 30, 9))
        self.assertEqual(read.track_spend(e, 13.0, datetime(2026, 12, 1, 8)), (3.0, 3.0, "", ""))

    def test_gap_of_days_restarts_today(self):
        e = {}
        read.track_spend(e, 10.0, datetime(2026, 12, 1, 9))
        today, month, since, _ = read.track_spend(e, 14.0, datetime(2026, 12, 5, 8))
        self.assertEqual((today, month, since), (0.0, 4.0, "2026-12-05T08:00"))

    def test_spent_going_down_starts_over(self):
        e = {}
        read.track_spend(e, 10.0, datetime(2026, 12, 1, 9))
        self.assertEqual(read.track_spend(e, 1.0, datetime(2026, 12, 1, 10))[:2], (0.0, 0.0))


class Records(unittest.TestCase):
    def test_limits_become_percent_and_order(self):
        with Sandbox() as box:
            box.write_record("codex", {"limits": [{"label": "5h", "percent": 0.28}]})
            box.write_record("claude", {"limits": [{"label": "Session", "percent": 0.6}, {"label": "Weekly", "percent": 1.4}]})
            box.write_record("kimi", {"limits": []})
            out = run_read()
        self.assertEqual([a["id"] for a in out["agents"]], ["claude", "codex"])
        self.assertEqual([l["percent"] for l in out["agents"][0]["limits"]], [60.0, 100.0])

    def test_alphabetical_by_name(self):
        with Sandbox() as box:
            for agent_id, name in (("openrouter", "OpenRouter"), ("claude", "Claude Code"), ("agy", "Antigravity"),
                                   ("grokbot", "Grok Bot"), ("grok", "Grok"), ("codex", "Codex")):
                box.write_record(agent_id, {"name": name, "limits": [{"label": "W", "percent": 0.1}],
                                            "todayTotalTokens": 5})
            out = run_read()
        self.assertEqual([a["name"] for a in out["agents"]],
                         ["Antigravity", "Claude Code", "Codex", "Grok", "Grok Bot", "OpenRouter"])
        self.assertEqual([t["name"] for t in out["todayByAgent"]],
                         ["Antigravity", "Claude Code", "Codex", "Grok", "Grok Bot", "OpenRouter"])

    def test_broken_records_are_skipped(self):
        with Sandbox() as box:
            with open(box.usage + "/bad.json", "w") as fh:
                fh.write("{not json")
            box.write_record("claude", {"limits": "nonsense"})
            self.assertEqual(run_read()["agents"], [])

    def test_prepaid_balance_and_tokens(self):
        with Sandbox() as box:
            box.write_record("openrouter", {"limits": [], "todayTotalTokens": 1200,
                                            "balance": {"remaining": 3.2, "funded": 50, "spent": 46.8, "currency": "USD"}})
            out = run_read()
        agent = out["agents"][0]
        self.assertEqual(agent["prepaid"]["remaining"], 3.2)
        self.assertEqual(out["todayTokens"], 1200)


class Caches(unittest.TestCase):
    """read.py shows what remote.py left behind, and never goes online."""

    def test_reads_caches_with_network_blocked(self):
        with Sandbox() as box:
            box.write_record("claude", {"limits": [{"label": "Weekly", "percent": 1.0}]})
            box.write_cache("claude-extra.json", {"ok": True, "enabled": True, "used": 27.26, "limit": 60.0,
                                                  "currency": "USD", "checkedAt": time.time()})
            box.write_cache("gemini.json", {"ok": True, "projects": ["proj-abcdef"], "day": datetime.now().date().isoformat(),
                                            "requests": 5, "errors": 0, "models": {"m": {"in": 10, "out": 20}},
                                            "perProject": [], "checkedAt": time.time()})
            with no_network():
                out = run_read("--claude-extra", "--gemini-projects=proj-abcdef")
        by_id = {a["id"]: a for a in out["agents"]}
        self.assertEqual(by_id["claude"]["extra"]["used"], 27.26)
        self.assertEqual(by_id["gemini-api"]["gemini"]["tokens"], 30)

    def test_old_result_is_stale(self):
        with Sandbox() as box:
            box.write_record("claude", {"limits": [{"label": "Weekly", "percent": 1.0}]})
            box.write_cache("claude-extra.json", {"ok": True, "enabled": True, "used": 1, "limit": 60,
                                                  "currency": "USD", "checkedAt": time.time() - 7200})
            out = run_read("--claude-extra")
        self.assertTrue(out["agents"][0]["extra"]["stale"])

    def test_yesterdays_gemini_is_not_shown(self):
        with Sandbox() as box:
            box.write_cache("gemini.json", {"ok": True, "projects": ["proj-abcdef"], "day": "2000-01-01",
                                            "requests": 5, "models": {}, "checkedAt": time.time()})
            out = run_read("--gemini-projects=proj-abcdef")
        gemini = out["agents"][0]
        self.assertNotIn("gemini", gemini)
        self.assertEqual(gemini["liveError"], "checking…")

    def test_openrouter_without_key_falls_back_to_omarchy(self):
        with Sandbox() as box:
            box.write_record("openrouter", {"limits": [], "balance": {"remaining": 3.2, "funded": 50, "spent": 46.8}})
            box.write_cache("openrouter-live.json", {"ok": False, "missing": True, "checkedAt": time.time()})
            out = run_read("--openrouter-live")
        self.assertEqual(out["agents"][0]["liveError"], "no OpenRouter key found")
        self.assertEqual(out["agents"][0]["prepaid"]["remaining"], 3.2)


if __name__ == "__main__":
    unittest.main()
