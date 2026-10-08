"use strict";

const GRAPH = "https://graph.microsoft.com/v1.0";
const STORE = "aird.onedriveBrowser.tokens";
const PKCE = "aird.onedriveBrowser.pkce";
const PENDING = "aird.onedriveBrowser.pending";
const LS_DEFAULT_SAVE = "aird.onedriveBrowser.defaultSave";
const SCOPES = "Files.ReadWrite offline_access User.Read";

function browserConfig() {
  return globalThis.__ONEDRIVE_BROWSER_CONFIG;
}

function jsonParseNull() {
  return null;
}

function b64url(bytes) {
  let bin = "";
  bytes.forEach((b) => { bin += String.fromCodePoint(b); });
  return btoa(bin).replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "");
}

function randomBytes(n) {
  const buf = new Uint8Array(n);
  crypto.getRandomValues(buf);
  return buf;
}

async function sha256b64url(text) {
  const data = new TextEncoder().encode(text);
  const hash = await crypto.subtle.digest("SHA-256", data);
  return b64url(new Uint8Array(hash));
}

function loadTokens() {
  try {
    return JSON.parse(sessionStorage.getItem(STORE) || "null");
  } catch {
    return null;
  }
}

function msTokenUrl(tenant) {
  if (tenant === "organizations") {
    return "https://login.microsoftonline.com/organizations/oauth2/v2.0/token";
  }
  if (tenant === "consumers") {
    return "https://login.microsoftonline.com/consumers/oauth2/v2.0/token";
  }
  return "https://login.microsoftonline.com/common/oauth2/v2.0/token";
}

function msAuthorizeUrl(tenant) {
  if (tenant === "organizations") {
    return "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize";
  }
  if (tenant === "consumers") {
    return "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize";
  }
  return "https://login.microsoftonline.com/common/oauth2/v2.0/authorize";
}

function storedSecret(value) {
  const text = typeof value === "string" ? value : "";
  const match = /^[A-Za-z0-9._~+/-]{1,8192}$/.exec(text);
  return match ? match[0] : "";
}

