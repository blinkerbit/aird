# Full-page editor

**Route:** `GET /edit/{path}` — `EditViewHandler` (`view_handlers.py`).  
**Template:** `aird/templates/edit.html`  
**Save:** `POST /edit` — `EditHandler` accepts JSON `{path, content}` or form-urlencoded (see handler).

**UI:** Separate from main nav chrome; legacy `--ds-*` CSS variables intentional for this page’s toolbar/editor chrome. Uses `AirdCore.getXSRFToken()` for saves.

**ABAC / features:** Writes go through handlers that enforce `file.edit` / ABAC rules.

**Do not change lightly:** Ctrl/Cmd+S handler; dirty `beforeunload` warning; JSON vs form POST contract if external clients exist.
