#!/usr/bin/env python3
"""The network checks, each run on its own timer by the widget so a slow or
stuck one never holds up the bar. Each writes its result, a failure included,
to a cache file in $XDG_STATE_HOME/omarchy-agent-stats/ that read.py reads:

  remote.py openrouter [--openrouter-age=S]   -> openrouter-live.json
  remote.py claude [--claude-age=S]           -> claude-extra.json
  remote.py gemini --gemini-projects=ID,ID [--gemini-age=S] -> gemini.json

OpenRouter: GET /api/v1/keys (all keys' spend, management key) or /key (one
key's spend and limit), /analytics/query (tokens), /credits (balance), with the
keys Omarchy reads (OPENROUTER_API_KEY / OPENROUTER_MANAGEMENT_KEY or
~/.config/omarchy/agents/openrouter.json), sent only to openrouter.ai.

Claude: Anthropic's usage endpoint (the one Claude Code's /usage reads) for
extra usage this month, with the access token Claude Code keeps in
$CLAUDE_CONFIG_DIR/.credentials.json, sent only to api.anthropic.com, never
refreshed or stored; skipped while it has lapsed.

Gemini: Google Cloud Monitoring for the chosen projects' Gemini API requests
and tokens today, with a short-lived token from `gcloud auth
print-access-token`, sent only to monitoring.googleapis.com, never stored.
Google has no API for Gemini spend or balance.

A check never runs more often than its age (with floors: Claude 180 s,
OpenRouter 30 s, Gemini 60 s); a failed one keeps the last good answer,
marked stale.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

_spec = importlib.util.spec_from_file_location("agent_stats_common", os.path.join(os.path.dirname(os.path.abspath(__file__)), "common.py"))
_common = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_common)
text, money, state_dir = _common.text, _common.money, _common.state_dir
load_spend, save_spend = _common.load_json, _common.save_json


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
    # Checks run in the background, so a slow answer only costs this check.
    with urllib.request.urlopen(request, timeout=20) as response:
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
    if cached.get("ok") and not FORCED and time.time() - fetched < still_fresh(age_arg("openrouter-age", OPENROUTER_MIN_AGE, 30)):
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
            # A slow or failed answer isn't a refusal: keep the account's last
            # figures rather than swap in one key's, which tell another story.
            if not refused and cached.get("ok") and cached.get("scope") == "account":
                result.update({k: cached[k] for k in ("today", "week", "month", "scope", "keyCount") if k in cached})
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
    # Likewise a balance that couldn't be read this time keeps its last value.
    if credit_errors and "remaining" not in result and cached.get("ok") and result:
        result.update({k: cached[k] for k in ("remaining", "funded", "spent") if k in cached})
    if not result:
        stale = dict(cached) if cached.get("ok") else {}
        stale.pop("tokens", None)
        stale.pop("spendStats", None)
        stale.update(ok=bool(stale), stale=True, error="; ".join(errors)[:80])
        return stale if stale.get("ok") else {"ok": False, "error": "; ".join(errors)[:80]}
    result.update(ok=True, stale=bool(errors), error="; ".join(errors)[:80], fetchedAt=time.time())
    save_spend(cache_path, result)
    return result


# --force (a refresh someone asked for): skip the "answer is still fresh" wait.
# Claude still honours Anthropic's retry-after and stays clear of Omarchy's
# own check, so a click can't add to its rate limit.
FORCED = "--force" in sys.argv[2:]


def still_fresh(age):
    """How long an answer stays fresh for a check due every `age` seconds. An
    answer is stamped when its requests finish, seconds after the check began,
    so without some slack the next timer finds it a little too new and skips
    a whole round."""
    return age - min(20, age * 0.2)


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


GEMINI_MIN_AGE = 300
MONITORING = "https://monitoring.googleapis.com/v3/projects/"
GEMINI_METRIC = "generativelanguage.googleapis.com/"
# Input tokens are only reported through the per-tier quota counters.
GEMINI_INPUT_METRICS = ("free_tier", "paid_tier", "paid_tier_2", "paid_tier_3")


def gcloud_path():
    found = shutil.which("gcloud")
    if found:
        return found
    for candidate in ("~/.local/share/mise/shims/gcloud", "~/google-cloud-sdk/bin/gcloud",
                      "/opt/google-cloud-cli/bin/gcloud", "/usr/lib/google-cloud-sdk/bin/gcloud"):
        path = os.path.expanduser(candidate)
        if os.access(path, os.X_OK):
            return path
    return None


def gcloud_token():
    """(token, error): a short-lived access token from the user's gcloud."""
    gcloud = gcloud_path()
    if not gcloud:
        return None, "gcloud not installed"
    try:
        out = subprocess.run([gcloud, "auth", "print-access-token"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, "gcloud: " + type(error).__name__
    token = out.stdout.strip()
    if out.returncode != 0 or not token or not token.isprintable() or len(token) > 4096:
        if "reauth" in out.stderr.lower() or "auth login" in out.stderr.lower():
            return None, "Google sign-in expired: run gcloud auth login"
        return None, "gcloud gave no token"
    return token, ""


def monitoring_series(project, token, metric_filter, start, end, group_by):
    """[(labels, total)] per group, each summed over [start, end]."""
    from urllib.parse import urlencode, quote
    period = max(60, int((end - start).total_seconds()))
    query = urlencode([
        ("filter", metric_filter),
        ("interval.startTime", start.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("interval.endTime", end.strftime("%Y-%m-%dT%H:%M:%SZ")),
        ("aggregation.alignmentPeriod", "%ds" % period),
        ("aggregation.perSeriesAligner", "ALIGN_DELTA"),
        ("aggregation.crossSeriesReducer", "REDUCE_SUM"),
    ] + [("aggregation.groupByFields", g) for g in group_by])
    request = urllib.request.Request(MONITORING + quote(project) + "/timeSeries?" + query,
                                     headers={"Authorization": "Bearer " + token, "User-Agent": "omarchy-agent-stats"})
    with urllib.request.urlopen(request, timeout=10) as response:
        data = json.loads(response.read(2_000_000))
    out = []
    for series in data.get("timeSeries") or []:
        labels = (series.get("metric") or {}).get("labels") or {}
        total = 0.0
        for point in series.get("points") or []:
            value = point.get("value") or {}
            try:
                total += float(value.get("int64Value", value.get("doubleValue", 0)))
            except (TypeError, ValueError):
                pass
        out.append(({k: text(v, 64) for k, v in labels.items()}, total))
    return out


def gemini_project(project, token, start, end):
    reqs = monitoring_series(project, token,
        'metric.type="serviceruntime.googleapis.com/api/request_count" AND resource.type="consumed_api" '
        'AND resource.labels.service="generativelanguage.googleapis.com"',
        start, end, ["metric.labels.response_code_class"])
    models = {}
    for labels, value in monitoring_series(project, token,
            'metric.type="%sgenerate_content_usage_output_token_count"' % GEMINI_METRIC, start, end, ["metric.labels.model"]):
        if labels.get("model"):
            models.setdefault(labels["model"], {"in": 0, "out": 0})["out"] += int(value)
    for tier in GEMINI_INPUT_METRICS:
        # A quota counter is reported once per limit it feeds (per minute,
        # per user, ...), each with the same count: take one per model.
        best = {}
        for labels, value in monitoring_series(project, token,
                'metric.type="%squota/generate_content_%s_input_token_count/usage"' % (GEMINI_METRIC, tier),
                start, end, ["metric.labels.model", "metric.labels.limit_name"]):
            model = labels.get("model")
            if model:
                best[model] = max(best.get(model, 0), int(value))
        for model, value in best.items():
            models.setdefault(model, {"in": 0, "out": 0})["in"] += value
    return {"requests": int(sum(v for _, v in reqs)),
            "errors": int(sum(v for l, v in reqs if not str(l.get("response_code_class", "")).startswith("2"))),
            "models": models}


def gemini_usage(projects, cache_path):
    cached = load_spend(cache_path)
    try:
        fetched = float(cached.get("fetchedAt") or 0)
    except (TypeError, ValueError):
        fetched = 0
    today = datetime.now().astimezone().date().isoformat()
    same = cached.get("projects") == projects and cached.get("day") == today
    if same and cached.get("ok") and not FORCED and time.time() - fetched < still_fresh(age_arg("gemini-age", GEMINI_MIN_AGE, 60)):
        return cached
    token, error = gcloud_token()
    if token:
        now = datetime.now().astimezone()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        end = now.astimezone(timezone.utc)
        per_project, errors = [], []

        def one(project):
            try:
                usage = gemini_project(project, token, start, end)
                usage["id"] = project
                return usage, None
            except urllib.error.HTTPError as err:
                return None, "%s: HTTP %d" % (project, err.code)
            except (OSError, ValueError, urllib.error.URLError) as err:
                return None, "%s: %s" % (project, type(err).__name__)

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(projects))) as pool:
            for usage, err in pool.map(one, projects):
                if usage:
                    per_project.append(usage)
                else:
                    errors.append(err)
        if per_project:
            models = {}
            for usage in per_project:
                for model, counts in usage["models"].items():
                    entry = models.setdefault(model, {"in": 0, "out": 0})
                    entry["in"] += counts["in"]
                    entry["out"] += counts["out"]
            result = {"ok": True, "stale": bool(errors), "error": "; ".join(errors)[:80], "fetchedAt": time.time(),
                      "projects": projects, "day": today,
                      "requests": sum(u["requests"] for u in per_project),
                      "errors": sum(u["errors"] for u in per_project),
                      "models": models,
                      "perProject": [{"id": u["id"], "requests": u["requests"],
                                      "tokens": sum(c["in"] + c["out"] for c in u["models"].values())} for u in per_project]}
            save_spend(cache_path, result)
            return result
        error = "; ".join(errors)[:80] or "no data"
    if same and cached.get("ok"):
        stale = dict(cached)
        stale.update(stale=True, error=error)
        return stale
    return {"ok": False, "error": error}


CLAUDE_USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# Seconds between checks, never fewer than 300: this endpoint shares
# Anthropic's rate limit with Omarchy's own Claude limits checks, and extra
# usage spend moves slowly.
CLAUDE_MIN_AGE = 300


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
    if cached.get("ok") and not FORCED and time.time() - fetched < still_fresh(age_arg("claude-age", CLAUDE_MIN_AGE, CLAUDE_MIN_AGE)):
        return cached
    # After a 429, wait as long as Anthropic asked before trying again.
    try:
        retry_at = float(cached.get("retryAt") or 0)
    except (TypeError, ValueError):
        retry_at = 0
    if retry_at > time.time():
        return cached
    # Space this out from Omarchy's own Claude checks, which ask the same
    # endpoint and share its rate limit: skip a run within a minute of one.
    omarchy = load_spend(os.path.join(os.path.dirname(state_dir()), "omarchy", "agents", "usage", "claude.json"))
    omarchy_checked = _common.stamp_seconds(omarchy.get("limitsFetchedAt"))
    if 0 < time.time() - omarchy_checked < 60 and cached.get("ok"):
        return dict(cached, _deferred=True)
    retry = {}
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
            if err.code == 429:
                try:
                    wait = max(60, min(3600, int(err.headers.get("Retry-After") or 600)))
                except (TypeError, ValueError):
                    wait = 600
                error = "Anthropic asked to wait; retrying at " + time.strftime("%H:%M", time.localtime(time.time() + wait))
                retry = {"retryAt": time.time() + wait}
            else:
                error = "refused (%d)" % err.code if err.code in (401, 403) else "HTTP %d" % err.code
        except (OSError, ValueError, urllib.error.URLError) as err:
            error = type(err).__name__
    if cached.get("ok"):
        stale = dict(cached)
        stale.update(stale=True, error=error, **retry)
        return stale
    return {"ok": False, "error": error, **retry}


CACHES = {"openrouter": "openrouter-live.json", "claude": "claude-extra.json", "gemini": "gemini.json"}


def gemini_projects_arg():
    for arg in sys.argv[2:]:
        if arg.startswith("--gemini-projects="):
            return sorted({p for p in arg.split("=", 1)[1].split(",")
                           if 6 <= len(p) <= 30 and p.replace("-", "").isalnum() and p[0].isalpha() and p == p.lower()})[:8]
    return []


def main():
    check = sys.argv[1] if len(sys.argv) > 1 else ""
    if check not in CACHES:
        print("usage: remote.py openrouter|claude|gemini [options]", file=sys.stderr)
        return 2
    path = os.path.join(state_dir(), CACHES[check])
    if check == "openrouter":
        result = openrouter_live(path)
    elif check == "claude":
        result = claude_extra(path)
    else:
        projects = gemini_projects_arg()
        if not projects:
            return 2
        result = gemini_usage(projects, path)
    # Nothing to check with (no key, no sign-in) is recorded too, so the bar
    # can say so rather than wait for a check that never comes.
    if result is None:
        result = {"ok": False, "missing": True}
    # Put off (Claude, right after one of Omarchy's checks): leave the cache
    # as it is and exit 75 so the widget tries again shortly.
    if result.pop("_deferred", False):
        return 75
    result["checkedAt"] = time.time()
    save_spend(path, result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
