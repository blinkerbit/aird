#!/usr/bin/env bash
# Kill existing aird services, then start them (aird + caddy by default).
# Usage: ./start.sh [--port 8001] [--no-caddy] [--foreground]

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.aird/run"
LOG_FILE="$RUN_DIR/aird.log"
PID_FILE="$RUN_DIR/aird.pid"
CADDY_PID_FILE="$RUN_DIR/caddy.pid"
CADDY_LOG="$RUN_DIR/caddy.log"

PORT="${AIRD_FEATURES_PORT:-${AIRD_PORT:-8001}}"
WANT_CADDY=1
FOREGROUND=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|-p) PORT="$2"; shift 2 ;;
    --no-caddy) WANT_CADDY=0; shift ;;
    --with-caddy) WANT_CADDY=1; shift ;;
    --foreground|-f) FOREGROUND=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [[ "${AIRD_WITH_CADDY:-}" == "0" ]]; then
  WANT_CADDY=0
fi

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

resolve_python() {
  if [[ -x "$ROOT/.venv/bin/python" ]]; then
    echo "$ROOT/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  if command -v python >/dev/null 2>&1; then
    command -v python
    return
  fi
  echo "python not found (expected .venv/bin/python or python3 on PATH)" >&2
  exit 1
}

mkdir -p "$RUN_DIR"

log "restart: stopping any existing aird services..."
bash "$ROOT/stop.sh" --port "$PORT"
sleep 0.4

PYTHON="$(resolve_python)"
log "using python: $PYTHON"
log "starting aird on port $PORT (transfer=$((PORT + 1)))..."

export AIRD_FEATURES_PORT="$PORT"
export AIRD_TRANSFER_PORT_HTTP="$((PORT + 1))"
export AIRD_DEV_OPEN_AUTH="${AIRD_DEV_OPEN_AUTH:-1}"
export AIRD_MESH_SECRET="${AIRD_MESH_SECRET:-dev-mesh-secret}"

cd "$ROOT"

if [[ "$FOREGROUND" -eq 1 ]]; then
  log "foreground mode (Ctrl+C to stop); caddy not started in this mode"
  exec "$PYTHON" -m aird --port "$PORT"
fi

if [[ -f "$LOG_FILE" ]]; then
  mv -f "$LOG_FILE" "$LOG_FILE.prev" 2>/dev/null || true
fi

nohup "$PYTHON" -m aird --port "$PORT" >"$LOG_FILE" 2>&1 &
aird_pid=$!
echo "$aird_pid" >"$PID_FILE"
log "aird pid $aird_pid written to $PID_FILE"
log "aird log: $LOG_FILE"

if [[ "$WANT_CADDY" -eq 1 ]]; then
  if ! command -v caddy >/dev/null 2>&1; then
    log "WARN: caddy not on PATH - skipped (install caddy or use --no-caddy)"
  else
    if [[ -f "$CADDY_LOG" ]]; then
      mv -f "$CADDY_LOG" "$CADDY_LOG.prev" 2>/dev/null || true
    fi
    nohup caddy run --config "$ROOT/deploy/Caddyfile" >"$CADDY_LOG" 2>&1 &
    caddy_pid=$!
    echo "$caddy_pid" >"$CADDY_PID_FILE"
    log "caddy pid $caddy_pid written to $CADDY_PID_FILE"
    log "caddy log: $CADDY_LOG"
  fi
fi

url="http://127.0.0.1:${PORT}/health"
ok=0
for _ in $(seq 1 30); do
  sleep 0.5
  if ! kill -0 "$aird_pid" 2>/dev/null; then
    log "FAILED: aird exited early. See $LOG_FILE"
    exit 1
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 2 "$url" >/dev/null 2>&1; then
      ok=1
      break
    fi
  fi
done

caddy_ok=0
if [[ "$WANT_CADDY" -eq 1 && -f "$CADDY_PID_FILE" ]] && command -v curl >/dev/null 2>&1; then
  for _ in $(seq 1 10); do
    if curl -fsS --max-time 2 "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
      caddy_ok=1
      break
    fi
    sleep 0.3
  done
fi

if [[ "$ok" -eq 1 ]]; then
  log "OK: aird started (pid $aird_pid) - $url"
  if [[ "$WANT_CADDY" -eq 1 && -f "$CADDY_PID_FILE" ]]; then
    if [[ "$caddy_ok" -eq 1 ]]; then
      log "OK: caddy started - http://127.0.0.1:8080/health"
    else
      log "WARN: caddy pid file present but :8080/health not ready - check $CADDY_LOG"
    fi
  fi
  exit 0
fi

if kill -0 "$aird_pid" 2>/dev/null; then
  if ! command -v curl >/dev/null 2>&1; then
    log "OK: aird process running (pid $aird_pid); curl missing so health not probed"
    exit 0
  fi
  log "WARN: aird process is running (pid $aird_pid) but $url not ready yet - check $LOG_FILE"
  exit 0
fi

log "FAILED: aird did not start"
exit 1
