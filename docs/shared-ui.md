# Shared UI & cross-cutting

## Partials  
- **`_app_nav_header.html`** — Expects optional `nav_title`, `nav_search_path`, `show_admin_link`. Uses `current_user`, `handler.get_display_username()`, `is_feature_enabled`, `user` must **not** be used for admin link (use `current_user` + role). Loads `command-palette.js`; theme dropdown uses `data-set-theme` (+ `theme.js` on pages that include it).

- **`_admin_tabs.html`** — Set `admin_active_tab` (`overview`|`users`|`ldap`|`audit`|`network-shares`|`tags`|`policies`|`user-attributes`). Uses `ldap_enabled` from namespace.

- **`_theme_login_corner.html`** — Login-themed pages only.

## Base handler namespace (`base_handler.py`)  
`csp_nonce`, `is_feature_enabled`, defaults for nav + `ldap_enabled`.

## CSP  
Inline scripts with `nonce="{{ csp_nonce }}"` must stay in sync with `render()` CSP header logic.

## Static versioning

Browse and most pages use `?v={{ static_version }}` on CSS/JS (package version + UI fingerprint from `get_static_version()`). Hard-refresh after deploy if assets look stale.

## Service worker

`GET /sw-transfer.js` — registered from `transfer-engine/engine.js` on browse; `updateViaCache: 'none'`. See [transfers.md](transfers.md).

## Security headers (browse / transfers)

`BaseHandler` sets COOP/COEP/CORP for `SharedArrayBuffer` / worker compression paths; CSP includes `worker-src 'self'`.
