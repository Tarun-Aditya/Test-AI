#!/usr/bin/env bash
# Connect this computer to a hub as an agent (run ./setup.sh first).
#
#   ./agent.sh --server http://HUB:8000 --token <token> [--name my-mac] [--slots 2]
#
# Or put the settings in agent.env (copy agent.env.example) and run ./agent.sh with no options; that is also what
# the autostart services in docs/deployment.md run. Stop with Ctrl+C. The agent reconnects by itself if the hub
# restarts or the network drops.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup.sh first."
  exit 1
fi
if [ -f agent.env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./agent.env
  set +a
fi
args=()
if [ -n "${AGENT_NAME:-}" ]; then args+=(--name "$AGENT_NAME"); fi
exec .venv/bin/python -m aiauto agent ${args[@]+"${args[@]}"} "$@"
