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
Browse uses cache-busting query on `app.css` / `browse/app.js`; other pages mostly no version—keep intentional to avoid stale asset bugs.
