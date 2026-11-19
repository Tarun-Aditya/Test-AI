#!/usr/bin/env bash
# One-step setup on macOS and Linux.
#
#   ./setup.sh            install into .venv (Python packages + Playwright's Chromium)
#   ./setup.sh --demo     the same, then fill the database with demo data (accounts, projects, real runs)
#   ./setup.sh --all-browsers   also Firefox and WebKit for Playwright
#
# Then start the hub with ./start.sh and open http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

demo=0; browsers="chromium"
for arg in "$@"; do
  case "$arg" in
    --demo) demo=1 ;;
    --all-browsers) browsers="chromium firefox webkit" ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see ./setup.sh --help)"; exit 2 ;;
  esac
done

PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for c in python3.13 python3.12 python3.11 python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  echo "Python 3.11 or newer is needed (found: ${PY:-none}). Install it from https://www.python.org/downloads/"
  echo "or your package manager, then run ./setup.sh again (or PYTHON=/path/to/python3 ./setup.sh)."
  exit 1
fi
echo "==> Python: $("$PY" --version)"

if [ ! -d .venv ]; then
  echo "==> Creating .venv"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate

echo "==> Installing Python packages"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

echo "==> Installing Playwright browsers: $browsers"
# shellcheck disable=SC2086
if ! python -m playwright install $browsers; then
  echo "Playwright could not install its browsers. On Linux it may need system libraries:"
  echo "  sudo .venv/bin/python -m playwright install-deps $browsers"
  exit 1
fi
if [ "$(uname)" = "Linux" ]; then
  echo "    (on a fresh Linux machine, if a browser won't start: sudo .venv/bin/python -m playwright install-deps)"
fi

if [ "$demo" = 1 ]; then
  echo "==> Loading demo data (runs the example tests; a few minutes)"
  python -m aiauto demo
fi

cat <<'EOF'

Setup done.
  Start the hub:   ./start.sh            (then open http://localhost:8000)
  Demo data:       ./setup.sh --demo     (or: .venv/bin/python -m aiauto demo)
  Guide:           docs/getting-started.md

Selenium finds or downloads the driver for the Chrome / Edge / Firefox you have installed by itself.
EOF
