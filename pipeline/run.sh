#!/usr/bin/env bash
# Start the Object Talk webapp: FastAPI backend on :8765 + Vite dev server on :5180.
# Usage:
#   ./run.sh         # start both, Ctrl+C stops both
#   ./run.sh up      # same as above
#   ./run.sh stop    # kill anything already running and exit
#   ./run.sh status  # report what's running

set -u
cd "$(dirname "$0")"

BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-5180}"

kill_existing() {
  pkill -f 'webapp.py' 2>/dev/null || true
  pkill -f 'web/node_modules/.bin/vite' 2>/dev/null || true
  pkill -f '/web .* vite' 2>/dev/null || true
  # Give them a moment to release the ports
  for i in 1 2 3 4 5; do
    if ! ss -ltn 2>/dev/null | grep -qE ":(${BACKEND_PORT}|${FRONTEND_PORT})\b"; then
      break
    fi
    sleep 0.3
  done
}

status() {
  echo "Backend  :${BACKEND_PORT}  $(ss -ltn 2>/dev/null | grep -qE ":${BACKEND_PORT}\b" && echo 'UP' || echo 'down')"
  echo "Frontend :${FRONTEND_PORT}  $(ss -ltn 2>/dev/null | grep -qE ":${FRONTEND_PORT}\b" && echo 'UP' || echo 'down')"
}

cleanup() {
  trap '' INT TERM
  echo
  echo "→ stopping servers..."
  [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  # Children might be group leaders (npm spawns node)
  [ -n "${BACKEND_PID:-}" ] && kill -- -"$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill -- -"$FRONTEND_PID" 2>/dev/null || true
  # Final sweep in case grandchildren survived
  pkill -f 'webapp.py' 2>/dev/null || true
  pkill -f 'web/node_modules/.bin/vite' 2>/dev/null || true
  echo "  stopped."
  exit 0
}

start() {
  kill_existing
  echo "→ starting backend (FastAPI) on :${BACKEND_PORT}..."
  PORT="$BACKEND_PORT" python3.13 webapp.py >/tmp/objtalk-backend.log 2>&1 &
  BACKEND_PID=$!
  # Wait briefly for backend to bind
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sS --max-time 1 "http://localhost:${BACKEND_PORT}/api/runs" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  echo "→ starting frontend (Vite) on :${FRONTEND_PORT}..."
  (cd web && npm run dev) >/tmp/objtalk-frontend.log 2>&1 &
  FRONTEND_PID=$!
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sS --max-time 1 "http://localhost:${FRONTEND_PORT}/" >/dev/null 2>&1; then
      break
    fi
    sleep 0.5
  done

  echo
  echo "=============================================="
  echo "  Frontend: http://localhost:${FRONTEND_PORT}/"
  echo "  Backend:  http://localhost:${BACKEND_PORT}/"
  echo "  Logs:     /tmp/objtalk-backend.log"
  echo "            /tmp/objtalk-frontend.log"
  echo "  Press Ctrl+C to stop both."
  echo "=============================================="

  trap cleanup INT TERM
  # Block until either child exits, then trigger cleanup
  wait -n 2>/dev/null
  cleanup
}

case "${1:-up}" in
  up|start|"")
    start
    ;;
  stop|down|kill)
    kill_existing
    echo "→ stopped."
    ;;
  status)
    status
    ;;
  restart)
    kill_existing
    start
    ;;
  *)
    echo "Usage: $0 [up|stop|status|restart]"
    exit 1
    ;;
esac
