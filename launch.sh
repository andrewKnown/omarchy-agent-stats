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

real=$(command -v omarchy-default-agent) || exec omarchy-agent --pick
shim=$(mktemp -d "${XDG_RUNTIME_DIR:-/tmp}/agent-stats.XXXXXX")
trap 'rm -rf "$shim"' EXIT

cat >"$shim/omarchy-default-agent" <<EOF
#!/bin/bash
if ((\$# == 0)); then echo "$agent"; else exec "$real" "\$@"; fi
EOF
chmod +x "$shim/omarchy-default-agent"

PATH="$shim:$PATH" omarchy-agent
