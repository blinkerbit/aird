# WireGuard + TLS deployment

Serve Aird only to WireGuard peers. Application login (username/password) and TLS remain required.

## Network layout

```
Client --UDP(WireGuard)--> Server wg0 (10.8.0.0/24) --TLS--> Tornado :443
```

1. Expose **UDP 51820** (WireGuard) publicly.
2. Block public **TCP 443**; clients reach HTTPS only over the VPN.
3. Install TLS cert (internal CA); distribute CA to WG clients.
4. Set `hostname` in config to the name clients use (e.g. `aird.vpn`).

## Recommended config (`config.json`)

```json
{
  "port": 443,
  "hostname": "aird.vpn",
  "ssl_cert": "/etc/aird/certs/fullchain.pem",
  "ssl_key": "/etc/aird/private/key.pem",
  "workers": 2
}
```

On a 16 GB / i7-7700HQ host, start with **2 workers** and increase to 4 if CPU allows.

Use **free-threaded** Python (`3.14t`) on Linux for parallel range upload disk I/O when available.

## Transfers

Thresholds are **admin-configurable** (Admin → Upload settings). Defaults:

| File size | Upload | Download |
|-----------|--------|----------|
| &lt; **100 MB** | `POST /upload` (single stream) | `GET /files/...?download=1` |
| ≥ **100 MB** | Parallel `PUT /api/upload/range/{id}` with `Content-Range` | Parallel `GET` with `Range` (206) |

Defaults for parallel mode: **90 MB** chunks, **16** concurrent streams. See [transfers.md](transfers.md).

Constants: `LARGE_FILE_THRESHOLD_BYTES`, `RANGE_CHUNK_BYTES`, `RANGE_UPLOAD_CONCURRENCY` in `aird/constants/__init__.py` (derived from `UPLOAD_CONFIG`).

**nginx/Caddy:** set `client_max_body_size` ≥ HTTP chunk size (e.g. `128m`).

## Deploy from Windows

`deploy_local.ps1` syncs source or a wheel to Ubuntu, creates a **uv** venv with **Python 3.14t**, and installs the package.

## Firewall (example)

```bash
ufw allow 51820/udp
ufw deny 443/tcp
# Allow 443 only on wg0 if binding to all interfaces
```

## Checklist

- [ ] WireGuard peers configured
- [ ] TLS trusted by clients
- [ ] `workers` set (2–4)
- [ ] `max_file_size_mb` ≥ largest expected file
- [ ] `single_request_max_mb` = 100 (not 0 unless you understand proxy limits)
- [ ] Reverse proxy body limit ≥ HTTP chunk size
- [ ] Redeploy; hard-refresh browsers after admin changes
