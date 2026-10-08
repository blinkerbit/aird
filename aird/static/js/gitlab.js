"use strict";

const TOKEN_KEY = (host) => `aird.gitlab.token.${host || "gitlab.com"}`;
const BRIDGE_KEY = "aird.gitlab.bridge";

function xsrf() {
  const match = /(?:^|; )_xsrf=([^;]+)/.exec(document.cookie);
  return match ? decodeURIComponent(match[1]) : "";
}

function currentPath() {
  return (document.getElementById("currentPath")?.value || "").replace(/^\/+/, "");
}

function cfg() {
  return globalThis.__GITLAB_CONFIG || {};
}

function qs(extra = {}) {
  const params = new URLSearchParams();
  params.set("path", extra.path ?? currentPath());
  if (cfg().shareId) params.set("share_id", cfg().shareId);
  Object.entries(extra).forEach(([k, v]) => {
    if (k === "path" || v == null || v === "") return;
    if (Array.isArray(v)) v.forEach((item) => params.append(k, item));
    else params.set(k, String(v));
  });
  return params.toString();
}

async function aird(method, url, body) {
  const opts = { method, credentials: "same-origin", headers: { Accept: "application/json" } };
  if (method !== "GET") {
    opts.headers["Content-Type"] = "application/json";
    opts.headers["X-XSRFToken"] = xsrf();
    if (body) opts.body = JSON.stringify(body);
  }
  const resp = await fetch(url, opts);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || resp.statusText);
  return data;
}

function trimSlashes(value) {
  let text = String(value || "");
  while (text.startsWith("/")) text = text.slice(1);
  while (text.endsWith("/")) text = text.slice(0, -1);
  return text;
}

function hostKey(host) {
  try {
    return new URL(host || "https://gitlab.com").host;
  } catch {
    return "gitlab.com";
  }
}

function viewerToken(host) {
  return localStorage.getItem(TOKEN_KEY(hostKey(host))) || "";
}

function setViewerToken(host, token) {
  const key = TOKEN_KEY(hostKey(host));
  if (token) localStorage.setItem(key, token);
  else localStorage.removeItem(key);
}

const GITLAB_API = "https://gitlab.com/api/v4";
const LOCAL_BRIDGES = new Set(["http://127.0.0.1:8765", "http://localhost:8765"]);

function bridgeBase() {
  const stored = String(localStorage.getItem(BRIDGE_KEY) || "").replace(/\/$/, "");
  return LOCAL_BRIDGES.has(stored) ? stored : "";
}

function gitlabHttpsOrigin(host) {
  const base = String(host || "https://gitlab.com").replace(/\/$/, "");
  if (base === "https://gitlab.com") return "https://gitlab.com";
  try {
    const parsed = new URL(base);
    const name = parsed.hostname.toLowerCase();
    const safe = parsed.protocol === "https:" && !parsed.username && !parsed.password && !parsed.port
      && /^[a-z0-9.-]+$/.test(name);
    const allowed = safe ? ["https://gitlab.com", `https://${name}`] : ["https://gitlab.com"];
    if (allowed.includes(base)) return base;
  } catch {
    /* fall through */
  }
  return "https://gitlab.com";
}

function gitlabApiRoot(host) {
  const bridge = bridgeBase();
  if (LOCAL_BRIDGES.has(bridge)) return `${bridge}/api/v4`;
  const origin = gitlabHttpsOrigin(host);
  if (origin === "https://gitlab.com") return GITLAB_API;
  return `${origin}/api/v4`;
}

function gitlabRequestUrl(host, path) {
  const root = gitlabApiRoot(host);
  const raw = String(path || "").replace(/^\//, "");
  const queryAt = raw.indexOf("?");
  const pathname = queryAt === -1 ? raw : raw.slice(0, queryAt);
  const query = queryAt === -1 ? "" : raw.slice(queryAt + 1);
  const segments = pathname.split("/").filter(Boolean).map((part) => encodeURIComponent(part));
  const url = new URL(root.endsWith("/") ? root : `${root}/`);
  url.pathname = `${url.pathname.replace(/\/$/, "")}/${segments.join("/")}`;
  if (query) url.search = `?${query}`;
  if (url.origin !== new URL(root).origin) throw new Error("Unsupported GitLab host");
  return url;
}

async function glFetch(host, token, path, { method = "GET", body } = {}) {
  const url = gitlabRequestUrl(host, path);
  const headers = { Accept: "application/json", "PRIVATE-TOKEN": token };
  if (body) headers["Content-Type"] = "application/json";
  if (bridgeBase()) headers["X-Gitlab-Host"] = gitlabHttpsOrigin(host);
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.message || data.error || resp.statusText);
  return data;
}

