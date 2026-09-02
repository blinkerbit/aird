"use strict";

function boardRow(board, index) {
  const b = board || {};
  return `<div class="board-row" data-index="${index}">
    <div class="board-row-grid">
      <label class="form-control">
        <span class="label-text text-xs">Label</span>
        <input type="text" class="input input-bordered input-xs board-label" value="${escapeAttr(b.label || "")}" placeholder="Sprint board" maxlength="80">
      </label>
      <label class="form-control">
        <span class="label-text text-xs">GitLab host</span>
        <input type="text" class="input input-bordered input-xs board-host" value="${escapeAttr(b.gitlab_host || "https://gitlab.com")}" placeholder="https://gitlab.com">
      </label>
      <label class="form-control">
        <span class="label-text text-xs">Issues / board project</span>
        <input type="text" class="input input-bordered input-xs board-issues" value="${escapeAttr(b.issues_project || "")}" placeholder="group/scrum">
      </label>
      <label class="form-control">
        <span class="label-text text-xs">Code project (MRs / CI)</span>
        <input type="text" class="input input-bordered input-xs board-code" value="${escapeAttr(b.code_project || "")}" placeholder="group/app">
      </label>
      <label class="form-control">
        <span class="label-text text-xs">Board id</span>
        <input type="number" class="input input-bordered input-xs board-iid" value="${escapeAttr(b.board_iid || "")}" placeholder="1" min="1">
      </label>
    </div>
    <button type="button" class="btn btn-ghost btn-xs mt-1 board-remove">Remove</button>
  </div>`;
}

function escapeAttr(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;");
}

function collectBoards() {
  const rows = document.querySelectorAll(".board-row");
  const boards = [];
  rows.forEach((row) => {
    const label = row.querySelector(".board-label")?.value?.trim();
    const issues = row.querySelector(".board-issues")?.value?.trim();
    if (!label || !issues) return;
    const code = row.querySelector(".board-code")?.value?.trim() || issues;
    const host = row.querySelector(".board-host")?.value?.trim() || "https://gitlab.com";
    const boardIid = row.querySelector(".board-iid")?.value?.trim();
    boards.push({
      label,
      gitlab_host: host,
      issues_project: issues,
      code_project: code,
      board_iid: boardIid ? Number(boardIid) : null,
    });
  });
  return boards;
}

function renderBoards(boards) {
  const list = document.getElementById("gitlabBoardsList");
  if (!list) return;
  const items = boards?.length ? boards : [{}];
  list.innerHTML = items.map((b, i) => boardRow(b, i)).join("");
  list.querySelectorAll(".board-remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.closest(".board-row")?.remove();
      if (!list.querySelector(".board-row")) renderBoards([{}]);
    });
  });
}

function selectPlugin(id) {
  document.querySelectorAll(".admin-plugins-nav-item").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.plugin === id);
  });
  document.querySelectorAll(".admin-plugins-pane").forEach((pane) => {
    pane.hidden = pane.dataset.plugin !== id;
  });
  const input = document.getElementById("selectedPluginInput");
  if (input) input.value = id;
  const url = new URL(location.href);
  url.searchParams.set("p", id);
  history.replaceState(null, "", url);
}

