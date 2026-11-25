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
# With --openrouter-live (the widget's "Query OpenRouter directly" setting)
# OpenRouter is asked itself, at most once a minute: GET /api/v1/key for the
# key's spend today, this week and this month (UTC periods) and its limit, and
# GET /api/v1/credits for the balance. The key is read from OPENROUTER_API_KEY
# or Omarchy's own ~/.config/omarchy/agents/openrouter.json (apiKey, and
# managementKey for the balance), and is only ever sent to openrouter.ai.
# A failed check keeps the last good answer, marked stale.
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta

ORDER = ["claude", "codex", "grok"]
MAX_AGENTS = 8
MAX_LIMITS = 4


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


def load_spend(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_spend(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".spend.")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass


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


OPENROUTER_API = "https://openrouter.ai/api/v1"
OPENROUTER_MIN_AGE = 55  # seconds between live checks


def openrouter_keys():
    api, manager = os.environ.get("OPENROUTER_API_KEY", ""), os.environ.get("OPENROUTER_MANAGEMENT_KEY", "")
    config = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    try:
        with open(os.path.join(config, "omarchy", "agents", "openrouter.json"), encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            api = api or str(data.get("apiKey") or "")
            manager = manager or str(data.get("managementKey") or "")
    except (OSError, ValueError):
        pass
    clean = lambda k: k.strip() if 0 < len(k.strip()) < 512 and k.strip().isprintable() and " " not in k.strip() else ""
    return clean(api), clean(manager)


def openrouter_get(path, key):
    request = urllib.request.Request(OPENROUTER_API + path, headers={
        "Authorization": "Bearer " + key, "Accept": "application/json", "User-Agent": "omarchy-agent-stats"})
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(200_000)
    data = json.loads(body).get("data")
    if not isinstance(data, dict):
        raise ValueError("no data")
    return data


def openrouter_live(cache_path):
    """Live OpenRouter numbers, or None when there's no key. Reuses an answer
    younger than OPENROUTER_MIN_AGE; a failed check keeps the last good one."""
    api, manager = openrouter_keys()
    if not api and not manager:
        return None
    cached = load_spend(cache_path)
    try:
        fetched = float(cached.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        fetched = 0
    if cached.get("ok") and time.time() - fetched < OPENROUTER_MIN_AGE:
        return cached
    result, errors = {}, []
    if api:
        try:
            key = openrouter_get("/key", api)
            for field, name in (("usage_daily", "today"), ("usage_weekly", "week"), ("usage_monthly", "month")):
                value = money(key.get(field))
                if value is not None:
                    result[name] = round(value, 4)
            limit, remaining = money(key.get("limit")), money(key.get("limit_remaining"))
            if limit and remaining is not None:
                result["limit"], result["limitRemaining"] = limit, min(remaining, limit)
                result["limitReset"] = text(key.get("limit_reset") or "", 12)
        except (OSError, ValueError, urllib.error.URLError) as error:
            errors.append("key: " + type(error).__name__)
    for credits_key in [k for k in (manager, api) if k]:
        try:
            credits = openrouter_get("/credits", credits_key)
            total, used = money(credits.get("total_credits")), money(credits.get("total_usage"))
            if total is not None and used is not None:
                result.update(remaining=round(max(0.0, total - used), 4), funded=round(total, 4), spent=round(used, 4))
            break
        except (OSError, ValueError, urllib.error.URLError) as error:
            refused = isinstance(error, urllib.error.HTTPError) and error.code in (401, 403)
            # An inference key may be refused here; that's no balance, not a fault.
            if not (refused and credits_key == api):
                errors.append("credits: " + type(error).__name__)
            if not refused:
                break
    if not result:
        stale = dict(cached) if cached.get("ok") else {}
        stale.update(ok=bool(stale), stale=True, error="; ".join(errors)[:80])
        return stale if stale.get("ok") else {"ok": False, "error": "; ".join(errors)[:80]}
    result.update(ok=True, stale=bool(errors), fetchedAt=time.time())
    save_spend(cache_path, result)
    return result


def main():
    live_openrouter = "--openrouter-live" in sys.argv[1:]
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
        live = openrouter_live(os.path.join(state, "omarchy-agent-stats", "openrouter-live.json"))
        oid, oname, ostale, olimits, obalance = openrouter_record or ("openrouter", "OpenRouter", False, [], None)
        if live and live.get("ok"):
            prepaid = {"remaining": live.get("remaining"), "funded": live.get("funded"), "spent": live.get("spent"),
                       "currency": "USD", "today": live.get("today"), "week": live.get("week"),
                       "month": live.get("month"), "todaySince": "", "monthSince": "", "live": True}
            if prepaid["remaining"] is None and obalance:
                prepaid.update(remaining=obalance["remaining"], funded=obalance["funded"], spent=obalance["spent"])
            if not olimits and live.get("limit"):
                used = (live["limit"] - live["limitRemaining"]) / live["limit"]
                reset = live.get("limitReset") or ""
                olimits = [{"label": (reset.capitalize() + " key limit") if reset else "Key limit",
                            "percent": round(max(0.0, min(1.0, used)) * 100, 1), "resetsAt": ""}]
            agents.append({"id": oid, "name": oname, "stale": bool(live.get("stale")), "limits": olimits,
                           "prepaid": prepaid, "liveError": text(live.get("error") or "", 80)})
        elif openrouter_record and (olimits or obalance):
            # No key for the live check, or it has never answered: use Omarchy's numbers.
            entry = {"id": oid, "name": oname, "stale": ostale, "limits": olimits,
                     "liveError": "no OpenRouter key found" if live is None else text(live.get("error") or "", 80)}
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
