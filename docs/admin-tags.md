# Admin — resource tags (ABAC)

**Route:** `GET /admin/tags` — `AdminTagsHandler`; mutations via `AdminTagAPIHandler` on `POST|PUT|DELETE /admin/api/abac/tags` (`abac_handlers.py`).  
**Template:** `admin_tags.html`.

**Behavior:** Create rules (`tag` + `glob_pattern` + priority); bulk/single delete; edit modal; “Create share” posts to `POST /share/create` with `share_type: 'tag'`.

**DB:** Resource tag rules table (see `aird/db/resource_tags.py`).

**Do not break:** JSON body shapes for API; XSRF header `X-XSRFToken` from `_xsrf` cookie in inline scripts.
