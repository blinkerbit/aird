#!/usr/bin/env python3
"""Add public /.well-known/assetlinks.json bypass to edge Caddyfile."""
from pathlib import Path

p = Path("/opt/edge/caddy/Caddyfile")
text = p.read_text()
if "@aird_assetlinks" in text:
    print("assetlinks route already present")
    raise SystemExit(0)

marker = "\t@pwa path"
insert = """\t@aird_assetlinks path /.well-known/assetlinks.json
\thandle @aird_assetlinks {
\t\treverse_proxy 127.0.0.1:18080 {
\t\t\theader_up X-Forwarded-Proto https
\t\t\theader_up X-Forwarded-Host {host}
\t\t}
\t}

"""
if marker not in text:
    raise SystemExit("pwa marker not found")
p.write_text(text.replace(marker, insert + marker))
print("assetlinks bypass added")