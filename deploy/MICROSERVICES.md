# Aird microservices

## Topology

Only Caddy (`:8080`) is public. Every backend binds `127.0.0.1`.

| Port | Process | Runtime | Responsibility |
|---:|---|---|---|
| 8000 | `aird-auth` | Granian | Login, sessions, WebAuthn, JWT, `forward_auth` |
| 8001 | `aird-browse` | Granian | UI, static files, file operations, config, bulk WS |
| 8002 | `aird-transfer` | Socketify | Upload/download bodies and disk I/O |
| 8004 | `aird-search` | Granian | Search page and WebSocket |
| 8005 | `aird-shares` | Granian | Share CRUD and public token links |
| 8006 | `aird-p2p` | Granian | P2P page and signaling |
| 8007 | `aird-admin` | Granian | Users, LDAP, ABAC, audit |
| 8080 | `aird-gateway` | Caddy | Public ingress, authentication, routing |

Port 8003 is reserved. Rust is not a service in the default topology.

## Upload path

1. Client sends `POST /upload` or `POST /api/upload/session` to Caddy.
2. Caddy calls `aird-auth:/auth/verify`.
3. On success, Caddy removes client-supplied identity headers and injects
   `X-Aird-User`, `X-Aird-Role`, and `X-Aird-Mesh`.
4. Socketify confines the destination to that user's filesystem root and
   writes the body to disk.

The browser must never connect directly to port 8002.

## Required environment

Production:

```text
AIRD_DEV_OPEN_AUTH=0
AIRD_ALLOW_INSECURE_MESH=0
AIRD_MESH_SECRET=<random secret shared only by Caddy and services>
AIRD_JWT_SECRET=<independent random JWT signing secret>
```

Generate at least 32 random bytes for each secret and keep them different.
Services refuse to start when `AIRD_MESH_SECRET` is missing unless
`AIRD_ALLOW_INSECURE_MESH=1` or `AIRD_DEV_OPEN_AUTH=1` is set.

Optional port overrides:

```text
AIRD_AUTH_PORT=8000
AIRD_BROWSE_PORT=8001
AIRD_TRANSFER_PORT_HTTP=8002
AIRD_SEARCH_PORT=8004
AIRD_SHARES_PORT=8005
AIRD_P2P_PORT=8006
AIRD_ADMIN_PORT=8007
```

These are loopback bind ports. The browser never contacts them directly; every
request reaches the mesh through Caddy on `:8080`.

`AIRD_TRANSFER_PUBLIC_PORT` is separate and browser-facing: it tells the upload
JavaScript to post bodies straight at a transfer port instead of the gateway.
Leave it unset (`0`) behind Caddy, otherwise uploads become cross-origin
requests that carry no mesh secret and fail.

## Windows development

The repository uses the existing uv-managed `.venv`.

```bat
deploy\start-ms.bat
```

The script uses Caddy from `PATH`, falling back to:

```text
D:\caddy_2.11.4_windows_amd64\caddy.exe
```

Validate Caddy manually:

```powershell
$env:AIRD_MESH_SECRET = "dev-mesh-secret"
& "D:\caddy_2.11.4_windows_amd64\caddy.exe" validate --config deploy\Caddyfile
```

## Authentication

- Browser: signed server-side session cookie.
- API/CLI: `POST /auth/token` with username/password returns a 15-minute HS256
  access token and refresh token.
- Renewal: `POST /auth/refresh`.
- Caddy validates cookie or Bearer JWT through `/auth/verify`.
- Services perform authorization after Caddy authentication (user root, role,
  feature flags, ABAC).

## Manual test gate

Run all tests through `http://127.0.0.1:8080`, never backend ports:

1. Login/logout and JWT issue/refresh.
2. Browse, create/rename/delete, upload, and download.
3. Search WebSocket.
4. Create/open/revoke a share.
5. P2P signaling.
6. Admin user and policy pages.
7. Verify direct backend requests without `X-Aird-Mesh` return 401.

Observability and monthly bandwidth quotas are Phase 3 and begin only after
this gate passes.
# Aird microservices (Caddy + Granian + socketify)

## Layout

| Service | Process | Port (default) | Owns |
|---------|---------|----------------|------|
| **Caddy** | `caddy run --config deploy/Caddyfile` | **8080** (edge) | TLS, **authentication**, routing |
| **aird-features** | Granian ASGI | **8001** | tags, search, users, browse UI/API, `/auth/verify` |
| **aird-transfer** | socketify | **8002** | `/upload`, `/files/?download`, upload sessions |
| **aird-transfer native** | Rust listener (optional) | **8003** (=8002+1) | zero-copy chunk bodies |

**Tornado is not the listen server** on this path. The legacy `python -m aird` monolith remains for migration only.

## Auth model

1. **Authentication (Caddy)** — `basicauth` (local) or later `forward_auth` → `aird-features:/auth/verify`.
2. Caddy injects `X-Aird-User`, `X-Aird-Role`, `X-Aird-Mesh`.
3. **Authorization (app)** — features/transfer check identity + path ACLs (stubs today; ABAC later).
4. Bind Granian/socketify to **127.0.0.1** only. Never expose 8001/8002 publicly.

## Quick start (dev)

```powershell
pip install granian
$env:AIRD_MESH_SECRET = "dev-mesh-secret"
$env:AIRD_DEV_OPEN_AUTH = "1"   # forward_auth accepts identity "dev" until real IdP

# three processes — or run deploy\start-ms.bat
python -m aird.ms.features
python -m aird.ms.transfer
caddy run --config deploy/Caddyfile
```

Install Caddy: https://caddyserver.com/docs/install

Probe:

```text
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/health/features
curl http://127.0.0.1:8080/health/transfer
curl http://127.0.0.1:8080/api/ping
```

Turn off open auth for real authn: unset `AIRD_DEV_OPEN_AUTH` and replace `forward_auth` with `basicauth` / OIDC while still setting `X-Aird-User`.

## Health

| URL | Meaning |
|-----|---------|
| `GET /health` | Caddy gateway |
| `GET /health/features` | Granian features |
| `GET /health/transfer` | socketify transfer |
| `GET :8001/health` | direct features (loopback) |
| `GET :8002/health` | direct transfer (loopback) |

## Migration status (Tornado → ASGI)

**In progress — not a complete Tornado deletion.**

| Area | Status |
|------|--------|
| Features HTTP (login, browse, admin UI, shares, file ops APIs) | Starlette routers under `aird/ms/routers/` |
| Upload/download | socketify `aird.ms.transfer` |
| Edge auth / routing | Caddy `deploy/Caddyfile` |
| Super Search / bulk / stream / P2P signal WebSockets | Partial stubs — monolith still needed for full WS |
| Tornado handlers package | Still in tree (legacy monolith) |
| Templates | Still Tornado template syntax via `tornado.template.Loader` |

## Default: ``python -m aird``

Uses **microservices mode** (Granian features on `--port`, socketify transfer on `port+1`).
Legacy single socketify+Tornado process: ``AIRD_MODE=monolith``.

Optional Caddy on `:8080` for edge auth / single origin — see Caddyfile above.