function encProject(project) {
  return encodeURIComponent(project || "");
}

function panelEl() {
  return document.getElementById("gitlabPanel");
}

function panelBodyEl() {
  return panelEl()?.querySelector(".gitlab-panel-body") ?? null;
}

function externalLink(href, text) {
  const link = document.createElement("a");
  link.className = "link";
  const safe = httpsUrl(href);
  if (safe) {
    link.href = safe;
    link.target = "_blank";
    link.rel = "noopener";
  }
  link.textContent = text;
  return link;
}

function formField(labelText, input) {
  const label = document.createElement("label");
  label.append(document.createTextNode(`${labelText} `), input);
  return label;
}

function textInput(name, value, opts = {}) {
  const input = document.createElement("input");
  input.name = name;
  input.value = String(value ?? "");
  if (opts.placeholder) input.placeholder = opts.placeholder;
  if (opts.type) input.type = opts.type;
  return input;
}

function httpsUrl(value) {
  try {
    const url = new URL(String(value || ""));
    return url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

function dashCard(label, body, wide) {
  const card = document.createElement("div");
  card.className = wide ? "gitlab-dash-card gitlab-dash-card--wide" : "gitlab-dash-card";
  const lab = document.createElement("div");
  lab.className = "gitlab-dash-label";
  lab.textContent = label;
  card.append(lab);
  if (body instanceof Node) card.append(body);
  else {
    const val = document.createElement("div");
    val.className = "gitlab-dash-value";
    val.textContent = body;
    card.append(val);
  }
  return card;
}

function listNote(list, className, text) {
  const note = document.createElement("p");
  note.className = className;
  note.textContent = text;
  list.replaceChildren(note);
}

function fileRow(className, full, name, icon, text) {
  const li = document.createElement("li");
  li.className = "gitlab-file-row";
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = className;
  btn.dataset.path = full;
  if (name) btn.dataset.name = name;
  const iconEl = document.createElement("span");
  iconEl.className = "gitlab-file-icon";
  iconEl.setAttribute("aria-hidden", "true");
  iconEl.textContent = icon;
  const nameEl = document.createElement("span");
  if (name) nameEl.className = "gitlab-file-name";
  nameEl.textContent = text;
  btn.append(iconEl, nameEl);
  li.append(btn);
  return { li, btn };
}

function badgeClass(status) {
  const s = String(status || "").toLowerCase();
  if (["success", "passed"].includes(s)) return "gitlab-badge--ok";
  if (["failed", "canceled"].includes(s)) return "gitlab-badge--fail";
  return "gitlab-badge--run";
}

let _status = null;
let _ws = null;
let _dashboard = null;
let _panelTarget = null;

function formatCacheAge(iso) {
  if (!iso) return "Never refreshed";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "Cached";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "Cached just now";
  if (mins < 60) return `Cached ${mins}m ago`;
  const hrs = Math.round(mins / 60);
  return `Cached ${hrs}h ago`;
}

async function loadDashboard() {
  const section = document.getElementById("gitlabDashboard");
  const grid = document.getElementById("gitlabDashboardGrid");
  const meta = document.getElementById("gitlabCacheMeta");
  if (!section || !grid) return;
  try {
    _dashboard = await aird("GET", `/api/gitlab/dashboard?${qs()}`);
  } catch (_) {
    section.hidden = true;
    return;
  }
  const hasData = _dashboard.binding || (_dashboard.boards || []).length;
  if (!hasData) {
    section.hidden = true;
    return;
  }
  section.hidden = false;
  const user = _dashboard.gitlab_user;
  const users = _dashboard.active_users || [];
  const issues = _dashboard.open_issues || [];
  const mrs = _dashboard.merge_requests || [];
  const signed = document.createElement("div");
  signed.className = user ? "gitlab-dash-value" : "gitlab-dash-value opacity-60";
  signed.textContent = user ? (user.name || user.username || "") : "Token required";
  const cards = [
    dashCard("Signed in", signed),
    dashCard("Open issues", String(_dashboard.open_issue_count ?? issues.length)),
    dashCard("Open MRs", String(_dashboard.open_mr_count ?? mrs.length)),
  ];
  if (users.length) {
    const people = document.createElement("div");
    people.className = "gitlab-dash-users";
    users.forEach((person) => {
      const span = document.createElement("span");
      span.className = "gitlab-dash-user";
      span.textContent = person.name || person.username || "";
      people.append(span);
    });
    cards.push(dashCard("Active on board", people, true));
  }
  cards.push(dashCard("Recent issues", issueLinks(issues), true), dashCard("Open merge requests", mrButtons(mrs), true));
  grid.replaceChildren(...cards);
  if (meta) meta.textContent = formatCacheAge(_dashboard.cache?.refreshed_at);
}

function issueLinks(issues) {
  const list = document.createElement("div");
  list.className = "gitlab-dash-list";
  if (!issues.length) {
    const empty = document.createElement("span");
    empty.className = "text-xs opacity-60";
    empty.textContent = "Refresh to load cached tickets";
    list.append(empty);
    return list;
  }
  issues.slice(0, 8).forEach((iss) => {
    const link = document.createElement("a");
    link.className = "link text-sm";
    const href = httpsUrl(iss.web_url);
    if (href) {
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener";
    }
    link.textContent = `#${iss.iid} ${iss.title || ""}`;
    list.append(link);
  });
  return list;
}

function mrButtons(mrs) {
  const list = document.createElement("div");
  list.className = "gitlab-dash-list";
  if (!mrs.length) {
    const empty = document.createElement("span");
    empty.className = "text-xs opacity-60";
    empty.textContent = "None";
    list.append(empty);
    return list;
  }
  mrs.slice(0, 6).forEach((mr) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "link text-sm gitlab-dash-mr";
    btn.dataset.iid = String(mr.iid ?? "");
    btn.textContent = `!${mr.iid} ${mr.title || ""}`;
    btn.addEventListener("click", () => void openMr(btn.dataset.iid));
    list.append(btn);
  });
  return list;
}

async function refreshTickets() {
  const btn = document.getElementById("gitlabRefreshBtn");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Refreshing…";
  }
  try {
    await aird("POST", `/api/gitlab/refresh?${qs()}`);
    await loadDashboard();
    await loadFolderFiles();
  } catch (err) {
    alert(err.message || "Refresh failed");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Refresh tickets";
    }
  }
}

