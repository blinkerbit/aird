# Aird UI — page documentation

Short, **page-specific** notes: routes, templates, handlers, and contracts to preserve when changing code.

| Doc | Primary routes |
|-----|----------------|
| [browse.md](browse.md) | `GET /files/…` (directory listing) |
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
| [error.md](error.md) | `error.html` (HTTP errors) |
| [shared-ui.md](shared-ui.md) | Partials (`_app_nav_header`, `_admin_tabs`), CSP, themes |

**Orphan:** `aird/templates/directory.html` appears unused by handlers; do not assume it is wired unless you add a route.

**Bootstrap:** `GET /` (`RootHandler`) redirects to `/files/`.
