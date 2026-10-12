# Admin — LDAP

**Routes:**
- `GET /admin/ldap` — `LDAPConfigHandler` → `admin_ldap.html`
- `/admin/ldap/create`, `/admin/ldap/edit/{id}`, `/admin/ldap/delete`, `/admin/ldap/sync` — sibling handlers (`admin_handlers.py`)

**Templates:** `ldap_config_create.html`, `ldap_config_edit.html`.

**Gate:** Tabs show LDAP link only when `ldap_enabled` (`settings ldap_server`). Same flag is injected in `BaseHandler.get_template_namespace()`.

**Do not bypass:** LDAP-only login path in `LoginHandler` vs `LDAPLoginHandler` wired in `main.py` affects `login.html` branch.

## Production LDAP (LDAPS)

Login and group sync call `make_ldap_server()` (`aird/core/ldap_server.py`). There is no switch to disable certificate checks.

| Key | CLI | Meaning |
|-----|-----|---------|
| `ldap` | `--ldap` | Turn LDAP login on |
| `ldap_server` | `--ldap-server` | Server URL |
| `ldap_base_dn` | `--ldap-base-dn` | Search base |
| `ldap_user_template` | `--ldap-user-template` | Bind DN, `{username}` placeholder |
| `ldap_filter_template` | `--ldap-filter-template` | Search filter |
| `ldap_attributes` | `--ldap-attributes` | Comma-separated attribute list |
| `ldap_attribute_map` | config only | Extra authorization attribute rules |

Use `ldaps://directory.example.com:636` in production. Aird then builds the ldap3 connection with `ssl.CERT_REQUIRED`, so the directory certificate chain and hostname are checked. The default trust store is the host's. Set `AIRD_LDAP_CA_FILE` to a PEM file when the directory uses a private CA. A missing or unreadable CA file fails the bind. Aird does not offer an insecure "skip verify" option.

`ldap://` sends the bind password in the clear. Use it only on a trusted local network, or not at all. Terminating TLS on a reverse proxy in front of Aird does **not** encrypt the LDAP bind; that hop is separate from the browser session.

Threat model: LDAPS protects the password from a network observer between Aird and the directory. It does not hide files from someone who can read the Aird host. The directory account used for bind should be limited to the search the filter needs.