async function refreshStatus() {
  _status = await aird("GET", `/api/gitlab/status?${qs()}`);
  return _status;
}

function renderStrip() {
  const strip = document.getElementById("gitlabStrip");
  if (!strip || !_status) return;
  const b = _status.binding;
  const bindBtn = document.createElement("button");
  bindBtn.type = "button";
  bindBtn.className = "btn btn-ghost btn-xs";
  bindBtn.id = "gitlabBindBtn";
  bindBtn.textContent = b ? "GitLab settings" : "Link GitLab";
  bindBtn.addEventListener("click", openBind);
  strip.replaceChildren(bindBtn);
  if (_status.is_self) {
    const hint = document.createElement("span");
    hint.className = "text-xs opacity-60";
    hint.textContent = _status.token_configured ? "Token ready" : "Token missing — set in Admin → Plugins";
    strip.append(hint);
  } else {
    const viewerBtn = document.createElement("button");
    viewerBtn.type = "button";
    viewerBtn.className = "btn btn-ghost btn-xs";
    viewerBtn.id = "gitlabViewerTokenBtn";
    viewerBtn.textContent = "Browser GitLab token";
    viewerBtn.addEventListener("click", openViewerToken);
    strip.append(viewerBtn);
  }
  const mrsBtn = document.createElement("button");
  mrsBtn.type = "button";
  mrsBtn.className = "btn btn-ghost btn-xs";
  mrsBtn.id = "gitlabMrsBtn";
  mrsBtn.textContent = "MRs";
  mrsBtn.addEventListener("click", openMrs);
  const boardBtn = document.createElement("button");
  boardBtn.type = "button";
  boardBtn.className = "btn btn-ghost btn-xs";
  boardBtn.id = "gitlabBoardBtn";
  boardBtn.textContent = "Board";
  boardBtn.addEventListener("click", openBoard);
  const ciBadge = document.createElement("span");
  ciBadge.id = "gitlabCiBadge";
  ciBadge.className = "gitlab-badge";
  ciBadge.textContent = "CI …";
  strip.append(mrsBtn, boardBtn, ciBadge);
}

async function loadCi() {
  const el = document.getElementById("gitlabCiBadge");
  if (!el || !_status?.binding) {
    if (el) el.textContent = "CI —";
    return;
  }
  try {
    let latest;
    if (_status.is_self) {
      const data = await aird("GET", `/api/gitlab/pipelines?${qs()}`);
      latest = data.latest;
    } else {
      const host = _status.binding.gitlab_host;
      const token = viewerToken(host);
      if (!token) {
        el.textContent = "CI (token)";
        return;
      }
      const list = await glFetch(
        host,
        token,
        `projects/${encProject(_status.binding.code_project)}/pipelines?per_page=1`
      );
      latest = Array.isArray(list) ? list[0] : null;
    }
    if (!latest) {
      el.textContent = "CI none";
      return;
    }
    el.className = `gitlab-badge ${badgeClass(latest.status)}`;
    el.textContent = `CI ${latest.status} #${latest.iid || latest.id}`;
  } catch (err) {
    el.textContent = "CI error";
    el.title = err.message || "";
  }
}

