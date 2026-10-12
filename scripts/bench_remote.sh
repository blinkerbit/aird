#!/bin/bash
set -eu
TOKEN=$(grep -oP '--token "\K[^"]+' /etc/systemd/system/aird.service | head -1)
export AIRD_BENCH_TOKEN="$TOKEN"
PY=/opt/aird/.venv/bin/python

echo "=== native extension ==="
$PY -c 'from aird.core.transfer_native import native_available, socket_pump_supported; print("native", native_available(), "pump", socket_pump_supported())'

echo "=== transfer strategy ==="
$PY -c '
import os, sqlite3
from aird.db import init_db
from aird.services.config_service import ConfigService
db = os.environ.get("AIRD_DB", "/opt/aird/.aird/aird.db")
conn = sqlite3.connect(db)
init_db(conn)
for ip in ("127.0.0.1", "192.168.1.1"):
    s = ConfigService().get_runtime_config(conn, ip)
    print(ip, "profile=", s.get("profile"), "transport=", s.get("uploadTransport"), "concurrency=", s.get("uploadConcurrency"))
'

for PORT in 8000 8001; do
  echo "=== bench localhost:$PORT 256MiB ==="
  $PY /tmp/bench_stream_upload.py --url "http://127.0.0.1:${PORT}" --size-mb 256 --label "port-${PORT}" || echo "port ${PORT} failed"
done

LAN_IP=$(hostname -I | awk '{print $1}')
echo "=== bench LAN IP ${LAN_IP}:8001 256MiB ==="
$PY /tmp/bench_stream_upload.py --url "http://${LAN_IP}:8001" --size-mb 256 --label lan-8001 || echo "lan 8001 failed"

echo "=== two parallel uploads on :8001 ==="
$PY /tmp/bench_stream_upload.py --url http://127.0.0.1:8001 --size-mb 256 --label parallel-1 &
PID1=$!
$PY /tmp/bench_stream_upload.py --url http://127.0.0.1:8001 --size-mb 256 --label parallel-2 &
PID2=$!
wait $PID1 || true
wait $PID2 || true

echo "=== recent detach logs ==="
journalctl -u aird -n 50 --no-pager | grep -i detach || true
