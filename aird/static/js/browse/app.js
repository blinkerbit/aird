(function() {
  "use strict";

    // Configurable constants
    const RELOAD_DELAY_MS = 500;
    const MIN_COLUMN_WIDTH = 50;
    const MAX_FILE_SIZE = globalThis.__BROWSE_CONFIG ? globalThis.__BROWSE_CONFIG.maxFileSize : 10737418240;
    const CAN_TAG = !!(globalThis.__BROWSE_CONFIG && globalThis.__BROWSE_CONFIG.canTag);

    const SelectionStore = {
      KEY: 'aird_browse_selections',
      _read() {
        try { return JSON.parse(sessionStorage.getItem(this.KEY)) || []; }
        catch { return []; }
      },
      _write(arr) { sessionStorage.setItem(this.KEY, JSON.stringify(arr)); },
      getAll() { return this._read(); },
      add(path) {
        const arr = this._read();
        if (!arr.includes(path)) { arr.push(path); this._write(arr); }
      },
      remove(path) {
        const arr = this._read().filter(p => p !== path);
        this._write(arr);
      },
      has(path) { return this._read().includes(path); },
      clear() { sessionStorage.removeItem(this.KEY); },
      count() { return this._read().length; },
      addMany(paths) {
        const arr = this._read();
        let changed = false;
        for (const p of paths) { if (!arr.includes(p)) { arr.push(p); changed = true; } }
        if (changed) this._write(arr);
      },
      removeMany(paths) {
        const set = new Set(paths);
        const arr = this._read().filter(p => !set.has(p));
        this._write(arr);
      }
    };

    function pathBasename(p) {
      const segs = String(p).split('/').filter(Boolean);
      return segs.length ? segs.at(-1) : p;
    }

    function escapeHtml(text) {
      return globalThis.AirdCore.escapeHtml(text);
    }

    function escapeAttr(text) {
      return globalThis.AirdCore.escapeAttr(text);
    }

    const showDialog = (...args) => {
      if (globalThis.AirdCore?.showDialog) {
        return globalThis.AirdCore.showDialog(...args);
      }
      const msg = args[0] ?? "";
      const opts = args[2];
      if (opts?.showCancel) {
        return Promise.resolve(globalThis.confirm(msg));
      }
      globalThis.alert(msg);
      return Promise.resolve(true);
    };

    /** True if keyboard events should be ignored (typing in a field). */
    function isInputKeyTarget(target) {
      const tag = (target.tagName || '').toLowerCase();
      return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
    }

    // File upload functionality (DOM only exists when feature file_upload is enabled)
    const uploadZone = document.getElementById("uploadZone");
    const fileInput = document.getElementById("fileInput");
    let uploadQueue = [];
    let isUploading = false;
    let uploadBatchHadError = false;
    let reloadTimer = null;

    if (uploadZone && fileInput) {
    uploadZone.addEventListener("click", () => fileInput.click());
    uploadZone.addEventListener("dragover", (e) => {
      e.preventDefault();
      uploadZone.classList.add("dragover");
    });
    uploadZone.addEventListener("dragleave", () => {
      uploadZone.classList.remove("dragover");
    });
    uploadZone.addEventListener("drop", async (e) => {
      e.preventDefault();
      uploadZone.classList.remove("dragover");
      // Try to detect folders via webkitGetAsEntry
      const items = e.dataTransfer.items;
      if (items?.length > 0 && typeof items[0].webkitGetAsEntry === 'function') {
        const entries = [];
        for (const item of items) {
          const entry = item.webkitGetAsEntry();
          if (entry) entries.push(entry);
        }
        const hasDir = entries.some(function(ent) { return ent.isDirectory; });
        if (hasDir) {
          const filesWithPaths = await traverseEntries(entries, '');
          handleFilesWithPaths(filesWithPaths);
          return;
        }
      }
      handleFiles(e.dataTransfer.files);
    });

    // --- Folder drag-and-drop helpers ---
    async function readAllEntries(reader) {
      const results = [];
      let batch;
      do {
        batch = await new Promise((res) => reader.readEntries(res));
        results.push(...batch);
      } while (batch.length > 0);
      return results;
    }

    function getFileFromEntry(entry) {
      return new Promise(function(resolve, reject) {
        entry.file(resolve, reject);
      });
    }

    async function traverseEntries(entries, pathPrefix) {
      const result = [];
      for (const entry of entries) {
        if (entry.isFile) {
          try {
            const file = await getFileFromEntry(entry);
            result.push({ file, relativePath: pathPrefix + file.name });
          } catch (e) {
            console.warn('Skipping unreadable entry:', e);
          }
        } else if (entry.isDirectory) {
          const reader = entry.createReader();
          const children = await readAllEntries(reader);
          const sub = await traverseEntries(children, pathPrefix + entry.name + '/');
          result.push(...sub);
        }
      }
      return result;
    }

    function handleFilesWithPaths(filesWithPaths) {
      if (filesWithPaths.length === 0) return;
      const rejected = [];
      for (const fw of filesWithPaths) {
        if (fw.file.size > MAX_FILE_SIZE) { rejected.push(fw.relativePath); continue; }

        const parts = fw.relativePath.split('/');
        const fileName = parts.pop();
        const subDir = parts.join('/');
        let uploadDir = document.getElementById('currentPath')?.value ?? '';
        if (subDir) uploadDir = uploadDir ? uploadDir + '/' + subDir : subDir;

        const queueItem = { file: fw.file, uploadDir, uploadName: fileName, uploadSignal: null, ttId: null };
        uploadQueue.push(queueItem);
      }
      if (rejected.length > 0) {
        const limitGB = (MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2);
        showDialog('Files exceed the ' + limitGB + ' GB limit: ' + rejected.join(", "), 'File Size Limit');
      }
      fileInput.value = "";
      clearTimeout(reloadTimer);
      if (!isUploading && uploadQueue.length > 0) {
        uploadBatchHadError = false;
        isUploading = true;
        globalThis.AirdTransferTracker?.openSidebar?.();
        processQueue();
      }
    }

    fileInput.addEventListener("change", (e) => {
      handleFiles(e.target.files);
    });

    function handleFiles(files) {
      if (files.length === 0) return;
      const rejected = [];
      for (const file of files) {
        if (file.size > MAX_FILE_SIZE) {
          rejected.push(file.name);
          continue;
        }

        uploadQueue.push({ file, uploadSignal: null, ttId: null });
      }

      if (rejected.length > 0) {
        const limitGB = (MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2);
        showDialog(`Files exceed the ${limitGB} GB limit: ${rejected.join(", ")}`, 'File Size Limit');
      }

      fileInput.value = "";
      clearTimeout(reloadTimer);
      if (!isUploading && uploadQueue.length > 0) {
        uploadBatchHadError = false;
        isUploading = true;
        globalThis.AirdTransferTracker?.openSidebar?.();
        processQueue();
      }
    }

    async function processQueue() {
      if (uploadQueue.length === 0) {
        isUploading = false;
        if (!uploadBatchHadError) {
          scheduleReload();
        }
        uploadBatchHadError = false;
        return;
      }

      const current = uploadQueue.shift();
      try {
        await uploadFile(current);
      } catch (err) {
        uploadBatchHadError = true;
        if (err?.message !== "cancelled") {
          console.warn("Upload failed:", err);
          void showDialog(
            friendlyUploadErrorMessage(err),
            "Upload failed"
          );
        }
      }

      processQueue();
    }

    function scheduleReload() {
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => {
        if (uploadQueue.length === 0 && !isUploading) {
          globalThis.location.reload();
        }
      }, RELOAD_DELAY_MS);
    }

    function formatBytes(bytes) {
      return globalThis.AirdCore.formatBytes(bytes);
    }

    function abortActiveUploads(item) {
      item.cancelled = true;
      if (item.uploadSignal) {
        item.uploadSignal.aborted = true;
      }
    }

    /** Plain-language message for the upload row (not console-only). */
    function friendlyUploadErrorMessage(err) {
      const raw = (err && err.message) ? String(err.message).trim() : "";
      if (!raw || raw === "cancelled") return raw;
      const lower = raw.toLowerCase();
      if (lower.includes("network") || lower.includes("websocket")) {
        return "Upload interrupted. Check your connection and try again.";
      }
      if (lower.includes("403") || lower.includes("access denied")) {
        return "Upload not allowed. Refresh the page and try again.";
      }
      if (lower.includes("413") || lower.includes("too large")) {
        return "This file is too large for the server limit.";
      }
      return "Upload could not be completed. Please try again.";
    }

    async function uploadFile(item) {
      const FTW = globalThis.AirdFileTransferWs;
      if (!FTW?.uploadFile) {
        throw new Error("WebSocket upload unavailable. Hard-refresh the page.");
      }
      const dir = item.uploadDir ?? document.getElementById('currentPath')?.value ?? '';
      const fname = item.uploadName ?? item.file.name;
      item.uploadSignal = { aborted: false };
      const TT = globalThis.AirdTransferTracker;
      const cancelFn = () => abortActiveUploads(item);
      await FTW.uploadFile(item.file, {
        uploadDir: dir,
        filename: fname,
        signal: item.uploadSignal,
        onCancel: cancelFn,
      });
    }

    } // end upload UI (uploadZone && fileInput)

    // CSRF Protection: Get XSRF token from cookie (used by upload + rest of page)
    function getXSRFToken() {
      return globalThis.AirdCore.getXSRFToken();
    }

    // Rename functionality
    async function renameItem(filepath) {
      const newName = await showDialog("Enter new name:", "Rename File", { prompt: true, showCancel: true });
      if (!newName) return;
      const formData = new URLSearchParams();
      formData.append("path", filepath);
      formData.append("new_name", newName);
      fetch("/rename", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-XSRFToken": getXSRFToken(),
        },
        body: formData.toString(),
      })
        .then((res) => {
          if (res.ok) {
            SelectionStore.remove(filepath);
            globalThis.location.reload();
          } else {
            res.text().then((t) => showDialog("Rename failed: " + t, "Error"));
          }
        })
        .catch((err) => showDialog("Rename failed: " + err.message, "Error"));
    }

    // Delete functionality
    async function deleteItem(filepath, isFolder) {
      const message = isFolder
        ? "Delete this folder and all files inside it? This cannot be undone."
        : "Are you sure you want to delete this item?";
      const confirmed = await showDialog(message, "Confirm Delete", { showCancel: true });
      if (!confirmed) return;
      const formData = new URLSearchParams();
      formData.append("path", filepath);
      if (isFolder) formData.append("recursive", "1");
      fetch("/delete", {
        method: "POST",
        headers: {
          "Content-Type": "application/x-www-form-urlencoded",
          "X-XSRFToken": getXSRFToken(),
        },
        body: formData.toString(),
      })
        .then((res) => {
          if (res.ok) {
            SelectionStore.remove(filepath);
            globalThis.location.reload();
          } else {
            res.text().then((t) => showDialog("Delete failed: " + t, "Error"));
          }
        })
        .catch((err) => showDialog("Delete failed: " + err.message, "Error"));
    }

    function getSelectedPaths() {
      return SelectionStore.getAll();
    }

    function getPageSelectedPaths() {
      return Array.from(document.querySelectorAll('.row-checkbox:checked')).map(cb => cb.dataset.path);
    }

    function syncCheckboxToStore(checkbox) {
      if (checkbox.checked) {
        SelectionStore.add(checkbox.dataset.path);
      } else {
        SelectionStore.remove(checkbox.dataset.path);
      }
    }

    let _selectionDrawerIsOpen = false;
    let _lastBulkCount = 0;
    let _selectionInitDone = false;

    function _setDrawerExpandedAttrs(expanded) {
      const v = expanded ? 'true' : 'false';
      const t = document.getElementById('selectionCountBtn');
      if (t) t.setAttribute('aria-expanded', v);
    }

    function closeSelectionDrawer() {
      const drawer = document.getElementById('browseSelectionDrawer');
      const backdrop = document.getElementById('browseSelectionBackdrop');
      if (!drawer) return;
      drawer.classList.remove('browse-selection-drawer--open');
      drawer.setAttribute('aria-hidden', 'true');
      if (backdrop) {
        backdrop.hidden = true;
        backdrop.classList.remove('browse-selection-backdrop--visible');
      }
      document.body.classList.remove('browse-selection-drawer-open');
      _selectionDrawerIsOpen = false;
      _setDrawerExpandedAttrs(false);
    }

    function openSelectionDrawer() {
      if (SelectionStore.count() === 0) return;
      const drawer = document.getElementById('browseSelectionDrawer');
      const backdrop = document.getElementById('browseSelectionBackdrop');
      if (!drawer) return;
      drawer.classList.add('browse-selection-drawer--open');
      drawer.setAttribute('aria-hidden', 'false');
      if (backdrop) {
        backdrop.hidden = false;
        backdrop.classList.add('browse-selection-backdrop--visible');
      }
      document.body.classList.add('browse-selection-drawer-open');
      _selectionDrawerIsOpen = true;
      _setDrawerExpandedAttrs(true);
    }

    function toggleSelectionDrawer() {
      if (_selectionDrawerIsOpen) {
        closeSelectionDrawer();
      } else {
        openSelectionDrawer();
      }
    }

    function bulkSelectionLabel(totalCount, otherCount) {
      let label = String(totalCount);
      if (otherCount > 0) label += ' (' + otherCount + ' from other folders)';
      return label;
    }

    function renderBulkDrawerList(listEl, allPaths) {
      if (!listEl) return;
      /* Build a path→isDir map from visible checkboxes */
      const dirSet = new Set();
      document.querySelectorAll('.row-checkbox').forEach(function (cb) {
        if (cb.dataset.isDir === '1') dirSet.add(cb.dataset.path);
      });
      const sorted = allPaths.slice().sort(function (a, b) {
        return String(a).localeCompare(String(b));
      });
      listEl.innerHTML = sorted
        .map(function (p) {
          const isDir = dirSet.has(p);
          const icon = isDir ? '📁' : '📄';
          const disp = p.startsWith('/') ? p : '/' + p.replace(/^\/+/, '');
          const parts = disp.split('/');
          const name = parts.at(-1) || disp;
          const dir = parts.slice(0, -1).join('/') || '/';
          return '<li class="browse-drawer-list-item">'
            + '<span class="browse-drawer-list-icon" aria-hidden="true">' + icon + '</span>'
            + '<span class="browse-drawer-list-body">'
            + '<span class="browse-drawer-list-name">' + escapeHtml(name) + '</span>'
            + '<span class="browse-drawer-list-dir">' + escapeHtml(dir) + '</span>'
            + '</span></li>';
        })
        .join('');
    }

    function bulkDrawerMetaText(otherCount) {
      return otherCount > 0
        ? otherCount + ' item(s) from other folders · paths from your home root'
        : 'Paths are relative to your home folder';
    }

    function bulkToolbarClearEmpty(countBtn) {
      _lastBulkCount = 0;
      if (countBtn) countBtn.hidden = true;
      closeSelectionDrawer();
    }

    function bulkToolbarShowSelection(countEl, countBtn, listEl, metaEl, allPaths, totalCount, otherCount) {
      countEl.textContent = bulkSelectionLabel(totalCount, otherCount);
      if (countBtn) countBtn.hidden = false;
      renderBulkDrawerList(listEl, allPaths);
      if (metaEl) metaEl.textContent = bulkDrawerMetaText(otherCount);
      _lastBulkCount = totalCount;
    }

    function updateBulkToolbar() {
      const allPaths = SelectionStore.getAll();
      const totalCount = allPaths.length;
      const pageCount = getPageSelectedPaths().length;
      const otherCount = totalCount - pageCount;
      const countEl = document.getElementById('bulkCount');
      const listEl = document.getElementById('selectionDrawerList');
      const metaEl = document.getElementById('selectionDrawerMeta');
      const countBtn = document.getElementById('selectionCountBtn');
      if (!countEl) return;

      if (totalCount === 0) {
        bulkToolbarClearEmpty(countBtn);
      } else {
        bulkToolbarShowSelection(countEl, countBtn, listEl, metaEl, allPaths, totalCount, otherCount);
      }

      const selectAll = document.getElementById('selectAllCheckbox');
      if (selectAll) {
        const total = document.querySelectorAll('.row-checkbox').length;
        selectAll.checked = total > 0 && pageCount === total;
        selectAll.indeterminate = pageCount > 0 && pageCount < total;
      }
    }

    async function newFolder() {
      const currentPath = document.getElementById('currentPath')?.value ?? '';
      const name = await showDialog('Enter folder name:', 'New folder', { prompt: true, showCancel: true });
      if (!name?.trim()) return;
      const formData = new URLSearchParams();
      formData.append('parent', currentPath);
      formData.append('name', name.trim());
      try {
        const res = await fetch('/mkdir', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() },
          body: formData.toString(),
        });
        if (res.ok) globalThis.location.reload();
        else showDialog('Create folder failed: ' + (await res.text()), 'Error');
      } catch (e) {
        showDialog('Create folder failed: ' + e.message, 'Error');
      }
    }

    function downloadUrlForPath(path) {
      const enc = String(path || '').split('/').filter(Boolean).map(encodeURIComponent).join('/');
      return `/files/${enc}?download=1`;
    }

    async function downloadFileViaWs(filePath) {
      const FTW = globalThis.AirdFileTransferWs;
      const Dl = globalThis.AirdDownloadManager;
      if (!FTW?.downloadFile) {
        globalThis.location.href = downloadUrlForPath(filePath);
        return;
      }
      if (!Dl?.DownloadBatch) {
        try {
          const result = await FTW.downloadFile(filePath);
          FTW.saveBlob(result.blob, result.filename);
        } catch (err) {
          showDialog('Download failed: ' + (err?.message || err), 'Download');
        }
        return;
      }
      const batch = new Dl.DownloadBatch({ title: 'Downloads' });
      batch.addWsItem(filePath, filePath);
      await batch.run();
    }

    async function listDirectoryForDownload(path) {
      const segments = String(path || '').split('/').filter(Boolean);
      const url = segments.length
        ? '/api/files/' + segments.map(encodeURIComponent).join('/')
        : '/api/files/';
      const res = await fetch(url);
      if (!res.ok) return null;
      return res.json();
    }

    async function expandSelectionToFiles(paths) {
      const out = [];
      const seen = new Set();
      const addFile = (p) => {
        if (p && !seen.has(p)) {
          seen.add(p);
          out.push(p);
        }
      };
      for (const path of paths) {
        const data = await listDirectoryForDownload(path);
        if (data && Array.isArray(data.files) && data.files.length > 0) {
          const walk = async (base, entries) => {
            for (const entry of entries) {
              const child = base ? `${base}/${entry.name}` : entry.name;
              if (entry.is_dir) {
                const sub = await listDirectoryForDownload(child);
                if (sub && Array.isArray(sub.files)) {
                  await walk(child, sub.files);
                }
              } else {
                addFile(child);
              }
            }
          };
          await walk(path, data.files);
        } else {
          addFile(path);
        }
      }
      return out;
    }

    function filesDownloadUrl(path) {
      const enc = String(path || '')
        .replace(/^\/+/, '')
        .split('/')
        .filter(Boolean)
        .map(encodeURIComponent)
        .join('/');
      return enc ? `/files/${enc}?download=1` : '/files/?download=1';
    }

    async function listDirectoryForDownload(remotePath) {
      const enc = String(remotePath || '')
        .replace(/^\/+/, '')
        .split('/')
        .filter(Boolean)
        .map(encodeURIComponent)
        .join('/');
      const url = enc ? `/api/files/${enc}` : '/api/files/';
      const res = await fetch(url);
      if (!res.ok) return null;
      const data = await res.json();
      return Array.isArray(data.files) ? data.files : [];
    }

    async function expandSelectionToFiles(paths) {
      const files = [];
      const seen = new Set();
      for (const raw of paths) {
        const p = String(raw || '').trim().replace(/^\/+/, '');
        if (!p) continue;
        const entries = await listDirectoryForDownload(p);
        if (entries === null) {
          if (!seen.has(p)) {
            seen.add(p);
            files.push(p);
          }
          continue;
        }
        if (!entries.length) continue;
        async function walk(dir, dirEntries) {
          for (const entry of dirEntries) {
            const child = dir ? `${dir}/${entry.name}` : entry.name;
            if (entry.is_dir) {
              const sub = await listDirectoryForDownload(child);
              if (sub && sub.length) await walk(child, sub);
            } else if (!seen.has(child)) {
              seen.add(child);
              files.push(child);
            }
          }
        }
        await walk(p, entries);
      }
      return files;
    }

    function pathIsDirectory(relPath) {
      const target = String(relPath || '');
      const match = document.querySelector(
        '.row-checkbox[data-path="' + CSS.escape(target) + '"]'
      );
      if (match) return match.dataset.isDir === '1';
      return false;
    }

    function selectionNeedsZip(paths) {
      if (paths.length > 1) return true;
      if (paths.length === 1) return pathIsDirectory(paths[0]);
      return false;
    }

    async function downloadSelectionAsZip(paths) {
      const res = await fetch('/api/download/zip', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-XSRFToken': getXSRFToken(),
        },
        body: JSON.stringify({ paths }),
      });
      if (!res.ok) {
        const msg = (await res.text()).trim() || `HTTP ${res.status}`;
        throw new Error(msg);
      }
      const blob = await res.blob();
      let name = 'aird-download.zip';
      if (paths.length === 1) {
        const base = String(paths[0]).replace(/\/+$/, '').split('/').pop();
        if (base) name = base + '.zip';
      }
      const save = globalThis.AirdFileTransferWs?.saveBlob;
      if (save) {
        save(blob, name);
      } else {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.click();
        URL.revokeObjectURL(url);
      }
    }

    async function bulkDownload() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      closeSelectionDrawer();

      if (selectionNeedsZip(paths)) {
        try {
          await downloadSelectionAsZip(paths);
        } catch (err) {
          showDialog('Could not download zip: ' + (err?.message || err), 'Download');
        }
        return;
      }

      const Dl = globalThis.AirdDownloadManager;
      if (!Dl?.DownloadBatch) {
        showDialog('Download manager failed to load. Hard-refresh the page.', 'Download');
        return;
      }
      const batch = new Dl.DownloadBatch({ title: 'Downloads' });
      const btn = document.getElementById('bulkDownloadBtn');
      if (btn) btn.disabled = true;
      try {
        const files = await expandSelectionToFiles(paths);
        if (!files.length) {
          await showDialog('No files to download in the current selection.', 'Download');
          batch.close();
          return;
        }
        for (const filePath of files) {
          if (globalThis.AirdFileTransferWs?.downloadFile && batch.addWsItem) {
            batch.addWsItem(filePath, filePath);
          } else {
            batch.addItem(filePath, filesDownloadUrl(filePath));
          }
        }
        await batch.run();
      } catch (e) {
        await showDialog('Could not prepare downloads: ' + (e?.message || String(e)), 'Download');
      } finally {
        if (btn) btn.disabled = false;
      }
    }

    async function bulkDelete() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const confirmed = await showDialog('Delete ' + paths.length + ' item(s)? This cannot be undone.', 'Confirm bulk delete', { showCancel: true });
      if (!confirmed) return;
      try {
        const res = await fetch('/api/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({ action: 'delete', paths }),
        });
        const data = await res.json();
        if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
        else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Bulk delete failed', 'Error');
      } catch (e) {
        showDialog('Bulk delete failed: ' + e.message, 'Error');
      }
    }

    // --- Folder Picker ---
    const FolderPicker = globalThis.AirdFolderPicker;

    async function bulkCopy() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const destDir = await FolderPicker.open('copy');
      if (destDir == null) return;
      let failed = 0;
      for (const path of paths) {
        const base = pathBasename(path);
        const fullDest = destDir ? destDir + '/' + base : base;
        const formData = new URLSearchParams();
        formData.append('path', path);
        formData.append('dest', fullDest);
        const res = await fetch('/copy', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
        if (!res.ok) failed++;
      }
      if (failed === 0) { SelectionStore.clear(); globalThis.location.reload(); }
      else showDialog(failed + ' of ' + paths.length + ' copy operation(s) failed.', 'Copy');
    }

    async function bulkMove() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const destDir = await FolderPicker.open('move');
      if (destDir == null) return;
      let failed = 0;
      for (const path of paths) {
        const base = pathBasename(path);
        const fullDest = destDir ? destDir + '/' + base : base;
        const formData = new URLSearchParams();
        formData.append('path', path);
        formData.append('dest', fullDest);
        const res = await fetch('/move', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
        if (!res.ok) failed++;
      }
      if (failed === 0) { SelectionStore.clear(); globalThis.location.reload(); }
      else showDialog(failed + ' of ' + paths.length + ' move operation(s) failed.', 'Move');
    }

    function bulkCreateShare() {
      // Stash the current selection in sessionStorage and jump to the
      // Share page, which picks the prefill up on load and opens the
      // configuration modal. sessionStorage avoids URL-length and
      // escaping issues for long/complex path lists.
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      try {
        sessionStorage.setItem('airdShareCreatePrefill', JSON.stringify({
          paths: paths,
          created_at: Date.now(),
        }));
      } catch (e) {
        console.warn('Failed to stash share prefill:', e);
      }
      globalThis.location.href = '/share';
    }

    async function bulkAddToShare() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const selectEl = document.getElementById('sharePickerSelect');
      const modal = document.getElementById('sharePickerModal');
      if (!selectEl || !modal) return;
      selectEl.innerHTML = '<option value="">Loading...</option>';
      modal.showModal();
      try {
        const res = await fetch('/share/list');
        const data = await res.json();
        if (data.error) { showDialog(data.error, 'Error'); modal.close(); return; }
        const shares = data.shares || {};
        selectEl.innerHTML = Object.keys(shares).length ? Object.entries(shares).map(([id, s]) => {
          const label = id + (s.paths?.length ? ' (' + s.paths.length + ' path(s))' : '');
          return '<option value="' + escapeAttr(id) + '">' + escapeHtml(label) + '</option>';
        }).join('') : '<option value="">No shares</option>';
      } catch (e) {
        console.warn('Failed to load shares:', e);
        selectEl.innerHTML = '<option value="">Error loading shares</option>';
      }
      const shareId = await new Promise((resolve) => {
        document.getElementById('sharePickerConfirm').onclick = () => { modal.close(); resolve(selectEl.value); };
        document.getElementById('sharePickerCancel').onclick = () => { modal.close(); resolve(null); };
        modal.addEventListener('cancel', (ev) => { ev.preventDefault(); modal.close(); resolve(null); }, { once: true });
      });
      if (!shareId) return;
      try {
        const res = await fetch('/api/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({ action: 'add_to_share', share_id: shareId, paths }),
        });
        const data = await res.json();
        if (data.ok) { SelectionStore.clear(); globalThis.location.reload(); }
        else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Add to share failed', 'Error');
      } catch (e) {
        showDialog('Add to share failed: ' + e.message, 'Error');
      }
    }

    /* -----------------------------------------------------------------------
     * Tag: add glob-exact tag rules for every selected file
     * ----------------------------------------------------------------------- */
    /* --- Tag-picker chip helpers (module-level to avoid nesting violations) --- */

    function _tagChipHtml(t) {
      return '<span class="tag-picker-chip">'
        + escapeHtml(t)
        + '<button type="button" class="tag-picker-chip-remove" data-tag="' + escapeAttr(t) + '" '
        + 'aria-label="Remove ' + escapeAttr(t) + '">×</button>'
        + '</span>';
    }

    function _renderTagChips(chipsEl, pendingTags, onRemove) {
      chipsEl.innerHTML = [...pendingTags].map(_tagChipHtml).join('');
      chipsEl.querySelectorAll('button[data-tag]').forEach(function (btn) {
        btn.addEventListener('click', function () { onRemove(btn.dataset.tag); });
      });
    }

    function _commitTagInput(inputEl, pendingTags) {
      inputEl.value.split(',')
        .map(function (s) { return s.trim().toLowerCase().replace(/\s+/g, '-'); })
        .filter(Boolean)
        .forEach(function (t) { pendingTags.add(t); });
      inputEl.value = '';
    }

    function _renderTagSuggestions(inputEl, existingTagNames, pendingTags, onPick, suggestionsId) {
      const sugId = suggestionsId || 'tagPickerSuggestions';
      const q = inputEl.value.trim().toLowerCase();
      const matches = q
        ? existingTagNames.filter(function (n) { return n.includes(q) && !pendingTags.has(n); })
        : existingTagNames.filter(function (n) { return !pendingTags.has(n); }).slice(0, 8);
      let sug = document.getElementById(sugId);
      if (!sug) {
        sug = document.createElement('div');
        sug.id = sugId;
        sug.className = 'tag-picker-suggestions';
        inputEl.parentNode.classList.add('tag-picker-input-wrap');
        inputEl.after(sug);
      }
      sug.innerHTML = matches.map(function (n) {
        return '<div class="tag-picker-suggestion-item" data-sug="' + escapeAttr(n) + '">'
          + escapeHtml(n) + '</div>';
      }).join('');
      sug.querySelectorAll('[data-sug]').forEach(function (el) {
        el.addEventListener('mousedown', function (e) {
          e.preventDefault();
          onPick(el.dataset.sug);
          sug.innerHTML = '';
        });
      });
      if (sugId === 'rowTagPopoverSuggestions') _scheduleRowTagPopoverPosition();
    }

    async function _postTagRule(tag, globPattern) {
      const res = await fetch('/admin/api/abac/tags', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
        body: JSON.stringify({ tag, glob_pattern: globPattern }),
      });
      return res.ok || res.status === 409;
    }

    async function _deleteTagRuleIds(ids) {
      if (!ids.length) return { ok: true, deleted: [] };
      const res = await fetch('/admin/api/abac/tags', {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
        body: JSON.stringify({ ids: ids }),
      });
      if (!res.ok) return { ok: false, deleted: [] };
      const data = await res.json().catch(function () { return {}; });
      return { ok: true, deleted: data.ids || [] };
    }

    function _normalizeRelPath(p) {
      return String(p).replace(/\\/g, '/').replace(/^\/+/, '');
    }

    function _globPatternToRegex(pattern) {
      let p = String(pattern).replace(/\\/g, '/').replace(/^\/+/, '').replace(/\/$/, '');
      if (!p) return null;
      let out = '';
      for (let i = 0; i < p.length; i += 1) {
        const ch = p[i];
        if (ch === '*' && p[i + 1] === '*') {
          if (p[i + 2] === '/') { out += '(?:.*/)?'; i += 2; }
          else { out += '.*'; i += 1; }
        } else if (ch === '*') {
          out += '[^/]*';
        } else if (ch === '?') {
          out += '[^/]';
        } else {
          out += ch.replace(/[.+^${}()|[\]\\]/g, '\\$&');
        }
      }
      return new RegExp('^' + out + '$');
    }

    function _ruleMatchesPath(rule, relPath) {
      const rel = _normalizeRelPath(relPath);
      const pat = String(rule.glob_pattern || '');
      if (!pat) return false;
      const normPat = _normalizeRelPath(pat);
      if (!pat.includes('*') && !pat.includes('?') && !pat.includes('**')) {
        return normPat === rel;
      }
      try {
        const re = _globPatternToRegex(pat);
        return re ? re.test(rel) : false;
      } catch {
        return false;
      }
    }

    function _tagsOnPath(rules, path) {
      const byTag = new Map();
      for (const rule of rules) {
        const tag = rule.tag || '';
        if (!tag || !_ruleMatchesPath(rule, path)) continue;
        if (!byTag.has(tag)) byTag.set(tag, []);
        byTag.get(tag).push(rule.id);
      }
      return byTag;
    }

    function _existingTagChipHtml(tag) {
      return '<span class="tag-picker-chip tag-picker-chip--existing">'
        + escapeHtml(tag)
        + '<button type="button" class="tag-picker-chip-remove" data-existing-tag="' + escapeAttr(tag) + '" '
        + 'aria-label="Remove tag ' + escapeAttr(tag) + '">×</button>'
        + '</span>';
    }

    function _renderExistingTagChips(existingEl, tagsOnPath, onRemove) {
      if (!existingEl) return;
      const names = [...tagsOnPath.keys()].sort(function (a, b) { return a.localeCompare(b); });
      existingEl.innerHTML = names.map(_existingTagChipHtml).join('');
      existingEl.querySelectorAll('[data-existing-tag]').forEach(function (btn) {
        btn.addEventListener('click', function () { onRemove(btn.dataset.existingTag); });
      });
    }

    function _setupTagPickerListeners(inputEl, chipsEl, pendingTags, existingTagNames, suggestionsId, signal) {
      function refresh() {
        _renderTagChips(chipsEl, pendingTags, function (t) { pendingTags.delete(t); refresh(); });
      }
      function onKeydown(e) {
        if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); _commitTagInput(inputEl, pendingTags); refresh(); return; }
        if (e.key === 'Backspace' && !inputEl.value && pendingTags.size) { pendingTags.delete([...pendingTags].at(-1)); refresh(); }
        if (e.key === 'Escape') { e.stopPropagation(); }
      }
      function onBlur() { _commitTagInput(inputEl, pendingTags); refresh(); }
      function onInput() {
        _renderTagSuggestions(inputEl, existingTagNames, pendingTags, function (picked) {
          pendingTags.add(picked);
          inputEl.value = '';
          refresh();
        }, suggestionsId);
      }
      const opts = signal ? { signal: signal } : undefined;
      inputEl.addEventListener('keydown', onKeydown, opts);
      inputEl.addEventListener('blur', onBlur, opts);
      inputEl.addEventListener('input', onInput, opts);
      return refresh;
    }

    function _awaitTagPickerClose(modal, inputEl, errEl, pendingTags, refresh) {
      return new Promise(function (resolve) {
        document.getElementById('tagPickerConfirm').onclick = function () {
          _commitTagInput(inputEl, pendingTags); refresh();
          if (!pendingTags.size) { errEl.textContent = 'Add at least one tag.'; errEl.classList.remove('hidden'); return; }
          modal.close(); resolve([...pendingTags]);
        };
        document.getElementById('tagPickerCancel').onclick = function () { modal.close(); resolve(null); };
        modal.addEventListener('cancel', function (ev) { ev.preventDefault(); modal.close(); resolve(null); }, { once: true });
      });
    }

    async function _applyTagRules(tags, paths) {
      let created = 0;
      let failed = 0;
      for (const path of paths) {
        const norm = path.startsWith('/') ? path : '/' + path.replace(/^\/+/, '');
        for (const tag of tags) {
          try {
            if (await _postTagRule(tag, norm)) { created++; } else { failed++; }
          } catch { failed++; }
        }
      }
      return { created, failed };
    }

    async function bulkAddTags() {
      const paths = SelectionStore.getAll();
      if (!paths.length) { showDialog('No files selected.', 'Info'); return; }
      const modal = document.getElementById('tagPickerModal');
      const inputEl = document.getElementById('tagPickerInput');
      const chipsEl = document.getElementById('tagPickerChips');
      const descEl = document.getElementById('tagPickerDesc');
      const errEl = document.getElementById('tagPickerError');
      if (!modal || !inputEl) return;

      descEl.textContent = 'Will tag ' + paths.length + ' selected item(s).';
      inputEl.value = '';
      errEl.classList.add('hidden');

      const pendingTags = new Set();
      let existingTagNames = [];
      try {
        const res = await fetch('/admin/api/abac/tags', { headers: { 'X-XSRFToken': getXSRFToken() } });
        if (res.ok) {
          const data = await res.json();
          existingTagNames = [...new Set((data.tags || []).map(function (t) { return t.tag; }))].sort((a, b) => a.localeCompare(b));
        }
      } catch { /* autocomplete is best-effort */ }

      const refresh = _setupTagPickerListeners(inputEl, chipsEl, pendingTags, existingTagNames);
      refresh();
      modal.showModal();

      const tags = await _awaitTagPickerClose(modal, inputEl, errEl, pendingTags, refresh);
      if (!tags) return;

      const { created, failed } = await _applyTagRules(tags, paths);
      const msg = created + ' tag rule(s) created for [' + tags.join(', ') + '].'
        + (failed ? ' ' + failed + ' already existed or failed.' : '');
      showDialog(msg, 'Tags applied');
    }

    /* -----------------------------------------------------------------------
     * Per-row tag popover (+ button in Tags column)
     * ----------------------------------------------------------------------- */
    let _rowTagPopoverAbort = null;
    let _rowTagPopoverPath = null;
    let _rowTagPopoverAnchor = null;
    const ROW_TAG_POPOVER_MIN_W = 288;
    const ROW_TAG_POPOVER_MIN_H = 140;

    function _ensureRowTagPopoverPortal(pop, backdrop) {
      if (pop && pop.parentNode !== document.body) document.body.appendChild(pop);
      if (backdrop && backdrop.parentNode !== document.body) document.body.appendChild(backdrop);
    }

    function _scheduleRowTagPopoverPosition() {
      const pop = document.getElementById('rowTagPopover');
      if (!pop || !_rowTagPopoverAnchor?.isConnected) return;
      requestAnimationFrame(function () {
        if (!_rowTagPopoverAnchor?.isConnected) return;
        _positionRowTagPopover(_rowTagPopoverAnchor, pop);
      });
    }

    function _isRowTagPopoverOpen() {
      return !!_rowTagPopoverAnchor;
    }

    function _parseTagsAttr(raw) {
      if (!raw) return [];
      return raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    }

    function _tagsCellTags(tagsCell) {
      return _parseTagsAttr(tagsCell?.dataset.tags || '');
    }

    function _renderTagsCellInner(tags, path) {
      let html = '';
      if (tags.length) {
        const overflow = tags.length > 1;
        html += '<span class="file-tag-list' + (overflow ? ' file-tag-list--overflow' : '') + '">';
        html += '<a href="/tagged/' + encodeURIComponent(tags[0]) + '" class="file-tag-chip" onclick="event.stopPropagation()">'
          + escapeHtml(tags[0]) + '</a>';
        if (overflow) {
          html += '<button type="button" class="file-tag-more file-tag-more-trigger"'
            + ' aria-label="Show all ' + tags.length + ' tags" title="Show all tags">'
            + '+' + (tags.length - 1) + '</button>';
        }
        html += '</span>';
      }
      if (CAN_TAG) {
        html += '<button type="button" class="row-tag-add-btn" data-path="' + escapeAttr(path) + '"'
          + ' title="Add tag" aria-label="Add tag to ' + escapeAttr(pathBasename(path)) + '">'
          + '<span aria-hidden="true">+</span></button>';
      } else if (!tags.length) {
        html = '<span class="tags-cell-empty">—</span>';
      }
      return html;
    }

    function _updateTagsCell(path, tags) {
      const row = document.querySelector('tr.file-row[data-path="' + CSS.escape(path) + '"]');
      const tagsCell = row?.querySelector('.tags-cell');
      if (!tagsCell) return;
      const sorted = [...new Set(tags)].sort(function (a, b) { return a.localeCompare(b); });
      tagsCell.dataset.tags = sorted.join(',');
      const inner = tagsCell.querySelector('.tags-cell-inner');
      if (inner) inner.innerHTML = _renderTagsCellInner(sorted, path);
    }

    function _positionFileTagsHoverPopover(anchorEl, pop) {
      if (!anchorEl || !pop) return;
      pop.removeAttribute('hidden');
      pop.style.display = 'block';
      pop.style.visibility = 'hidden';
      pop.style.left = '0';
      pop.style.top = '0';

      const rect = anchorEl.getBoundingClientRect();
      const margin = 8;
      const gap = 6;
      const popW = pop.offsetWidth;
      const popH = pop.offsetHeight;

      let left = rect.left;
      let top = rect.bottom + gap;
      if (left + popW > window.innerWidth - margin) {
        left = Math.max(margin, window.innerWidth - margin - popW);
      }
      if (top + popH > window.innerHeight - margin) {
        const above = rect.top - gap - popH;
        top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
      }

      pop.style.left = Math.round(left) + 'px';
      pop.style.top = Math.round(top) + 'px';
      pop.style.visibility = 'visible';
    }

    function _initFileTagsHoverPopover() {
      const pop = document.getElementById('fileTagsHoverPopover');
      const listEl = pop?.querySelector('.file-tags-hover-popover-list');
      const table = document.getElementById('fileTable');
      if (!pop || !listEl || !table) return;

      let hideTimer = null;
      let activeAnchor = null;

      function hidePopover() {
        pop.hidden = true;
        pop.style.display = '';
        pop.style.visibility = '';
        pop.style.left = '';
        pop.style.top = '';
        activeAnchor = null;
      }

      function scheduleHide() {
        clearTimeout(hideTimer);
        hideTimer = setTimeout(hidePopover, 130);
      }

      function cancelHide() {
        clearTimeout(hideTimer);
      }

      function showPopover(anchorEl, tags) {
        if (!tags.length) return;
        cancelHide();
        activeAnchor = anchorEl;
        listEl.innerHTML = tags.map(function (t) {
          return '<a href="/tagged/' + encodeURIComponent(t) + '" class="file-tag-chip"'
            + ' onclick="event.stopPropagation()">' + escapeHtml(t) + '</a>';
        }).join('');
        _positionFileTagsHoverPopover(anchorEl, pop);
      }

      function tagsFromEventTarget(target) {
        const trigger = target.closest('.file-tag-list--overflow, .file-tag-more-trigger');
        if (!trigger) return null;
        const list = trigger.classList.contains('file-tag-list')
          ? trigger
          : trigger.closest('.file-tag-list');
        const cell = (list || trigger).closest('.tags-cell');
        if (!cell) return null;
        const tags = _tagsCellTags(cell);
        if (tags.length <= 1) return null;
        return { anchor: list || trigger, tags: tags };
      }

      table.addEventListener('mouseover', function (e) {
        const info = tagsFromEventTarget(e.target);
        if (!info) return;
        showPopover(info.anchor, info.tags);
      });

      table.addEventListener('mouseout', function (e) {
        const info = tagsFromEventTarget(e.target);
        if (!info) return;
        const related = e.relatedTarget;
        if (related && (info.anchor.contains(related) || pop.contains(related))) return;
        scheduleHide();
      });

      pop.addEventListener('mouseenter', cancelHide);
      pop.addEventListener('mouseleave', scheduleHide);

      table.addEventListener('focusin', function (e) {
        const btn = e.target.closest('.file-tag-more-trigger');
        if (!btn) return;
        const cell = btn.closest('.tags-cell');
        const tags = _tagsCellTags(cell);
        if (tags.length <= 1) return;
        const list = btn.closest('.file-tag-list') || btn;
        showPopover(list, tags);
      });

      table.addEventListener('focusout', function (e) {
        if (!e.target.closest('.file-tag-more-trigger')) return;
        scheduleHide();
      });

      document.addEventListener('scroll', function () {
        if (!activeAnchor || pop.hidden) return;
        _positionFileTagsHoverPopover(activeAnchor, pop);
      }, true);

      window.addEventListener('resize', function () {
        if (!activeAnchor || pop.hidden) return;
        _positionFileTagsHoverPopover(activeAnchor, pop);
      });
    }

    function _positionRowTagPopover(anchorEl, pop) {
      if (!anchorEl || !pop) return;

      pop.removeAttribute('hidden');
      pop.style.display = 'block';
      pop.style.position = 'fixed';
      pop.style.right = 'auto';
      pop.style.bottom = 'auto';
      pop.style.margin = '0';
      pop.style.visibility = 'hidden';
      pop.style.left = '0';
      pop.style.top = '0';

      const rect = anchorEl.getBoundingClientRect();
      const margin = 8;
      const gap = 6;
      const popW = Math.max(pop.offsetWidth || 0, ROW_TAG_POPOVER_MIN_W);
      const popH = Math.max(pop.offsetHeight || 0, ROW_TAG_POPOVER_MIN_H);

      let left = rect.left;
      let top = rect.bottom + gap;

      if (left + popW > window.innerWidth - margin) {
        left = Math.max(margin, window.innerWidth - margin - popW);
      }
      if (left < margin) left = margin;

      if (top + popH > window.innerHeight - margin) {
        const above = rect.top - gap - popH;
        top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
      }

      pop.style.left = Math.round(left) + 'px';
      pop.style.top = Math.round(top) + 'px';
      pop.style.visibility = 'visible';
    }

    function closeRowTagPopover() {
      const pop = document.getElementById('rowTagPopover');
      const backdrop = document.getElementById('rowTagPopoverBackdrop');
      if (_rowTagPopoverAbort) {
        _rowTagPopoverAbort.abort();
        _rowTagPopoverAbort = null;
      }
      _rowTagPopoverPath = null;
      _rowTagPopoverAnchor = null;
      if (pop) {
        pop.hidden = true;
        pop.style.display = '';
        pop.style.visibility = '';
        pop.style.left = '';
        pop.style.top = '';
        const sug = document.getElementById('rowTagPopoverSuggestions');
        if (sug) sug.remove();
        const wrap = document.getElementById('rowTagPopoverInput')?.parentNode;
        wrap?.classList.remove('tag-picker-input-wrap');
      }
      if (backdrop) backdrop.hidden = true;
    }

    async function _fetchAllTagRules() {
      try {
        const res = await fetch('/admin/api/abac/tags', { headers: { 'X-XSRFToken': getXSRFToken() } });
        if (!res.ok) return [];
        const data = await res.json();
        return data.tags || [];
      } catch {
        return [];
      }
    }

    async function _fetchExistingTagNames() {
      const rules = await _fetchAllTagRules();
      return [...new Set(rules.map(function (t) { return t.tag; }))].sort(function (a, b) {
        return a.localeCompare(b);
      });
    }

    async function openRowTagPopover(path, anchorEl) {
      if (!CAN_TAG) return;
      closeRowTagPopover();

      const pop = document.getElementById('rowTagPopover');
      const backdrop = document.getElementById('rowTagPopoverBackdrop');
      const inputEl = document.getElementById('rowTagPopoverInput');
      const chipsEl = document.getElementById('rowTagPopoverChips');
      const existingEl = document.getElementById('rowTagPopoverExisting');
      const fileEl = document.getElementById('rowTagPopoverFile');
      const errEl = document.getElementById('rowTagPopoverError');
      const applyBtn = document.getElementById('rowTagPopoverApply');
      const cancelBtn = document.getElementById('rowTagPopoverCancel');
      if (!pop || !inputEl || !chipsEl || !existingEl || !applyBtn || !cancelBtn) return;

      _rowTagPopoverPath = path;
      _rowTagPopoverAnchor = anchorEl;
      _rowTagPopoverAbort = new AbortController();
      const signal = _rowTagPopoverAbort.signal;
      _ensureRowTagPopoverPortal(pop, backdrop);

      inputEl.value = '';
      chipsEl.innerHTML = '';
      existingEl.innerHTML = '';
      errEl.classList.add('hidden');
      errEl.textContent = '';
      fileEl.textContent = pathBasename(path);

      const pendingTags = new Set();
      const allRules = await _fetchAllTagRules();
      const tagsOnPath = _tagsOnPath(allRules, path);
      const existingTagNames = [...new Set(allRules.map(function (t) { return t.tag; }))].sort(function (a, b) {
        return a.localeCompare(b);
      });

      function syncTagsCellFromMap() {
        _updateTagsCell(path, [...tagsOnPath.keys()]);
      }

      function renderExisting() {
        _renderExistingTagChips(existingEl, tagsOnPath, async function (tagName) {
          const ids = tagsOnPath.get(tagName) || [];
          if (!ids.length) return;
          applyBtn.disabled = true;
          const result = await _deleteTagRuleIds(ids);
          applyBtn.disabled = false;
          if (!result.ok) {
            errEl.textContent = 'Could not remove tag "' + tagName + '".';
            errEl.classList.remove('hidden');
            return;
          }
          errEl.classList.add('hidden');
          tagsOnPath.delete(tagName);
          renderExisting();
          syncTagsCellFromMap();
          _scheduleRowTagPopoverPosition();
        });
      }
      renderExisting();

      const refresh = _setupTagPickerListeners(
        inputEl, chipsEl, pendingTags, existingTagNames, 'rowTagPopoverSuggestions', signal
      );
      refresh();
      _renderTagSuggestions(inputEl, existingTagNames, pendingTags, function (picked) {
        pendingTags.add(picked);
        inputEl.value = '';
        refresh();
      }, 'rowTagPopoverSuggestions');

      backdrop.hidden = false;
      _positionRowTagPopover(anchorEl, pop);
      _scheduleRowTagPopoverPosition();
      inputEl.focus();

      const onApply = async function () {
        _commitTagInput(inputEl, pendingTags);
        refresh();
        if (!pendingTags.size) {
          closeRowTagPopover();
          return;
        }
        applyBtn.disabled = true;
        const tags = [...pendingTags];
        const { created, failed } = await _applyTagRules(tags, [path]);
        applyBtn.disabled = false;
        if (created === 0 && failed > 0) {
          errEl.textContent = 'Could not add tag(s). They may already exist.';
          errEl.classList.remove('hidden');
          return;
        }
        for (const tag of tags) {
          if (!tagsOnPath.has(tag)) tagsOnPath.set(tag, []);
        }
        syncTagsCellFromMap();
        closeRowTagPopover();
        if (failed > 0) {
          showDialog(created + ' tag rule(s) added. ' + failed + ' already existed or failed.', 'Tags');
        }
      };

      applyBtn.addEventListener('click', onApply, { signal: signal });
      cancelBtn.addEventListener('click', closeRowTagPopover, { signal: signal });
      backdrop.addEventListener('click', closeRowTagPopover, { signal: signal });
      document.addEventListener('keydown', function rowPopEsc(e) {
        if (e.key === 'Escape' && _isRowTagPopoverOpen()) {
          e.preventDefault();
          e.stopPropagation();
          closeRowTagPopover();
        }
      }, { signal: signal });
      window.addEventListener('resize', _scheduleRowTagPopoverPosition, { signal: signal });
      document.addEventListener('scroll', _scheduleRowTagPopoverPosition, { signal: signal, capture: true });
      inputEl.addEventListener('input', _scheduleRowTagPopoverPosition, { signal: signal });
    }

    /* -----------------------------------------------------------------------
     * Share by tag: create a dynamic share from a tag's glob patterns
     * ----------------------------------------------------------------------- */
    async function openShareByTag() {
      const modal = document.getElementById('shareByTagModal');
      const selectEl = document.getElementById('shareByTagSelect');
      const patternsEl = document.getElementById('shareByTagPatterns');
      const errEl = document.getElementById('shareByTagError');
      if (!modal || !selectEl) return;
      selectEl.innerHTML = '<option value="">Loading tags…</option>';
      patternsEl.textContent = '';
      errEl.classList.add('hidden');
      let allTagRules = [];
      try {
        const res = await fetch('/admin/api/abac/tags', { headers: { 'X-XSRFToken': getXSRFToken() } });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const data = await res.json();
        allTagRules = data.tags || [];
      } catch {
        selectEl.innerHTML = '<option value="">Failed to load tags</option>';
      }
      /* Build unique tag names */
      const tagNames = [...new Set(allTagRules.map(function (r) { return r.tag; }))].sort((a, b) => a.localeCompare(b));
      if (tagNames.length) {
        selectEl.innerHTML = tagNames.map(function (t) {
          return '<option value="' + escapeAttr(t) + '">' + escapeHtml(t) + '</option>';
        }).join('');
      } else {
        selectEl.innerHTML = '<option value="">No tags defined — create one in Admin → Tags</option>';
      }
      function updatePatternPreview() {
        const chosen = selectEl.value;
        const patterns = allTagRules.filter(function (r) { return r.tag === chosen; }).map(function (r) { return r.glob_pattern; });
        patternsEl.textContent = patterns.length
          ? 'Patterns: ' + patterns.join(', ')
          : '';
      }
      selectEl.onchange = updatePatternPreview;
      updatePatternPreview();
      modal.showModal();
      const chosenTag = await new Promise(function (resolve) {
        document.getElementById('shareByTagConfirm').onclick = function () {
          if (!selectEl.value) { errEl.textContent = 'Please select a tag.'; errEl.classList.remove('hidden'); return; }
          modal.close(); resolve(selectEl.value);
        };
        document.getElementById('shareByTagCancel').onclick = function () { modal.close(); resolve(null); };
        modal.addEventListener('cancel', function (ev) { ev.preventDefault(); modal.close(); resolve(null); }, { once: true });
      });
      if (!chosenTag) return;
      const patterns = allTagRules.filter(function (r) { return r.tag === chosenTag; }).map(function (r) { return r.glob_pattern; });
      if (!patterns.length) { showDialog('No glob patterns defined for tag "' + chosenTag + '".', 'Error'); return; }
      try {
        const res = await fetch('/share/create', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({
            share_type: 'tag',
            tag_name: chosenTag,
            paths: [],
            allow_list: [],
            avoid_list: [],
          }),
        });
        const data = await res.json();
        if (!res.ok) { showDialog(data.error || ('Share creation failed (HTTP ' + res.status + ')'), 'Error'); return; }
        const shareId = data.share_id || data.id || '';
        const tokenLine = data.secret_token ? '\nToken: ' + data.secret_token : '';
        showDialog('Dynamic share created for tag "' + chosenTag + '".' + tokenLine + '\nShare ID: ' + shareId + '\n\nAccess it at /shared/' + shareId, 'Share created');
      } catch (e) {
        showDialog('Failed to create share: ' + e.message, 'Error');
      }
    }

    function calculateSkipRows(allRows) {
      // Skip empty directory message
      if (
        allRows.length > 0 &&
        allRows[0].children.length === 1 &&
        allRows[0].children[0].textContent.includes("This directory is empty")
      ) {
        return 1;
      }
      return 0;
    }

    // Table sorting functionality
    const _sortState = { column: null, direction: 'asc' };

    function _updateSortIndicators(column, direction) {
      document.querySelectorAll('[data-sort-column]').forEach((th) => {
        const c = Number.parseInt(th.dataset.sortColumn, 10);
        if (c === column) {
          th.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
        } else {
          th.setAttribute('aria-sort', 'none');
        }
      });
    }

    function sortTable(columnIndex) {
      const table = document.getElementById("fileTable");
      const tbody = table.querySelector("tbody");
      const allRows = Array.from(tbody.querySelectorAll("tr"));
      const skipRows = calculateSkipRows(allRows);
      const rows = allRows.slice(skipRows);

      // Toggle direction if re-clicking; otherwise pick a sensible default
      let direction;
      if (_sortState.column === columnIndex) {
        direction = _sortState.direction === 'asc' ? 'desc' : 'asc';
      } else {
        direction = (columnIndex === 2 || columnIndex === 3) ? 'desc' : 'asc';
      }
      _sortState.column = columnIndex;
      _sortState.direction = direction;
      const mult = direction === 'asc' ? 1 : -1;

      rows.sort((a, b) => {
        if (columnIndex === 2) {
          const av = Number.parseInt(a.children[2].dataset.bytes, 10) || -1;
          const bv = Number.parseInt(b.children[2].dataset.bytes, 10) || -1;
          return (av - bv) * mult;
        }
        if (columnIndex === 3) {
          const av = Number.parseInt(a.children[3].dataset.timestamp, 10) || 0;
          const bv = Number.parseInt(b.children[3].dataset.timestamp, 10) || 0;
          return (av - bv) * mult;
        }
        // Name column (0): keep directory grouping regardless of direction
        const nameCol = 0;
        const aIsDir = a.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";
        const bIsDir = b.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";
        if (aIsDir !== bIsDir) return bIsDir ? 1 : -1;
        const aVal = a.children[nameCol].textContent.trim();
        const bVal = b.children[nameCol].textContent.trim();
        return aVal.localeCompare(bVal) * mult;
      });

      rows.forEach((row) => tbody.appendChild(row));
      _updateSortIndicators(columnIndex, direction);

      // Keep mobile selector in sync when present
      const mobileSel = document.getElementById('mobileSortSelect');
      if (mobileSel) mobileSel.value = String(columnIndex);
    }

    document.addEventListener('DOMContentLoaded', function () {
      const table = document.getElementById('fileTable');
      if (!table) return;
      const resizers = table.querySelectorAll('.resizer');
      let th = null;
      let startX, startWidth;

      resizers.forEach(resizer => {
        resizer.addEventListener('mousedown', function (e) {
          e.preventDefault();
          th = e.target.parentElement;
          startX = e.pageX;
          startWidth = th.offsetWidth;

          for (const cell of table.querySelectorAll('th')) {
            cell.style.width = `${cell.offsetWidth}px`;
          }

          document.addEventListener('mousemove', mouseMove);
          document.addEventListener('mouseup', mouseUp);
        });
      });

      function mouseMove(e) {
        const width = startWidth + (e.pageX - startX);
        if (width > MIN_COLUMN_WIDTH) { // Minimum width
          th.style.width = width + 'px';
        }
      }

      function mouseUp() {
        document.removeEventListener('mousemove', mouseMove);
        document.removeEventListener('mouseup', mouseUp);
      }
    });

    // Share popup functions
    function _createEl(tag, props, text) {
      const el = document.createElement(tag);
      if (props) {
        for (const key of Object.keys(props)) {
          if (key === 'className') el.className = props[key];
          else if (key === 'dataset') Object.assign(el.dataset, props[key]);
          else el.setAttribute(key, props[key]);
        }
      }
      if (text != null) el.textContent = text;
      return el;
    }

    function formatShareAccessLabel(share) {
      const users = share.allowed_users;
      if (!users?.length) return 'Public Access';
      const n = users.length;
      const suffix = n === 1 ? '' : 's';
      return 'Restricted (' + n + ' user' + suffix + ')';
    }

    function _renderShareItem(share, origin) {
      const item = _createEl('div', { className: 'share-item' });
      item.appendChild(_createEl('div', { className: 'share-id' }, share.id));

      const urlWrap = _createEl('div', { className: 'share-url' });
      const a = _createEl('a', { href: share.url }, origin + share.url);
      urlWrap.appendChild(a);
      item.appendChild(urlWrap);

      const isRestricted = share.allowed_users && share.allowed_users.length > 0;
      const accessClass = isRestricted ? 'restricted' : 'public';
      item.appendChild(_createEl('div', { className: 'share-access ' + accessClass }, formatShareAccessLabel(share)));

      if (isRestricted) {
        const usersBox = _createEl('div', { className: 'share-users' });
        usersBox.appendChild(_createEl('div', { className: 'share-users-title' }, 'Allowed Users:'));
        for (const u of share.allowed_users) {
          usersBox.appendChild(_createEl('span', { className: 'user-tag' }, u));
        }
        item.appendChild(usersBox);
      }

      if (share.modify_users?.length) {
        const modBox = _createEl('div', { className: 'share-users' });
        modBox.appendChild(_createEl('div', { className: 'share-users-title' }, 'Modify Users:'));
        for (const u of share.modify_users) {
          modBox.appendChild(_createEl('span', { className: 'user-tag' }, '\u270f\ufe0f ' + u));
        }
        item.appendChild(modBox);
      }

      if (share.secret_token) {
        const tokenBlock = _createEl('div', { className: 'share-token-block' });
        tokenBlock.appendChild(_createEl('strong', null, '\uD83D\uDD10 Secret Token:'));
        tokenBlock.appendChild(document.createElement('br'));
        const code = _createEl('code', { className: 'share-token-code' }, share.secret_token);
        tokenBlock.appendChild(code);
        const copyTok = _createEl('button', {
          className: 'btn btn-sm',
          dataset: { action: 'copyToClipboard', text: share.secret_token },
        }, 'Copy');
        tokenBlock.appendChild(copyTok);
        item.appendChild(tokenBlock);
      }

      const actions = _createEl('div', { className: 'share-actions' });
      const copyBtn = _createEl('button', { className: 'btn', dataset: { action: 'copyToClipboard', text: origin + share.url } }, 'Copy Link');
      const openBtn = _createEl('button', { className: 'btn', dataset: { action: 'openShare', url: share.url } }, 'Open Share');
      const revokeBtn = _createEl('button', { className: 'btn', dataset: { action: 'revokeShare', id: share.id } }, 'Revoke');
      actions.appendChild(copyBtn); actions.appendChild(openBtn); actions.appendChild(revokeBtn);
      item.appendChild(actions);
      return item;
    }

    async function showShareDetails(filePath) {
      const popup = document.getElementById('sharePopup');
      const content = document.getElementById('sharePopupContent');

      popup.showModal();
      content.textContent = '';
      content.appendChild(_createEl('div', { className: 'p-6 text-center text-base-content/50' }, 'Loading share details…'));

      try {
        const response = await fetch('/api/share/details?path=' + encodeURIComponent(filePath));
        const data = await response.json();

        content.textContent = '';
        if (data.error) {
          content.appendChild(_createEl('div', { className: 'share-error-msg' }, 'Error: ' + data.error));
          return;
        }
        if (!data.shares || data.shares.length === 0) {
          content.appendChild(_createEl('div', { className: 'share-empty-msg' }, 'This file is not currently shared.'));
          return;
        }

        document.querySelector('.popup-title').textContent = 'Share Details - ' + filePath.split('/').pop();
        const origin = location.origin;
        for (const share of data.shares) {
          content.appendChild(_renderShareItem(share, origin));
        }
      } catch (error) {
        console.error('Error loading share details:', error);
        content.textContent = '';
        content.appendChild(_createEl('div', { className: 'share-error-msg' }, 'Failed to load share details'));
      }
    }

    function closeSharePopup() {
      const popup = document.getElementById('sharePopup');
      if (popup?.open) popup.close();
    }

    function copyToClipboard(text, btn) {
      globalThis.AirdCore.copyToClipboard(text, btn);
    }

    function openShare(url) {
      globalThis.location.href = url;
    }

    async function revokeShare(shareId) {
      const confirmed = await showDialog('Are you sure you want to revoke this share?', 'Confirm Revoke', { showCancel: true });
      if (!confirmed) return;

      try {
        const formData = new URLSearchParams();
        formData.append('id', shareId);
        const res = await fetch('/share/revoke', {
          method: 'POST',
          headers: { 'X-XSRFToken': getXSRFToken() },
          body: formData
        });
        if (!res.ok) {
          const detail = (await res.text().catch(() => '')).trim() || ('HTTP ' + res.status);
          showDialog('Failed to revoke share: ' + detail, 'Error');
          return;
        }
        closeSharePopup();
        globalThis.location.reload();
      } catch (error) {
        console.error('Error revoking share:', error);
        showDialog('Failed to revoke share: ' + (error.message || 'network error'), 'Error');
      }
    }

    // Event delegation for CSP compliance - handle all clicks through event listeners
    document.addEventListener('DOMContentLoaded', function () {
      FolderPicker.init();

      // Sort headers
      document.querySelectorAll('[data-sort-column]').forEach(function (el) {
        el.addEventListener('click', function () {
          sortTable(Number.parseInt(this.dataset.sortColumn, 10));
        });
      });

      // Shared icons
      document.querySelectorAll('[data-share-path]').forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.stopPropagation();
          e.preventDefault();
          showShareDetails(this.dataset.sharePath);
        });
      });

      // Rename buttons
      document.querySelectorAll('[data-rename-path]').forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.preventDefault();
          renameItem(this.dataset.renamePath);
        });
      });

      // Delete buttons
      document.querySelectorAll('[data-delete-path]').forEach(function (el) {
        el.addEventListener('click', function (e) {
          e.preventDefault();
          deleteItem(this.dataset.deletePath, this.dataset.isDir === '1');
        });
      });

      // Popup close button
      document.querySelectorAll('.popup-close').forEach(function (el) {
        el.addEventListener('click', function () {
          closeSharePopup();
        });
      });

      closeSelectionDrawer();

      // Restore selections from sessionStorage (drawer stays closed until user opens it)
      document.querySelectorAll('.row-checkbox').forEach(function (cb) {
        if (SelectionStore.has(cb.dataset.path)) {
          cb.checked = true;
        }
      });
      updateBulkToolbar();
      _selectionInitDone = true;

      // Select all checkbox
      const selectAllCheckbox = document.getElementById('selectAllCheckbox');
      if (selectAllCheckbox) {
        selectAllCheckbox.addEventListener('click', function (e) {
          e.stopPropagation();
        });
        selectAllCheckbox.addEventListener('change', function () {
          const allPagePaths = Array.from(document.querySelectorAll('.row-checkbox')).map(cb => cb.dataset.path);
          document.querySelectorAll('.row-checkbox').forEach(cb => { cb.checked = selectAllCheckbox.checked; });
          if (selectAllCheckbox.checked) {
            SelectionStore.addMany(allPagePaths);
          } else {
            SelectionStore.removeMany(allPagePaths);
          }
          updateBulkToolbar();
        });
      }
      document.querySelectorAll('.row-checkbox').forEach(function (cb) {
        cb.addEventListener('change', function () {
          syncCheckboxToStore(cb);
          updateBulkToolbar();
        });
      });
      const newFolderBtn = document.getElementById('newFolderBtn');
      if (newFolderBtn) newFolderBtn.addEventListener('click', newFolder);
      const bulkDownloadBtn = document.getElementById('bulkDownloadBtn');
      if (bulkDownloadBtn) bulkDownloadBtn.addEventListener('click', bulkDownload);
      const bulkDeleteBtn = document.getElementById('bulkDeleteBtn');
      if (bulkDeleteBtn) bulkDeleteBtn.addEventListener('click', bulkDelete);
      const bulkCopyBtn = document.getElementById('bulkCopyBtn');
      if (bulkCopyBtn) bulkCopyBtn.addEventListener('click', bulkCopy);
      const bulkMoveBtn = document.getElementById('bulkMoveBtn');
      if (bulkMoveBtn) bulkMoveBtn.addEventListener('click', bulkMove);
      const bulkAddToShareBtn = document.getElementById('bulkAddToShareBtn');
      if (bulkAddToShareBtn) bulkAddToShareBtn.addEventListener('click', bulkAddToShare);
      const bulkCreateShareBtn = document.getElementById('bulkCreateShareBtn');
      if (bulkCreateShareBtn) bulkCreateShareBtn.addEventListener('click', bulkCreateShare);
      const bulkAddTagsBtn = document.getElementById('bulkAddTagsBtn');
      if (bulkAddTagsBtn) bulkAddTagsBtn.addEventListener('click', bulkAddTags);
      const shareByTagBtn = document.getElementById('shareByTagBtn');
      if (shareByTagBtn) shareByTagBtn.addEventListener('click', openShareByTag);
      const clearSelectionBtn = document.getElementById('clearSelectionBtn');
      if (clearSelectionBtn) clearSelectionBtn.addEventListener('click', function () {
        SelectionStore.clear();
        document.querySelectorAll('.row-checkbox:checked').forEach(function (cb) { cb.checked = false; });
        const sa = document.getElementById('selectAllCheckbox');
        if (sa) sa.checked = false;
        updateBulkToolbar();
      });

      const selectionCountBtn = document.getElementById('selectionCountBtn');
      if (selectionCountBtn) {
        selectionCountBtn.addEventListener('click', toggleSelectionDrawer);
      }
      const selectionDrawerClose = document.getElementById('selectionDrawerClose');
      if (selectionDrawerClose) {
        selectionDrawerClose.addEventListener('click', closeSelectionDrawer);
      }
      const browseSelectionBackdrop = document.getElementById('browseSelectionBackdrop');
      if (browseSelectionBackdrop) {
        browseSelectionBackdrop.addEventListener('click', closeSelectionDrawer);
      }
      document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && _selectionDrawerIsOpen) {
          closeSelectionDrawer();
        }
      });

      // Copy current path button in breadcrumb
      const copyPathBtn = document.getElementById('copyPathBtn');
      if (copyPathBtn) {
        copyPathBtn.addEventListener('click', async function () {
          const path = document.getElementById('currentPath')?.value ?? '';
          const text = '/' + (path.replace(/^\/+/, ''));
          try {
            await navigator.clipboard.writeText(text);
            const original = copyPathBtn.textContent;
            copyPathBtn.classList.add('copied');
            copyPathBtn.textContent = '✓';
            setTimeout(() => {
              copyPathBtn.classList.remove('copied');
              copyPathBtn.textContent = original;
            }, 1200);
          } catch (e) {
            console.warn('Copy path failed:', e);
            showDialog('Failed to copy path to clipboard', 'Error');
          }
        });
      }

      // Mobile sort select (mirrors clicking the column header)
      const mobileSortSelect = document.getElementById('mobileSortSelect');
      if (mobileSortSelect) {
        mobileSortSelect.addEventListener('change', function () {
          const col = Number.parseInt(this.value, 10);
          if (!Number.isNaN(col)) sortTable(col);
        });
      }

      _initFileTagsHoverPopover();

      document.getElementById('fileTable')?.addEventListener('click', function (e) {
        const dl = e.target.closest('.download-btn');
        if (dl) {
          e.preventDefault();
          const path = dl.dataset.downloadPath
            || dl.closest('tr.file-row')?.dataset.path;
          if (path) void downloadFileViaWs(path);
          return;
        }
      });

      // Per-row tag add (+) buttons (re-bound after cell refresh)
      document.getElementById('fileTable')?.addEventListener('click', function (e) {
        const btn = e.target.closest('.row-tag-add-btn');
        if (!btn) return;
        e.preventDefault();
        e.stopPropagation();
        openRowTagPopover(btn.dataset.path, btn);
      });

      // Event delegation for dynamically generated share popup buttons
      document.addEventListener('click', function (e) {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        const action = el.dataset.action;
        if (action === 'copyToClipboard') {
          e.preventDefault();
          copyToClipboard(el.dataset.text, el);
        } else if (action === 'openShare') {
          e.preventDefault();
          openShare(el.dataset.url);
        } else if (action === 'revokeShare') {
          e.preventDefault();
          revokeShare(el.dataset.id);
        }
      });

      // --- Keyboard Shortcuts ---
      let shortcutsOverlay = null;

      function showShortcutsHelp() {
        if (shortcutsOverlay) { hideShortcutsHelp(); return; }
        shortcutsOverlay = document.createElement('div');
        shortcutsOverlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
        const card = document.createElement('div');
        card.style.cssText = 'background:var(--ds-surface);border:2px solid var(--ds-border-strong);border-radius:8px;padding:24px 32px;max-width:420px;width:90%;font-family:monospace;font-size:13px;color:var(--ds-text);';
        card.innerHTML = '<h3 style="margin:0 0 16px 0;">Keyboard Shortcuts</h3>' +
          '<table style="width:100%;border-collapse:collapse;">' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">?</td><td>Show this help</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">/</td><td>Focus search (if available)</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">n</td><td>New folder</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">u</td><td>Upload file</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Ctrl+A</td><td>Select all files</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Delete</td><td>Delete selected files</td></tr>' +
          '<tr><td style="padding:4px 12px 4px 0;font-weight:bold;">Escape</td><td>Deselect / close</td></tr>' +
          '</table>' +
          '<p style="margin:16px 0 0;font-size:11px;color:#888;">Press Escape or ? to close</p>';
        shortcutsOverlay.appendChild(card);
        shortcutsOverlay.addEventListener('click', function(e) { if (e.target === shortcutsOverlay) hideShortcutsHelp(); });
        document.body.appendChild(shortcutsOverlay);
      }

      function hideShortcutsHelp() {
        if (shortcutsOverlay) { shortcutsOverlay.remove(); shortcutsOverlay = null; }
      }

      function _handleEscapeKey() {
        if (shortcutsOverlay) { hideShortcutsHelp(); return; }
        if (_isRowTagPopoverOpen()) { closeRowTagPopover(); return; }
        if (globalThis.AirdCore.cancelActiveDialog()) return;
        const fpOverlay = document.getElementById('folderPickerOverlay');
        if (fpOverlay?.classList.contains('show')) { FolderPicker.close(null); return; }
        const sharePicker = document.getElementById('sharePickerModal');
        if (sharePicker?.open) {
          document.getElementById('sharePickerCancel')?.click();
          return;
        }
        const sharePopup = document.getElementById('sharePopup');
        if (sharePopup?.open) { closeSharePopup(); return; }
        SelectionStore.clear();
        document.querySelectorAll('.row-checkbox:checked').forEach(function(cb) { cb.checked = false; });
        const selectAll = document.getElementById('selectAllCheckbox');
        if (selectAll) selectAll.checked = false;
        updateBulkToolbar();
      }

      function handleShortcutQuestionMark(e) {
        if (e.key !== '?') return false;
        e.preventDefault();
        showShortcutsHelp();
        return true;
      }

      function handleShortcutSlash(e) {
        if (e.key !== '/') return false;
        e.preventDefault();
        const searchInput = document.querySelector('input[type="text"][placeholder*="earch"], input[type="search"]');
        if (searchInput) searchInput.focus();
        return true;
      }

      function handleShortcutNewFolder(e) {
        if (e.key !== 'n' || e.ctrlKey || e.metaKey) return false;
        e.preventDefault();
        document.getElementById('newFolderBtn')?.click();
        return true;
      }

      function handleShortcutUpload(e) {
        if (e.key !== 'u' || e.ctrlKey || e.metaKey) return false;
        e.preventDefault();
        document.getElementById('fileInput')?.click();
        return true;
      }

      function handleShortcutSelectAll(e) {
        if (e.key !== 'a' || (!e.ctrlKey && !e.metaKey)) return false;
        e.preventDefault();
        const selectAllCb = document.getElementById('selectAllCheckbox');
        if (selectAllCb) {
          selectAllCb.checked = true;
          selectAllCb.dispatchEvent(new Event('change', { bubbles: true }));
        }
        return true;
      }

      function handleShortcutDelete(e) {
        if (e.key !== 'Delete') return false;
        const checked = document.querySelectorAll('.row-checkbox:checked');
        if (checked.length > 0) document.getElementById('bulkDeleteBtn')?.click();
        return true;
      }

      document.addEventListener('keydown', function browseGlobalKeydown(e) {
        if (e.key === 'Escape') {
          _handleEscapeKey();
          return;
        }
        if (isInputKeyTarget(e.target)) return;
        if (handleShortcutQuestionMark(e)) return;
        if (handleShortcutSlash(e)) return;
        if (handleShortcutNewFolder(e)) return;
        if (handleShortcutUpload(e)) return;
        if (handleShortcutSelectAll(e)) return;
        handleShortcutDelete(e);
      });
      // --- Column Resize ---
      (function initColumnResize() {
        const table = document.getElementById('fileTable');
        if (!table) return;
        const resizers = table.querySelectorAll('.col-resizer');
        if (!resizers.length) return;

        const STORAGE_KEY = 'aird_browse_col_widths';
        const ths = Array.from(table.querySelectorAll('thead th'));

        function saveWidths() {
          try {
            const widths = ths.map(function (th) { return th.style.width || ''; });
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(widths));
          } catch (_) { /* ignore */ }
        }

        function restoreWidths() {
          try {
            const raw = sessionStorage.getItem(STORAGE_KEY);
            if (!raw) return;
            const widths = JSON.parse(raw);
            if (!Array.isArray(widths) || widths.length !== ths.length) return;
            widths.forEach(function (w, i) {
              if (w) ths[i].style.width = w;
            });
          } catch (_) { /* ignore */ }
        }

        restoreWidths();

        resizers.forEach(function (resizer) {
          let startX, startW, th;

          function onMouseMove(e) {
            const diff = e.clientX - startX;
            const newW = Math.max(40, startW + diff);
            th.style.width = newW + 'px';
          }

          function onMouseUp() {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
            resizer.classList.remove('col-resizing');
            table.classList.remove('col-resize-active');
            saveWidths();
          }

          resizer.addEventListener('mousedown', function (e) {
            e.preventDefault();
            e.stopPropagation();
            th = resizer.parentElement;
            startX = e.clientX;
            startW = th.offsetWidth;
            resizer.classList.add('col-resizing');
            table.classList.add('col-resize-active');
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
          });
        });
      })();
    });

})();
