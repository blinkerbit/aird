# Error page

**Template:** `error.html`  
**Used from:** `BaseHandler.write_error` and explicit `404` in `catch-all`/`MainHandler`-related paths (`view_handlers.py`).

**Behavior:** Shows `status_code` and `error_message`; links to `/files/` and `/`.

**Styling:** Centered card; `theme.js` for persisted theme.
