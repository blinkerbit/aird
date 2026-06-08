# File view (inline / raw / streaming)

**Route:** `GET /files/{path}` — file branch of `MainHandler.serve_file` (`view_handlers.py`).  
**Template:** `aird/templates/file.html`  
**Loads:** `aird-core.js` (head), inline nonce script for fetch/WebSocket logic, `theme.js`.

**Modes:** Normal HTML view (`file.html`), `?download=1`, `?mode=raw` (media viewer & client decode), optional `open_editor`; line range via `start_line` / `end_line` with `DEFAULT_FILE_VIEW_LINE_LIMIT`.

**Sockets:** `WS /features` for toggling `[data-feature]` buttons; `WS /stream/{path}` with optional `filter`, `n` for live log streaming.

**ABAC:** Enforced on read/stream actions via base handler PEP (alongside features).

**Do not change without audit:** `#file-content` table structure expected by DOM script; MIME/disposition semantics in `_serve_*` helpers; CSP nonce on inline script; pairing of gutter + content cells for stream mode.
