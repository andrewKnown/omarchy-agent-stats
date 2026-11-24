#!/usr/bin/env python3
# Reads the usage records omarchy-agent-usage-update writes and prints a small,
# validated JSON summary for the bar: one entry per agent that has limits.
# Every field is optional and bounded, so a change in the record format
# degrades to "no meters" rather than an error.
import json
import os
import sys

ORDER = ["claude", "codex", "grok"]
MAX_AGENTS = 8
MAX_LIMITS = 4


def text(value, limit=48):
    return str(value)[:limit] if isinstance(value, (str, int, float)) else ""


def main():
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    usage_dir = os.path.join(state, "omarchy", "agents", "usage")
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
            continue
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
        if not limits:
            continue
        agents.append({
            "id": agent_id,
            "name": agent_name,
            "stale": record.get("limitsStale") is True,
            "limits": limits,
        })

    def rank(agent):
        aid = agent["id"]
        return (ORDER.index(aid) if aid in ORDER else len(ORDER), aid)

    agents.sort(key=rank)
    today.sort(key=lambda item: -item[1])
    json.dump({
        "agents": agents[:MAX_AGENTS],
        "todayTokens": sum(t for _, t in today),
        "todayByAgent": [{"name": n, "tokens": t} for n, t in today[:MAX_AGENTS]],
    }, sys.stdout)


if __name__ == "__main__":
    main()
