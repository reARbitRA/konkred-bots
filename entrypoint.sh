#!/usr/bin/env bash
# Supervise both runtimes in one container. If either exits, stop the other so
# the hosting platform can restart a clean, complete service.
set -uo pipefail

GATEWAY_PID=""
BOTS_PID=""
stopping=0

terminate() {
  if (( stopping )); then
    return
  fi
  stopping=1
  [[ -n "$BOTS_PID" ]] && kill -TERM "$BOTS_PID" 2>/dev/null || true
  [[ -n "$GATEWAY_PID" ]] && kill -TERM "$GATEWAY_PID" 2>/dev/null || true
}

trap terminate INT TERM

# Keep the gateway private. The public Python process is the only service bound
# to the platform's PORT and reaches Node over loopback.
HOST=127.0.0.1 PORT="${GATEWAY_PORT:-3000}" \
  node /app/gateway/src/server.mjs &
GATEWAY_PID=$!

cd /app/bots
python main.py &
BOTS_PID=$!

# Bash 5's wait -n returns as soon as either supervised service exits.
set +e
wait -n "$GATEWAY_PID" "$BOTS_PID"
status=$?
set -e

terminate
wait "$BOTS_PID" 2>/dev/null || true
wait "$GATEWAY_PID" 2>/dev/null || true
exit "$status"
