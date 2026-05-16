# Aird — technical overview (blinkerbit/aird)

Concise reference for contributors and automation: **what the product does**, **why**, and **where limits live in code**.

---

## Purpose

Aird is a browser-accessible file workspace: authenticate, browse a user’s file tree, open files, optionally share access without standing up separate user accounts for every recipient.

---

## 1. Authentication (`/login`)

**Supported methods**

- **LDAP**: username + password (when LDAP is configured).
- **Local**: username + password **or** a configured **access token** (same form route).

**Bootstrap / first admin**

- There is no implicit “first password” for a fresh install. An operator uses a **pre-provisioned token** (or equivalent bootstrap credential) to sign in, then opens **Admin** and creates normal users. After that, routine access is username/password or token as designed.

**Related code/docs**: handler flow in `aird/handlers/auth_handlers.py`, field limits in `aird/constants/input_limits.py`, short notes in `docs/login.md`.

---

## 2. Ad‑hoc access (token sharing)

Users may share a **token** instead of creating accounts—recipients sign in with that token and see what that identity is allowed to see (similar in spirit to sharing a read-only `python -m http.server` URL, but backed by Aird auth and policy).

This is optional to product narrative; formal **file shares** (links, allowed users, etc.) exist in parallel via the share features.

---

## 3. Browse and open (`/files/...`)

### Navigation

- **Directories** → listing for that path; primary action is **open** (click name) to drill down.
- **Files** → open in the viewer (line window, etc.), or **download** / **raw** via URL query params as implemented in `aird/handlers/view_handlers.py`.

### Path breadcrumb (above the listing)

- The bar shows **`Home /` …** using forward slashes between segments, matching the `/files/...` URL shape (see `browse.html`).
- **`Home`** always links to **`/files/`** (root of browsing). Semantically this is the **authenticated user’s file root** — the sandbox directory Aird maps to `get_user_root` for that runtime/login — **not** the operating system’s “home folder” (`~`) or `C:\\Users\\...` literally labeled “Home”.
- Segments after `Home` are **relative path components** inside that root (e.g. `Home / .pytest_cache / v`).
- Optional **copy control** puts the relative path as used in APIs (slash-prefixed) on the clipboard; it is still confined to paths under that same root server-side.

### Listing actions (permission-gated)

Each row in the browse table (`aird/templates/browse.html`) should expose **only** actions the user is allowed to use. Visibility is driven by **feature flags** (`features[...]` passed from `MainHandler`) and, when the ABAC engine is enabled, by PDP decisions on relevant actions (`file.read`, `file.download`, `file.write`-family, etc.—see handlers using `require_action` / `check_access`).

| Control | Applies to | When shown (baseline) |
|--------|---------------|-------------------------|
| **Download** | Files only | `file_download` feature on |
| **Edit** | Files only | `file_edit` on → `/edit/...` |
| **Rename** | Files **and** directories | `file_rename` on |
| **Delete** | Files **and** directories | `file_delete` on (folder deletes may require non-empty handling / confirmations in UI) |

If a capability is disabled in admin feature flags or denied by policy, the corresponding button must not appear (or must fail safely server-side).

### Multi-select and bulk actions

The browse page supports **multi-select**: a checkbox on each row (files and folders), optional **select all visible**, and a floating control that opens a **selection drawer** listing chosen paths (`browse.html` + `aird/static/js/browse/app.js`).

Users can apply **bulk operations** on the selection. Behaviour must remain permission-aware: **only show bulk controls when the underlying features are enabled**; **each server request** (`/copy`, `/move`, `/api/bulk`, etc.) must re-validate auth, feature flags, `is_within_root`, and ABAC as it does today for single-item operations.

| Bulk capability | Typical client flow | Server / notes |
|-----------------|---------------------|----------------|
| **Delete** | Drawer → Confirm → `POST /api/bulk` with `{ action: "delete", paths: [...] }` | `BulkHandler` (`aird/handlers/file_op_handlers.py`); caps on path count / JSON body size (`MAX_BULK_PATHS`, `MAX_BULK_JSON_BYTES`). |
| **Copy** | Folder picker destination → sequential `POST /copy` per path | Same path rules as row-level copy (`CopyHandler`). |
| **Move** | Folder picker destination → sequential `POST /move` per path | Same path rules as row-level move (`MoveHandler`). |
| **Shares** | “Create share” (prefills share UI from selection) / “Add to existing share” → `POST /api/bulk` `add_to_share` | Requires `file_share`; needs DB. |
| **Tags** (admin) | Bulk tag assignment from drawer | Admin-only UI branch in template. |

