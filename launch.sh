#!/bin/bash
# Launch one specific agent through Omarchy's own launcher (omarchy-agent), so
# it gets the same terminal, permission flags and active account as the
# Super-key launch. omarchy-agent asks `omarchy-default-agent` which agent to
# run; a one-off shim earlier on PATH answers with the agent that was clicked.
# Your saved default is never changed. If a future Omarchy stops asking that
# command, this degrades to launching your default agent.
set -euo pipefail

agent=${1:-}
[[ $agent =~ ^[a-z][a-z0-9-]{0,23}$ ]] || exit 1
fallback=${2:-}
# Not installed: open the provider's page (an https URL the widget passes) or
# Omarchy's Agents panel, instead of a click that shows nothing.
if ! command -v "$agent" >/dev/null; then
  if [[ $fallback =~ ^https://[A-Za-z0-9./?=_-]+$ ]]; then
    exec xdg-open "$fallback"
  fi
  exec omarchy-shell omarchy.agents toggle
fi

# Cursor CLI builds from late 2026 move their sign-in into the desktop keyring
# whenever they start inside a desktop session, which Omarchy 4.5 can't read
# yet (omacom/omarchy#19559). While the sign-in sits in auth.json (signed in
# with the display hidden, the documented workaround), start Cursor the same
# way Omarchy does but with the display hidden from it, so launching it from
# the bar doesn't move the sign-in back and blank its usage. Once the sign-in
# is in the keyring this does nothing and Omarchy's launcher runs as usual.
if [[ $agent == cursor-agent ]] && command -v omarchy-launch-tui >/dev/null &&
  python3 -I -c 'import json,os,sys
d = json.load(open(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "cursor", "auth.json")))
sys.exit(0 if isinstance(d, dict) and d.get("accessToken") and not d.get("storedIn") else 1)' 2>/dev/null; then
  [[ $PWD == "$HOME" || ! -d $PWD ]] && [[ -d $HOME/Work ]] && cd "$HOME/Work"
  exec omarchy-launch-tui --app-id=org.omarchy.agent env -u WAYLAND_DISPLAY -u DISPLAY cursor-agent --yolo --trust
fi

real=$(command -v omarchy-default-agent) || exec omarchy-agent --pick
shim=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/agent-stats.XXXXXX")
trap 'rm -rf "$shim"' EXIT

cat >"$shim/omarchy-default-agent" <<EOF
#!/bin/bash
if ((\$# == 0)); then echo "$agent"; else exec "$real" "\$@"; fi
EOF
chmod +x "$shim/omarchy-default-agent"

PATH="$shim:$PATH" omarchy-agent
