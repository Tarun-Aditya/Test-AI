#!/usr/bin/env bash
# Start the hub (web app + machine hub) from the .venv that ./setup.sh created.
#
#   ./start.sh                 http://localhost:8000
#   PORT=9000 ./start.sh       another port
#   ./start.sh --host 127.0.0.1   only this computer can reach it (default: every network interface)
#
# The first start prints a one-time setup code in this window: open the web app and enter it to create the admin
# account (with demo data, sign in as admin / autopilot-demo instead). Stop with Ctrl+C.
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Run ./setup.sh first."
  exit 1
fi
exec .venv/bin/python -m aiauto server --port "${PORT:-8000}" "$@"
