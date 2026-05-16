# Admin — settings overview

**Routes:** `GET|POST /admin` — `AdminHandler` (`admin_handlers.py`).  
**Template:** `admin.html`  
**Auth:** Authenticated admin (`require_admin` patterns); separate `admin` cookie path from optional `/admin/login` flow.

**Behavior:** Saves feature toggles (incl. `abac_engine`, `abac_audit_decisions`), upload limits, extension allow-list, WebSocket pool settings. Inline script toggles allowed-extensions section visibility.

**Post-save:** Calls `FeatureFlagSocketHandler.send_updates()` so `/features` subscribers refresh.

**Also:** `_admin_tabs.html` partial; max width classes align with tab bar (`max-w-4xl` here vs `max-w-6xl` on some sub-pages — intentional looseness OK).

**Related:** `GET /admin/websocket-stats` serves **plain JSON** (no HTML)—linked from Overview tab “WebSocket stats ↗”.

**Do not rename casually:** Form field names map to persisted settings in handler; mismatches silently drop updates.
