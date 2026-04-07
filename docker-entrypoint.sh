#!/bin/sh
set -e
# Detect number of processors
CORES=$(nproc 2>/dev/null || echo 1)
if [ "$CORES" -lt 1 ]; then
    CORES=1
fi
echo "Detected $CORES CPU core(s). Starting $CORES aird instance(s)..."

UPSTREAMS=""

for i in $(seq 1 $CORES); do
    PORT=$((7999 + i))
    echo "Starting aird instance $i on port $PORT..."
    python -m aird --port $PORT --multi-user --root "${AIRD_ROOT:-/project_root}" &
    UPSTREAMS="$UPSTREAMS 127.0.0.1:$PORT"
done

# Wait for aird instances to be ready
sleep 3

# Generate actual Caddyfile with load balancing
cat > /etc/caddy/Caddyfile <<EOF
:80 {
    reverse_proxy $UPSTREAMS {
        lb_policy round_robin
    }
}
EOF

# Start Caddy in foreground (gateway on port 80)
echo "Starting Caddy load balancer on port 80..."
exec caddy run --config /etc/caddy/Caddyfile
