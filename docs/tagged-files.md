# Tagged files (by resource tag)

**Route:** `GET /tagged/{tag_name}` — `TaggedFilesHandler` (`view_handlers.py`).  
**Template:** `tagged_files.html`.

**Behavior:** Resolves globs from SQLite resource-tag rules (`get_files_by_tag_patterns` path); lists matching file paths relative to configurable roots; warns if tag has no patterns.

**Uses:** `_app_nav_header.html`; include `theme.js` with other app pages so nav theme controls work (see `tagged_files.html`).

**ABAC relevance:** Tags are resource attributes consumed by PDP conditions (`tag_present` etc.); pattern logic lives in `aird/core/file_operations.py` / `aird/db/resource_tags.py`.