**Bulk download**: a sensible product extension is **download many as ZIP** or **iterate single-file downloads** with clear UX limits; today, **per-file download remains the row-level action**. Any bulk-download feature must validate **every path** server-side against `user_root` (no archive slip paths).

### Toolbar: create in the **current directory**

At the opened directory level, the UI **must** allow:

1. **New folder** — implemented when `folder_create` is enabled (toolbar button → POST to folder create endpoint). Parent path is the current browse path.
2. **New plain “untitled” file** — **product expectation**: create an empty starter file (e.g. `untitled.txt` with disambiguation if the name exists) in the **current** directory without letting the client choose an absolute path. *If this button is not yet in `browse.html`, add it alongside “New folder” and wire it to an API/handler that uses the same path rules below.*

Upload (drop zone) already creates files **under** the current directory when `file_upload` is on; it must follow the same path-safety rules on the server.

### Backend path validation (non-negotiable)

Malicious clients must not bypass the browser and create/rename/move files **outside** the authenticated user’s root using `..`, `'\'`, UNC, mixed separators, or crafted `parent`/`path` parameters.

**Expected pattern** (already used in `aird/handlers/file_op_handlers.py` and related code):

- Resolve paths with **`os.path.abspath`** + **`os.path.join`** from a fixed **`user_root`** (see `get_user_root`).
- Reject any resolved path **`not is_within_root(..., user_root)`** (`aird/core/security.py`).
- **Folder names**: reject `.`, `..`, empty names, **`/`** and **`\\`** inside the *name segment* so “smart” payloads cannot splice extra path components (`CreateFolderHandler` is the reference implementation).
- **Rename / delete / move / copy / upload destination**: same checks on both source and target; use `os.path.basename` where only a filename is allowed.

Any new endpoint for “create empty file” must duplicate this discipline: **never** trust raw path strings from the client without joining under a verified parent and re-checking `is_within_root`.

**Code anchors**: `aird/handlers/view_handlers.py` (`MainHandler` → `browse.html`), `aird/handlers/file_op_handlers.py` (`CreateFolderHandler`, `DeleteHandler`, `RenameHandler`, `UploadHandler`, …), `aird/core/security.py`.

---

## 4. Large files and memory (`mmap`)

For **serving file bytes efficiently**, the server uses **memory-mapped reads** when the file is at or above a size threshold, so the engine does not read entire multi‑MB/GB files into a single large buffer for those code paths.

**Source of truth**: `MMAP_MIN_SIZE` in `aird/constants/__init__.py` (currently **1 MiB**), used by `aird/core/mmap_handler.py` (`MMapFileHandler.should_use_mmap`).

---

## 5. Initial file view: line window (protect the UI)

When opening a text file in **view** mode, the UI defaults to showing a **bounded line range** (first chunk of lines) unless the URL already specifies a range. That limits how many table rows / how much text is painted at once—reducing the chance of the front end choking on absurdly long lines or enormous line counts in one render.

**Source of truth**: `DEFAULT_FILE_VIEW_LINE_LIMIT` in `aird/constants/__init__.py` (currently **1000** lines), passed into `aird/templates/file.html` as `DEFAULT_FILE_VIEW_LINES`. Query parameters `start_line` / `end_line` control the window.

**Note:** The viewer fetches content via `mode=raw` and decodes in the browser; the line window primarily caps **what is displayed**, not the conceptual “never load big files.” Very large files still imply large downloads and client memory—use **download** or operational limits for those cases.

**In-browser editing** is a separate path: files above **`MAX_READABLE_FILE_SIZE`** (currently **50 MiB** in `aird/constants/__init__.py`) are rejected for the editor to avoid huge in-memory edits—see `aird/handlers/view_handlers.py` (`EditViewHandler`).

---

## 6. View all lines and download

After open, the file page is expected to let users:

- Adjust the **line range** (including effectively “all lines” by setting the range to cover the whole file when line metadata is available).
- **Download** the file (e.g. `?download=1` on the file route; PDFs are handled as download in the default flow).

Exact UI labels may change; the capability is encoded in `view_handlers` + `file.html` actions.

---

## 7. Super Search (`/search`)