function parentPath(dir) {
  const parts = trimSlashes(dir).split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

function navigateTo(path) {
  setCurrentPath(path);
  const url = new URL(location.href);
  const rel = currentPath();
  if (rel) url.searchParams.set("path", rel);
  else url.searchParams.delete("path");
  history.replaceState(null, "", url);
  void bootGitlab();
}

function renderPathBar() {
  const bar = document.getElementById("gitlabPathBar");
  if (!bar) return;
  const dir = currentPath();
  const parts = dir.split("/").filter(Boolean);
  const nodes = [];
  const home = document.createElement("a");
  home.href = "#";
  home.className = "gitlab-path-crumb";
  home.dataset.path = "";
  home.textContent = "Home";
  nodes.push(home);
  parts.forEach((part, i) => {
    const sep = document.createElement("span");
    sep.className = "opacity-40";
    sep.textContent = "/";
    nodes.push(sep);
    const link = document.createElement("a");
    link.href = "#";
    link.className = "gitlab-path-crumb";
    link.dataset.path = parts.slice(0, i + 1).join("/");
    link.textContent = part;
    nodes.push(link);
  });
  bar.replaceChildren(...nodes);
  bar.querySelectorAll(".gitlab-path-crumb").forEach((link) => {
    link.addEventListener("click", (ev) => {
      ev.preventDefault();
      navigateTo(link.dataset.path || "");
    });
  });
}

function commentLabel(count, issues) {
  const bits = [];
  if (issues?.length) bits.push(`${issues.length} issue(s)`);
  if (count) bits.push(`${count} comment(s)`);
  return bits.length ? bits.join(", ") : "Comments";
}

function joinRel(dir, name) {
  const base = trimSlashes(dir);
  return base ? `${base}/${name}` : name;
}

function setCurrentPath(path) {
  const rel = String(path || "").replace(/^\/+/, "");
  const hidden = document.getElementById("currentPath");
  if (hidden) hidden.value = rel;
  const input = document.getElementById("gitlabPath");
  if (input) input.value = rel;
}

async function loadFolderFiles() {
  const list = document.getElementById("gitlabFileList");
  if (!list || !_status) return;
  const dir = currentPath();
  const enc = dir.split("/").filter(Boolean).map(encodeURIComponent).join("/");
  listNote(list, "text-sm opacity-60", "Loading files…");
  let files = [];
  try {
    const res = await fetch(enc ? `/api/files/${enc}` : "/api/files/", { credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "Could not list folder");
    files = data.files || [];
  } catch (err) {
    listNote(list, "text-error text-sm", err.message || "Could not list folder");
    return;
  }
  const names = files.map((f) => f.name).filter(Boolean);
  const counts = {};
  let issuesBy = {};
  if (names.length) {
    try {
      const meta = await aird("GET", `/api/gitlab/folder-meta?${qs({ name: names })}`);
      Object.assign(counts, meta.comment_counts || {});
    } catch (_) { /* ignore */ }
    try {
      if (_status.is_self && _status.binding && _status.token_configured) {
        const data = await aird("GET", `/api/gitlab/cached-issues?${qs({ name: names })}`);
        issuesBy = data.issues_by_name || {};
      }
    } catch (_) { /* ignore */ }
  }
  if (!files.length && !dir) {
    listNote(list, "text-sm opacity-60", "This folder is empty.");
    return;
  }
  const rows = [];
  if (dir) {
    const up = fileRow("gitlab-file-link gitlab-open-dir", parentPath(dir), "", "📁", "..");
    up.btn.addEventListener("click", () => navigateTo(up.btn.dataset.path || ""));
    rows.push(up.li);
  }
  files.forEach((f) => {
    const full = joinRel(dir, f.name);
    const label = commentLabel(counts[f.name] || 0, issuesBy[f.name] || []);
    const linkClass = f.is_dir ? "gitlab-file-link gitlab-open-dir" : "gitlab-file-link gitlab-open-file";
    const row = fileRow(linkClass, full, f.name, f.is_dir ? "📁" : "📄", f.name);
    row.btn.addEventListener("click", () => {
      if (f.is_dir) navigateTo(full);
      else void openCommentsPanel(full, f.name, false);
    });
    const comments = document.createElement("button");
    comments.type = "button";
    comments.className = "btn btn-ghost btn-xs gitlab-open-comments";
    comments.textContent = label;
    comments.addEventListener("click", (ev) => {
      ev.stopPropagation();
      void openCommentsPanel(full, f.name, !!f.is_dir);
    });
    row.li.append(comments);
    rows.push(row.li);
  });
  list.replaceChildren(...rows);
  renderPathBar();
}

function openPanel(title, content) {
  const panel = panelEl();
  if (!panel) return;
  panel.hidden = false;
  panel.querySelector(".gitlab-panel-title").textContent = title;
  const body = panel.querySelector(".gitlab-panel-body");
  if (!body) return;
  if (content instanceof Node) body.replaceChildren(content);
  else {
    const p = document.createElement("p");
    p.className = "text-sm";
    p.textContent = String(content);
    body.replaceChildren(p);
  }
}

function setPanelError(message) {
  const p = document.createElement("p");
  p.className = "text-error";
  p.textContent = message || "";
  panelBodyEl()?.replaceChildren(p);
}

function closePanel() {
  const panel = panelEl();
  if (panel) panel.hidden = true;
  _panelTarget = null;
  if (_ws) {
    _ws.close();
    _ws = null;
  }
}

function openBind() {
  const b = _status?.binding || {};
  const form = document.createElement("form");
  form.className = "gitlab-form";
  form.id = "gitlabBindForm";
  form.append(
    formField("GitLab host", textInput("gitlab_host", b.gitlab_host || "https://gitlab.com")),
    formField("Code project (CI / MRs)", textInput("code_project", b.code_project || "", { placeholder: "group/app" })),
    formField("Issues / board project", textInput("issues_project", b.issues_project || "", { placeholder: "group/scrum" })),
    formField("Board id", textInput("board_iid", b.board_iid || "")),
    formField("Repo path prefix", textInput("repo_path_prefix", b.repo_path_prefix || "")),
  );
  const actions = document.createElement("div");
  actions.className = "mt-3 flex gap-2";
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-primary btn-sm";
  saveBtn.type = "submit";
  saveBtn.textContent = "Save";
  actions.append(saveBtn);
  if (b.code_project) {
    const removeBtn = document.createElement("button");
    removeBtn.className = "btn btn-ghost btn-sm";
    removeBtn.type = "button";
    removeBtn.id = "gitlabUnbind";
    removeBtn.textContent = "Remove";
    actions.append(removeBtn);
  }
  form.append(actions);
  openPanel("Link GitLab", form);
  document.getElementById("gitlabBindForm")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    try {
      await aird("PUT", `/api/gitlab/bindings?${qs()}`, Object.fromEntries(fd.entries()));
      await bootGitlab();
      closePanel();
    } catch (err) {
      alert(err.message);
    }
  });
  document.getElementById("gitlabUnbind")?.addEventListener("click", async () => {
    await aird("DELETE", `/api/gitlab/bindings?${qs()}`);
    await bootGitlab();
    closePanel();
  });
}

