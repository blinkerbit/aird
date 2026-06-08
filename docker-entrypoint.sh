#!/bin/sh
set -e
# P2P rooms live in process memory — multiple aird instances behind round-robin break
# anonymous share links (sender on worker A, recipient hits worker B). Default to one
# instance; set AIRD_DOCKER_INSTANCES>1 only if you accept broken P2P or add shared state.
CORES="${AIRD_DOCKER_INSTANCES:-1}"
if [ "$CORES" -lt 1 ]; then
    CORES=1
fi
if [ "$CORES" -gt 1 ]; then
    echo "WARNING: $CORES aird instances — in-memory P2P rooms are not shared across workers."
fi
echo "Starting $CORES aird instance(s)..."

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
