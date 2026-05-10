# Login & admin login

## App login  
**Routes:** `GET|POST /login` — `LoginHandler` or `LDAPLoginHandler` (`auth_handlers.py`, selected in `main.py`).

**Template:** `login.html` (`constants.LOGIN_HTML`).

**Behavior (non-LDAP):** `AuthRequest` fields `username`, `password`, `token`. Prefer username/password when both supplied; else `ACCESS_TOKEN` via `secrets.compare_digest`; sets cookies and optional `next` redirect.

**LDAP mode:** LDAP-only form; token block hidden in template.

## Admin login  
**Routes:** `GET|POST /admin/login` — `AdminLoginHandler`.  
**Template:** `admin_login.html`. Two POST forms: credentials (must be admin user) vs `ADMIN_TOKEN`.

**Do not break:** XSSF via `xsrf_form_html()`; cookie names and secure flags set in handlers; safe `next` validation where applied.