function openViewerToken() {
  const host = _status?.binding?.gitlab_host || "https://gitlab.com";
  const wrap = document.createDocumentFragment();
  const intro = document.createElement("p");
  intro.className = "text-sm opacity-70";
  intro.textContent = "Never sent to Aird. Needed to see live GitLab data on a share.";
  const form = document.createElement("form");
  form.className = "gitlab-form";
  form.id = "gitlabVTok";
  form.append(
    formField("Token", textInput("token", viewerToken(host), { type: "password" })),
    formField(
      "CORS bridge (optional)",
      textInput("bridge", localStorage.getItem(BRIDGE_KEY) || "", { placeholder: "http://127.0.0.1:8765" }),
    ),
  );
  const saveBtn = document.createElement("button");
  saveBtn.className = "btn btn-primary btn-sm mt-2";
  saveBtn.type = "submit";
  saveBtn.textContent = "Save in this browser";
  form.append(saveBtn);
  wrap.append(intro, form);
  openPanel("Your GitLab token (browser only)", wrap);
  document.getElementById("gitlabVTok")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    setViewerToken(host, fd.get("token"));
    const bridgeField = fd.get("bridge");
    const bridge = (typeof bridgeField === "string" ? bridgeField : "").trim().replace(/\/$/, "");
    if (LOCAL_BRIDGES.has(bridge)) localStorage.setItem(BRIDGE_KEY, bridge);
    else localStorage.removeItem(BRIDGE_KEY);
    closePanel();
    void loadCi();
  });
}

