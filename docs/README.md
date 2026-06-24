# Aird UI — page documentation

Short, **page-specific** notes: routes, templates, handlers, and contracts to preserve when changing code.

| Doc | Primary routes |
|-----|----------------|
| [browse.md](browse.md) | `GET /files/…` (directory listing) |
| [transfers.md](transfers.md) | Upload/download HTTP + WS stack, admin limits |
| [file-view.md](file-view.md) | `GET /files/…` (file), `GET /stream/…`, `WS /features` |
| [edit.md](edit.md) | `GET /edit/…`, `POST /edit` |
| [login.md](login.md) | `GET|POST /login`, `GET|POST /admin/login` |
| [profile.md](profile.md) | `GET|POST /profile` |
| [super-search.md](super-search.md) | `GET /search`, `WS /search/ws` |
| [share.md](share.md) | `GET /share`, share APIs |
| [shared-access.md](shared-access.md) | `/shared/…`, verify, file download |
| [tagged-files.md](tagged-files.md) | `GET /tagged/{tag}` |
| [p2p.md](p2p.md) | `GET /p2p`, `POST /p2p/signal` |
| [admin-settings.md](admin-settings.md) | `GET|POST /admin` |
| [admin-users.md](admin-users.md) | `/admin/users`, create, edit, delete |
| [admin-ldap.md](admin-ldap.md) | `/admin/ldap`… |
| [admin-audit.md](admin-audit.md) | `/admin/audit`, decisions WS/API |
| [admin-network-shares.md](admin-network-shares.md) | `/admin/network-shares` |
| [admin-tags.md](admin-tags.md) | `/admin/tags`, tag API |
| [admin-policies.md](admin-policies.md) | `/admin/policies`, policy API |
| [admin-user-attributes.md](admin-user-attributes.md) | `/admin/user-attributes`, attributes API |
| [wireguard-deploy.md](wireguard-deploy.md) | VPN + TLS production layout |
| [error.md](error.md) | `error.html` (HTTP errors) |
| [shared-ui.md](shared-ui.md) | Partials (`_app_nav_header`, `_admin_tabs`), CSP, themes |
| [developer-ui-quality.md](developer-ui-quality.md) | A11y, ESLint, SonarQube |

**Product overview:** [../README.md](../README.md)  
**ABAC roadmap (extended):** [abac.md](abac.md) — implemented engine details also in `specdoc.md` §12.

**Orphan:** `aird/templates/directory.html` appears unused by handlers; do not assume it is wired unless you add a route.

**Bootstrap:** `GET /` (`RootHandler`) redirects to `/files/`.
