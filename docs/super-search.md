# Super Search — technical overview (blinkerbit/aird)

Concise reference for humans and tools: **what the Super Search screen does**, **how it talks to the server**, and **where limits and safety are enforced**. For the rest of the product, see `specdoc.md` at the repo root.

---

## Purpose

**Super Search** lets an authenticated user scan their **Aird file root** with a **glob file pattern**, optionally in two modes: **search inside file contents** or **search filenames only**. Results stream over a **WebSocket** so the UI can update while the walk runs.

---

## Routes and feature flag

| Item | Value |
|------|--------|
| **Page (HTML)** | `GET /search` — `SuperSearchHandler` in `aird/handlers/api_handlers.py` |
| **Search socket** | `GET` / `WS` `ws(s)://…/search/ws` — `SuperSearchWebSocketHandler` (same module) |
| **Template** | `aird/templates/super_search.html` |
| **Client script** | `/static/js/pages/super-search.js` (plus `aird-core.js`, `theme.js`) |
| **Availability** | **`super_search`** feature flag; handler requires the feature before rendering. Nav link is gated the same way. |

**Stable contract for automation:** `<body data-search-base-path="…">` carries the **browse-relative** path used to seed the default pattern (see below). Do not rename without updating tests and docs.

---

## Page chrome (matching the UI)

- **Title:** “Super Search” (`<h1>`).
- **Global header:** Standard app nav (`_app_nav_header.html`) — user chip, Browse, Search, Share, P2P (if enabled), theme, ⌘K-style palette if present, logout.

---

## Breadcrumb (path context)

Rendered in `super_search.html` below the title:

- **`🏠 Home`** → links to **`/files/`** (same meaning as Browse: **Aird file root** for this login/`get_user_root`, **not** the OS account home folder).
- **Optional segments** — when `GET /search?path=…` supplied a subdirectory context, crumbs mirror **`/files/…`** path parts (each segment links back into Browse).
- **Terminal crumb:** **`🔍 Super Search`** (non-link) shows the user is still in the Super Search workflow.

Same mental model as `specdoc.md` § Path breadcrumb for `/files`.

---

## File pattern (wildcard / glob)

- **Control:** `#pattern` — “File Pattern (Wildcard/Glob)”.
- **Static help panel** under the input lists glob building blocks (`*`, `**`, `?`, `[abc]`, `{a,b}`).

**Boot-time behavior (`super-search.js`):**