function buildCommentsPanelBody(issues, comments, canWrite, target) {
  const body = document.createDocumentFragment();
  if (issues.length) {
    const issueHead = document.createElement("h4");
    issueHead.className = "font-semibold text-sm mt-2";
    issueHead.textContent = "Linked issues";
    body.append(issueHead);
    issues.forEach((iss) => {
      const row = document.createElement("div");
      row.append(externalLink(iss.web_url, `#${iss.iid} ${iss.title || ""}`));
      body.append(row);
    });
  } else {
    const emptyIssues = document.createElement("p");
    emptyIssues.className = "text-xs opacity-60";
    emptyIssues.textContent = `No GitLab issue mentions for this ${target}.`;
    body.append(emptyIssues);
  }
  const commentsHead = document.createElement("h4");
  commentsHead.className = "font-semibold text-sm mt-3";
  commentsHead.textContent = "Aird comments";
  body.append(commentsHead);
  if (!comments.length) {
    const emptyComments = document.createElement("p");
    emptyComments.className = "text-xs opacity-60";
    emptyComments.textContent = "No comments yet.";
    body.append(emptyComments);
  } else {
    comments.forEach((c) => {
      const block = document.createElement("div");
      block.className = "gitlab-comment";
      block.dataset.id = String(c.id ?? "");
      const meta = document.createElement("div");
      meta.className = "gitlab-comment-meta";
      meta.textContent = `${c.author_username || ""} · ${c.created_at || ""}${c.gitlab_note_id ? " · promoted" : ""}`;
      const text = document.createElement("div");
      text.textContent = c.body || "";
      block.append(meta, text);
      if (canWrite && !c.gitlab_note_id && issues[0]) {
        const promote = document.createElement("button");
        promote.type = "button";
        promote.className = "btn btn-ghost btn-xs gitlab-promote";
        promote.dataset.id = String(c.id ?? "");
        promote.dataset.iid = String(issues[0].iid ?? "");
        promote.textContent = "Promote";
        block.append(promote);
      }
      body.append(block);
    });
  }
  if (canWrite) {
    const form = document.createElement("form");
    form.id = "gitlabCommentForm";
    form.className = "gitlab-form mt-3";
    const area = document.createElement("textarea");
    area.name = "body";
    area.className = "textarea textarea-bordered w-full";
    area.rows = 3;
    form.append(formField("Comment", area));
    const postBtn = document.createElement("button");
    postBtn.className = "btn btn-primary btn-sm mt-2";
    postBtn.type = "submit";
    postBtn.textContent = "Post";
    form.append(postBtn);
    body.append(form);
  } else {
    const ro = document.createElement("p");
    ro.className = "text-xs opacity-60";
    ro.textContent = "Read-only share — you can view comments.";
    body.append(ro);
  }
  return body;
}

async function openCommentsPanel(fullPath, name, isDir = false) {
  const rel = (fullPath || "").replace(/^\/+/, "");
  _panelTarget = { fullPath, name, isDir };
  openPanel(name || rel, "Loading…");
  const q = qs({ path: rel });
  let comments = [];
  let canWrite = false;
  try {
    const data = await aird("GET", `/api/gitlab/comments?${q}`);
    comments = data.comments || [];
    canWrite = data.can_write;
  } catch (err) {
    setPanelError(err.message);
    return;
  }
  let issues = [];
  try {
    if (_status?.is_self) {
      const data = await aird("GET", `/api/gitlab/cached-issues?${qs({ path: rel, name: [name] })}`);
      issues = data.issues_by_name?.[name] ?? [];
    }
  } catch (_) { /* ignore */ }
  const target = isDir ? "folder" : "file";
  panelBodyEl()?.replaceChildren(buildCommentsPanelBody(issues, comments, canWrite, target));
  document.getElementById("gitlabCommentForm")?.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const body = new FormData(ev.target).get("body");
    try {
      await aird("POST", `/api/gitlab/comments?${q}`, { body, gitlab_issue_iid: issues[0]?.iid });
      void openCommentsPanel(fullPath, name, isDir);
    } catch (err) {
      alert(err.message);
    }
  });
  panelEl().querySelectorAll(".gitlab-promote").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        if (_status.is_self) {
          await aird("POST", `/api/gitlab/comments/${btn.dataset.id}/promote?${q}`, {
            gitlab_issue_iid: Number(btn.dataset.iid),
          });
        } else {
          const host = _status.binding.gitlab_host;
          const token = viewerToken(host);
          const project = _status.binding.issues_project || _status.binding.code_project;
          const note = await glFetch(
            host,
            token,
            `projects/${encProject(project)}/issues/${btn.dataset.iid}/notes`,
            { method: "POST", body: { body: btn.closest(".gitlab-comment").innerText } }
          );
          await aird("PATCH", `/api/gitlab/comments/${btn.dataset.id}?${q}`, {
            gitlab_issue_iid: Number(btn.dataset.iid),
            gitlab_note_id: note.id,
          });
        }
        void openCommentsPanel(fullPath, name, isDir);
      } catch (err) {
        alert(err.message);
      }
    });
  });
  connectCommentsWs(rel);
}