Content / filename search is limited to **`get_user_root`** (`os.walk` with **`followlinks=False`**). The WebSocket emits **`scanning`** messages showing **relative file paths** often enough to avoid a frozen UI—**each glob match** before reading, plus **ticks while skipping non-matching files**. User-supplied patterns are screened for traversal/abs forms (`validate_super_search_glob`). Details: **`docs/super-search.md`**.

---

## 8. Shares (`/share`)

Creators open **Share**, browse permitted paths (same notion of “reachable” tree as `/files`), select files/folders, then **Configure Share**.

- **Public (link audience):** no entries in **`allowed_users`** — recipients are not checked against an Aird login for that gate.
- **Restricted:** non-empty **`allowed_users`** — the shared list and files require the **`user`** session cookie username to match; link + optional secret token alone are not enough.

**Secret token:** If enabled on the share, recipients pass verification (`/shared/{id}/verify`) so `_is_token_valid` succeeds unless the administrator disabled the token on that share.

**Path shares — static vs dynamic:** **Static** stores an expanded snapshot of files (folders flattened at creation/update time). **Dynamic** stores folder roots and includes **new** files added under those folders later (local trees only in dynamic mode).

**Tag shares:** Implemented on the API as **`share_type: "tag"`** plus **`tag_name`**; membership is computed from resource-tag glob rules **at access time** (live set of matching files). The Share page modal does **not** yet expose tag-based creation—the API is authoritative until the UI adds it.

### Expiry (time-limited validity)

- Creators may set **`expiry_date`** (ISO datetime; UI: datetime-local on **`share.html`** / manage modal in **`share/app.js`**). **`ShareCreateRequest`** / **`POST /share/update`** persist it (`aird/domain/contracts.py`, `aird/db/shares.py`).
- While the row exists, **`SharedListHandler`** and **`SharedFileHandler`** reject access with **410** when **`is_share_expired`** is true (`aird/db/shares.py` — compare `expiry_date` to current time).
- **`cleanup_expired_shares`** deletes expired rows from SQLite on a periodic timer (**`main.py`**, roughly hourly), so the share **drops out of the DB** after cleanup as well as returning **410** at request time before deletion.

### Recipients who may upload (modify)

- Besides **`allowed_users`** (who may open the link when the share is restricted), **`modify_users`** lists account names that get **write** privilege on the shared surface. **`_is_user_allowed_for_modify`** (`aird/handlers/share_handlers.py`) requires a signed-in **`user`** cookie matching **`modify_users`**; otherwise the share is **read-only** for that session. The recipient file list template shows **Modify** vs **Read-only** via **`can_modify`** (`shared_list.html`).
- **Product intent:** those users can **add or upload files** into the shared folder set (same path rules and share membership as reads). Wire any upload endpoint to the same gates: not expired, token valid, user in **`modify_users`**, path under the share’s effective file set.

### “Shared with you” (recipient discovery)

- When **user A** restricts a share with **`allowed_users`** containing **user B’s** username, **B** should see that share under **Shared with me** after login. **`list_shares_accessible_to_user`** (`aird/db/shares.py`) returns shares where **`allowed_users` is unset** (link-public audience) **or** the username is in **`allowed_users`**. The **Profile** page renders that list (`profile.html`, **`shared_with_me`** from **`auth_handlers`**). Optional dedicated **`/shared-with-me`** route is not required if profile remains the hub—keep naming consistent in the UI (“Shared with me”).

**Manage:** Active shares load from **`GET /share/list`**; **Manage** uses **`POST /share/update`** and **`POST /share/revoke`**. Full mechanics: **`docs/share.md`**; recipient routes: **`docs/shared-access.md`**.

---

## 9. P2P share (`/p2p`)

**Goal:** Let a **source** user offer **selected files** to **one or more recipients** over the public internet. Recipients open a **join link** and **download** those files **directly from the source’s browser** (device-to-device). Aird’s servers **do not store or proxy file bytes** for this path—only what is needed to **rendezvous** peers.

### Who can open the page and the link

- **Without logging in:** When the **`p2p_transfer`** feature is on, the login page can surface **anonymous P2P** entry; unauthenticated users use the same **`/p2p`** UI and room URLs.
- **While logged in:** Nav exposes **P2P** for authenticated users; behavior is the same transfer page, with optional ABAC evaluation for **`p2p.transfer`** on the page load path (see `P2PTransferHandler` in `aird/handlers/p2p_handlers.py`).