function appReturnPath(value) {
  const fallback = "/files/";
  try {
    const url = new URL(value || fallback, globalThis.location.origin);
    if (url.origin !== globalThis.location.origin || !url.pathname.startsWith("/")) return fallback;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

function tokenExpiresAt(tokens) {
  const at = Number(tokens?.expires_at);
  if (Number.isFinite(at)) return at;
  const expiresIn = Number(tokens?.expires_in);
  if (Number.isFinite(expiresIn)) {
    return Date.now() + Math.max(60, expiresIn) * 1000;
  }
  return 0;
}

function saveTokens(tokens) {
  if (!tokens || typeof tokens !== "object") return;
  const access = storedSecret(tokens.access_token);
  const refresh = storedSecret(tokens.refresh_token);
  if (!access) return;
  const safe = {
    access_token: access,
    refresh_token: refresh,
    expires_at: tokenExpiresAt(tokens),
  };
  sessionStorage.setItem(STORE, JSON.stringify(safe));
}

function savePkce({ verifier, state, returnTo }) {
  const safeVerifier = storedSecret(verifier);
  const safeState = storedSecret(state);
  if (!safeVerifier || !safeState) return;
  sessionStorage.setItem(PKCE, JSON.stringify({
    verifier: safeVerifier,
    state: safeState,
    returnTo: appReturnPath(returnTo),
  }));
}

function clearTokens() {
  sessionStorage.removeItem(STORE);
}

function isSignedIn() {
  const tokens = loadTokens();
  return !!(tokens?.access_token || tokens?.refresh_token);
}

function getDefaultSave() {
  try {
    return JSON.parse(localStorage.getItem(LS_DEFAULT_SAVE) || "null");
  } catch {
    return null;
  }
}

function setDefaultSave(opts) {
  const path = opts?.path;
  const folderId = opts?.folderId;
  const parts = String(path || "").split("/").filter(Boolean);
  const norm = parts.join("/");
  if (!norm && !folderId) {
    localStorage.removeItem(LS_DEFAULT_SAVE);
    return null;
  }
  const next = {
    path: norm,
    folderId: folderId || "",
    savedAt: Date.now(),
  };
  localStorage.setItem(LS_DEFAULT_SAVE, JSON.stringify(next));
  return next;
}

async function resolveDefaultFolderId(token) {
  const saved = getDefaultSave();
  if (!saved) return null;
  if (saved.folderId) return saved.folderId;
  if (!saved.path) return null;
  const folderId = await ensureFolderPath(token, saved.path.split("/").filter(Boolean));
  setDefaultSave({ path: saved.path, folderId });
  return folderId;
}

function redirectUri() {
  return `${globalThis.location.origin}/onedrive-browser/callback`;
}

function authorizeUrl(clientId, tenant, challenge, state) {
  const u = new URL(msAuthorizeUrl(tenant));
  u.searchParams.set("client_id", clientId);
  u.searchParams.set("response_type", "code");
  u.searchParams.set("redirect_uri", redirectUri());
  u.searchParams.set("response_mode", "query");
  u.searchParams.set("scope", SCOPES);
  u.searchParams.set("code_challenge", challenge);
  u.searchParams.set("code_challenge_method", "S256");
  u.searchParams.set("state", state);
  return u.toString();
}

async function exchangeCode(code, verifier, clientId, tenant) {
  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: "authorization_code",
    code,
    redirect_uri: redirectUri(),
    code_verifier: verifier,
    scope: SCOPES,
  });
  const res = await fetch(msTokenUrl(tenant), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const data = await res.json().catch(jsonParseNull);
  if (!res.ok) throw new Error(data?.error_description || data?.error || "Token exchange failed");
  return {
    access_token: data.access_token,
    refresh_token: data.refresh_token || "",
    expires_at: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
  };
}

async function refreshTokens(tokens, clientId, tenant) {
  if (!tokens?.refresh_token) return null;
  const body = new URLSearchParams({
    client_id: clientId,
    grant_type: "refresh_token",
    refresh_token: tokens.refresh_token,
    scope: SCOPES,
  });
  const res = await fetch(msTokenUrl(tenant), {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body,
  });
  const data = await res.json().catch(jsonParseNull);
  if (!res.ok || !data) return null;
  const next = {
    access_token: data.access_token,
    refresh_token: data.refresh_token || tokens.refresh_token,
    expires_at: Date.now() + Math.max(60, Number(data.expires_in) || 3600) * 1000,
  };
  saveTokens(next);
  return next;
}

async function loadPublicConfig() {
  const existing = browserConfig();
  if (existing?.clientId) return existing;
  const res = await fetch("/api/onedrive-browser/config", { credentials: "same-origin" });
  const data = await res.json().catch(jsonParseNull);
  if (!res.ok || !data?.client_id) throw new Error(data?.error || "OneDrive browser is not configured.");
  globalThis.__ONEDRIVE_BROWSER_CONFIG = {
    enabled: true,
    clientId: data.client_id,
    tenant: data.tenant || "common",
  };
  return globalThis.__ONEDRIVE_BROWSER_CONFIG;
}

async function ensureAccessToken() {
  const publicCfg = await loadPublicConfig();
  let tokens = loadTokens();
  if (tokens?.access_token && tokens.expires_at > Date.now() + 30_000) return tokens.access_token;
  if (tokens) {
    const refreshed = await refreshTokens(tokens, publicCfg.clientId, publicCfg.tenant);
    if (refreshed?.access_token) return refreshed.access_token;
  }
  const verifier = b64url(randomBytes(32));
  const challenge = await sha256b64url(verifier);
  const state = b64url(randomBytes(16));
  savePkce({
    verifier,
    state,
    returnTo: globalThis.location.href,
  });
  globalThis.location.assign(authorizeUrl(publicCfg.clientId, publicCfg.tenant, challenge, state));
  return null;
}

async function finishCallback() {
  const params = new URLSearchParams(globalThis.location.search);
  const err = params.get("error_description") || params.get("error");
  const msg = document.getElementById("odCbMsg");
  if (err) {
    if (msg) msg.textContent = err;
    return;
  }
  const code = params.get("code");
  const state = params.get("state");
  const pkce = JSON.parse(sessionStorage.getItem(PKCE) || "null");
  sessionStorage.removeItem(PKCE);
  if (!code || pkce?.state !== state) {
    if (msg) msg.textContent = "OneDrive sign-in was cancelled.";
    return;
  }
  const publicCfg = await loadPublicConfig();
  const verifier = storedSecret(pkce.verifier);
  if (!verifier) {
    if (msg) msg.textContent = "OneDrive sign-in was cancelled.";
    return;
  }
  const tokens = await exchangeCode(code, verifier, publicCfg.clientId, publicCfg.tenant);
  saveTokens(tokens);
  const next = appReturnPath(pkce.returnTo);
  if (next.startsWith("/")) globalThis.location.replace(next);
}

async function graph(token, path, opts) {
  const headers = {
    Authorization: `Bearer ${token}`,
    Accept: "application/json",
  };
  const extraHeaders = opts?.headers;
  if (extraHeaders && typeof extraHeaders === "object" && !Array.isArray(extraHeaders)) {
    Object.assign(headers, extraHeaders);
  }
  const res = await fetch(`${GRAPH}${path}`, {
    method: opts?.method,
    body: opts?.body,
    headers,
  });
  const data = await res.json().catch(jsonParseNull);
  if (!res.ok) throw new Error(data?.error?.message || data?.error || res.statusText);
  return data;
}

function encPath(p) {
  return String(p || "").split("/").filter(Boolean).map(encodeURIComponent).join("/");
}

async function listFolders(token, itemId) {
  const path = itemId && itemId !== "root"
    ? `/me/drive/items/${itemId}/children?$select=id,name,folder`
    : "/me/drive/root/children?$select=id,name,folder";
  const data = await graph(token, path);
  return (data.value || []).filter((item) => item.folder);
}

async function createFolder(token, parentId, name) {
  const url = parentId && parentId !== "root"
    ? `/me/drive/items/${parentId}/children`
    : "/me/drive/root/children";
  return graph(token, url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, folder: {}, "@microsoft.graph.conflictBehavior": "fail" }),
  });
}

