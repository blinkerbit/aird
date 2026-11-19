#!/usr/bin/env python3
"""Patch /opt/edge/caddy/Caddyfile for Aird WebSocket bypass."""
from pathlib import Path

p = Path("/opt/edge/caddy/Caddyfile")
text = p.read_text()
if "@aird_ws" in text:
    print("already patched")
    raise SystemExit(0)

marker = "\t# LiveKit SFU"
insert = """\t# Aird WebSockets: session cookies on Aird; Authentik forward_auth breaks Upgrade (302).
\t@aird_ws path /ws/chat /ws/bulk /ws/policy-decisions /p2p/signal /search/ws /features /runtime-config /stream/*
\thandle @aird_ws {
\t\treverse_proxy 127.0.0.1:18080 {
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {host}
\t\t\tflush_interval -1
\t\t\ttransport http {
\t\t\t\tread_timeout 3600s
\t\t\t\twrite_timeout 3600s
\t\t\t}
\t\t}
\t}

"""
if marker not in text:
    raise SystemExit("marker not found in Caddyfile")
p.write_text(text.replace(marker, insert + marker))
print("patched OK")