**Room / share URL:** Join links are **`/p2p?room=<roomId>`** (same origin as the app). Recipients need only that URL (and a working browser); they do not need an Aird account for the anonymous flow.

### Selecting files and what gets transferred

- The **source** chooses **which files** to send (multi-select / file-picker style as implemented in the P2P UI).
- After connection, **payload** is moved **browser → browser** using **WebRTC** (data channel with chunked transfers—see `aird/static/js/pages/p2p-page.js` and `aird/static/js/p2p/transfer-service.js`). This matches the **signaling-centric backend:** the server runs a **WebSocket** at **`/p2p/signal`** (`P2PSignalingHandler`) to relay **SDP offers/answers, ICE candidates, and control messages** between peers (`aird/services/p2p_service.py`, `aird/handlers/p2p_handlers.py`). **File contents are not uploaded to Aird for redistribution.**

### STUN / NAT traversal

- Establishing connectivity across NATs uses **ICE** with **public STUN** endpoints. The UI lets users pick among **preset, no-cost STUN bundles** (e.g. Google, Mozilla, Ekiga/OpenSTUN, others as listed in `p2p_transfer.html` / `STUN_OPTIONS` in `p2p-page.js`). Users can switch STUN presets and reconnect without leaving the room when the UI supports it.

### Share affordance: link + QR code

- When the source **creates a room / hits share**, the client generates:
  - A **copyable join URL** for the session, and
  - A **QR code** encoding that same URL (canvas render via bundled QR helper in `p2p-page.js`).
- **Multiple recipients** open the link (or scan the QR); each obtains the selected files **from the sender’s machine** once the peer connection is up. Reliability and ordering follow the implemented WebRTC transfer protocol—not server-side replication.

### Distinction from `/share`

- **`/share`:** Server-backed shares, persisted paths/tag rules, download through normal Aird file routes—see §8.
- **`/p2p`:** **Ephemeral** peer exchange; server is **signaling + HTML/JS only** for file bytes. Operate under **feature flag** **`p2p_transfer`** (admin toggle).

**Related code/docs:** `aird/handlers/p2p_handlers.py`, `aird/static/js/pages/p2p-page.js`, `docs/p2p.md`.

---

## 10. Admin: users, password reset, settings

The **Admin** area (separate admin auth; see `docs/admin-settings.md`, `docs/admin-users.md`) configures global behavior. **User management** lives under **`/admin/users`**: list, create, edit, deactivate/delete, and **reset password** for other accounts.

### Reset password (temporary secret + forced rotation)

- Admin action **Reset password** (`UserPasswordResetHandler` in `aird/handlers/admin_handlers.py`) sets the user’s password to a **one-time random value** (URL-safe token, e.g. **`secrets.token_urlsafe(32)`**), and sets **`must_change_password`** on that user.
- The **plain temporary password is shown only once** to the admin on the response page (`admin_users.html`); it is **not** emailed. The admin must deliver it out of band.
- When the user signs in with that value, the app **does not** proceed to the app shell: it **redirects** to **`/auth/mandatory-password`** so the user **must choose a new password** (`must_change_password` flow in `aird/handlers/auth_handlers.py`, `aird/db/users.py`). After a successful change, **`must_change_password`** is cleared. **This limits exposure** of the admin-visible secret and matches a **forced password update** security model.
- An **`admin_password_reset`** audit entry should be written when the DB is available.

### Admin “Settings / overview” form (persisted controls)

The main admin settings POST (see `admin.html` + `AdminHandler` in `aird/handlers/admin_handlers.py`) persists **feature flags**, **upload limits**, **allowed extensions**, and **WebSocket bucket limits**. Values load from SQLite (`aird/db/config.py`) with defaults from `aird/constants/__init__.py` where applicable.

**Feature flags** (toggle each on/off globally):

| Flag | Typical effect |
|------|----------------|
| File upload | Upload UI and upload endpoints |
| File download | Download / streaming download affordances |
| File edit | Editor route |
| File rename | Rename actions |
| File delete | Delete file actions |
| File share | Share creation / management flows |
| Create folder | New folder actions |
| Delete folder | Folder delete actions |
| Super search | `/search` and related WS |
| P2P transfer | `/p2p` and signaling |
| Gzip compression | Compressed responses where implemented |
| Allow simple passwords | Relaxes password rules when enabled |
| ABAC engine | Attribute-based enforcement |
| ABAC decision audit | Logging of PDP decisions |

