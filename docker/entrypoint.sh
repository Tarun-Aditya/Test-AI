#!/usr/bin/env bash
# hub  [--port 8000]                       -> web app + agent hub, data in /data
# agent --server URL [--name N] [--token T] -> execution agent (headed runs go to a virtual display)
# anything else                            -> run as a command (e.g. python -m aiauto run -f test.txt)
set -e
export AIAUTO_CHROMEDRIVER="${AIAUTO_CHROMEDRIVER:-$(cat /opt/chromedriver-path 2>/dev/null)}"

# virtual screen so "headed" runs work without a monitor (Xvfb ships with the base image).
# Xvfb picks a free display itself (-displayfd) and reports it once it accepts connections, so a display
# already in use (e.g. --network host next to another X server) is never a problem.
if [ -z "$DISPLAY" ] && [ -z "$NO_XVFB" ]; then
  fifo=$(mktemp -u) && mkfifo "$fifo"
  Xvfb -displayfd 3 -screen 0 "${XVFB_SCREEN:-1920x1080x24}" -nolisten tcp 3>"$fifo" >/tmp/xvfb.log 2>&1 &
  if read -r -t 15 num <"$fifo"; then
    export DISPLAY=":$num"
    echo "virtual display $DISPLAY ready (headed runs)" >&2
  else
    echo "warning: Xvfb did not start; only headless runs will work (see /tmp/xvfb.log)" >&2
  fi
  rm -f "$fifo"
fi

mkdir -p /data
cd /data
case "$1" in
  hub|server)
    shift
    export AIAUTO_ARTIFACTS="${AIAUTO_ARTIFACTS:-/data/artifacts}"
    exec python3 -m aiauto server --host 0.0.0.0 --port "${PORT:-8000}" "$@" ;;
  agent)
    shift
    exec python3 -m aiauto agent --out /data/agent_artifacts "$@" ;;
  *)
    exec "$@" ;;
esac