function connectCommentsWs(rel) {
  if (_ws) _ws.close();
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = qs({ path: rel });
  _ws = new WebSocket(`${proto}://${location.host}/ws/file-comments?${q}`);
  _ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data);
      if (msg.type?.startsWith("comment_") && _panelTarget) {
        const { fullPath, name, isDir } = _panelTarget;
        void openCommentsPanel(fullPath, name, isDir);
      }
    } catch (_) { /* ignore */ }
  };
}

async function openMrs() {
  openPanel("Merge requests", "Loading…");
  try {
    let mrs;
    if (_status.is_self) {
      mrs = (await aird("GET", `/api/gitlab/mrs?${qs()}`)).merge_requests || [];
    } else {
      const host = _status.binding.gitlab_host;
      mrs = await glFetch(host, viewerToken(host), `projects/${encProject(_status.binding.code_project)}/merge_requests?state=opened`);
    }
    const list = document.createDocumentFragment();
    if (!(mrs || []).length) {
      const empty = document.createElement("p");
      empty.textContent = "No open MRs";
      list.append(empty);
    } else {
      mrs.forEach((mr) => {
        const card = document.createElement("div");
        card.className = "gitlab-card";
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "link gitlab-mr";
        btn.dataset.iid = String(mr.iid ?? "");
        btn.textContent = `!${mr.iid} ${mr.title || ""}`;
        btn.addEventListener("click", () => void openMr(btn.dataset.iid));
        const status = document.createElement("div");
        status.className = "text-xs opacity-60";
        status.textContent = mr.merge_status || mr.detailed_merge_status || "";
        card.append(btn, status);
        list.append(card);
      });
    }
    panelBodyEl()?.replaceChildren(list);
  } catch (err) {
    setPanelError(err.message);
  }
}

async function openMr(iid) {
  openPanel(`MR !${iid}`, "Loading…");
  try {
    let mr, notes;
    if (_status.is_self) {
      const data = await aird("GET", `/api/gitlab/mrs/${iid}?${qs()}`);
      mr = data.merge_request;
      notes = data.notes;
    } else {
      const host = _status.binding.gitlab_host;
      const token = viewerToken(host);
      const project = encProject(_status.binding.code_project);
      mr = await glFetch(host, token, `projects/${project}/merge_requests/${iid}`);
      notes = await glFetch(host, token, `projects/${project}/merge_requests/${iid}/notes?sort=asc`);
    }
    const body = document.createDocumentFragment();
    const title = document.createElement("p");
    title.textContent = mr.title || "";
    const desc = document.createElement("p");
    desc.className = "text-sm";
    desc.textContent = mr.description || "";
    const pipe = document.createElement("p");
    pipe.className = "text-xs opacity-60";
    pipe.textContent = `Pipeline: ${mr.head_pipeline?.status || "—"}`;
    body.append(title, desc, pipe);
    (notes || []).filter((n) => !n.system).forEach((n) => {
      const block = document.createElement("div");
      block.className = "gitlab-comment";
      const meta = document.createElement("div");
      meta.className = "gitlab-comment-meta";
      meta.textContent = n.author?.username || "";
      const text = document.createElement("div");
      text.textContent = n.body || "";
      block.append(meta, text);
      body.append(block);
    });
    const form = document.createElement("form");
    form.id = "gitlabMrNote";
    form.className = "gitlab-form";
    const area = document.createElement("textarea");
    area.name = "body";
    area.className = "textarea textarea-bordered w-full";
    area.rows = 2;
    form.append(formField("Comment", area));
    const commentBtn = document.createElement("button");
    commentBtn.className = "btn btn-primary btn-sm mt-2";
    commentBtn.type = "submit";
    commentBtn.textContent = "Comment";
    form.append(commentBtn);
    body.append(form);
    panelBodyEl()?.replaceChildren(body);
    document.getElementById("gitlabMrNote")?.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const body = new FormData(ev.target).get("body");
      try {
        if (_status.is_self) {
          await aird("POST", `/api/gitlab/mrs/${iid}/notes?${qs()}`, { body });
        } else {
          const host = _status.binding.gitlab_host;
          await glFetch(
            host,
            viewerToken(host),
            `projects/${encProject(_status.binding.code_project)}/merge_requests/${iid}/notes`,
            { method: "POST", body: { body } }
          );
        }
        void openMr(iid);
      } catch (err) {
        alert(err.message);
      }
    });
  } catch (err) {
    setPanelError(err.message);
  }
}