**Upload settings**

- **Max file size (MB):** Allowed range **1–10240** (UI default baseline **512** unless configured).
- **Allow all file types:** When off, uploads are constrained to **selected extensions** from the whitelist UI (`allow_ext` checkboxes).

**WebSocket connections** (three named buckets—each has **max connections** **1–1000** and **idle timeout (sec)** **30–7200**)

- **Feature flags** — used for the **`/features`** WebSocket class (`FeatureFlagSocketHandler` in `aird/handlers/api_handlers.py`).
- **File streaming** — file stream WebSocket limits.
- **Search** — Super Search WebSocket limits.

### Real-time effect for logged-in users

**Product requirement:** When an admin saves settings, **capability changes apply without requiring users to log out.**

**Implementation anchors**

- Persisted flags are saved, then **`invalidate_feature_flags_cache()`** runs (`aird/utils/util.py`) so **`get_current_feature_flags()`** reflects the DB immediately on subsequent reads.
- **`FeatureFlagSocketHandler.send_updates()`** broadcasts the updated flag map to all clients connected to **`/features`** (`aird/handlers/admin_handlers.py` after save).
- **Handlers and operations** (`is_feature_enabled`, `rename`/`upload`/etc.) **must consult current flags** (or equivalents) **per request** so that, for example, **File rename OFF** causes **immediate server-side refusal** even if an old page still drew a Rename control until the UI refreshes or receives the WS payload.

WebSocket **limit** tweaks apply to **new** connections according to **`WebSocketConnectionManager`** wiring; existing sockets may retain prior limits until reconnect—document as operational detail.

---

## 11. Network shares — SMB / WebDAV (admin only)

This is **not** the browser **file share** feature from §8. **Network shares** expose a **chosen local folder** on the host running Aird as a **standalone SMB or WebDAV** endpoint so other machines on the LAN (or routed networks) can mount or open it like a normal network drive. **Only administrators** may define or manage them (`@require_admin` on `AdminNetworkSharesHandler` et al.).

### UI and routes

- **Page:** **`/admin/network-shares`** (`aird/templates/admin_network_shares.html`, Admin tab “Network shares”).
- **Active shares** table shows each share’s **name**, **protocol** (SMB / WebDAV badge), **port**, **folder path**, **credential username**, **read-only** flag, **running** status (whether the embedded server thread is active), plus **Disable/Enable** and **Delete**.
- **Create:** `POST /admin/network-shares` — **Share name**, **Folder path** (must exist on disk), **Protocol** (**`webdav`** — Class 2 locking / digest-oriented stack in product copy, or **`smb`** — SMB1/2 via embedded server), **Port** (**1–65535** validated in handler; UI recommends **above 1024** to reduce privilege friction), **Username** / **Password** for clients authenticating to that share, optional **Read-only**. On success the share is persisted (`network_shares` table, `aird/db/network_shares.py`) and **`NetworkShareManager.start_share`** starts the listener (`aird/network_share_manager.py`).
- **Toggle / delete:** `POST /admin/network-shares/toggle`, `POST /admin/network-shares/delete` (`AdminNetworkShareToggleHandler`, `AdminNetworkShareDeleteHandler`).

Shares marked **enabled** in the DB are **auto-started** at application startup (`_auto_start_network_shares` in `aird/main.py`).

### Runtime notes

- **WebDAV** and **SMB** are served by **in-process Python stacks** (`wsgidav`/`cheroot` and `pysmbserver` when installed); if a dependency is missing, that protocol cannot run—see **`aird/network_share_manager.py`**.
- These endpoints are **separate listeners** from the main Aird HTTPS/HTTP app; clients connect to **`host:port`** for the selected protocol using the credentials the admin configured.

**Related docs:** **`docs/admin-network-shares.md`**.

---

## 12. ABAC — attribute-based access control

When the **`abac_engine`** admin feature flag is **on**, Aird evaluates **policies** (SQLite `policies` table) before allowing sensitive actions. When it is **off**, handlers keep their legacy checks (**feature flags**, role/session, path sandboxing) without calling the PDP. **`abac_audit_decisions`** separately controls whether outcomes are persisted to **`policy_decisions`** and emitted on the internal event bus.

### PDP and PEP (implemented)

