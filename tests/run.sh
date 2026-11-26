#!/bin/bash
# Run every test: bash tests/run.sh. Offline; uses temporary folders and
# stand-in commands only, never your real keys, config or the network.
set -euo pipefail
cd "$(dirname "$0")"
bash -n ../launch.sh
python3 -c 'import json; json.load(open("../manifest.json"))'
python3 -B -m unittest discover -s . -p 'test_*.py' "$@"
