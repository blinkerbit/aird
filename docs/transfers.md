# File transfers

How uploads and downloads work in the browser UI, HTTP handlers, and admin settings.

---

## Browser stack

| Script | Role |
|--------|------|
| `transfer-engine/engine.js` | Service worker registration (`sw-transfer.js`), resume metadata |
| `transfer-engine/resume-store.js` | IndexedDB resume state |
| `file-transfer-http.js` | **Primary** upload/download API (`AirdFileTransferHttp`) |
| `file-transfer-ws.js` | Optional WebSocket upload/download (`AirdFileTransferWs`) |
| `transfer-tracker.js` | Sidebar progress UI |
| `download-manager.js` | Multi-file download orchestration |
| `browse/app.js` | Upload queue, drag-and-drop, error messages |

`browse.html` injects `globalThis.__BROWSE_CONFIG` (nonce script) with limits from the server:

| Key | Source constant |
|-----|-----------------|
| `maxFileSize` | `MAX_FILE_SIZE` |
| `largeFileThreshold` | `LARGE_FILE_THRESHOLD_BYTES` |
| `rangeChunkBytes` | `RANGE_CHUNK_BYTES` |
| `rangeUploadConcurrency` | `RANGE_UPLOAD_CONCURRENCY` |
| `rangeDownloadConcurrency` | `RANGE_DOWNLOAD_CONCURRENCY` |
| `rangePipelineDepth` | `RANGE_PIPELINE_DEPTH` |
| `wsChunkBytes` | `WS_CHUNK_BYTES` |

**Hard-refresh** `/files/` after changing admin upload settings so `__BROWSE_CONFIG` matches the server.

---

## Upload routing (`file-transfer-http.js`)

```
file.size >= largeFileThreshold?
  yes → rangedUpload()     # parallel HTTP Content-Range PUTs
  no  → smallUpload()      # single POST /upload
```

Large files use **HTTP parallel ranges only** (WebSocket is not attempted first in the default UI path).

### Small upload

- `POST /upload` with `Content-Type: application/octet-stream`
- Headers: `X-Upload-Dir`, `X-Upload-Filename`, `X-XSRFToken`
- Body: entire file in one request

### Parallel range upload

1. `POST /api/upload/range/session` — JSON `{ upload_dir, filename, total_size }` → `{ upload_id, chunk_bytes, total_size }`
2. Client splits file into `chunk_bytes` slices; up to `rangeUploadConcurrency` **concurrent** `PUT` requests:
   - `PUT /api/upload/range/{upload_id}`
   - Header `Content-Range: bytes {start}-{end}/{total_size}`
   - Body: raw bytes for that range
3. Server writes each range **in place** at `start` in a temp file (`ranged_upload_handlers.py` — no separate part files, no concat copy).
4. When all ranges are received, handler finalizes (`truncate` + `finalize_upload_to_disk`).
5. Resume: `GET /api/upload/range/{id}/status` returns completed ranges.

**Adaptive concurrency:** client halves parallel streams on HTTP 429, then ramps back up.

---

## Download routing

```
HEAD → size
size >= largeFileThreshold? → rangedDownload()  # parallel Range GET
else                        → smallDownload()
```

Parallel downloads fetch `206` responses with `Range` headers and reassemble in the client.

---

## Server handlers

| Route | Handler |
|-------|---------|
| `POST /upload` | `UploadHandler` (`file_op_handlers.py`) |
| `POST /api/upload/range/session` | `RangedUploadSessionHandler` |
| `PUT /api/upload/range/{id}` | `RangedUploadChunkHandler` |
| `GET /api/upload/range/{id}/status` | `RangedUploadStatusHandler` |
| `GET /files/{path}?download=1` | `MainHandler` / `serve_file` |
| `WS /ws/file-transfer` | `FileTransferWebSocketHandler` (optional) |

**Limits enforced server-side:**

- `total_size > MAX_FILE_SIZE` → 413 at session create
- Per-chunk body must match `Content-Range` length
- Chunk larger than `2 × RANGE_CHUNK_BYTES` → 413 (bounds RAM per request)
- Rate limiting via `TransferRateLimiter`

---

## Admin upload settings (`POST /admin`)

Persisted in SQLite via `config_service.save_upload_config`; reloaded with `sync_upload_config_from_db()` on handlers.

| Form field | Config key | Default | Notes |
|------------|------------|---------|--------|
| Max file size (MB) | `max_file_size_mb` | 10240 | Hard cap per file |
| Single-request max (MB) | `single_request_max_mb` | 100 | Files **≥** this use parallel HTTP. **`0`** = use **100 MB** default (proxy-safe), not one POST up to max size |
| HTTP chunk (MB) | `range_chunk_mb` | 90 | Per range `PUT` body |
| HTTP parallelism | `range_upload_concurrency` | 16 | Concurrent upload streams (1–64) |
| WS chunk (MB) | `ws_chunk_mb` | 90 | WebSocket fallback only |

Derived constants (`refresh_upload_derived_constants` in `aird/constants/__init__.py`):

- `RANGE_CHUNK_BYTES` ← `range_chunk_mb`
- `WS_CHUNK_BYTES` ← `ws_chunk_mb`
- `LARGE_FILE_THRESHOLD_BYTES` ← `single_request_max_mb` (or 100 MB when 0)
- `UPLOAD_REQUEST_MAX_BODY_SIZE` ← max(single threshold, chunk + headroom)

---

## Reverse proxy

nginx/Caddy `client_max_body_size` (or equivalent) must be **≥ HTTP chunk size**. Otherwise large single `POST` or range `PUT` bodies return 413 from the proxy with a misleading client message.

Example nginx:

```nginx
client_max_body_size 128m;
```

---

## Free-threaded Python

On `3.13t` / `3.14t`, chunk disk writes run in `asyncio.to_thread` with `os.pwrite` where available. SQLite uses `ThreadSafeConnection` (`aird/db/sync.py`). Multiple parallel range PUTs for the same session write to non-overlapping offsets.

---

## Related

- [browse.md](browse.md) — upload UI entry points
- [admin-settings.md](admin-settings.md) — admin form overview
- [wireguard-deploy.md](wireguard-deploy.md) — production checklist
