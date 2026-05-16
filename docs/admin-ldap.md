# Admin — LDAP

**Routes:**
- `GET /admin/ldap` — `LDAPConfigHandler` → `admin_ldap.html`
- `/admin/ldap/create`, `/admin/ldap/edit/{id}`, `/admin/ldap/delete`, `/admin/ldap/sync` — sibling handlers (`admin_handlers.py`)

**Templates:** `ldap_config_create.html`, `ldap_config_edit.html`.

**Gate:** Tabs show LDAP link only when `ldap_enabled` (`settings ldap_server`). Same flag is injected in `BaseHandler.get_template_namespace()`.

**Do not bypass:** LDAP-only login path in `LoginHandler` vs `LDAPLoginHandler` wired in `main.py` affects `login.html` branch.
