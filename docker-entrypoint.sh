#!/bin/sh
set -e

# Start aird in background (listens on 127.0.0.1:8000)
python -m aird --port 8000 &
AIRD_PID=$!

# Wait for aird to be ready
sleep 3

# Start Caddy in foreground (gateway on port 80)
exec caddy run --config /etc/caddy/Caddyfile