- Reads **`document.body.dataset.searchBasePath`** (from `current_path` on `GET /search`).
- Prefills **`#pattern`**:  
  - If there is a current path → pattern like  
    `{normalizedPath}\**\*.txt` on Windows using `\`, or POSIX-style separators on Unix.  
  - If opened from “root context” → e.g. `**\*.txt` (Windows) or `**/*.txt` (POSIX).
- Replaces part of the help copy with JS to stress: **`\*` vs `*` / `**`**, **`?`**, and **path separator `\` vs `/`** plus the line **“Searches from root directory only.”**

**Interpretation of “searches from root directory only”:** The server **`os.walk`s only under `get_user_root`**, with **`followlinks=False`** so directory symlinks cannot redirect the walker outside the sandbox. Matching uses paths **relative to that root** (slashes normalized). The walk never leaves the resolved user root; opened files come only from that tree.

**Pattern hardening:** `validate_super_search_glob()` in `aird/core/input_validation.py` rejects patterns containing **`..` segments**, **`//` prefixes (UNC / absolute-looking)**, or **`X:/` drive-letter roots**. Matching is still only against paths under Home; these rules block misleading or sloppy client input.

---

## Common pattern chips

One-click presets (see template):

| Button label | Payload written to `#pattern` |
|--------------|----------------------------------|
| `*.py` | `*.py` |
| `*.js/ts` | `*.js,*.ts` |
| `**/*.java` | `**/*.java` |
| `!node_modules` | `!node_modules` |
| `*.{md,txt,log}` | `*.{md,txt,log}` |

---

## Search mode

Radios **`searchMode`**:

| Mode | Value | Meaning |
|------|--------|---------|
| **Search Content** (default) | `content` | Find **`search_text`** **inside** file bodies for paths matching the glob. |
| **Search Filenames** | `filename` | Match **`search_text`** against **file/path names** (`fnmatch` semantics when `*`/`?`/brackets appear in search text); no line-by-line read for hits. |

**Dynamic labels:**

- Content: subtitle “Search for text within file contents”; helper “Case-sensitive text search within matching files”.
- Filename: labels switch to filename-focused copy and wildcard hint (“Use wildcards like `*` and `?`…”).

---

## Search text input

- **Content mode:** `#searchText` — placeholder “Text to search for…”; **case-sensitive** substring scan per line (`search_text in line`).
- **Filename mode:** same field; treated as pattern/s substring per server logic (`_search_one_file` filename branch).

Both **pattern** and **search_text** must be non-empty **in the UI** before `Start`; otherwise status shows an error string.

---

## Primary actions

| Control | Behavior |
|---------|----------|
| **🚀 Start Super Search** | Opens **`/search/ws`**, sends first JSON `{ pattern, search_text, search_mode }`. |
| **⏹ Cancel** | Sends stop / closes client side; disabled until search starts (`super-search.js`). |
| **🗑 Clear Results** | Clears client-side results map/UI. |

**Keyboard:** **Enter** in pattern or search field starts search if idle.

---

## Status, progress, and results

| Element | Role |
|---------|------|
| **`#status` / `#statusText`** | Default idle copy: “Ready to super search. Enter a file pattern and search text above.”; updates for connect, scanning, completion, auth errors. |
| **`#progressContainer`** | Progress bar + “files scanned” + **scanning ticker** (current relative file path during content walk). |
| **`#results`** | Streams in **matches** grouped by file; filename hits use line number **`0`** and a folder icon marker in JS. |

**Footer tip (`#idleHint`):**  
“💡 Tip: choose a search mode above, enter a file pattern and search text, then press **Enter** or click **Start Super Search**.”

---

## WebSocket message protocol (client ↔ server)

**Client → server (first frame after open):**

```json
{
  "pattern": "<glob string>",
  "search_text": "<string>",
  "search_mode": "content" | "filename"
}
```

**Server → client** (`type` field; JSON):

| `type` | Purpose |
|--------|---------|
| `auth_required` | Session invalid; payload may include `redirect`; client redirects to login. |
| `search_start` | Acknowledgment; echoes `pattern`, `search_text`, `search_mode`. |
| `scanning` | **Liveness:** `file_path` (relative path under the user root), `files_searched` counter. Sent **once each glob-hit file** right before searching it, and periodically for **skipped** files (every 40 visits + the first skipped file) so long runs with a narrow glob do not look hung. Client shows this in the **scanning ticker** + counts. |
| `match` | One hit: `file_path`, `line_number`, `line_content`, `search_text`, `match_positions`, etc. |
| `search_complete` | Finished with matches. |
| `no_files` | Completed with zero matches (`message`, `files_searched`). |
| `cancelled` | Cancelled run. |
| `error` / `file_error` | Failure notices. |

**Auth:** WS uses `authenticate_handler`; **every** inbound message rechecks the session; expiry mid-search closes or aborts gracefully with `auth_required`-style messaging.

---

## Server-side limits and safety

| Concern | Implementation |
|---------|----------------|
| **Payload size / abuse** | `validate_ws_search()` in `aird/core/input_validation.py` enforces `WS_SEARCH_PATTERN_MAX_LEN` and `WS_SEARCH_TEXT_MAX_LEN` (`aird/constants/input_limits.py`). Over-limit → error message on socket. |
| **Content search on huge files** | For **content** mode, if `stat().st_size > MAX_READABLE_FILE_SIZE` (`aird/constants/__init__.py` / `aird.config`), that file is **skipped** (no line-by-line read, no matches). Filename mode still considers the path/name. |
| **Other large-file tooling** | General **mmap**-based serving/search utilities live in `aird/core/mmap_handler.py`; Super Search’s hot path here is **async line reads** after the size gate. |
| **Path containment** | Walk anchored at **`pathlib.Path(get_user_root(handler)).resolve()`**, **`followlinks=False`**. Matching uses **`fnmatch`** on relative paths (`\`→`/`); no reads outside that tree regardless of clever glob text (see pattern hardening above). |
| **Glob pattern rejection** | `validate_super_search_glob()` runs on **`on_message`** after length checks; bad patterns yield `{ "type": "error", … }` and never start `perform_search`. |

**Do not** weaken WebSocket **`check_origin`** for production without review (`is_valid_websocket_origin`).

---

## Operational notes for contributors

- Changing **`data-search-base-path`**, **`/search/ws` URL**, or the **initial JSON envelope** breaks the client unless `super-search.js` is updated together.
- Server and client must agree on **`search_mode`** strings exactly: `content` | `filename`.
- When enhancing the UI, keep breadcrumbs and “Home means file root” wording consistent with `browse.html`.

---

## Related docs

| Topic | Location |
|--------|----------|
| Product-wide overview | `specdoc.md` |
| mmap / file size philosophy | `specdoc.md` §§ 4–5 |
| WS search limits (central) | `aird/constants/input_limits.py` |

---

*When this document disagrees with the code, treat the repository as authoritative and refresh this page in the same PR.*
