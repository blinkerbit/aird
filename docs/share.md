# Share (`/share`)

**Purpose:** Authenticated creators browse their allowed trees, multi-select files/folders (and optionally cloud attachments), configure access, then create shares. The same page lists non-revoked shares and opens **Manage** for edits.

**Routes:** `GET /share` — `ShareFilesHandler` (`aird/handlers/share_handlers.py`).  
**Template:** `aird/templates/share.html`  
**Client:** `/static/js/share/app.js`, `aird-core.js`, `theme.js`

---

## Creation flow (paths)

1. **Browser** loads entries under the user root via `/api/files/…` (same permission model as main browse—only reachable paths appear).
2. User selects rows; **Share Selected** opens **Configure Share**.
3. **POST `/share/create`** — JSON built in `generateShareLink()` (`paths`, `allowed_users`, `modify_users`, `share_type`, `allow_list`, `avoid_list`, `disable_token`, `expiry_date`).

**Directories:** Selecting a folder includes it according to **`share_type`** (see below). Individual files under a folder may be listed explicitly or implied by recursion.

---

## Public vs restricted (viewers)

| UI | Payload | Recipient |
|----|---------|-----------|
| **Public** — anyone with the link | `allowed_users: []` | No Aird login required for the viewer list check. |
| **Restricted** — only selected users | non-empty `allowed_users` | Viewer must present the **`user`** secure cookie identifying a username in that list (see `_is_user_allowed` in `share_handlers.py`). Anonymous users fail even with a valid link/token. |

**Secret token:** Independent of public/restricted. If the share has a `secret_token` and **Disable secret token** was not checked, `_is_token_valid` must pass first (Bearer or `share_token_{id}` cookie after `/shared/{id}/verify`). If the token is disabled (`secret_token` cleared), link access does not require that step.

**Editors:** Optional `modify_users`; empty means read-only for everyone on the shared surface. Editors must match the same cookie-based username check (`_is_user_allowed_for_modify`).

---

## Static vs dynamic (path-based shares)

Handled in `_collect_paths_from_request` / `_get_share_file_list` / `_is_path_in_share`:

| `share_type` | Stored in DB | At access time |
|--------------|--------------|----------------|
| **`static`** | After create/update, **expands directories** into the concrete file path list (`get_all_files_recursive`). | Membership is that **frozen** list (plus allow/avoid glob filtering). New files under a previously shared folder do **not** appear. |
| **`dynamic`** | Stores **folder paths** (directories only for local dynamic; files alone are not used as “live roots” the same way—see handler). | Re-walks those folders; **new files** under a shared directory **do** appear. |

**Cloud:** Cloud file entries are downloaded into a per-share staging area for **static** or non-dynamic flows. **Dynamic shares reject cloud** paths (400).

---

## Tag-based shares (`share_type: "tag"`)

**API:** `POST /share/create` with `share_type: "tag"` and **`tag_name`** (required). `paths` may be empty; validation skips path requirements for tag mode (`validate_share_create_struct`).

**Resolution:** For each access, `_get_tag_glob_patterns` loads ABAC/resource-tag rows matching `tag_name`, then `_get_share_file_list` unions files via `get_files_by_tag_patterns`, then applies `allow_list` / `avoid_list`.

**Static vs dynamic for tags:** The **Share** page radios only affect **path** shares. Tag shares always expand from **current** tag ↔ glob rules in the DB—new files matching those rules effectively behave like **live** membership. A separate “frozen tag snapshot” mode is **not** implemented; product wording that distinguishes static/dynamic **for tags** should be treated as future work unless the code gains a persisted snapshot.

**UI gap:** Tag creation/editing label is **not** exposed on `share.html` / `share/app.js` today (no `tag_name` field). Use the API or extend the modal to match `ShareCreateRequest` / update payloads.

---

## Listing and editing

| Action | Method / route |
|--------|----------------|
| List | `GET /share/list` — populates Active Shares table. |
| Detail (management modal) | `GET /api/share/details_by_id?id=…` |
| Update | `POST /share/update` — token toggle, expiry, allow/avoid glob lists, **`share_type`** static/dynamic, user lists, path adds/removals per handler. |
| Revoke | `POST /share/revoke?id=…` |

The management modal **`shareTypeEdit`** radios are **static vs dynamic only**. For existing **`tag`** shares, the UI treats `share_type !== "static"` as “dynamic” for the radio state—prefer fixing the modal to preserve **`tag`** or block unsafe type changes via API guards.

---

## Recipient experience

See **`docs/shared-access.md`**: `/shared/{id}`, `/shared/{id}/verify`, `/shared/{id}/file/{path}`.

---

## Other APIs

User search (`/api/users/search`), cloud browser (`/api/cloud/…`), file listing (`/api/files/…`) — wired from `share/app.js`.

**Do not change lightly:** JSON field names and modal IDs expected by `share/app.js` and share validators (`aird/core/input_validation.py`, `SHARE_JSON_BODY_MAX_BYTES`).