async function openBoard() {
  openPanel("Scrum board", "Loading…");
  try {
    let payload;
    if (_status.is_self) {
      payload = await aird("GET", `/api/gitlab/board?${qs()}`);
    } else {
      const host = _status.binding.gitlab_host;
      const token = viewerToken(host);
      const project = _status.binding.issues_project || _status.binding.code_project;
      const boards = await glFetch(host, token, `projects/${encProject(project)}/boards`);
      const board = _status.binding.board_iid
        ? await glFetch(host, token, `projects/${encProject(project)}/boards/${_status.binding.board_iid}`)
        : boards[0];
      const issues = await glFetch(host, token, `projects/${encProject(project)}/issues?state=opened&per_page=100`);
      payload = { board, lists: board?.lists || [], issues, busyness: null };
    }
    const busy = payload.busyness?.users || [];
    const body = document.createDocumentFragment();
    const busyWrap = document.createElement("div");
    if (!busy.length) {
      const hint = document.createElement("p");
      hint.className = "text-xs opacity-60";
      hint.textContent = "Busyness available for folder owner.";
      busyWrap.append(hint);
    } else {
      busy.forEach((u) => {
        const row = document.createElement("div");
        row.className = "gitlab-busy";
        row.textContent = `${u.username || ""} · ${u.allocated_hours}h / ${u.capacity_hours}h free ${u.free_hours}h `;
        const bar = document.createElement("div");
        bar.className = "gitlab-busy-bar";
        const fill = document.createElement("span");
        fill.style.width = `${Math.min(100, (u.load_ratio || 0) * 100)}%`;
        bar.append(fill);
        row.append(bar);
        busyWrap.append(row);
      });
    }
    body.append(busyWrap);
    const boardRow = document.createElement("div");
    boardRow.className = "gitlab-board-row mt-2";
    const lists = payload.lists || [];
    const issues = payload.issues || [];
    if (!lists.length) {
      const empty = document.createElement("p");
      empty.textContent = "No board lists";
      boardRow.append(empty);
    } else {
      lists.forEach((lane) => {
        const label = lane.label?.name || lane.list_type || "Open";
        const laneEl = document.createElement("div");
        laneEl.className = "gitlab-board-lane";
        const strong = document.createElement("strong");
        strong.textContent = label;
        laneEl.append(strong);
        const cards = issues.filter((iss) => {
          const labels = (iss.labels || []).map((x) => (typeof x === "string" ? x : x.name));
          return labels.includes(label) || (!lane.label && !labels.length);
        });
        if (!cards.length) {
          const emptyLane = document.createElement("p");
          emptyLane.className = "text-xs opacity-50";
          emptyLane.textContent = "Empty";
          laneEl.append(emptyLane);
        } else {
          cards.forEach((iss) => {
            const stale = (iss.days_since_comment || 0) >= 3;
            const card = document.createElement("div");
            card.className = stale ? "gitlab-card gitlab-card--stale" : "gitlab-card";
            card.append(externalLink(iss.web_url, `#${iss.iid} ${iss.title || ""}`));
            const meta = document.createElement("div");
            meta.className = "text-xs opacity-60";
            const bits = [];
            if (iss.days_in_lane != null) bits.push(`${Number(iss.days_in_lane).toFixed(1)}d in lane`);
            if (iss.days_since_comment != null) bits.push(`last comment ${Number(iss.days_since_comment).toFixed(1)}d`);
            meta.textContent = bits.join(" · ");
            card.append(meta);
            laneEl.append(card);
          });
        }
        boardRow.append(laneEl);
      });
    }
    body.append(boardRow);
    panelBodyEl()?.replaceChildren(body);
  } catch (err) {
    setPanelError(err.message);
  }
}

export async function bootGitlab() {
  if (!cfg().enabled) return;
  try {
    await refreshStatus();
  } catch (err) {
    console.warn("GitLab status", err);
    return;
  }
  renderStrip();
  await Promise.all([loadCi(), loadDashboard()]);
  await loadFolderFiles();
}

export function initGitlabUi() {
  if (!cfg().enabled) return;
  document.getElementById("gitlabPanelClose")?.addEventListener("click", closePanel);
  document.getElementById("gitlabRefreshBtn")?.addEventListener("click", refreshTickets);
  document.getElementById("gitlabPathForm")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    setCurrentPath(document.getElementById("gitlabPath")?.value);
    navigateTo(currentPath());
  });
  void bootGitlab();
}
