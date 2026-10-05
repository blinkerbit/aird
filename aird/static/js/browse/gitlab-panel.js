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

function note(text) {
  const node = document.createElement("p");
  node.className = "text-xs opacity-60";
  node.textContent = text;
  return node;
}

function safeTicketUrl(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:" && parsed.protocol !== "http:") return null;
    return parsed.href;
  } catch {
    return null;
  }
}

function issueBlock(name, issues) {
  const wrap = document.createElement("div");
  wrap.className = "browse-gitlab-file";
  const title = document.createElement("strong");
  title.textContent = name;
  const list = document.createElement("div");
  list.className = "browse-gitlab-issues";
  issues.forEach((iss) => {
    const href = safeTicketUrl(iss.web_url);
    const label = `#${iss.iid} ${iss.title || ""}`;
    if (!href) {
      const span = document.createElement("span");
      span.className = "text-sm";
      span.textContent = label;
      list.appendChild(span);
      return;
    }
    const link = document.createElement("a");
    link.className = "link text-sm";
    link.href = href;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = label;
    list.appendChild(link);
  });
  wrap.append(title, list);
  return wrap;
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
    body.replaceChildren(note("No files in this folder."));
    return;
  }
  try {
    const data = await aird("GET", `/api/gitlab/cached-issues?${qs({ name: names })}`);
    if (meta) meta.textContent = formatCacheAge(data.cache?.refreshed_at);
    const mapped = data.issues_by_name || {};
    const withIssues = names.filter((n) => (mapped[n] || []).length);
    if (!withIssues.length) {
      body.replaceChildren(note("No cached ticket references for files here. Hit Refresh after linking this folder to GitLab."));
      return;
    }
    body.replaceChildren(...withIssues.map((name) => issueBlock(name, mapped[name] || [])));
  } catch (err) {
    const errNode = note(err.message || "GitLab lookup failed");
    errNode.classList.add("text-error");
    body.replaceChildren(errNode);
  }
}

export function initBrowseGitlabPanel() {
  if (!cfg().enabled) return;
  void loadBrowseGitlab();
  const observer = new MutationObserver(() => void loadBrowseGitlab());
  const table = document.getElementById("fileTable");
  if (table) observer.observe(table.querySelector("tbody") || table, { childList: true, subtree: true });
}
