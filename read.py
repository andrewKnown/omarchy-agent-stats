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
#
# With --claude-extra (the "Claude extra usage" setting) Anthropic's usage
# endpoint, the one Claude Code's /usage and Omarchy's collector read, is asked
# at most every five minutes for the account's extra usage (pay-as-you-go spend
# past the plan's limits) this month. It uses the access token Claude Code keeps
# in $CLAUDE_CONFIG_DIR/.credentials.json (default ~/.claude), sends it only to
# api.anthropic.com, never refreshes or stores it, and skips the check while it
# has lapsed (Claude Code renews it when it next runs).
import json
import math
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

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


def openrouter_get(path, key, kind=dict, *, payload=None):
    request = urllib.request.Request(OPENROUTER_API + path, headers={
        "Accept": "application/json", "Content-Type": "application/json",
        "User-Agent": "omarchy-agent-stats"},
        data=json.dumps(payload).encode() if payload is not None else None)
    request.add_unredirected_header("Authorization", "Bearer " + key)
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(2_000_000)
    response_data = json.loads(body)
    data = response_data.get("data") if isinstance(response_data, dict) else None
    if not isinstance(data, kind):
        raise ValueError("no data")
    return data


SPEND_FIELDS = (("usage_daily", "today"), ("usage_weekly", "week"), ("usage_monthly", "month"))


