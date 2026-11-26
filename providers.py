#!/usr/bin/env python3
"""Set up the providers Omarchy Agent Stats shows, in a terminal.

Opened from the widget's menu (Providers…). Lists every provider Omarchy's
Agents plugin has a collector for, with its current state, and per provider:

  - API-key providers whose key Omarchy reads from its own
    ~/.config/omarchy/agents/<id>.json (OpenRouter, Z.ai, Ollama Cloud):
    set, replace or remove the key, typed hidden, saved owner-only in that
    same file so Omarchy uses it too.
  - Claude, Codex and Grok: sign in or add an account through Omarchy's own
    `omarchy agent account add`.
  - Everyone else: Omarchy reads their own CLI's sign-in; shows Omarchy's
    help text for it.

"Test" re-collects that provider with Omarchy's own updater and shows what it
now reports. Keys are never printed in full, logged, or sent anywhere by this
script; testing hands them to Omarchy's collector, which talks to the provider.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

STATE = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
CONFIG = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
USAGE_DIR = os.path.join(STATE, "omarchy", "agents", "usage")
AGENTS_CONFIG = os.path.join(CONFIG, "omarchy", "agents")
OMARCHY_BIN = os.path.join(os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy", "bin")

# Keys Omarchy's collectors read from ~/.config/omarchy/agents/<id>.json:
# (field, what it is, environment variable that takes precedence).
KEYED = {
    "openrouter": [
        ("apiKey", "API key", "OPENROUTER_API_KEY"),
        ("managementKey", "Management key (optional: account-wide spend)", "OPENROUTER_MANAGEMENT_KEY"),
    ],
    "zai": [("apiKey", "API key (GLM Coding Plan)", "ZAI_API_KEY")],
    "ollama": [("apiKey", "API key (Ollama Cloud)", "OLLAMA_API_KEY")],
}
ACCOUNTS = ("claude", "codex", "grok")
KEY_PAGES = {
    "openrouter": "openrouter.ai/settings/keys (management keys: openrouter.ai/settings/management-keys)",
    "zai": "z.ai/manage-apikey/apikey-list",
    "ollama": "ollama.com/settings/keys",
}
HAS_GUM = shutil.which("gum") is not None
WIDGET_ID = "io.github.andrewknown.agent-stats"
HERE = os.path.dirname(os.path.abspath(__file__))


def reader():
    """remote.py from this plugin, for its Google Cloud helpers."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_stats_remote", os.path.join(HERE, "remote.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def widget_setting(key, default=""):
    shell = read_json(os.path.join(CONFIG, "omarchy", "shell.json"))
    stack = [shell]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            if item.get("id") == WIDGET_ID:
                return item.get(key, default)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return default


def save_widget_setting(key, value, as_json=False):
    cmd = ["omarchy-bar", "set", WIDGET_ID, key, json.dumps(value) if as_json else str(value)]
    subprocess.run(cmd + (["--json"] if as_json else []), stdout=subprocess.DEVNULL)


# The widget's display and alert settings, each with the choices offered here
# as (label, value). Anything else can still be set in shell.json.
DISPLAY = [
    ("activityPulse", "Pulse while an agent is working", [("Off", False), ("On", True)]),
    ("warnAt", "Turn a meter red at % used", [("70%", 70), ("80%", 80), ("90%", 90), ("95%", 95)]),
    ("lowAlert", "Low usage ring", [("Off", "Off"), ("Ring", "Ring"), ("Pulsing ring", "Pulse")]),
    ("lowAlertAt", "Ring when % left is at or below", [("5%", 5), ("10%", 10), ("20%", 20), ("30%", 30)]),
    ("prepaidText", "Prepaid providers show", [("Balance", "Balance"), ("Spent today", "Today"), ("Spent this month", "Month")]),
    ("lowBalanceAt", "Ring when a balance drops to", [("Off", 0), ("$1", 1), ("$5", 5), ("$10", 10), ("$20", 20)]),
    ("openrouterLive", "OpenRouter numbers", [("Local estimates (no network)", False), ("Live from the OpenRouter API", True)]),
    ("claudeExtra", "Claude extra usage check", [("Off", False), ("On (asks Anthropic every 5 min)", True)]),
    ("extraText", "Claude extra usage shows", [("Credit left ($ and %)", "Left"), ("Amount spent", "Spent")]),
]


def manifest_defaults():
    return read_json(os.path.join(HERE, "manifest.json")).get("barWidget", {}).get("defaults", {})


def manage_display():
    defaults = manifest_defaults()
    while True:
        clear()
        heading("Display & alerts")
        rows = {}
        for key, label, choices in DISPLAY:
            current = widget_setting(key, defaults.get(key))
            shown = next((l for l, v in choices if v == current), str(current))
            rows[f"{label}: {shown}"] = (key, label, choices)
        picked = choose("Pick a setting to change (Esc to go back)", list(rows) + ["Back"])
        if picked in (None, "Back"):
            return
        key, label, choices = rows[picked]
        value_label = choose(label, [l for l, _ in choices])
        if value_label is None:
            continue
        value = dict(choices)[value_label]
        save_widget_setting(key, value, as_json=not isinstance(value, str))


# ------------------------------------------------------------------ ui bits

def say(text=""):
    print(text, flush=True)


def clear():
    if sys.stdout.isatty():
        print("\033[H\033[2J", end="", flush=True)


def heading(text):
    if HAS_GUM:
        subprocess.run(["gum", "style", "--bold", "--margin", "1 0 0 0", text])
    else:
        say("\n== " + text)


def choose(prompt, options):
    """Pick one of options; None on Escape / Ctrl-C."""
    if HAS_GUM:
        result = subprocess.run(["gum", "choose", "--header", prompt, *options], stdout=subprocess.PIPE, text=True)
        choice = result.stdout.strip()
        return choice if result.returncode == 0 and choice in options else None
    say(prompt)
    for i, option in enumerate(options, 1):
        say(f"  {i}. {option}")
    try:
        picked = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    return options[int(picked) - 1] if picked.isdigit() and 0 < int(picked) <= len(options) else None


def secret(prompt):
    if HAS_GUM:
        result = subprocess.run(["gum", "input", "--password", "--width", "80", "--placeholder", prompt],
                                stdout=subprocess.PIPE, text=True)
        return result.stdout.strip() if result.returncode == 0 else ""
    import getpass
    try:
        return getpass.getpass(prompt + ": ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def confirm(prompt):
    if HAS_GUM:
        return subprocess.run(["gum", "confirm", prompt]).returncode == 0
    try:
        return input(prompt + " [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def pause():
    try:
        input("\nPress Enter to go back…")
    except (EOFError, KeyboardInterrupt):
        pass


# ------------------------------------------------------------------ data

def read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def record(agent_id):
    return read_json(os.path.join(USAGE_DIR, agent_id + ".json"))


def provider_ids():
    ids = set()
    try:
        for name in os.listdir(OMARCHY_BIN):
            if name.startswith("omarchy-agent-usage-") and name != "omarchy-agent-usage-update":
                ids.add(name[len("omarchy-agent-usage-"):])
    except OSError:
        pass
    try:
        ids.update(n[:-5] for n in os.listdir(USAGE_DIR) if n.endswith(".json"))
    except OSError:
        pass
    return sorted(i for i in ids if i.replace("-", "").isalnum() and len(i) <= 24)


def money(value):
    try:
        return "$%.2f" % float(value)
    except (TypeError, ValueError):
        return "?"


def status(rec):
    """One short line describing what Omarchy currently reports."""
    if not rec:
        return "no record yet"
    parts = []
    limits = rec.get("limits") if isinstance(rec.get("limits"), list) else []
    if limits:
        shown = []
        for item in limits[:3]:
            try:
                shown.append("%s %d%%" % (str(item.get("label", ""))[:22], round(float(item.get("percent")) * 100)))
            except (TypeError, ValueError, AttributeError):
                pass
        parts.append(", ".join(shown))
    balance = rec.get("balance")
    if isinstance(balance, dict) and balance.get("remaining") is not None:
        parts.append("balance " + money(balance.get("remaining")))
    if rec.get("usageStatusText"):
        parts.append(str(rec["usageStatusText"])[:60])
    if rec.get("limitsStale"):
        parts.append("stale")
    if not parts:
        parts.append("ready (local usage only)" if rec.get("ready") else "not set up")
    return " · ".join(parts)


def masked(key):
    key = str(key)
    return key[:6] + "…" + key[-4:] if len(key) > 14 else "…" * 3


def key_source(agent_id, field, env):
    if os.environ.get(env):
        return "from $" + env + " (" + masked(os.environ[env]) + "); it overrides the saved key"
    saved = read_json(os.path.join(AGENTS_CONFIG, agent_id + ".json")).get(field)
    return ("saved (" + masked(saved) + ")") if saved else "not set"


def save_field(agent_id, field, value):
    os.makedirs(AGENTS_CONFIG, mode=0o700, exist_ok=True)
    path = os.path.join(AGENTS_CONFIG, agent_id + ".json")
    data = read_json(path)
    if value:
        data[field] = value
    else:
        data.pop(field, None)
    fd, tmp = tempfile.mkstemp(dir=AGENTS_CONFIG, prefix="." + agent_id + ".")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def valid_key(value):
    return 8 <= len(value) <= 512 and value.isprintable() and not any(c.isspace() for c in value)


# ------------------------------------------------------------------ actions

def test(agent_id):
    say(f"\nAsking Omarchy to re-collect {agent_id}…")
    updater = os.path.join(OMARCHY_BIN, "omarchy-agent-usage-update")
    try:
        subprocess.run([updater, "--force", agent_id], timeout=120,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as error:
        say("Couldn't run Omarchy's updater: " + type(error).__name__)
        return
    rec = record(agent_id)
    say("Now: " + status(rec))
    if rec.get("authHelpText"):
        say("Omarchy says: " + str(rec["authHelpText"])[:300])


def manage_keys(agent_id, name):
    fields = KEYED[agent_id]
    while True:
        clear()
        heading(f"{name} keys")
        for field, label, env in fields:
            say(f"  {label}: {key_source(agent_id, field, env)}")
        say(f"  Get keys at {KEY_PAGES.get(agent_id, 'the provider')}")
        options = []
        for field, label, env in fields:
            options.append("Set " + label.split(" (")[0].lower())
            if read_json(os.path.join(AGENTS_CONFIG, agent_id + ".json")).get(field):
                options.append("Remove " + label.split(" (")[0].lower())
        options += ["Test", "Back"]
        picked = choose(f"{name}: {status(record(agent_id))}", options)
        if picked in (None, "Back"):
            return
        if picked == "Test":
            test(agent_id)
            pause()
            continue
        for field, label, env in fields:
            short = label.split(" (")[0].lower()
            if picked == "Set " + short:
                value = secret(f"Paste the {short} (hidden)")
                if not value:
                    say("Nothing changed.")
                elif not valid_key(value):
                    say("That doesn't look like a key (spaces or wrong length); nothing changed.")
                else:
                    save_field(agent_id, field, value)
                    say(f"Saved to {AGENTS_CONFIG}/{agent_id}.json (owner-only).")
                    test(agent_id)
                pause()
            elif picked == "Remove " + short and confirm(f"Remove the saved {short}?"):
                save_field(agent_id, field, "")
                say("Removed.")
                pause()


def manage_account(agent_id, name):
    while True:
        clear()
        heading(name)
        picked = choose(f"{name}: {status(record(agent_id))}",
                        ["Sign in / add an account", "List accounts", "Test", "Back"])
        if picked in (None, "Back"):
            return
        if picked == "Test":
            test(agent_id)
        elif picked == "List accounts":
            subprocess.run([os.path.join(OMARCHY_BIN, "omarchy-agent-account-list"), agent_id])
        else:
            say(f"\nStarting Omarchy's sign-in for {name}. Follow the prompts; Ctrl-C cancels.\n")
            subprocess.run([os.path.join(OMARCHY_BIN, "omarchy-agent-account-add"), agent_id])
            test(agent_id)
        pause()


def choose_many(prompt, options, selected):
    if HAS_GUM:
        cmd = ["gum", "choose", "--no-limit", "--header", prompt]
        if selected:
            cmd.append("--selected=" + ",".join(selected))
        result = subprocess.run(cmd + options, stdout=subprocess.PIPE, text=True)
        return [line for line in result.stdout.splitlines() if line in options] if result.returncode == 0 else None
    say(prompt + " (numbers separated by spaces)")
    for i, option in enumerate(options, 1):
        say(f"  {i}. {'[x]' if option in selected else '[ ]'} {option}")
    try:
        picked = input("> ").split()
    except (EOFError, KeyboardInterrupt):
        return None
    return [options[int(n) - 1] for n in picked if n.isdigit() and 0 < int(n) <= len(options)]


def gemini_status():
    projects = [p for p in str(widget_setting("geminiProjects", "")).split(",") if p]
    if not projects:
        return "off"
    cache = read_json(os.path.join(STATE, "omarchy-agent-stats", "gemini.json"))
    if cache.get("ok"):
        tokens = sum(c.get("in", 0) + c.get("out", 0) for c in (cache.get("models") or {}).values())
        return "%d requests, %s tokens today · %d project(s)%s" % (
            cache.get("requests", 0), f"{tokens:,}", len(projects), " · stale" if cache.get("stale") else "")
    return "%d project(s), not checked yet" % len(projects)


def find_gemini_projects():
    """[(project, requests in the last 7 days)] for projects with the Gemini API on."""
    from datetime import datetime, timedelta, timezone
    r = reader()
    token, error = r.gcloud_token()
    if not token:
        say("Google Cloud: " + error)
        return None
    gcloud = r.gcloud_path()
    say("Listing your Google Cloud projects…")
    listed = subprocess.run([gcloud, "projects", "list", "--format=value(projectId)"],
                            capture_output=True, text=True, timeout=120).stdout.split()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    def requests(project):
        try:
            series = r.monitoring_series(project, token,
                'metric.type="serviceruntime.googleapis.com/api/request_count" AND resource.type="consumed_api" '
                'AND resource.labels.service="generativelanguage.googleapis.com"', start, end, [])
            return project, int(sum(v for _, v in series))
        except Exception:
            return project, 0

    from concurrent.futures import ThreadPoolExecutor
    say(f"Checking {len(listed)} projects for Gemini API traffic in the last 7 days…")
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = [(p, n) for p, n in pool.map(requests, listed) if n]
    return found


def manage_gemini():
    gcloud = reader().gcloud_path()
    while True:
        clear()
        heading("Google Gemini API (AI Studio)")
        say("Shows requests and tokens today per model from Google Cloud Monitoring, for the")
        say("projects your AI Studio keys belong to. Google has no API for Gemini spend or")
        say("prepay balance; see aistudio.google.com/spend for those.")
        if not gcloud:
            say("\nNeeds the Google Cloud CLI (gcloud). Install it, e.g.: omarchy pkg aur add google-cloud-cli")
            pause()
            return
        account = subprocess.run([gcloud, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
                                 capture_output=True, text=True, timeout=60).stdout.strip()
        say("\nGoogle Cloud account: " + (account or "not signed in"))
        say("Projects: " + (str(widget_setting("geminiProjects", "")) or "none"))
        picked = choose("Gemini API: " + gemini_status(),
                        ["Choose projects", "Sign in to Google Cloud", "Turn off", "Test", "Back"])
        if picked in (None, "Back"):
            return
        if picked == "Sign in to Google Cloud":
            subprocess.run([gcloud, "auth", "login"])
            pause()
        elif picked == "Turn off":
            save_widget_setting("geminiProjects", "")
            say("Gemini API turned off.")
            pause()
        elif picked == "Test":
            projects = [p for p in str(widget_setting("geminiProjects", "")).split(",") if p]
            if not projects:
                say("Choose projects first.")
            else:
                r = reader()
                result = r.gemini_usage(sorted(projects), os.path.join(STATE, "omarchy-agent-stats", "gemini.json.test"))
                try:
                    os.remove(os.path.join(STATE, "omarchy-agent-stats", "gemini.json.test"))
                except OSError:
                    pass
                if result.get("ok"):
                    say("Today: %d requests (%d failed)" % (result["requests"], result["errors"]))
                    for model, c in (result.get("models") or {}).items():
                        say(f"  {model}: {c['in']:,} in · {c['out']:,} out")
                if result.get("error"):
                    say("Problem: " + result["error"])
            pause()
        else:
            found = find_gemini_projects()
            if found is None:
                pause()
                continue
            if not found:
                say("No project had Gemini API requests in the last 7 days.")
                pause()
                continue
            labels = {f"{p}  ({n} requests in 7 days)": p for p, n in found}
            current = set(str(widget_setting("geminiProjects", "")).split(","))
            chosen = choose_many("Pick the projects to show (space to toggle, enter to save)",
                                 list(labels), [l for l, p in labels.items() if p in current])
            if chosen is None:
                continue
            value = ",".join(sorted(labels[l] for l in chosen))
            save_widget_setting("geminiProjects", value)
            say("Saved: " + (value or "none (Gemini off)"))
            pause()


def cursor_storage():
    """Where the Cursor CLI keeps its sign-in: "file", "keyring" or "none"."""
    data = read_json(os.path.join(CONFIG, "cursor", "auth.json"))
    if data.get("accessToken"):
        return "file"
    return "keyring" if data.get("storedIn") else "none"


def manage_cursor(agent_id, name):
    """Cursor and Grok Bot share the Cursor CLI sign-in."""
    while True:
        clear()
        heading(name)
        if agent_id == "grokbot":
            say("Grok Bot is Cursor's separate Grok allowance: it uses your Cursor sign-in.")
        where = cursor_storage()
        if where == "file":
            say("Cursor sign-in: in ~/.config/cursor/auth.json, which Omarchy reads.")
            say("Clicking Cursor in the bar starts it so the sign-in stays there.")
        elif where == "keyring":
            say("Cursor sign-in: in your desktop keyring. Omarchy 4.5 can't read it yet")
            say("(omacom/omarchy#19559), so Cursor and Grok Bot show no usage. Signing in")
            say("again with the display hidden saves it to the file Omarchy reads; once")
            say("Omarchy reads the keyring, a normal `cursor-agent login` is enough.")
        else:
            say("Cursor: not signed in.")
        options = ["Sign in so Omarchy can read it", "Test", "Back"]
        if shutil.which("cursor-agent") is None:
            say("\nThe Cursor CLI (cursor-agent) isn't installed.")
            options = ["Test", "Back"]
        picked = choose(f"{name}: {status(record(agent_id))}", options)
        if picked in (None, "Back"):
            return
        if picked == "Test":
            test(agent_id)
        else:
            say("\nOpen the link it prints in your browser and approve; Ctrl-C cancels.\n")
            env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY")}
            env["NO_OPEN_BROWSER"] = "1"
            subprocess.run(["cursor-agent", "login"], env=env)
            say("Sign-in now: " + {"file": "in the file Omarchy reads.", "keyring": "still in the keyring.",
                                   "none": "not signed in."}[cursor_storage()])
            test("cursor")
            test("grokbot")
        pause()


def manage_other(agent_id, name):
    rec = record(agent_id)
    clear()
    heading(name)
    say("Omarchy reads this provider from its own app or CLI's sign-in.")
    if rec.get("authHelpText"):
        say("Omarchy says: " + str(rec["authHelpText"])[:400])
    else:
        say("Sign in with that provider's CLI; details are in Omarchy's Agents README:")
        say("  " + os.path.join(os.environ.get("OMARCHY_PATH") or "/usr/share/omarchy", "shell/plugins/agents/README.md"))
    if choose(f"{name}: {status(rec)}", ["Test", "Back"]) == "Test":
        test(agent_id)
        pause()


def main():
    while True:
        ids = provider_ids()
        rows = []
        for agent_id in ids:
            rec = record(agent_id)
            name = str(rec.get("name") or agent_id)[:24]
            ready = bool(rec.get("limits") or rec.get("balance"))
            rows.append((not ready, name.lower(), agent_id, name, status(rec)))
        # Alphabetical by name; ● and ○ still show which are set up.
        gemini_on = bool(widget_setting("geminiProjects", ""))
        rows.append((not gemini_on, "gemini api", "gemini-api", "Gemini API (AI Studio)", gemini_status()))
        rows.sort(key=lambda r: (r[1], r[2]))
        rows.insert(0, (False, "", "display", "Display & alerts", "rings, colours, what prepaid and extra usage show"))
        clear()
        mark = lambda r: "⚙" if r[2] == "display" else "●" if not r[0] else "○"
        labels = {f"{mark(r)} {r[3]:<20} {r[4]}"[:110]: r for r in rows}
        heading("Agent Stats settings and providers  (● shown in the bar  ○ nothing to show yet)")
        picked = choose("Pick a setting or provider (Esc to quit)", list(labels) + ["Quit"])
        if picked in (None, "Quit"):
            return 0
        _, _, agent_id, name, _ = labels[picked]
        if agent_id == "display":
            manage_display()
        elif agent_id in ("cursor", "grokbot"):
            manage_cursor(agent_id, name)
        elif agent_id == "gemini-api":
            manage_gemini()
        elif agent_id in KEYED:
            manage_keys(agent_id, name)
        elif agent_id in ACCOUNTS:
            manage_account(agent_id, name)
        else:
            manage_other(agent_id, name)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