- **Policy Decision Point (PDP):** **`PolicyService`** (`aird/services/policy_service.py`) loads policies (ordered by **`priority`** then **`id`**), attaches **resource tags** from **`TagService`**, filters by **`target_actions`** (literal **`"*"`** matches every action), evaluates each **`condition`** JSON AST, applies **deny-overrides**, then **first matching permit**, otherwise **default deny**. Malformed policies are skipped with a log warning.
- **Policy Enforcement Point (PEP):** **`BaseHandler.check_access`** builds an **`AccessRequest`** (`aird/domain/models.py`) and calls the PDP. **`@require_action("…")`** (`aird/handlers/base_handler.py`) wraps handlers: **no-op** if the engine is disabled; otherwise **403** with reason on deny. **`@require_admin`** can additionally require **`admin.access`** when ABAC is on.

### Attributes in each request

- **Subject:** username, **`role`**, **`clearance`** (from admin **user attributes** when set; else heuristic), **`groups`** (comma-separated **`groups`** attribute), **`quota_bytes`**, **`extra`** key/value pairs from the user-attributes row (`managed_device` drives **`environment.is_managed_device`**).
- **Resource:** path, **extension**, optional **size**, and **tags** (from resource-tag globs / **`TagService`**).
- **Environment:** request timestamp, **`remote_ip`**, **`is_corporate_ip`** (matches **`CORPORATE_IP_CIDRS`** when configured — `base_handler._is_corporate_ip`), **`is_managed_device`**.

Condition AST primitives include **`equals`**, **`in`**, **`and`/`or`/`not`**, **`tag_present`**, **`time_between`**, **`ip_in_cidr`**, and **`attr`** paths into the flattened attribute map (`policy_service.py` docstring lists shapes).

### Actions and surfaces

Representative **`target_actions`** used in decorators and handlers include **`file.read`**, **`file.list`**, **`file.download`**, **`file.write`**, **`file.delete`**, **`file.rename`**, **`share.view`**, **`share.create`**, **`p2p.transfer`**, **`favorites.toggle`**, **`admin.access`**. New endpoints must declare consistent action names when they should participate in ABAC.

### Admin UI and data

- **Policies:** **`/admin/policies`** — create/edit **`effect`**, **`target_actions`**, **`priority`**, **`enabled`**, **`condition`** JSON (**`docs/admin-policies.md`**).
- **Resource tags:** glob→tag mappings — **`docs/admin-tags.md`** (tags feed **`tag_present`** and resource enrichment).
- **User attributes:** per-user key/values (**clearance**, **managed_device**, custom dimensions) — **`docs/admin-user-attributes.md`**.
- **Seeds:** first-run **`DEFAULT_POLICIES`** in **`aird/db/policy_seeds.py`** (e.g. admin shadow-permit, default user permits, demo **time-gated PII** deny, optional large-P2P rule) — idempotent inserts only for missing **`name`** rows.

### Extended narrative

**`docs/abac.md`** holds the longer RBAC→ABAC roadmap and architecture discussion; **`specdoc.md`** and **`docs/admin-policies.md`** track **shipping** behavior. Where they disagree with code, **trust the implementation** and fix docs in the same change.

---

## Related documentation

| Topic | Location |
|--------|----------|
| Input/body size limits (API & forms) | `docs/input-validation.md`, `aird/constants/input_limits.py` |
| Login behavior | `docs/login.md` |
| Super Search (UI, WebSocket protocol, limits) | `docs/super-search.md` |
| Share creation & management | `docs/share.md` |
| Public share access (token, list, fetch) | `docs/shared-access.md` |
| P2P transfer (WebRTC, signaling, anonymous entry) | `docs/p2p.md` |
| Admin settings (flags, upload, WebSocket limits) | `docs/admin-settings.md` |
| Admin user list & password reset | `docs/admin-users.md` |
| Network shares (SMB / WebDAV admin UI) | `docs/admin-network-shares.md` |
| ABAC roadmap & extended architecture | `docs/abac.md` |
| Admin ABAC policies (UI / API) | `docs/admin-policies.md` |
| Resource tags | `docs/admin-tags.md` |
| User attributes (ABAC subject) | `docs/admin-user-attributes.md` |
| Developer UI / accessibility & Sonar expectations | `docs/developer-ui-quality.md` |

---

*This file is product-oriented technical documentation for GitHub; when behavior and constants disagree, **trust the code** and update this doc in the same PR.*