async function uploadBlob(token, parentId, filename, blob) {
  const safe = encodeURIComponent(filename);
  const target = parentId && parentId !== "root"
    ? `/me/drive/items/${parentId}:/${safe}:/content`
    : `/me/drive/root:/${safe}:/content`;
  const res = await fetch(`${GRAPH}${target}`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": blob.type || "application/octet-stream" },
    body: blob,
  });
  if (!res.ok) {
    const data = await res.json().catch(jsonParseNull);
    throw new Error(data?.error?.message || "Upload failed");
  }
}

function downloadUrl(relPath) {
  return `/files/${encPath(relPath)}?download=1`;
}

async function fetchAirdBlob(relPath) {
  const res = await fetch(downloadUrl(relPath), { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not read file from Aird");
  return res.blob();
}

async function listAirdDir(relPath) {
  const res = await fetch(`/api/files/${encPath(relPath)}`, { credentials: "same-origin" });
  if (!res.ok) throw new Error("Could not list folder");
  const data = await res.json();
  return data.files || [];
}

async function uploadTree(token, parentId, relPath, isDir) {
  const name = relPath.split("/").filter(Boolean).pop() || relPath;
  if (!isDir) {
    const blob = await fetchAirdBlob(relPath);
    await uploadBlob(token, parentId, name, blob);
    return;
  }
  const folder = await createFolder(token, parentId, name).catch(async () => {
    const kids = await listFolders(token, parentId);
    return kids.find((k) => k.name === name);
  });
  if (!folder?.id) throw new Error("Could not create OneDrive folder");
  const children = await listAirdDir(relPath);
  for (const child of children) {
    const childPath = relPath ? `${relPath}/${child.name}` : child.name;
    await uploadTree(token, folder.id, childPath, !!child.is_dir);
  }
}

function showPicker({ token, onPick, title }) {
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10000;display:flex;align-items:center;justify-content:center;";
  overlay.innerHTML = `
    <div style="background:var(--color-base-100,#fff);color:inherit;border-radius:12px;max-width:28rem;width:92%;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.25);">
      <div style="padding:12px 16px;border-bottom:1px solid var(--color-base-300,#ddd);display:flex;justify-content:space-between;gap:8px;align-items:center;">
        <strong>${title || "Save to OneDrive"}</strong>
        <button type="button" class="od-pick-close btn btn-ghost btn-xs">✕</button>
      </div>
      <div class="od-pick-crumbs text-xs px-4 py-2 opacity-70"></div>
      <div class="od-pick-list" style="overflow:auto;padding:8px 12px;min-height:12rem;"></div>
      <div style="padding:12px;border-top:1px solid var(--color-base-300,#ddd);display:flex;gap:8px;flex-wrap:wrap;">
        <button type="button" class="od-pick-new btn btn-ghost btn-sm">New folder</button>
        <button type="button" class="od-pick-save btn btn-primary btn-sm" style="margin-left:auto;">Use this folder</button>
      </div>
      <p class="od-pick-msg text-xs px-4 pb-3"></p>
    </div>`;
  document.body.appendChild(overlay);
  const stack = [{ id: "root", name: "OneDrive" }];
  const listEl = overlay.querySelector(".od-pick-list");
  const crumbEl = overlay.querySelector(".od-pick-crumbs");
  const msgEl = overlay.querySelector(".od-pick-msg");

  function say(text, err) {
    msgEl.textContent = text || "";
    msgEl.style.color = err ? "var(--color-error,#b91c1c)" : "";
  }

  async function render() {
    crumbEl.textContent = stack.map((s) => s.name).join(" / ");
    listEl.textContent = "Loading…";
    try {
      const folders = await listFolders(token, stack.at(-1).id);
      listEl.innerHTML = "";
      if (stack.length > 1) {
        const up = document.createElement("button");
        up.type = "button";
        up.className = "btn btn-ghost btn-sm w-full justify-start";
        up.textContent = "← Up";
        up.addEventListener("click", () => { stack.pop(); void render(); });
        listEl.append(up);
      }
      folders.forEach((folder) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-ghost btn-sm w-full justify-start";
        btn.textContent = `📁 ${folder.name}`;
        btn.addEventListener("click", () => { stack.push({ id: folder.id, name: folder.name }); void render(); });
        listEl.append(btn);
      });
      if (!folders.length) {
        const empty = document.createElement("p");
        empty.className = "text-sm opacity-60 px-2";
        empty.textContent = "No subfolders.";
        listEl.append(empty);
      }
    } catch (err) {
      listEl.textContent = "";
      say(err.message, true);
    }
  }

  overlay.querySelector(".od-pick-close").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  overlay.querySelector(".od-pick-new").addEventListener("click", async () => {
    const name = globalThis.prompt("New folder name");
    if (!name?.trim()) return;
    try {
      const created = await createFolder(token, stack.at(-1).id, name.trim());
      stack.push({ id: created.id, name: created.name || name.trim() });
      await render();
    } catch (err) {
      say(err.message, true);
    }
  });
  overlay.querySelector(".od-pick-save").addEventListener("click", async () => {
    try {
      say("Working…");
      await onPick(stack.at(-1).id, stack.map((s) => s.name));
      overlay.remove();
    } catch (err) {
      say(err.message, true);
    }
  });
  void render();
}

function sanitizePendingPath(raw) {
  const parts = String(raw).replaceAll("\\", "/").split("/").filter(Boolean);
  if (parts.some((p) => p === "..")) return "";
  return parts.join("/").slice(0, 2048);
}

function savePendingItems(items) {
  if (!Array.isArray(items)) return;
  const safe = items
    .filter((item) => item && typeof item.path === "string")
    .map((item) => ({
      path: sanitizePendingPath(item.path),
      isDir: !!item.isDir,
    }))
    .filter((item) => item.path);
  if (!safe.length) return;
  sessionStorage.setItem(PENDING, JSON.stringify(safe));
}

export async function savePathsToOneDrive(items, opts) {
  if (!items?.length) return;
  savePendingItems(items);
  const options = opts && typeof opts === 'object' ? opts : Object.create(null);
  const token = await ensureAccessToken();
  if (!token) return;
  sessionStorage.removeItem(PENDING);

  const forcePick = !!options.forcePick;
  let folderId = null;
  if (!forcePick) {
    try {
      folderId = await resolveDefaultFolderId(token);
    } catch (_) {
      folderId = null;
    }
  }

  if (folderId) {
    for (const item of items) {
      await uploadTree(token, folderId, item.path, !!item.isDir);
    }
    return { folderId, usedDefault: true };
  }

  return new Promise((resolve, reject) => {
    showPicker({
      token,
      title: options.title || "Save to OneDrive",
      onPick: async (pickedId, names) => {
        for (const item of items) {
          await uploadTree(token, pickedId, item.path, !!item.isDir);
        }
        if (options.rememberAsDefault) {
          const path = Array.isArray(names) && names.length > 1 ? names.slice(1).join("/") : "";
          setDefaultSave({ path, folderId: pickedId });
        }
        resolve({ folderId: pickedId, usedDefault: false });
      },
    });
  });
}

async function resumePending() {
  const raw = sessionStorage.getItem(PENDING);
  if (!raw || !loadTokens()?.access_token) return;
  sessionStorage.removeItem(PENDING);
  try {
    const items = JSON.parse(raw);
    await savePathsToOneDrive(items);
  } catch {
    /* user can retry */
  }
}

export function initOneDriveBrowserUi() {
  if (globalThis.location.pathname === "/onedrive-browser/callback") {
    finishCallback().catch((err) => {
      const el = document.getElementById("odCbMsg");
      if (el) el.textContent = err.message || "Sign-in failed.";
    });
    return;
  }
  if (!browserConfig()?.enabled) return;
  resumePending().catch(() => {});
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initOneDriveBrowserUi);
} else {
  initOneDriveBrowserUi();
}

