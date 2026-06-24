# Admin — settings overview

**Routes:** `GET|POST /admin` — `AdminHandler` (`admin_handlers.py`).  
**Template:** `admin.html`  
**Auth:** Authenticated admin (`require_admin` patterns); separate `admin` cookie path from optional `/admin/login` flow.

---

## Overview tab

**Feature flags** (persisted to SQLite, broadcast on save):

| Flag | Purpose |
|------|---------|
| `file_upload` / `file_delete` / `file_rename` / `file_download` / `file_edit` | Core file ops |
| `file_share` | Share UI |
| `super_search` | `/search` |
| `compression` | HTTP `Content-Encoding` on eligible downloads |
| `p2p_transfer` | `/p2p` |
| `folder_create` / `folder_delete` | Directory ops |
| `allow_simple_passwords` | Password policy |
| `abac_engine` / `abac_audit_decisions` | ABAC PDP + audit stream |
| `webauthn` | Passkey login |
| `smb_server` / `webdav_server` | Embedded network shares |

**WebSocket pool limits** (`WEBSOCKET_CONFIG`): max connections and idle timeouts for feature-flag, file-streaming, and search sockets.

**Upload settings** — see [transfers.md](transfers.md) for full behavior:

| Field | Key | Default |
|-------|-----|---------|
| Max file size (MB) | `max_file_size_mb` | 10240 |
| Single-request max (MB) | `single_request_max_mb` | 100 (`0` → 100 MB parallel threshold) |
| HTTP chunk (MB) | `range_chunk_mb` | 90 |
| HTTP parallelism | `range_upload_concurrency` | 16 |
| WS chunk (MB) | `ws_chunk_mb` | 90 |
| Allow all file types | `allow_all_file_types` | off + extension checklist |

**Extension allow-list:** when “allow all” is off, checked extensions persist to `UPLOAD_ALLOWED_EXTENSIONS`.

---

## Post-save

Calls `FeatureFlagSocketHandler.send_updates()` so `/features` WebSocket subscribers refresh. Upload constants recomputed via `refresh_upload_derived_constants()`.

**Related:** `GET /admin/websocket-stats` — plain JSON pool stats (linked from Overview).

**Also:** `_admin_tabs.html` partial; max width classes align with tab bar (`max-w-4xl` here vs `max-w-6xl` on some sub-pages).

---

## Do not rename casually

Form field names map to persisted settings in `AdminHandler.post`; mismatches silently drop updates.