document.addEventListener("DOMContentLoaded", () => {
  const cfg = globalThis.__ADMIN_PLUGINS || {};
  renderBoards(cfg.gitlabBoards || []);

  document.getElementById("gitlabAddBoardBtn")?.addEventListener("click", () => {
    const list = document.getElementById("gitlabBoardsList");
    if (!list) return;
    const index = list.querySelectorAll(".board-row").length;
    list.insertAdjacentHTML("beforeend", boardRow({}, index));
    const last = list.lastElementChild;
    last?.querySelector(".board-remove")?.addEventListener("click", () => {
      last.remove();
      if (!list.querySelector(".board-row")) renderBoards([{}]);
    });
  });

  document.querySelectorAll(".admin-plugins-nav-item").forEach((btn) => {
    btn.addEventListener("click", () => selectPlugin(btn.dataset.plugin));
  });

  const params = new URLSearchParams(location.search);
  const initial = params.get("p") || document.getElementById("selectedPluginInput")?.value;
  if (initial) selectPlugin(initial);

  document.getElementById("adminPluginsForm")?.addEventListener("submit", () => {
    const hidden = document.getElementById("gitlabBoardsJson");
    if (hidden) hidden.value = JSON.stringify(collectBoards());
  });

  function syncGitlabTokenPanels() {
    const source = document.querySelector('input[name="gitlab_token_source"]:checked')?.value || "env";
    const envBlock = document.getElementById("gitlabTokenEnvBlock");
    const storedBlock = document.getElementById("gitlabTokenStoredBlock");
    if (envBlock) envBlock.hidden = source !== "env";
    if (storedBlock) storedBlock.hidden = source !== "stored";
  }

  document.querySelectorAll('input[name="gitlab_token_source"]').forEach((input) => {
    input.addEventListener("change", syncGitlabTokenPanels);
  });
  syncGitlabTokenPanels();

  function syncOnedriveAuthPanels() {
    const source = document.querySelector('input[name="onedrive_auth_source"]:checked')?.value || "device";
    const device = document.getElementById("onedriveDeviceBlock");
    const env = document.getElementById("onedriveEnvBlock");
    const stored = document.getElementById("onedriveStoredBlock");
    if (device) device.hidden = source !== "device";
    if (env) env.hidden = source !== "env";
    if (stored) stored.hidden = source !== "stored";
  }

  document.querySelectorAll('input[name="onedrive_auth_source"]').forEach((input) => {
    input.addEventListener("change", syncOnedriveAuthPanels);
  });
  syncOnedriveAuthPanels();

  let _odPollTimer = null;

  function xsrf() {
    const m = document.cookie.match(/(?:^|; )_xsrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function odAdminApi(url, opts = {}) {
    const headers = Object.assign({ Accept: "application/json", "X-XSRFToken": xsrf() }, opts.headers || {});
    const res = await fetch(url, Object.assign({ credentials: "same-origin", method: "POST" }, opts, { headers }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok && !data.pending) throw new Error(data.error || res.statusText);
    return data;
  }

  function stopOdPoll() {
    if (_odPollTimer) {
      clearInterval(_odPollTimer);
      _odPollTimer = null;
    }
  }

  async function pollOdDevice() {
    const status = document.getElementById("onedriveDeviceStatus");
    try {
      const data = await odAdminApi("/api/onedrive-host/device-poll");
      if (data.pending) return;
      stopOdPoll();
      if (status) status.textContent = "Signed in — reload page to refresh status.";
      document.getElementById("onedriveDevicePrompt")?.classList.add("hidden");
    } catch (err) {
      stopOdPoll();
      if (status) status.textContent = err.message;
    }
  }

  document.getElementById("onedriveDeviceStart")?.addEventListener("click", async () => {
    const status = document.getElementById("onedriveDeviceStatus");
    const prompt = document.getElementById("onedriveDevicePrompt");
    stopOdPoll();
    if (status) status.textContent = "Starting…";
    try {
      const data = await odAdminApi("/api/onedrive-host/device-start");
      const url = document.getElementById("onedriveDeviceUrl");
      const code = document.getElementById("onedriveDeviceCode");
      if (url) {
        url.href = data.verification_uri || "#";
        url.textContent = data.verification_uri || "";
      }
      if (code) code.textContent = data.user_code || "";
      prompt?.classList.remove("hidden");
      if (status) status.textContent = "Waiting for sign-in…";
      const interval = Math.max(3, Number(data.interval) || 5) * 1000;
      _odPollTimer = setInterval(() => { pollOdDevice().catch(() => {}); }, interval);
    } catch (err) {
      if (status) status.textContent = err.message;
    }
  });

  async function odHostApi(url, opts = {}) {
    const method = opts.method || "GET";
    const headers = Object.assign({ Accept: "application/json", "X-XSRFToken": xsrf() }, opts.headers || {});
    if (opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(url, Object.assign({ credentials: "same-origin", method }, opts, { headers }));
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function mapRow(map, index) {
    const m = map || {};
    return `<div class="od-host-map-row border border-base-300 rounded-lg p-3 space-y-2" data-map-id="${escapeAttr(m.id || "")}" data-index="${index}">
      <div class="grid gap-2 md:grid-cols-2">
        <label class="form-control"><span class="label-text text-xs">Username</span>
          <input type="text" class="input input-bordered input-xs map-username" value="${escapeAttr(m.username || "admin")}"></label>
        <label class="form-control"><span class="label-text text-xs">Local path</span>
          <input type="text" class="input input-bordered input-xs map-local" value="${escapeAttr(m.local_path || "")}" placeholder="Documents"></label>
        <label class="form-control"><span class="label-text text-xs">Remote path</span>
          <input type="text" class="input input-bordered input-xs map-remote" value="${escapeAttr(m.remote_path || "")}" placeholder="backup/docs"></label>
        <label class="form-control"><span class="label-text text-xs">Ignore patterns (optional)</span>
          <input type="text" class="input input-bordered input-xs map-ignore" value="${escapeAttr(m.ignore_extra || "")}"></label>
      </div>
      <div class="flex gap-2">
        <button type="button" class="btn btn-primary btn-xs map-save">Save</button>
        <button type="button" class="btn btn-ghost btn-xs map-delete">Delete</button>
      </div>
    </div>`;
  }

  async function refreshOdHostStatus() {
    const line = document.getElementById("odHostStatusLine");
    const failed = document.getElementById("odHostFailedList");
    const pauseBtn = document.getElementById("odHostPauseBtn");
    if (!line) return;
    try {
      const data = await odHostApi("/api/onedrive-host/status");
      const rt = data.runtime || {};
      const run = data.last_run || {};
      const parts = [];
      parts.push(rt.paused ? "Paused" : "Running");
      if (rt.in_progress?.length) parts.push(`${rt.in_progress.length} in progress`);
      if (rt.queued?.length) parts.push(`${rt.queued.length} queued`);
      if (run.last_finished_at) parts.push(`Last: ${String(run.last_finished_at).replace("T", " ").slice(0, 19)} UTC`);
      line.textContent = parts.join(" · ") || "Idle";
      if (pauseBtn) pauseBtn.textContent = rt.paused ? "Resume sync" : "Pause sync";
      if (failed) {
        const items = (rt.failed || []).slice(0, 8);
        failed.innerHTML = items.length
          ? `<strong>Failed:</strong> ${items.map((f) => escapeAttr(f.path || "?")).join(", ")}`
          : "";
      }
    } catch (err) {
      line.textContent = err.message;
    }
  }

  async function loadOdHostMaps() {
    const list = document.getElementById("odHostMapsList");
    if (!list) return;
    const data = await odHostApi("/api/onedrive-host/maps");
    const maps = data.maps || [];
    list.innerHTML = maps.length ? maps.map((m, i) => mapRow(m, i)).join("") : mapRow({}, 0);
    list.querySelectorAll(".map-save").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest(".od-host-map-row");
        const body = {
          id: row.dataset.mapId || undefined,
          username: row.querySelector(".map-username")?.value?.trim(),
          local_path: row.querySelector(".map-local")?.value?.trim(),
          remote_path: row.querySelector(".map-remote")?.value?.trim(),
          ignore_extra: row.querySelector(".map-ignore")?.value || "",
        };
        await odHostApi("/api/onedrive-host/maps", { method: "PUT", body: JSON.stringify(body) });
        await loadOdHostMaps();
        await refreshOdHostStatus();
      });
    });
    list.querySelectorAll(".map-delete").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const row = btn.closest(".od-host-map-row");
        const id = row.dataset.mapId;
        if (id && !confirm("Delete this folder map?")) return;
        if (id) await odHostApi(`/api/onedrive-host/maps?id=${encodeURIComponent(id)}`, { method: "DELETE" });
        row.remove();
        if (!list.querySelector(".od-host-map-row")) list.innerHTML = mapRow({}, 0);
      });
    });
  }

  async function loadOdHostRules() {
    const box = document.getElementById("odHostIncludeAird");
    if (!box) return;
    const data = await odHostApi("/api/onedrive-host/rules");
    box.checked = !!data.include_aird_config;
    box.addEventListener("change", async () => {
      await odHostApi("/api/onedrive-host/rules", {
        method: "PUT",
        body: JSON.stringify({ include_aird_config: box.checked }),
      });
    });
  }

  document.getElementById("odHostSyncNowBtn")?.addEventListener("click", async () => {
    await odHostApi("/api/onedrive-host/sync", { method: "POST", body: "{}" });
    await refreshOdHostStatus();
  });

  document.getElementById("odHostRetryBtn")?.addEventListener("click", async () => {
    await odHostApi("/api/onedrive-host/retry", { method: "POST", body: "{}" });
    await refreshOdHostStatus();
  });

  document.getElementById("odHostPauseBtn")?.addEventListener("click", async () => {
    const data = await odHostApi("/api/onedrive-host/rules");
    const paused = !data.sync_paused;
    await odHostApi("/api/onedrive-host/rules", {
      method: "PUT",
      body: JSON.stringify({ sync_paused: paused }),
    });
    await refreshOdHostStatus();
  });

  document.getElementById("odHostAddMapBtn")?.addEventListener("click", () => {
    const list = document.getElementById("odHostMapsList");
    if (!list) return;
    const index = list.querySelectorAll(".od-host-map-row").length;
    list.insertAdjacentHTML("beforeend", mapRow({}, index));
  });

  if (document.getElementById("odHostStatusPanel")) {
    refreshOdHostStatus().catch(() => {});
    loadOdHostMaps().catch(() => {});
    loadOdHostRules().catch(() => {});
    setInterval(() => { refreshOdHostStatus().catch(() => {}); }, 15000);
  }
});