async function listChildren(token, itemId) {
  const path = itemId && itemId !== "root"
    ? `/me/drive/items/${itemId}/children?$select=id,name,folder,file`
    : "/me/drive/root/children?$select=id,name,folder,file";
  const data = await graph(token, path);
  return data.value || [];
}

async function downloadItemText(token, itemId) {
  const res = await fetch(`${GRAPH}/me/drive/items/${itemId}/content`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(jsonParseNull);
    throw new Error(data?.error?.message || "Download failed");
  }
  return res.text();
}

function showFilePicker({ token, title, filter, onPick }) {
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:10000;display:flex;align-items:center;justify-content:center;";
  overlay.innerHTML = `
    <div style="background:var(--color-base-100,#fff);color:inherit;border-radius:12px;max-width:28rem;width:92%;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 10px 40px rgba(0,0,0,.25);">
      <div style="padding:12px 16px;border-bottom:1px solid var(--color-base-300,#ddd);display:flex;justify-content:space-between;gap:8px;align-items:center;">
        <strong>${title || "Open from OneDrive"}</strong>
        <button type="button" class="od-pick-close btn btn-ghost btn-xs">✕</button>
      </div>
      <div class="od-pick-crumbs text-xs px-4 py-2 opacity-70"></div>
      <div class="od-pick-list" style="overflow:auto;padding:8px 12px;min-height:12rem;"></div>
      <p class="od-pick-msg text-xs px-4 pb-3"></p>
    </div>`;
  document.body.appendChild(overlay);
  const stack = [{ id: "root", name: "OneDrive" }];
  const listEl = overlay.querySelector(".od-pick-list");
  const crumbEl = overlay.querySelector(".od-pick-crumbs");
  const msgEl = overlay.querySelector(".od-pick-msg");

  function say(text, err) {
    msgEl.textContent = text || "";
    msgEl.style.color = err ? "var(--color-error,#b91c1c)" : "";
  }

  async function render() {
    crumbEl.textContent = stack.map((s) => s.name).join(" / ");
    listEl.textContent = "Loading…";
    try {
      const items = await listChildren(token, stack.at(-1).id);
      const folders = items.filter((item) => item.folder);
      const files = items.filter((item) => item.file && (!filter || filter(item)));
      listEl.innerHTML = "";
      if (stack.length > 1) {
        const up = document.createElement("button");
        up.type = "button";
        up.className = "btn btn-ghost btn-sm w-full justify-start";
        up.textContent = "← Up";
        up.addEventListener("click", () => { stack.pop(); void render(); });
        listEl.append(up);
      }
      folders.forEach((folder) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-ghost btn-sm w-full justify-start";
        btn.textContent = `📁 ${folder.name}`;
        btn.addEventListener("click", () => { stack.push({ id: folder.id, name: folder.name }); void render(); });
        listEl.append(btn);
      });
      files.forEach((file) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "btn btn-ghost btn-sm w-full justify-start";
        btn.textContent = `📄 ${file.name}`;
        btn.addEventListener("click", async () => {
          try {
            say("Loading…");
            await onPick(file);
            overlay.remove();
          } catch (err) {
            say(err.message, true);
          }
        });
        listEl.append(btn);
      });
      if (!folders.length && !files.length) {
        const empty = document.createElement("p");
        empty.className = "text-sm opacity-60 px-2";
        empty.textContent = "This folder is empty.";
        listEl.append(empty);
      }
    } catch (err) {
      listEl.textContent = "";
      say(err.message, true);
    }
  }

  overlay.querySelector(".od-pick-close").addEventListener("click", () => overlay.remove());
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
  void render();
}

async function ensureFolderPath(token, parts) {
  let parentId = "root";
  for (const name of parts) {
    const kids = await listChildren(token, parentId);
    let folder = kids.find((k) => k.folder && k.name === name);
    if (!folder) {
      folder = await createFolder(token, parentId, name);
    }
    parentId = folder.id;
  }
  return parentId;
}

globalThis.AirdOneDriveBrowser = {
  savePathsToOneDrive,
  initOneDriveBrowserUi,
  ensureAccessToken,
  isSignedIn,
  signOut: () => {
    clearTokens();
  },
  signIn: async () => ensureAccessToken(),
  getDefaultSave,
  setDefaultSave,
  resolveDefaultFolderId,
  showFolderPicker: (token, onPick, opts) => new Promise((resolve, reject) => {
    showPicker({
      token,
      title: opts?.title || "Choose OneDrive folder",
      onPick: async (folderId, names) => {
        try {
          const result = await onPick(folderId, names);
          resolve(result);
        } catch (err) {
          reject(err);
        }
      },
    });
  }),
  showFilePicker,
  uploadBlob,
  downloadItemText,
  ensureFolderPath,
  listChildren,
};
