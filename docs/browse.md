# Browse (directory listing)

**Route:** `GET /files/{path}` — directory branch of `MainHandler` (`aird/handlers/view_handlers.py`).  
**Template:** `aird/templates/browse.html`  
**Script:** `/static/js/browse/app.js?v=…`, `/static/js/aird-core.js`, `theme.js` (defer).

**Behavior:** Authenticated listing under `get_user_root()`. Builds `files`, `current_path`, `parent_path`, `file_tags_map` from `list_resource_tags` + `get_tags_for_path`; shared-folder markers via `augment_with_shared_status`. Respects SQLite feature flags (`features` dict).

**Related APIs:** `POST /upload`, `POST /mkdir`, `POST /delete`, `POST /rename`, `POST /copy`, `POST /move`, `POST /api/bulk`, `POST /api/favorites/toggle`, `GET /api/files/…` (folder picker), `POST /share/create`, admin tag modals hitting ABAC/tag APIs.

**ABAC:** `check_access("file.list", …)` before listing.

**Do not change without audit:** Hidden `currentPath`; `csp_nonce` on inline scripts; checkbox/bulk-drawer IDs consumed by `browse/app.js`; `join_path` / `escape` usage in templates; `features` keys expected by toolbar and actions.
