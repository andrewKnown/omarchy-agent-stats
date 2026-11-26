#!/usr/bin/env python3
# Reads the usage records omarchy-agent-usage-update writes and prints a small,
# validated JSON summary for the bar: one entry per agent that has limits or
# a prepaid balance. Every field is optional and bounded, so a change in the
# record format degrades to "no meters" rather than an error.
#
# Prepaid balances (OpenRouter, DeepSeek, Fireworks) only carry an all-time
# "spent" total, so spend today and this month come from snapshots of that
# total kept in $XDG_STATE_HOME/omarchy-agent-stats/spend.json: no network,
# no keys. A snapshot taken mid-day or mid-month is marked with its time.
#
# The network checks (live OpenRouter, Claude extra usage, Gemini API) run in
# remote.py on their own timers and leave their results in cache files; this
# script only reads those, so it never waits on the network. --openrouter-live,
# --claude-extra and --gemini-projects=ID,ID say which caches to show, and the
# --*-age=S options how old a result may get before it's marked stale.
import importlib.util
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone

_spec = importlib.util.spec_from_file_location("agent_stats_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)
text, money = _common.text, _common.money
load_spend, save_spend = _common.load_json, _common.save_json

ORDER = ["claude", "codex", "grok"]
MAX_AGENTS = 8
MAX_LIMITS = 4


def read_balance(record):
    raw = record.get("balance")
    if not isinstance(raw, dict):
        return None
    remaining, funded, spent = money(raw.get("remaining")), money(raw.get("funded")), money(raw.get("spent"))
    if remaining is None or spent is None:
        return None
    currency = text(raw.get("currency") or "USD", 3).upper()
    return {"remaining": remaining, "funded": funded, "spent": spent,
            "currency": currency if currency.isalpha() else "USD"}


def track_spend(entry, spent, now):
    """Update one agent's snapshots in place; returns (today, month,
    todaySince, monthSince), the *Since being "" for a full period."""
    today, month = now.date().isoformat(), now.strftime("%Y-%m")
    stamp = now.isoformat(timespec="minutes")
    last, last_date = money(entry.get("last")), str(entry.get("lastDate") or "")
    # Spent going down means another account or key: start over.
    if last is not None and spent < last - 0.005:
        entry.clear()
        last, last_date = None, ""
    yesterday = (now.date() - timedelta(days=1)).isoformat()
    if entry.get("day") != today:
        # Carried over from yesterday's last reading, the day is whole;
        # after a longer gap the old reading would hold several days.
        if last is not None and last_date == yesterday:
            entry.update(day=today, dayStart=last, daySince="")
        else:
            entry.update(day=today, dayStart=spent, daySince=stamp)
    if entry.get("month") != month:
        first = date(now.year, now.month, 1)
        prev_month = (first - timedelta(days=1)).strftime("%Y-%m")
        if last is not None and last_date[:7] == prev_month and last_date == yesterday:
            entry.update(month=month, monthStart=last, monthSince="")
        else:
            entry.update(month=month, monthStart=spent, monthSince=stamp)
    entry.update(last=spent, lastDate=today)
    day_start = money(entry.get("dayStart"))
    month_start = money(entry.get("monthStart"))
    return (round(max(0.0, spent - (day_start if day_start is not None else spent)), 4),
            round(max(0.0, spent - (month_start if month_start is not None else spent)), 4),
            text(entry.get("daySince"), 32), text(entry.get("monthSince"), 32))


def age_arg(name, default, floor):
    """A --NAME=SECONDS option from the widget's update frequency."""
    for arg in sys.argv[1:]:
        if arg.startswith("--" + name + "="):
            try:
                return max(floor, min(3600, int(arg.split("=", 1)[1])))
            except ValueError:
                break
    return default


def cached(name, age):
    """A remote.py result from its cache file: None when there is nothing to
    check with (no key or sign-in) or no check has run yet ({"pending": True}
    then). A good answer older than three checks is marked stale."""
    data = load_spend(os.path.join(_common.state_dir(), name))
    if not data:
        return {"ok": False, "pending": True}
    if data.get("missing"):
        return None
    try:
        checked = float(data.get("checkedAt") or data.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        checked = 0
    if data.get("ok") and time.time() - checked > max(3 * age, 900):
        data["stale"] = True
        data["error"] = data.get("error") or "not checked since " + time.strftime("%H:%M", time.localtime(checked))
    return data


def main():
    live_openrouter = "--openrouter-live" in sys.argv[1:]
    want_claude_extra = "--claude-extra" in sys.argv[1:]
    gemini_projects = []
    for arg in sys.argv[1:]:
        if arg.startswith("--gemini-projects="):
            gemini_projects = sorted({p for p in arg.split("=", 1)[1].split(",")
                                      if 6 <= len(p) <= 30 and p.replace("-", "").isalnum() and p[0].isalpha() and p == p.lower()})[:8]
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    spend_path = os.path.join(state, "omarchy-agent-stats", "spend.json")
    spend = load_spend(spend_path)
    spend_changed = False
    now = datetime.now().astimezone()
    usage_dir = os.path.join(state, "omarchy", "agents", "usage")
    openrouter_record = None
    agents = []
    today = []  # (name, tokens) for every agent with tokens today, limits or not
    try:
        names = sorted(n for n in os.listdir(usage_dir) if n.endswith(".json"))
    except OSError:
        names = []
    for name in names:
        path = os.path.join(usage_dir, name)
        try:
            if os.path.getsize(path) > 2_000_000:
                continue
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        agent_id = text(record.get("id") or name[:-5], 24)
        agent_name = text(record.get("name") or agent_id, 32)
        try:
            tokens = int(record.get("todayTotalTokens") or 0)
        except (TypeError, ValueError):
            tokens = 0
        if 0 < tokens < 10**13:
            today.append((agent_name, tokens))
        raw = record.get("limits")
        if not isinstance(raw, list):
            raw = []
        limits = []
        for item in raw[:MAX_LIMITS]:
            if not isinstance(item, dict):
                continue
            try:
                percent = float(item.get("percent"))
            except (TypeError, ValueError):
                continue
            limits.append({
                "label": text(item.get("label")),
                # Records store the share used as a 0-1 fraction.
                "percent": round(max(0.0, min(1.0, percent)) * 100, 1),
                "resetsAt": text(item.get("resetsAt"), 40),
            })
        balance = read_balance(record)
        if not limits and not balance:
            continue
        entry = {
            "id": agent_id,
            "name": agent_name,
            "stale": record.get("limitsStale") is True,
            "limits": limits,
        }
        if agent_id == "openrouter" and live_openrouter:
            openrouter_record = (agent_id, agent_name, record.get("limitsStale") is True, limits, balance)
            continue
        if balance:
            snap = spend.get(agent_id)
            if not isinstance(snap, dict):
                snap = spend[agent_id] = {}
            before = dict(snap)
            today_spend, month_spend, day_since, month_since = track_spend(snap, balance["spent"], now)
            spend_changed = spend_changed or snap != before
            balance.update(today=today_spend, month=month_spend, todaySince=day_since, monthSince=month_since)
            entry["prepaid"] = balance
        agents.append(entry)

    if live_openrouter:
        live = cached("openrouter-live.json", age_arg("openrouter-age", 55, 30))
        oid, oname, ostale, olimits, obalance = openrouter_record or ("openrouter", "OpenRouter", False, [], None)
        if live and live.get("ok"):
            prepaid = {"remaining": live.get("remaining"), "funded": live.get("funded"), "spent": live.get("spent"),
                       "currency": "USD", "today": live.get("today"), "week": live.get("week"),
                       "month": live.get("month"), "todaySince": "", "monthSince": "", "live": True,
                       "scope": text(live.get("scope") or "", 8), "keyCount": live.get("keyCount")}
            tokens = live.get("tokens")
            if isinstance(tokens, dict) and tokens.get("date") == datetime.now(timezone.utc).date().isoformat():
                prepaid["tokens"] = tokens
                stats = live.get("spendStats")
                if isinstance(stats, dict) and stats.get("date") == tokens["date"]:
                    prepaid["spendStats"] = stats
            if prepaid["remaining"] is None and obalance:
                prepaid.update(remaining=obalance["remaining"], funded=obalance["funded"], spent=obalance["spent"])
            if live.get("scope") == "account":
                olimits = []  # one key's cap says little next to the whole account's spend
            elif not olimits and live.get("limit"):
                used = (live["limit"] - live["limitRemaining"]) / live["limit"]
                reset = live.get("limitReset") or ""
                olimits = [{"label": (reset.capitalize() + " key limit") if reset else "Key limit",
                            "percent": round(max(0.0, min(1.0, used)) * 100, 1), "resetsAt": ""}]
            agents.append({"id": oid, "name": oname, "stale": bool(live.get("stale")), "limits": olimits,
                           "prepaid": prepaid, "liveError": text(live.get("error") or "", 80)})
        elif openrouter_record and (olimits or obalance):
            # No key for the live check, or it has never answered: use Omarchy's numbers.
            entry = {"id": oid, "name": oname, "stale": ostale, "limits": olimits,
                     "liveError": "no OpenRouter key found" if live is None
                     else "checking…" if live.get("pending") else text(live.get("error") or "", 80)}
            if obalance:
                snap = spend.get(oid)
                if not isinstance(snap, dict):
                    snap = spend[oid] = {}
                before = dict(snap)
                t, m, ds, ms = track_spend(snap, obalance["spent"], now)
                spend_changed = spend_changed or snap != before
                obalance.update(today=t, month=m, todaySince=ds, monthSince=ms)
                entry["prepaid"] = obalance
            agents.append(entry)

    if want_claude_extra:
        extra = cached("claude-extra.json", age_arg("claude-age", 300, 300))
        if extra is not None and extra.get("pending"):
            extra = None
        for agent in agents:
            if agent["id"] == "claude" and extra is not None:
                if extra.get("ok"):
                    agent["extra"] = {k: extra.get(k) for k in ("enabled", "used", "limit", "currency", "reached", "stale")}
                    if extra.get("error"):
                        agent["extra"]["error"] = text(extra["error"], 80)
                else:
                    agent["extra"] = {"error": text(extra.get("error") or "unavailable", 80)}

    if gemini_projects:
        g = cached("gemini.json", age_arg("gemini-age", 300, 60)) or {"ok": False, "error": "gcloud not set up"}
        # A cache for other projects, or from before midnight, isn't today's.
        if g.get("ok") and (g.get("projects") != gemini_projects or g.get("day") != now.date().isoformat()):
            g = {"ok": False, "pending": True}
        if g.get("pending"):
            g["error"] = "checking…"
        entry = {"id": "gemini-api", "name": "Gemini API", "stale": bool(g.get("stale")), "limits": []}
        if g.get("ok"):
            entry["gemini"] = {k: g.get(k) for k in ("requests", "errors", "models", "perProject")}
            entry["gemini"]["tokens"] = sum(c["in"] + c["out"] for c in (g.get("models") or {}).values())
        if g.get("error"):
            entry["liveError"] = text(g["error"], 80)
        agents.append(entry)

    if spend_changed:
        save_spend(spend_path, spend)

    # Subscriptions first (Claude, Codex, Grok, then the rest), prepaid last.
    def rank(agent):
        aid = agent["id"]
        return ("prepaid" in agent, ORDER.index(aid) if aid in ORDER else len(ORDER), aid)

    agents.sort(key=rank)
    today.sort(key=lambda item: -item[1])
    json.dump({
        "agents": agents[:MAX_AGENTS],
        "todayTokens": sum(t for _, t in today),
        "todayByAgent": [{"name": n, "tokens": t} for n, t in today[:MAX_AGENTS]],
    }, sys.stdout)


if __name__ == "__main__":
    main()
