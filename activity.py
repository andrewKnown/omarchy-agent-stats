#!/usr/bin/env python3
# Prints a JSON list of agent ids whose CLI is busy right now: a process for
# that agent used noticeable CPU over a one-second sample. An open but idle
# session doesn't count. Reads /proc only; no network, no state files.
import json
import os
import sys
import time

# Usage record id -> process names that agent's CLI runs as.
PROCESS_NAMES = {
    "claude": {"claude"},
    "codex": {"codex"},
    "grok": {"grok"},
    "copilot": {"copilot"},
    "cursor": {"cursor-agent"},
    "kimi": {"kimi"},
    "qwen": {"qwen"},
    "muse": {"muse"},
    "hermes": {"hermes"},
    "devin": {"devin"},
    "agy": {"agy"},
    "opencode": {"opencode"},
}
NAME_TO_ID = {name: aid for aid, names in PROCESS_NAMES.items() for name in names}
SAMPLE_SECONDS = 1.0
# Clock ticks (1/100 s) a whole agent must use in the sample to count as busy:
# 3 ticks is 3% of one core, well above an idle session's near-zero.
BUSY_TICKS = 3


def agent_for(pid):
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as fh:
            comm = fh.read().strip()
        if comm in NAME_TO_ID:
            return NAME_TO_ID[comm]
        # Node-based CLIs run as "node": match the script they were started with.
        if comm == "node":
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                argv = fh.read(4096).split(b"\0")
            for arg in argv[1:3]:
                base = os.path.basename(arg.decode("utf-8", "replace"))
                if base in NAME_TO_ID:
                    return NAME_TO_ID[base]
    except OSError:
        pass
    return None


def cpu_ticks(pid):
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            stat = fh.read()
        # Fields after the parenthesised command name; utime and stime are 14 and 15.
        fields = stat[stat.rindex(")") + 2:].split()
        return int(fields[11]) + int(fields[12])
    except (OSError, ValueError, IndexError):
        return None


def sample(pids):
    out = {}
    for pid, aid in pids.items():
        ticks = cpu_ticks(pid)
        if ticks is not None:
            out[pid] = ticks
    return out


def main():
    pids = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            aid = agent_for(entry)
            if aid:
                pids[entry] = aid
    busy = set()
    if pids:
        before = sample(pids)
        time.sleep(SAMPLE_SECONDS)
        after = sample(pids)
        used = {}
        for pid, ticks in after.items():
            if pid in before:
                aid = pids[pid]
                used[aid] = used.get(aid, 0) + max(0, ticks - before[pid])
        busy = {aid for aid, ticks in used.items() if ticks >= BUSY_TICKS}
    json.dump(sorted(busy), sys.stdout)


if __name__ == "__main__":
    main()
