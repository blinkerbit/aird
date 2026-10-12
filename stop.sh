#!/usr/bin/env bash
# Stop aird services (features, transfer, and caddy).
# Usage: ./stop.sh [--port 8001]

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$ROOT/.aird/run"
PID_FILE="$RUN_DIR/aird.pid"
CADDY_PID_FILE="$RUN_DIR/caddy.pid"

PORT="${AIRD_FEATURES_PORT:-${AIRD_PORT:-8001}}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port|-p) PORT="$2"; shift 2 ;;
    --with-caddy|--no-caddy) shift ;; # accepted for compat; caddy always stopped
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

kill_pid() {
  local pid="$1"
  local label="$2"
  if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]]; then
    return 1
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    log "already stopped: $label (pid $pid)"
    return 0
  fi
  if kill "$pid" 2>/dev/null; then
    sleep 0.2
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
      sleep 0.1
    fi
    if kill -0 "$pid" 2>/dev/null; then
      log "FAILED to kill: $label (pid $pid)"
      return 1
    fi
    log "killed: $label (pid $pid)"
    return 0
  fi
  log "FAILED to kill: $label (pid $pid)"
  return 1
}

pids_on_port() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
  elif command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$port" 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+$' || true
  elif command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$port" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' || true
  fi
}

match_aird_pids() {
  # shellcheck disable=SC2009
  ps -eo pid=,args= 2>/dev/null | while read -r pid args; do
    case "$args" in
      *aird.cli*|*"stop.sh"*|*"start.sh"*) continue ;;
    esac
    if [[ "$args" =~ python([0-9.]*)?\ -m\ aird([.]ms[.](features|transfer))?($|[[:space:]]) ]] \
      || [[ "$args" =~ (^|[[:space:]])aird(-features|-transfer)?($|[[:space:]]) ]] \
      || { [[ "$args" == *granian* ]] && [[ "$args" == *aird* ]]; }; then
      echo "$pid"
    fi
  done
}

log "stopping aird services (features=:$PORT transfer=:$((PORT + 1)) native=:$((PORT + 2)) caddy=:8080)"

killed=0
failed=0

if [[ -f "$PID_FILE" ]]; then
  if kill_pid "$(tr -d '[:space:]' < "$PID_FILE")" "aird"; then
    killed=$((killed + 1))
  else
    failed=$((failed + 1))
  fi
  rm -f "$PID_FILE"
fi

if [[ -f "$CADDY_PID_FILE" ]]; then
  if kill_pid "$(tr -d '[:space:]' < "$CADDY_PID_FILE")" "caddy"; then
    killed=$((killed + 1))
  else
    failed=$((failed + 1))
  fi
  rm -f "$CADDY_PID_FILE"
fi

while read -r pid; do
  [[ -z "$pid" ]] && continue
  if kill_pid "$pid" "aird-match"; then
    killed=$((killed + 1))
  else
    failed=$((failed + 1))
  fi
done < <(match_aird_pids | sort -u)

ports=("$PORT" "$((PORT + 1))" "$((PORT + 2))" 8000 8080)
# unique
ports=($(printf '%s\n' "${ports[@]}" | awk '!a[$0]++'))

for p in "${ports[@]}"; do
  while read -r pid; do
    [[ -z "$pid" ]] && continue
    name="$(ps -p "$pid" -o comm= 2>/dev/null || echo pid)"
    if kill_pid "$pid" "port:$p ($name)"; then
      killed=$((killed + 1))
    else
      failed=$((failed + 1))
    fi
  done < <(pids_on_port "$p" | sort -u)
done

# shellcheck disable=SC2009
ps -eo pid=,args= 2>/dev/null | while read -r pid args; do
  if [[ "$args" == *caddy* && "$args" == *Caddyfile* ]]; then
    if kill_pid "$pid" "caddy"; then
      killed=$((killed + 1))
    else
      failed=$((failed + 1))
    fi
  fi
done

if [[ "$killed" -eq 0 && "$failed" -eq 0 ]]; then
  log "no aird/caddy processes found"
else
  log "stop done: killed=$killed failed=$failed"
fi

if [[ "$failed" -gt 0 ]]; then
  exit 1
fi
exit 0
