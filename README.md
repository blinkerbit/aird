# Aird

<p align="center">
  <img src="aird/static/img/logo-full.svg" alt="Aird" width="280">
</p>

![Aird demo](./demo.webp)

**Aird** is a self-hosted file browser, editor, and sharing platform built on **Python** and **Tornado**. It targets fast local and LAN use: parallel HTTP transfers for large files, real-time log streaming, content search, secure shares, optional multi-user isolation, and an admin console that applies settings without restarts.

---

## Highlights

| Area | What you get |
|------|----------------|
| **File manager** | Browse, upload, download, rename, move, copy, bulk ops, in-browser text edit, ZIP download |
| **Large transfers** | Parallel **HTTP `Content-Range`** uploads/downloads (primary path for big files) |
| **Search** | **Super Search** — glob + regex content search with live WebSocket progress |
| **Streaming** | Tail log files over WebSocket with filters |
| **Sharing** | Token-based public/private shares, static or live folder views |
| **Security** | CSRF, CSP, path traversal checks, optional **ABAC** policies, WebAuthn, LDAP |
| **Integrations** | Google Drive / OneDrive browse, P2P WebRTC rooms, optional embedded SMB & WebDAV |
| **Ops** | SQLite-backed settings, audit log, feature flags, transfer rate limits, health endpoint |

---

## File transfers

Large uploads are designed for **high-throughput links** (e.g. gigabit LAN or VPN):

1. **Small files** — single `POST /upload`.
2. **Large files** — client opens a range session (`POST /api/upload/range/session`), then sends many parallel **`PUT`** requests with `Content-Range`. Each chunk is written **in place** at the correct byte offset on disk (no separate part files, no concat step at the end).
3. **Downloads** — `GET /files/...?download=1` with optional `Range` for parallel fetch.

Defaults (all adjustable in **Admin → Upload settings**):

| Setting | Default | Notes |
|---------|---------|--------|
| Max file size | 10 240 MB (10 GiB) | Hard cap per file |
| Single-request max | 100 MB | Files **≥** this use parallel HTTP ranges |
| HTTP chunk size | 90 MB | Per range `PUT` body |
| HTTP parallelism | 16 | Concurrent upload streams |

**Behind nginx/Caddy:** set `client_max_body_size` (or equivalent) to at least your HTTP chunk size. If **Single-request max** is `0`, Aird uses a **100 MB** parallel threshold (proxy-safe), not a single POST for the entire max file size.

Optional WebSocket upload (`/ws/file-transfer`) remains for specialized paths; the browser UI uses **HTTP parallel** by default.

Transfer progress, cancel, and resume metadata are handled in the browser (`transfer-tracker.js`, `transfer-engine/`, service worker `sw-transfer.js`).

---

## Quick start

### Install

```bash
pip install aird

# Optional: HTTP response compression codecs (gzip is always available)
pip install "aird[compress]"

# From source
git clone https://github.com/blinkerbit/aird.git
cd aird
pip install -e .
```

**Python:** 3.10+ required. On Linux, **free-threaded** builds (`3.13t` / `3.14t`) are supported and recommended for parallel disk I/O; the server detects nogil at startup and sizes the I/O thread pool accordingly.

### Run

```bash
# Default: port 8000, current directory as root
python -m aird

# Custom root and port
python -m aird --root /data --port 8080

# Multi-user (per-user home directories under root)
python -m aird --root /data --multi-user

# TLS
python -m aird --ssl-cert /path/cert.pem --ssl-key /path/key.pem --port 443

# Worker processes (Linux; default is auto from CPU topology)
python -m aird --workers 4
```

On first start, random **access** and **admin** tokens are printed unless you set them via `--token`, `--admin-token`, `config.json`, or `AIRD_ACCESS_TOKEN`.

Open `http://localhost:8000/` → redirects to `/files/`.

### CLI client

```bash
pip install aird   # includes aird-cli
aird-cli config set server https://your-host
aird-cli login
aird-cli ls /
```

---

## Configuration

### `config.json`

```json
{
  "port": 8000,
  "root": "/srv/files",
  "hostname": "files.example.com",
  "token": "your-access-token",
  "admin_token": "your-admin-token",
  "multi_user": false,
  "workers": 2,
  "ldap": {
    "enabled": false,
    "server": "ldap://ldap.example.com",
    "base_dn": "dc=example,dc=com"
  },
  "feature_flags": {
    "file_upload": true,
    "super_search": true,
    "abac_engine": false
  }
}
```

```bash
python -m aird --config /etc/aird/config.json
```

### Environment variables (common)

