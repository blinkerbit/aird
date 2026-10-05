"use strict";

const TOKEN_KEY = (host) => `aird.gitlab.token.${host || "gitlab.com"}`;
const BRIDGE_KEY = "aird.gitlab.bridge";

function xsrf() {
  const m = document.cookie.match(/(?:^|; )_xsrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
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

function apiRoot(host) {
  const bridge = localStorage.getItem(BRIDGE_KEY);
  const base = (host || "https://gitlab.com").replace(/\/$/, "");
  if (bridge) return `${bridge.replace(/\/$/, "")}/api/v4`;
  return `${base}/api/v4`;
}

async function glFetch(host, token, path, { method = "GET", body } = {}) {
  const url = `${apiRoot(host)}/${String(path).replace(/^\//, "")}`;
  const headers = { Accept: "application/json", "PRIVATE-TOKEN": token };
  if (body) headers["Content-Type"] = "application/json";
  if (bridgeHost()) headers["X-Gitlab-Host"] = (host || "https://gitlab.com").replace(/\/$/, "");
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.message || data.error || resp.statusText);
  return data;
}

function bridgeHost() {
  return Boolean(localStorage.getItem(BRIDGE_KEY));
}

function encProject(project) {
  return encodeURIComponent(project || "");
}

function panelEl() {
  return document.getElementById("gitlabPanel");
}

function setHtml(el, html) {
  if (el) el.innerHTML = html;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
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
  const userHtml = user
    ? `<div class="gitlab-dash-card"><div class="gitlab-dash-label">Signed in</div><div class="gitlab-dash-value">${escapeHtml(user.name || user.username)}</div></div>`
    : `<div class="gitlab-dash-card"><div class="gitlab-dash-label">Signed in</div><div class="gitlab-dash-value opacity-60">Token required</div></div>`;
  const teamHtml = users.length
    ? `<div class="gitlab-dash-card gitlab-dash-card--wide"><div class="gitlab-dash-label">Active on board</div><div class="gitlab-dash-users">${users.map((u) => `<span class="gitlab-dash-user">${escapeHtml(u.name || u.username)}</span>`).join("")}</div></div>`
    : "";
  grid.innerHTML = `${userHtml}
    <div class="gitlab-dash-card"><div class="gitlab-dash-label">Open issues</div><div class="gitlab-dash-value">${_dashboard.open_issue_count ?? issues.length}</div></div>
    <div class="gitlab-dash-card"><div class="gitlab-dash-label">Open MRs</div><div class="gitlab-dash-value">${_dashboard.open_mr_count ?? mrs.length}</div></div>
    ${teamHtml}
    <div class="gitlab-dash-card gitlab-dash-card--wide">
      <div class="gitlab-dash-label">Recent issues</div>
      <div class="gitlab-dash-list">${issues.slice(0, 8).map((iss) =>
        `<a class="link text-sm" href="${escapeHtml(iss.web_url || "#")}" target="_blank">#${iss.iid} ${escapeHtml(iss.title)}</a>`
      ).join("") || "<span class='text-xs opacity-60'>Refresh to load cached tickets</span>"}</div>
    </div>
    <div class="gitlab-dash-card gitlab-dash-card--wide">
      <div class="gitlab-dash-label">Open merge requests</div>
      <div class="gitlab-dash-list">${mrs.slice(0, 6).map((mr) =>
        `<button type="button" class="link text-sm gitlab-dash-mr" data-iid="${mr.iid}">!${mr.iid} ${escapeHtml(mr.title)}</button>`
      ).join("") || "<span class='text-xs opacity-60'>None</span>"}</div>
    </div>`;
  if (meta) meta.textContent = formatCacheAge(_dashboard.cache?.refreshed_at);
  grid.querySelectorAll(".gitlab-dash-mr").forEach((btn) => {
    btn.addEventListener("click", () => void openMr(btn.dataset.iid));
  });
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
  const bits = [];
  bits.push(`<button type="button" class="btn btn-ghost btn-xs" id="gitlabBindBtn">${b ? "GitLab settings" : "Link GitLab"}</button>`);
  if (_status.is_self) {
    bits.push(`<span class="text-xs opacity-60">${_status.token_configured ? "Token ready" : "Token missing — set in Admin → Plugins"}</span>`);
  } else {
    bits.push(`<button type="button" class="btn btn-ghost btn-xs" id="gitlabViewerTokenBtn">Browser GitLab token</button>`);
  }
  bits.push(`<button type="button" class="btn btn-ghost btn-xs" id="gitlabMrsBtn">MRs</button>`);
  bits.push(`<button type="button" class="btn btn-ghost btn-xs" id="gitlabBoardBtn">Board</button>`);
  bits.push(`<span id="gitlabCiBadge" class="gitlab-badge">CI …</span>`);
  strip.innerHTML = bits.join("");
  strip.querySelector("#gitlabBindBtn")?.addEventListener("click", openBind);
  strip.querySelector("#gitlabViewerTokenBtn")?.addEventListener("click", openViewerToken);
  strip.querySelector("#gitlabMrsBtn")?.addEventListener("click", openMrs);
  strip.querySelector("#gitlabBoardBtn")?.addEventListener("click", openBoard);
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
  const crumbs = ['<a href="#" class="gitlab-path-crumb" data-path="">Home</a>'];
  parts.forEach((part, i) => {
    const partial = parts.slice(0, i + 1).join("/");
    crumbs.push(`<span class="opacity-40">/</span><a href="#" class="gitlab-path-crumb" data-path="${escapeHtml(partial)}">${escapeHtml(part)}</a>`);
  });
  bar.innerHTML = crumbs.join(" ");
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
  list.innerHTML = `<p class="text-sm opacity-60">Loading files…</p>`;
  let files = [];
  try {
    const res = await fetch(enc ? `/api/files/${enc}` : "/api/files/", { credentials: "same-origin" });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "Could not list folder");
    files = data.files || [];
  } catch (err) {
    list.innerHTML = `<p class="text-error text-sm">${escapeHtml(err.message)}</p>`;
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
    list.innerHTML = `<p class="text-sm opacity-60">This folder is empty.</p>`;
    return;
  }
  const rows = [];
  if (dir) {
    rows.push(`<li class="gitlab-file-row">
      <button type="button" class="gitlab-file-link gitlab-open-dir" data-path="${escapeHtml(parentPath(dir))}">
        <span class="gitlab-file-icon" aria-hidden="true">📁</span>
        <span>..</span>
      </button>
    </li>`);
  }
  files.forEach((f) => {
    const full = joinRel(dir, f.name);
    const n = counts[f.name] || 0;
    const issues = issuesBy[f.name] || [];
    const label = commentLabel(n, issues);
    const icon = f.is_dir ? "📁" : "📄";
    const linkClass = f.is_dir ? "gitlab-file-link gitlab-open-dir" : "gitlab-file-link gitlab-open-file";
    rows.push(`<li class="gitlab-file-row">
      <button type="button" class="${linkClass}" data-path="${escapeHtml(full)}" data-name="${escapeHtml(f.name)}" data-is-dir="${f.is_dir ? "1" : "0"}">
        <span class="gitlab-file-icon" aria-hidden="true">${icon}</span>
        <span class="gitlab-file-name">${escapeHtml(f.name)}</span>
      </button>
      <button type="button" class="btn btn-ghost btn-xs gitlab-open-comments" data-path="${escapeHtml(full)}" data-name="${escapeHtml(f.name)}">${escapeHtml(label)}</button>
    </li>`);
  });
  list.innerHTML = rows.join("");
  list.querySelectorAll(".gitlab-open-dir").forEach((btn) => {
    btn.addEventListener("click", () => navigateTo(btn.dataset.path || ""));
  });
  list.querySelectorAll(".gitlab-open-file").forEach((btn) => {
    btn.addEventListener("click", () => void openCommentsPanel(btn.dataset.path, btn.dataset.name, false));
  });
  list.querySelectorAll(".gitlab-open-comments").forEach((btn) => {
    btn.addEventListener("click", (ev) => {
      ev.stopPropagation();
      const row = btn.closest(".gitlab-file-row");
      const isDir = row?.querySelector(".gitlab-open-dir") != null;
      void openCommentsPanel(btn.dataset.path, btn.dataset.name, isDir);
    });
  });
  renderPathBar();
}

function openPanel(title, html) {
  const panel = panelEl();
  if (!panel) return;
  panel.hidden = false;
  panel.querySelector(".gitlab-panel-title").textContent = title;
  setHtml(panel.querySelector(".gitlab-panel-body"), html);
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
  openPanel(
    "Link GitLab",
    `<form class="gitlab-form" id="gitlabBindForm">
      <label>GitLab host <input name="gitlab_host" value="${escapeHtml(b.gitlab_host || "https://gitlab.com")}" /></label>
      <label>Code project (CI / MRs) <input name="code_project" value="${escapeHtml(b.code_project || "")}" placeholder="group/app" /></label>
      <label>Issues / board project <input name="issues_project" value="${escapeHtml(b.issues_project || "")}" placeholder="group/scrum" /></label>
      <label>Board id <input name="board_iid" value="${escapeHtml(b.board_iid || "")}" /></label>
      <label>Repo path prefix <input name="repo_path_prefix" value="${escapeHtml(b.repo_path_prefix || "")}" /></label>
      <div class="mt-3 flex gap-2">
        <button class="btn btn-primary btn-sm" type="submit">Save</button>
        ${b.code_project ? '<button class="btn btn-ghost btn-sm" type="button" id="gitlabUnbind">Remove</button>' : ""}
      </div>
    </form>`
  );
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
  openPanel(
    "Your GitLab token (browser only)",
    `<p class="text-sm opacity-70">Never sent to Aird. Needed to see live GitLab data on a share.</p>
     <form class="gitlab-form" id="gitlabVTok">
       <label>Token <input name="token" type="password" value="${escapeHtml(viewerToken(host))}" /></label>
       <label>CORS bridge (optional) <input name="bridge" placeholder="http://127.0.0.1:8765" value="${escapeHtml(localStorage.getItem(BRIDGE_KEY) || "")}" /></label>
       <button class="btn btn-primary btn-sm mt-2" type="submit">Save in this browser</button>
     </form>`
  );
  document.getElementById("gitlabVTok")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const fd = new FormData(ev.target);
    setViewerToken(host, fd.get("token"));
    const bridge = String(fd.get("bridge") || "").trim();
    if (bridge) localStorage.setItem(BRIDGE_KEY, bridge);
    else localStorage.removeItem(BRIDGE_KEY);
    closePanel();
    void loadCi();
  });
}

async function openCommentsPanel(fullPath, name, isDir = false) {
  const rel = (fullPath || "").replace(/^\/+/, "");
  _panelTarget = { fullPath, name, isDir };
  openPanel(name || rel, `<p class="text-sm">Loading…</p>`);
  const q = qs({ path: rel });
  let comments = [];
  let canWrite = false;
  try {
    const data = await aird("GET", `/api/gitlab/comments?${q}`);
    comments = data.comments || [];
    canWrite = data.can_write;
  } catch (err) {
    setHtml(panelEl().querySelector(".gitlab-panel-body"), `<p class="text-error">${escapeHtml(err.message)}</p>`);
    return;
  }
  let issues = [];
  try {
    if (_status?.is_self) {
      const data = await aird("GET", `/api/gitlab/cached-issues?${qs({ path: rel, name: [name] })}`);
      issues = (data.issues_by_name || {})[name] || [];
    }
  } catch (_) { /* ignore */ }
  const target = isDir ? "folder" : "file";
  const issueHtml = issues.length
    ? `<h4 class="font-semibold text-sm mt-2">Linked issues</h4>` +
      issues.map((iss) => `<div><a class="link" href="${escapeHtml(iss.web_url)}" target="_blank">#${iss.iid} ${escapeHtml(iss.title)}</a></div>`).join("")
    : `<p class="text-xs opacity-60">No GitLab issue mentions for this ${target}.</p>`;
  const commentsHtml = comments.map((c) => `
    <div class="gitlab-comment" data-id="${escapeHtml(c.id)}">
      <div class="gitlab-comment-meta">${escapeHtml(c.author_username)} · ${escapeHtml(c.created_at || "")}${c.gitlab_note_id ? " · promoted" : ""}</div>
      <div>${escapeHtml(c.body)}</div>
      ${canWrite && !c.gitlab_note_id && issues[0] ? `<button type="button" class="btn btn-ghost btn-xs gitlab-promote" data-id="${escapeHtml(c.id)}" data-iid="${issues[0].iid}">Promote</button>` : ""}
    </div>`).join("") || "<p class='text-xs opacity-60'>No comments yet.</p>";
  const form = canWrite
    ? `<form id="gitlabCommentForm" class="gitlab-form mt-3">
         <label>Comment <textarea name="body" class="textarea textarea-bordered w-full" rows="3"></textarea></label>
         <button class="btn btn-primary btn-sm mt-2" type="submit">Post</button>
       </form>`
    : `<p class="text-xs opacity-60">Read-only share — you can view comments.</p>`;
  setHtml(
    panelEl().querySelector(".gitlab-panel-body"),
    `${issueHtml}<h4 class="font-semibold text-sm mt-3">Aird comments</h4>${commentsHtml}${form}`
  );
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
      if (msg.type && msg.type.startsWith("comment_") && _panelTarget) {
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
    const html = (mrs || []).map((mr) =>
      `<div class="gitlab-card"><button type="button" class="link gitlab-mr" data-iid="${mr.iid}">!${mr.iid} ${escapeHtml(mr.title)}</button>
       <div class="text-xs opacity-60">${escapeHtml(mr.merge_status || mr.detailed_merge_status || "")}</div></div>`
    ).join("") || "<p>No open MRs</p>";
    setHtml(panelEl().querySelector(".gitlab-panel-body"), html);
    panelEl().querySelectorAll(".gitlab-mr").forEach((btn) => {
      btn.addEventListener("click", () => void openMr(btn.dataset.iid));
    });
  } catch (err) {
    setHtml(panelEl().querySelector(".gitlab-panel-body"), `<p class="text-error">${escapeHtml(err.message)}</p>`);
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
    const noteHtml = (notes || []).filter((n) => !n.system).map((n) =>
      `<div class="gitlab-comment"><div class="gitlab-comment-meta">${escapeHtml(n.author?.username || "")}</div>${escapeHtml(n.body)}</div>`
    ).join("");
    setHtml(
      panelEl().querySelector(".gitlab-panel-body"),
      `<p>${escapeHtml(mr.title)}</p><p class="text-sm">${escapeHtml(mr.description || "")}</p>
       <p class="text-xs opacity-60">Pipeline: ${escapeHtml(mr.head_pipeline?.status || "—")}</p>
       ${noteHtml}
       <form id="gitlabMrNote" class="gitlab-form"><label>Comment <textarea name="body" class="textarea textarea-bordered w-full" rows="2"></textarea></label>
       <button class="btn btn-primary btn-sm mt-2" type="submit">Comment</button></form>`
    );
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
    setHtml(panelEl().querySelector(".gitlab-panel-body"), `<p class="text-error">${escapeHtml(err.message)}</p>`);
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
    const busyHtml = busy.map((u) => {
      const pct = Math.min(100, (u.load_ratio || 0) * 100);
      return `<div class="gitlab-busy">${escapeHtml(u.username)} · ${u.allocated_hours}h / ${u.capacity_hours}h free ${u.free_hours}h
        <div class="gitlab-busy-bar"><span style="width:${pct}%"></span></div></div>`;
    }).join("");
    const lists = payload.lists || [];
    const issues = payload.issues || [];
    const lanes = lists.map((lane) => {
      const label = lane.label?.name || lane.list_type || "Open";
      const cards = issues.filter((iss) => {
        const labels = (iss.labels || []).map((x) => (typeof x === "string" ? x : x.name));
        return labels.includes(label) || (!lane.label && !labels.length);
      });
      const cardsHtml = cards.map((iss) => {
        const stale = (iss.days_since_comment || 0) >= 3;
        return `<div class="gitlab-card${stale ? " gitlab-card--stale" : ""}">
          <a class="link" href="${escapeHtml(iss.web_url || "#")}" target="_blank">#${iss.iid} ${escapeHtml(iss.title)}</a>
          <div class="text-xs opacity-60">${iss.days_in_lane != null ? `${Number(iss.days_in_lane).toFixed(1)}d in lane` : ""}
            ${iss.days_since_comment != null ? ` · last comment ${Number(iss.days_since_comment).toFixed(1)}d` : ""}</div>
        </div>`;
      }).join("");
      return `<div class="gitlab-board-lane"><strong>${escapeHtml(label)}</strong>${cardsHtml || "<p class='text-xs opacity-50'>Empty</p>"}</div>`;
    }).join("");
    setHtml(
      panelEl().querySelector(".gitlab-panel-body"),
      `<div>${busyHtml || "<p class='text-xs opacity-60'>Busyness available for folder owner.</p>"}</div>
       <div class="gitlab-board-row mt-2">${lanes || "<p>No board lists</p>"}</div>`
    );
  } catch (err) {
    setHtml(panelEl().querySelector(".gitlab-panel-body"), `<p class="text-error">${escapeHtml(err.message)}</p>`);
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
  await Promise.all([void loadCi(), loadDashboard()]);
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
