# Profile

**Routes:** `GET|POST /profile` — `ProfileHandler` (`aird/handlers/auth_handlers.py`).

**Template:** `profile.html` (`PROFILE_TEMPLATE`).

**Behavior:** Theme select (`.theme-select` + `theme.js`). Password change only when `user` dict present and non-LDAP; token-only sessions show “not available”. May show quota and “Shared with me” from DB.

**Do not change lightly:** Conditional branches for `ldap_enabled` vs local auth; display username via `escape` for template safety.
