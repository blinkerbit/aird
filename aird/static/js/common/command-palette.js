/**
 * Aird command palette (Cmd+K / Ctrl+K).
 *
 * Self-bootstrapping: just include this script anywhere in the page and
 * the palette becomes available globally. Commands are static for v1
 * (open Tags, open Policies, open Audit log, open Admin) plus a dynamic
 * "Tag selection as ..." placeholder that is enabled when the page
 * exposes a `window.AirdCommandPalette.getSelection()` callback.
 */

(function () {
  if (window.__airdCommandPaletteInitialized) return;
  window.__airdCommandPaletteInitialized = true;

  const STATIC_COMMANDS = [
    { id: "open-tags", label: "Open: Resource tags", url: "/admin/tags" },
    { id: "open-policies", label: "Open: ABAC policies", url: "/admin/policies" },
    { id: "open-audit", label: "Open: Audit log", url: "/admin/audit" },
    { id: "open-admin", label: "Open: Admin settings", url: "/admin" },
    { id: "open-browse", label: "Open: File browser", url: "/files/" },
    { id: "open-share", label: "Open: Shares", url: "/share" },
    { id: "open-search", label: "Open: Super search", url: "/search" },
  ];

  let dialog = null;
  let input = null;
  let listEl = null;

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.id = "aird-command-palette";
    dialog.className = "modal";
    dialog.innerHTML =
      '<form method="dialog" class="modal-box w-full max-w-xl p-0">' +
      '  <div class="px-4 py-3 border-b border-base-300">' +
      '    <input id="aird-command-palette-input" type="text" autocomplete="off"' +
      '           class="input input-ghost w-full text-base"' +
      '           placeholder="Type a command\u2026 (Cmd+K / Ctrl+K)">' +
      "  </div>" +
      '  <ul id="aird-command-palette-list" class="menu menu-sm max-h-80 overflow-y-auto p-2"></ul>' +
      "</form>";
    document.body.appendChild(dialog);
    input = dialog.querySelector("#aird-command-palette-input");
    listEl = dialog.querySelector("#aird-command-palette-list");
    input.addEventListener("input", renderList);
    input.addEventListener("keydown", onInputKeyDown);
    dialog.addEventListener("close", function () {
      input.value = "";
    });
    dialog.addEventListener("click", function (ev) {
      if (ev.target === dialog) {
        dialog.close();
      }
    });
    return dialog;
  }

  function buildCommandList() {
    const commands = STATIC_COMMANDS.slice();
    const ext = window.AirdCommandPalette && window.AirdCommandPalette.commands;
    if (Array.isArray(ext)) {
      ext.forEach(function (cmd) {
        if (cmd && cmd.label) commands.push(cmd);
      });
    }
    const tagFn =
      window.AirdCommandPalette && window.AirdCommandPalette.getSelection;
    if (typeof tagFn === "function") {
      commands.unshift({
        id: "tag-selection",
        label: "Tag selection as\u2026",
        action: tagSelection,
      });
    }
    return commands;
  }

  function notify(title, message) {
    if (globalThis.AirdCore?.showDialog) {
      return globalThis.AirdCore.showDialog(message, title);
    }
    globalThis.alert(message);
  }

  function tagSelection() {
    const tag = window.prompt("Tag name (e.g. pii, finance):");
    if (!tag) return;
    const selFn = window.AirdCommandPalette.getSelection;
    const paths = typeof selFn === "function" ? selFn() : [];
    if (!Array.isArray(paths) || paths.length === 0) {
      notify("Tag selection", "Select at least one file first.");
      return;
    }
    const xsrfInput = document.querySelector('input[name="_xsrf"]');
    const xsrf = xsrfInput ? xsrfInput.value : "";
    const headers = { "Content-Type": "application/json" };
    if (xsrf) headers["X-XSRFToken"] = xsrf;
    Promise.all(
      paths.map(function (path) {
        return fetch("/admin/api/abac/tags", {
          method: "POST",
          headers: headers,
          body: JSON.stringify({
            tag: tag,
            glob_pattern: path,
            priority: 0,
          }),
        });
      })
    ).then(function (results) {
      const failed = results.filter(function (r) { return !r.ok; }).length;
      if (failed > 0) {
        notify(
          "Tag selection",
          "Tagged " +
            (paths.length - failed) +
            "/" +
            paths.length +
            " items. " +
            failed +
            " failed (likely admin-only)."
        );
      } else {
        notify("Tag selection", "Tagged " + paths.length + " item(s) as '" + tag + "'.");
      }
    });
  }

  function renderList() {
    if (!listEl) return;
    const term = (input.value || "").trim().toLowerCase();
    const commands = buildCommandList();
    const filtered = commands.filter(function (cmd) {
      if (!term) return true;
      return cmd.label.toLowerCase().indexOf(term) !== -1;
    });
    listEl.innerHTML = "";
    filtered.forEach(function (cmd, idx) {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "justify-between text-sm";
      btn.dataset.cmdIndex = String(idx);
      btn.textContent = cmd.label;
      btn.addEventListener("click", function () {
        executeCommand(cmd);
      });
      li.appendChild(btn);
      listEl.appendChild(li);
    });
    listEl.dataset.size = String(filtered.length);
  }

  function executeCommand(cmd) {
    if (!cmd) return;
    if (typeof cmd.action === "function") {
      try {
        cmd.action();
      } catch (err) {
        console.error("command failed", err);
      }
      dialog.close();
      return;
    }
    if (cmd.url) {
      dialog.close();
      window.location.href = cmd.url;
    }
  }

  function onInputKeyDown(ev) {
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const first = listEl.querySelector("button");
    if (first) first.click();
  }

  function open() {
    ensureDialog();
    renderList();
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
      setTimeout(function () { input.focus(); }, 10);
    }
  }

  function isPaletteShortcut(ev) {
    const key = (ev.key || "").toLowerCase();
    if (key !== "k") return false;
    return ev.metaKey || ev.ctrlKey;
  }

  document.addEventListener("keydown", function (ev) {
    if (isPaletteShortcut(ev)) {
      ev.preventDefault();
      open();
    } else if (ev.key === "Escape" && dialog && dialog.open) {
      dialog.close();
    }
  });

  function attachTriggers() {
    document.querySelectorAll("[data-aird-cmdk]").forEach(function (el) {
      if (el.__airdCmdkBound) return;
      el.__airdCmdkBound = true;
      el.addEventListener("click", function (ev) {
        ev.preventDefault();
        open();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attachTriggers);
  } else {
    attachTriggers();
  }

  window.AirdCommandPalette = window.AirdCommandPalette || {};
  window.AirdCommandPalette.open = open;
})();