def account_analytics(manager, now):
    today = now.date()
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    start = month
    for _ in range(3):
        start = (start - timedelta(days=1)).replace(day=1)
    response = openrouter_get("/analytics/query", manager, payload={
        "metrics": ["tokens_total", "total_usage"],
        "granularity": "day",
        "time_range": {
            "start": start.isoformat() + "T00:00:00Z",
            "end": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    })
    rows, metadata = response.get("data"), response.get("metadata")
    if not isinstance(rows, list) or not isinstance(metadata, dict) or metadata.get("truncated") is not False:
        raise ValueError("incomplete token results")
    daily = {}
    costs = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid token row")
        stamp, count = row.get("date__day"), row.get("tokens_total")
        if not isinstance(stamp, str) or type(count) is not int or not 0 <= count < 10**13:
            raise ValueError("invalid token row")
        day = datetime.fromisoformat(stamp).date()
        if not start <= day <= today or day in daily:
            raise ValueError("unexpected token bucket")
        daily[day] = count
        costs[day] = money(row.get("total_usage"))
    tokens = {
        "date": today.isoformat(),
        "today": daily.get(today, 0),
        "week": sum(count for day, count in daily.items() if day >= week),
        "month": sum(count for day, count in daily.items() if day >= month),
    }
    spend = None
    if all(value is not None for value in costs.values()):
        month_spend = sum(value for day, value in costs.items() if day >= month)
        completed_days = (today - month).days
        spend = {
            "date": today.isoformat(),
            "today": costs.get(today, 0),
            "month": month_spend,
            "dailyAverage": (month_spend - costs.get(today, 0)) / completed_days if completed_days else None,
            "monthlyAverage": sum(value for day, value in costs.items() if day < month) / 3,
        }
    return tokens, spend


def account_spend(manager):
    """Spend today, this week and this month summed over every key on the
    account, from the management-key-only GET /keys (paged)."""
    totals, seen = {name: 0.0 for _, name in SPEND_FIELDS}, 0
    for page in range(20):
        keys = openrouter_get("/keys?offset=%d" % seen, manager, list)
        if not keys:
            break
        for key in keys:
            if isinstance(key, dict):
                for field, name in SPEND_FIELDS:
                    totals[name] += money(key.get(field)) or 0.0
        seen += len(keys)
    return {name: round(value, 4) for name, value in totals.items()}, seen


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
    if cached.get("ok") and time.time() - fetched < age_arg("openrouter-age", OPENROUTER_MIN_AGE, 30):
        return cached
    result, errors = {}, []
    # With a management key, spend is the whole account's: every key summed.
    if manager:
        try:
            spend, count = account_spend(manager)
            result.update(spend, scope="account", keyCount=count)
        except (OSError, ValueError, urllib.error.URLError) as error:
            refused = isinstance(error, urllib.error.HTTPError) and error.code in (401, 403)
            errors.append("keys: " + ("refused" if refused else type(error).__name__))
        try:
            tokens, spend_stats = account_analytics(manager, datetime.now(timezone.utc))
            result["tokens"] = tokens
            if spend_stats is not None:
                result["spendStats"] = spend_stats
        except (OSError, ValueError):
            # Token analytics permissions and availability are independent of spend.
            pass
    # Otherwise, or if that failed, the one key's own spend and limit.
    if api and "scope" not in result:
        try:
            key = openrouter_get("/key", api)
            result["scope"] = "key"
            for field, name in SPEND_FIELDS:
                value = money(key.get(field))
                if value is not None:
                    result[name] = round(value, 4)
            limit, remaining = money(key.get("limit")), money(key.get("limit_remaining"))
            if limit and remaining is not None:
                result["limit"], result["limitRemaining"] = limit, min(remaining, limit)
                result["limitReset"] = text(key.get("limit_reset") or "", 12)
        except (OSError, ValueError, urllib.error.URLError) as error:
            errors.append("key: " + type(error).__name__)
    # Either key may be the one OpenRouter answers /credits for; a refusal
    # only matters when neither gets the balance.
    credit_errors = []
    for credits_key in list(dict.fromkeys(k for k in (api, manager) if k)):
        try:
            credits = openrouter_get("/credits", credits_key)
            total, used = money(credits.get("total_credits")), money(credits.get("total_usage"))
            if total is not None and used is not None:
                result.update(remaining=round(max(0.0, total - used), 4), funded=round(total, 4), spent=round(used, 4))
            credit_errors = []
            break
        except (OSError, ValueError, urllib.error.URLError) as error:
            refused = isinstance(error, urllib.error.HTTPError) and error.code in (401, 403)
            credit_errors.append("credits: " + ("refused" if refused else type(error).__name__))
            if not refused:
                break
    errors.extend(credit_errors)
    if not result:
        stale = dict(cached) if cached.get("ok") else {}
        stale.pop("tokens", None)
        stale.pop("spendStats", None)
        stale.update(ok=bool(stale), stale=True, error="; ".join(errors)[:80])
        return stale if stale.get("ok") else {"ok": False, "error": "; ".join(errors)[:80]}
    result.update(ok=True, stale=bool(errors), error="; ".join(errors)[:80], fetchedAt=time.time())
    save_spend(cache_path, result)
    return result


def age_arg(name, default, floor):
    """A --NAME=SECONDS option from the widget's update frequency, kept within
    [floor, 3600]; default when absent."""
    for arg in sys.argv[1:]:
        if arg.startswith("--" + name + "="):
            try:
                return max(floor, min(3600, int(arg.split("=", 1)[1])))
            except ValueError:
                break
    return default


CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CLAUDE_MIN_AGE = 300  # seconds by default; never below 180, Anthropic refuses faster probes


def minor_amount(obj):
    """An {amount_minor, exponent} money object as a float, or None."""
    if not isinstance(obj, dict):
        return None
    amount, exponent = money(obj.get("amount_minor")), obj.get("exponent", 2)
    if amount is None or isinstance(exponent, bool) or not isinstance(exponent, int) or not 0 <= exponent <= 4:
        return None
    return amount / (10 ** exponent)


def parse_extra(payload):
    spend, extra = payload.get("spend"), payload.get("extra_usage")
    result = {}
    if isinstance(spend, dict):
        used, limit = minor_amount(spend.get("used")), minor_amount(spend.get("limit"))
        if used is not None:
            result = {"enabled": spend.get("enabled") is True, "used": round(used, 2),
                      "limit": round(limit, 2) if limit else None,
                      "currency": text((spend.get("used") or {}).get("currency") or "USD", 3)}
    if not result and isinstance(extra, dict):
        places = extra.get("decimal_places", 2)
        places = places if isinstance(places, int) and not isinstance(places, bool) and 0 <= places <= 4 else 2
        used, limit = money(extra.get("used_credits")), money(extra.get("monthly_limit"))
        if used is not None:
            result = {"enabled": extra.get("is_enabled") is True, "used": round(used / 10 ** places, 2),
                      "limit": round(limit / 10 ** places, 2) if limit else None,
                      "currency": text(extra.get("currency") or "USD", 3)}
    if not result:
        raise ValueError("no extra usage in answer")
    if isinstance(extra, dict):
        result["reached"] = extra.get("spend_limit_reached") is True
    if not result["currency"].isalpha():
        result["currency"] = "USD"
    return result


def claude_extra(cache_path):
    """Claude's extra usage this month, or None when there's no sign-in."""
    config = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    try:
        with open(os.path.join(config, ".credentials.json"), encoding="utf-8") as fh:
            oauth = json.load(fh).get("claudeAiOauth") or {}
    except (OSError, ValueError, AttributeError):
        return None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not 0 < len(token) < 4096 or not token.isprintable():
        return None
    cached = load_spend(cache_path)
    try:
        fetched = float(cached.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        fetched = 0
    if cached.get("ok") and time.time() - fetched < age_arg("claude-age", CLAUDE_MIN_AGE, 180):
        return cached
    expires = oauth.get("expiresAt")
    if isinstance(expires, (int, float)) and not isinstance(expires, bool) and expires / 1000 < time.time():
        error = "sign-in lapsed; opening Claude Code renews it"
    else:
        try:
            request = urllib.request.Request(CLAUDE_USAGE_URL, headers={
                "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20",
                "Accept": "application/json", "User-Agent": "omarchy-agent-stats"})
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read(1_000_000))
            result = parse_extra(payload if isinstance(payload, dict) else {})
            result.update(ok=True, stale=False, fetchedAt=time.time())
            save_spend(cache_path, result)
            return result
        except urllib.error.HTTPError as err:
            error = "refused (%d)" % err.code if err.code in (401, 403) else "HTTP %d" % err.code
        except (OSError, ValueError, urllib.error.URLError) as err:
            error = type(err).__name__
    if cached.get("ok"):
        stale = dict(cached)
        stale.update(stale=True, error=error)
        return stale
    return {"ok": False, "error": error}


def main():
    live_openrouter = "--openrouter-live" in sys.argv[1:]
    want_claude_extra = "--claude-extra" in sys.argv[1:]
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

    if want_claude_extra:
        extra = claude_extra(os.path.join(state, "omarchy-agent-stats", "claude-extra.json"))
        for agent in agents:
            if agent["id"] == "claude" and extra is not None:
                if extra.get("ok"):
                    agent["extra"] = {k: extra.get(k) for k in ("enabled", "used", "limit", "currency", "reached", "stale")}
                    if extra.get("error"):
                        agent["extra"]["error"] = text(extra["error"], 80)
                else:
                    agent["extra"] = {"error": text(extra.get("error") or "unavailable", 80)}

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
