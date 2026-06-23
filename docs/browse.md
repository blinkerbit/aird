# Browse (directory listing)

**Route:** `GET /files/{path}` — directory branch of `MainHandler` (`aird/handlers/view_handlers.py`).  
**Template:** `aird/templates/browse.html`  
**Scripts:** `aird-core.js`, transfer stack (see [transfers.md](transfers.md)), `browse/app.js`, `folder-size-scan.js`, `theme.js` (defer).

**Behavior:** Authenticated listing under `get_user_root()`. Builds `files`, `current_path`, `parent_path`, `file_tags_map` from `list_resource_tags` + `get_tags_for_path`; shared-folder markers via `augment_with_shared_status`. Respects SQLite feature flags (`features` dict).

`view_handlers` calls `sync_upload_config_from_db()` before render and passes upload limits into `__BROWSE_CONFIG` (inline nonce script).

---

## Uploads (UI)

- Drag-and-drop or file picker → queue in `browse/app.js`
- Calls `AirdFileTransferHttp.uploadFile()` (see [transfers.md](transfers.md))
- Client-side size check against `__BROWSE_CONFIG.maxFileSize` before queueing
- Progress / cancel: `AirdTransferTracker` sidebar
- Service worker: `sw-transfer.js` (registered from `transfer-engine/engine.js`)

**Related APIs:** `POST /upload`, `POST /api/upload/range/session`, `PUT /api/upload/range/{id}`, `POST /mkdir`, `POST /delete`, `POST /rename`, `POST /copy`, `POST /move`, `POST /api/bulk`, `POST /api/favorites/toggle`, `GET /api/files/…` (folder picker), `POST /share/create`, admin tag modals.

**ABAC:** `check_access("file.list", …)` before listing; upload uses `file.write`.

---

## Do not change without audit

Hidden `currentPath`; `csp_nonce` on inline scripts; `__BROWSE_CONFIG` keys; checkbox/bulk-drawer IDs consumed by `browse/app.js`; `join_path` / `escape` usage in templates; `features` keys expected by toolbar and actions.
