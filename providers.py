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


# ------------------------------------------------------------------ ui bits

def say(text=""):
    print(text, flush=True)


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


def manage_other(agent_id, name):
    rec = record(agent_id)
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
        rows.sort()
        labels = {f"{'●' if not r[0] else '○'} {r[3]:<20} {r[4]}"[:110]: r for r in rows}
        heading("Agent Stats providers  (● shown in the bar  ○ nothing to show yet)")
        picked = choose("Pick a provider to set up (Esc to quit)", list(labels) + ["Quit"])
        if picked in (None, "Quit"):
            return 0
        _, _, agent_id, name, _ = labels[picked]
        if agent_id in KEYED:
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
