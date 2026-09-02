"use strict";

function cfg() {
  return globalThis.__GITLAB_BROWSE_CONFIG || {};
}

function currentPath() {
  return (document.getElementById("currentPath")?.value || "").replace(/^\/+/, "");
}

function xsrf() {
  const m = document.cookie.match(/(?:^|; )_xsrf=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function qs(extra = {}) {
  const params = new URLSearchParams();
  params.set("path", extra.path ?? currentPath());
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

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function collectFileNames() {
  return [...document.querySelectorAll("#fileTable .file-row")]
    .map((row) => {
      const isDir = row.querySelector(".row-checkbox")?.dataset?.isDir === "1";
      if (isDir) return null;
      const path = row.dataset.path || "";
      return path.split("/").pop();
    })
    .filter(Boolean);
}

function ensurePanel() {
  let panel = document.getElementById("browseGitlabPanel");
  if (panel) return panel;
  const anchor = document.getElementById("browseListingPanel");
  if (!anchor) return null;
  panel = document.createElement("section");
  panel.id = "browseGitlabPanel";
  panel.className = "browse-gitlab-panel";
  panel.hidden = true;
  panel.innerHTML = `
    <div class="browse-gitlab-head">
      <strong>Related GitLab tickets</strong>
      <div class="flex items-center gap-2">
        <span id="browseGitlabCacheMeta" class="text-xs opacity-60"></span>
        <button type="button" class="btn btn-ghost btn-xs" id="browseGitlabRefresh">Refresh</button>
        <a href="/gitlab" class="btn btn-ghost btn-xs" id="browseGitlabOpen">Open GitLab</a>
      </div>
    </div>
    <div class="browse-gitlab-body" id="browseGitlabBody"></div>`;
  anchor.parentNode?.insertBefore(panel, anchor);
  panel.querySelector("#browseGitlabRefresh")?.addEventListener("click", refreshBrowseGitlab);
  return panel;
}

function formatCacheAge(iso) {
  if (!iso) return "Not cached";
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "Just now";
  if (mins < 60) return `${mins}m ago`;
  return `${Math.round(mins / 60)}h ago`;
}

async function refreshBrowseGitlab() {
  const btn = document.getElementById("browseGitlabRefresh");
  if (btn) {
    btn.disabled = true;
    btn.textContent = "…";
  }
  try {
    await aird("POST", `/api/gitlab/refresh?${qs()}`);
    await loadBrowseGitlab();
  } catch (err) {
    console.warn("GitLab refresh", err);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = "Refresh";
    }
  }
}

async function loadBrowseGitlab() {
  if (!cfg().enabled) return;
  const panel = ensurePanel();
  const body = document.getElementById("browseGitlabBody");
  const meta = document.getElementById("browseGitlabCacheMeta");
  if (!panel || !body) return;
  let status;
  try {
    status = await aird("GET", `/api/gitlab/status?${qs()}`);
  } catch (_) {
    panel.hidden = true;
    return;
  }
  if (!status.binding || !status.is_self || !status.token_configured) {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const link = document.getElementById("browseGitlabOpen");
  if (link) {
    const path = currentPath();
    link.href = path ? `/gitlab?path=${encodeURIComponent(path)}` : "/gitlab";
  }
  const names = collectFileNames().slice(0, 40);
  if (!names.length) {
    body.innerHTML = `<p class="text-xs opacity-60">No files in this folder.</p>`;
    return;
  }
  try {
    const data = await aird("GET", `/api/gitlab/cached-issues?${qs({ name: names })}`);
    if (meta) meta.textContent = formatCacheAge(data.cache?.refreshed_at);
    const mapped = data.issues_by_name || {};
    const withIssues = names.filter((n) => (mapped[n] || []).length);
    if (!withIssues.length) {
      body.innerHTML = `<p class="text-xs opacity-60">No cached ticket references for files here. Hit Refresh after linking this folder to GitLab.</p>`;
      return;
    }
    body.innerHTML = withIssues.map((name) => {
      const issues = mapped[name] || [];
      const items = issues.map((iss) =>
        `<a class="link text-sm" href="${escapeHtml(iss.web_url)}" target="_blank">#${iss.iid} ${escapeHtml(iss.title)}</a>`
      ).join("");
      return `<div class="browse-gitlab-file"><strong>${escapeHtml(name)}</strong><div class="browse-gitlab-issues">${items}</div></div>`;
    }).join("");
  } catch (err) {
    body.innerHTML = `<p class="text-error text-sm">${escapeHtml(err.message)}</p>`;
  }
}

export function initBrowseGitlabPanel() {
  if (!cfg().enabled) return;
  loadBrowseGitlab();
  const observer = new MutationObserver(() => loadBrowseGitlab());
  const table = document.getElementById("fileTable");
  if (table) observer.observe(table.querySelector("tbody") || table, { childList: true, subtree: true });
}