| Variable | Purpose |
|----------|---------|
| `AIRD_ACCESS_TOKEN` | Login token |
| `AIRD_COOKIE_SECRET` | Persistent session signing (set in production) |
| `AIRD_CORPORATE_IP_CIDRS` | Comma-separated CIDRs for ABAC / WAN compression rules |
| `AIRD_GDRIVE_ACCESS_TOKEN` / `AIRD_ONEDRIVE_ACCESS_TOKEN` | Cloud providers |

### Admin console

`GET /admin` — feature flags, upload limits, extension allow-list, WebSocket pool limits, LDAP, network shares, ABAC policies/tags, users, audit. Changes persist to SQLite and apply without restart (feature-flag subscribers refresh over `/features` WebSocket).

---

## Production deployment

### Reverse proxy

Aird listens on one port (default **8000**). Terminate TLS at **Caddy**, **nginx**, or similar.

**nginx example** (adjust chunk size to match Admin → HTTP chunk):

```nginx
client_max_body_size 128m;

location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

WebSocket routes (`/stream/`, `/search/ws`, `/features`, `/ws/…`) need `Upgrade` and `Connection` headers if you proxy them.

### Ubuntu deploy script

`deploy_local.ps1` (run from Windows) syncs source or a wheel to a remote host, creates a **uv** venv with **Python 3.14t**, and installs the package. See `docs/wireguard-deploy.md` for VPN-only TLS layouts.

### Systemd

Run `python -m aird` (or the venv equivalent) as a service user with `--config` pointing at your JSON file. Logs go to the data directory (`aird.log` under the platform app data path).

---

## API overview

Authentication: session cookie after `/login`, bearer token, or `Authorization` header where supported. Mutating requests require the `_xsrf` cookie + `X-XSRFToken` header.

| Operation | Method / path |
|-----------|----------------|
| List directory | `GET /api/files/{path}` |
| Upload (small) | `POST /upload` |
| Upload (large) | `POST /api/upload/range/session` then `PUT /api/upload/range/{id}` |
| Upload status | `GET /api/upload/range/{id}/status` |
| Download | `GET /files/{path}?download=1` |
| Edit | `GET /edit/{path}`, `POST /edit` |
| Delete / rename / mkdir | `POST /delete`, `POST /rename`, `POST /mkdir` |
| Search UI | `GET /search` |
| Search (live) | WebSocket `/search/ws` |
| Log stream | WebSocket `/stream/{path}` |
| Shares | `POST /share/create`, `GET /share/list`, … |
| Health | `GET /health` |

Page-level UI contracts and routes are documented under [`docs/`](docs/README.md).

---

## Architecture notes

- **Async I/O:** Tornado event loop; on Linux optionally **uvloop**.
- **Free-threaded Python:** SQLite access is wrapped (`aird/db/sync.py`); upload chunk writes use `asyncio.to_thread` / `os.pwrite` so parallel range PUTs do not block the loop.
- **HTTP compression:** `gzip` by default; optional `zstandard` via `pip install aird[compress]` (loaded only on builds where the extension is nogil-safe).
- **Security headers:** COOP/COEP/CORP for transfer workers; strict CSP on HTML pages.
- **ABAC:** Optional policy engine (`abac_engine` flag) with admin-defined policies, tags, and user attributes.

---

## Development

### Frontend assets

```bash
npm install
npm run css:build          # Tailwind → aird/static/css/app.css
npm run js:share           # Bundle share UI
npm run vendor:fflate      # Compression worker dependency
```

### Tests

```bash
python -m pytest tests/
```

### Project layout

```
aird/
  handlers/       # HTTP & WebSocket handlers
  services/       # Config, quota, share, audit, …
  static/js/      # Browser UI & transfer engine
  templates/      # Jinja2 pages
  core/           # Compression, mmap, rate limits, …
docs/             # Per-page UI & admin documentation
```

---

## Security

- Path traversal and symlink checks on file access
- Argon2 password hashing for local users
- CSRF on state-changing requests
- Upload extension allow-list (or allow-all) with max size enforced server-side
- Optional storage quotas (multi-user)
- Audit trail and live policy-decision stream for ABAC

Report issues via GitHub. For production, set `AIRD_COOKIE_SECRET`, use TLS, and restrict admin routes.

---

## License

**Business Source License 1.1 (BSL)** — see [LICENSE](LICENSE). Converts to **Apache 2.0** after the change date in the license file.

Commercial **File-Management-as-a-Service** use requires a separate license. Contact **Viswanatha Srinivas P**.

---

## Links

- **Repository:** https://github.com/blinkerbit/aird  
- **PyPI:** https://pypi.org/project/aird/  
- **UI / admin docs:** [docs/README.md](docs/README.md) · [docs/transfers.md](docs/transfers.md)  
- **Issues:** https://github.com/blinkerbit/aird/issues  

---

Made by **Viswanatha Srinivas P**
