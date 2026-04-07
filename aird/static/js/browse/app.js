(function() {
  "use strict";

    // Configurable constants
    const RELOAD_DELAY_MS = 500;
    const MIN_COLUMN_WIDTH = 50;
    const MAX_FILE_SIZE = globalThis.__BROWSE_CONFIG ? globalThis.__BROWSE_CONFIG.maxFileSize : 10737418240;

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

    // Fallback for image thumbnails that fail to load
    document.addEventListener('error', function(e) {
      if (e.target.tagName === 'IMG' && e.target.dataset.fallback) {
        e.target.outerHTML = e.target.dataset.fallback;
      }
    }, true);

    // Custom dialog function
    function showDialog(message, title = 'Confirm', options = {}) {
      return new Promise((resolve) => {
        document.getElementById('dialogTitle').textContent = title;
        document.getElementById('dialogMessage').textContent = message;

        const confirmBtn = document.getElementById('dialogConfirmBtn');
        const cancelBtn = document.getElementById('dialogCancelBtn');
        const inputContainer = document.getElementById('dialogInputContainer');
        const input = document.getElementById('dialogInput');

        confirmBtn.textContent = options.confirmText || 'OK';
        cancelBtn.style.display = options.showCancel ? 'inline-block' : 'none';
        inputContainer.style.display = options.prompt ? 'block' : 'none';
        input.value = options.prompt ? (options.defaultValue || '') : '';

        document.getElementById('customDialogModal').classList.add('show');

        if (options.prompt) {
          input.focus();
          input.select();
        }

        confirmBtn.onclick = () => {
          document.getElementById('customDialogModal').classList.remove('show');
          resolve(options.prompt ? input.value : true);
        };

        cancelBtn.onclick = () => {
          document.getElementById('customDialogModal').classList.remove('show');
          resolve(options.prompt ? null : false);
        };

        // Handle Enter key for prompt
        if (options.prompt) {
          input.onkeydown = (e) => {
            if (e.key === 'Enter') {
              confirmBtn.click();
            } else if (e.key === 'Escape') {
              cancelBtn.click();
            }
          };
        }
      });
    }

    /** True if keyboard events should be ignored (typing in a field). */
    function isInputKeyTarget(target) {
      const tag = (target.tagName || '').toLowerCase();
      return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
    }

    // File upload functionality
    const uploadZone = document.getElementById("uploadZone");
    const fileInput = document.getElementById("fileInput");
    const progressContainer = document.getElementById("progressContainer");
    let uploadQueue = [];
    let isUploading = false;
    let reloadTimer = null;

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

        const item = document.createElement("div");
        item.style.marginTop = "5px";
        const nameEl = document.createElement("div");
        nameEl.textContent = fw.relativePath;
        const bar = document.createElement("progress");
        bar.max = 100; bar.value = 0; bar.style.width = "100%";
        const info = document.createElement("span");
        info.style.display = "block"; info.style.fontSize = "12px";
        const cancelBtn = document.createElement("button");
        cancelBtn.textContent = "Cancel"; cancelBtn.style.marginTop = "2px";
        item.appendChild(nameEl); item.appendChild(bar); item.appendChild(info); item.appendChild(cancelBtn);
        progressContainer.appendChild(item);

        // Compute the upload dir: current path + relative folder path
        const parts = fw.relativePath.split('/');
        const fileName = parts.pop();
        const subDir = parts.join('/');
        let uploadDir = document.getElementById('currentPath')?.value ?? '';
        if (subDir) uploadDir = uploadDir ? uploadDir + '/' + subDir : subDir;

        const queueItem = { file: fw.file, bar, info, cancelBtn, item, xhr: null, start: null, uploadDir, uploadName: fileName };
        queueItem.cancelBtn.addEventListener("click", function() {
          if (queueItem.xhr) { queueItem.xhr.abort(); }
          else {
            const idx = uploadQueue.indexOf(queueItem);
            if (idx !== -1) uploadQueue.splice(idx, 1);
            queueItem.item.remove();
            if (progressContainer.children.length === 0) progressContainer.style.display = "none";
          }
        });
        uploadQueue.push(queueItem);
      }
      if (rejected.length > 0) {
        const limitGB = (MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2);
        showDialog('Files exceed the ' + limitGB + ' GB limit: ' + rejected.join(", "), 'File Size Limit');
      }
      fileInput.value = "";
      clearTimeout(reloadTimer);
      if (!isUploading && uploadQueue.length > 0) {
        isUploading = true;
        progressContainer.style.display = "block";
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

        const item = document.createElement("div");
        item.style.marginTop = "5px";
        const nameEl = document.createElement("div");
        nameEl.textContent = file.name;
        const bar = document.createElement("progress");
        bar.max = 100;
        bar.value = 0;
        bar.style.width = "100%";
        const info = document.createElement("span");
        info.style.display = "block";
        info.style.fontSize = "12px";
        const cancelBtn = document.createElement("button");
        cancelBtn.textContent = "Cancel";
        cancelBtn.style.marginTop = "2px";

        item.appendChild(nameEl);
        item.appendChild(bar);
        item.appendChild(info);
        item.appendChild(cancelBtn);
        progressContainer.appendChild(item);

        const queueItem = { file, bar, info, cancelBtn, item, xhr: null, start: null };

        cancelBtn.addEventListener("click", () => {
          if (queueItem.xhr) {
            queueItem.xhr.abort();
          } else {
            const idx = uploadQueue.indexOf(queueItem);
            if (idx !== -1) {
              uploadQueue.splice(idx, 1);
            }
            item.remove();
            if (progressContainer.children.length === 0) {
              progressContainer.style.display = "none";
            }
          }
        });

        uploadQueue.push(queueItem);
      }

      if (rejected.length > 0) {
        const limitGB = (MAX_FILE_SIZE / (1024 * 1024 * 1024)).toFixed(2);
        showDialog(`Files exceed the ${limitGB} GB limit: ${rejected.join(", ")}`, 'File Size Limit');
      }

      fileInput.value = "";
      clearTimeout(reloadTimer);
      if (!isUploading && uploadQueue.length > 0) {
        isUploading = true;
        progressContainer.style.display = "block";
        processQueue();
      }
    }

    async function processQueue() {
      if (uploadQueue.length === 0) {
        isUploading = false;
        if (progressContainer.children.length === 0) {
          progressContainer.style.display = "none";
        }
        scheduleReload();
        return;
      }

      const current = uploadQueue.shift();
      try {
        await uploadFile(current);
      } catch (err) {
        console.warn('Upload error (message already shown to user):', err);
      }

      processQueue();
    }

    function scheduleReload() {
      clearTimeout(reloadTimer);
      reloadTimer = setTimeout(() => {
        if (uploadQueue.length === 0 && !isUploading) {
          location.reload();
        }
      }, RELOAD_DELAY_MS);
    }

    // CSRF Protection: Get XSRF token from cookie
    function getXSRFToken() {
      const match = /_xsrf=([^;]*)/.exec(document.cookie);
      return match ? decodeURIComponent(match[1]) : '';
    }

    function formatBytes(bytes) {
      const units = ["B", "KB", "MB", "GB", "TB"];
      let i = 0;
      while (bytes >= 1024 && i < units.length - 1) {
        bytes /= 1024;
        i++;
      }
      return `${bytes.toFixed(1)} ${units[i]}`;
    }

    function uploadFile(item) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        item.xhr = xhr;
        item.start = Date.now();
        xhr.open("POST", "/upload");

        xhr.upload.addEventListener("progress", (e) => {
          if (e.lengthComputable) {
            const percent = (e.loaded / e.total) * 100;
            const elapsed = (Date.now() - item.start) / 1000;
            const speed = e.loaded / (elapsed || 1);
            const remaining = e.total - e.loaded;
            item.bar.value = percent;
            item.info.textContent = `${Math.round(percent)}% - ${formatBytes(e.loaded)} of ${formatBytes(e.total)} (${formatBytes(remaining)} left) @ ${formatBytes(speed)}/s`;
          }
        });

        xhr.addEventListener("load", () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            item.bar.value = 100;
            item.info.textContent = "Completed";
            item.cancelBtn.remove();
            resolve();
          } else {
            item.info.textContent = "Failed";
            item.cancelBtn.remove();
            reject(new Error(xhr.responseText || `Upload of ${item.file.name} failed`));
          }
        });

        xhr.addEventListener("error", () => {
          item.info.textContent = "Error";
          item.cancelBtn.remove();
          reject(new Error(`Upload of ${item.file.name} failed`));
        });

        xhr.addEventListener("abort", () => {
          item.info.textContent = "Cancelled";
          item.cancelBtn.remove();
          reject(new Error(`Upload of ${item.file.name} cancelled`));
        });

        // Stream raw file bytes with headers the backend expects
        const dir = item.uploadDir ?? document.getElementById('currentPath')?.value ?? '';
        const fname = item.uploadName ?? item.file.name;
        xhr.setRequestHeader("X-Upload-Dir", encodeURIComponent(dir));
        xhr.setRequestHeader("X-Upload-Filename", encodeURIComponent(fname));
        xhr.setRequestHeader("Content-Type", "application/octet-stream");
        // CSRF Protection
        const xsrfToken = getXSRFToken();
        if (xsrfToken) {
          xhr.setRequestHeader("X-XSRFToken", xsrfToken);
        }

        xhr.send(item.file);
      });
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
            location.reload();
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
            location.reload();
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

    function updateBulkToolbar() {
      const allPaths = SelectionStore.getAll();
      const totalCount = allPaths.length;
      const pageCount = getPageSelectedPaths().length;
      const otherCount = totalCount - pageCount;
      const bar = document.getElementById('selectionBar');
      const countEl = document.getElementById('bulkCount');
      const filesList = document.getElementById('selectedFilesList');
      if (!bar || !countEl) return;

      if (totalCount === 0) {
        bar.classList.remove('visible');
      } else {
        let label = totalCount + ' selected';
        if (otherCount > 0) {
          label += ' (' + otherCount + ' from other folders)';
        }
        countEl.textContent = label;

        if (filesList) {
          const displayPaths = allPaths.slice(0, 10);
          filesList.innerHTML = displayPaths.map(function (p) {
            const name = p.split('/').findLast(Boolean) ?? p;
            return '<span class="selected-file" title="' + p.replaceAll('"', '&quot;') + '">' +
              name.replaceAll('<', '&lt;') + '</span>';
          }).join('');
          if (allPaths.length > 10) {
            filesList.innerHTML += '<span class="selected-file">+' + (allPaths.length - 10) + ' more</span>';
          }
        }

        bar.classList.add('visible');
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
        if (res.ok) location.reload();
        else showDialog('Create folder failed: ' + (await res.text()), 'Error');
      } catch (e) {
        showDialog('Create folder failed: ' + e.message, 'Error');
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
        if (data.ok) { SelectionStore.clear(); location.reload(); }
        else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Bulk delete failed', 'Error');
      } catch (e) {
        showDialog('Bulk delete failed: ' + e.message, 'Error');
      }
    }

    // --- Folder Picker ---
    const FolderPicker = {
      _overlay: null,
      _body: null,
      _breadcrumb: null,
      _destDisplay: null,
      _confirmBtn: null,
      _titleEl: null,
      _currentPath: '',
      _mode: 'copy',
      _resolve: null,

      init() {
        this._overlay = document.getElementById('folderPickerOverlay');
        this._body = document.getElementById('fpBody');
        this._breadcrumb = document.getElementById('fpBreadcrumb');
        this._destDisplay = document.getElementById('fpDestDisplay');
        this._confirmBtn = document.getElementById('fpConfirmBtn');
        this._titleEl = document.getElementById('fpTitle');

        document.getElementById('fpCloseBtn').addEventListener('click', () => this.close(null));
        document.getElementById('fpCancelBtn').addEventListener('click', () => this.close(null));
        this._confirmBtn.addEventListener('click', () => this.close(this._currentPath));
        this._overlay.addEventListener('click', (e) => {
          if (e.target === this._overlay) this.close(null);
        });
        document.getElementById('fpNewFolderBtn').addEventListener('click', () => this._createFolder());
      },

      open(mode) {
        this._mode = mode;
        this._titleEl.textContent = mode === 'copy' ? 'Copy to...' : 'Move to...';
        this._confirmBtn.textContent = mode === 'copy' ? 'Paste here' : 'Move here';
        this._overlay.classList.add('show');
        const startPath = document.getElementById('currentPath')?.value ?? '';
        this._navigate(startPath);

        return new Promise((resolve) => { this._resolve = resolve; });
      },

      close(result) {
        this._overlay.classList.remove('show');
        if (this._resolve) {
          this._resolve(result);
          this._resolve = null;
        }
      },

      async _navigate(path) {
        this._currentPath = path;
        this._renderBreadcrumb(path);
        this._destDisplay.textContent = '/' + (path || '');
        this._body.innerHTML = '<div class="fp-loading">Loading...</div>';

        try {
          const res = await fetch('/api/files/' + encodeURI(path));
          if (!res.ok) throw new Error('Failed to load');
          const data = await res.json();
          const folders = (data.files || []).filter(f => f.is_dir);
          this._renderFolders(folders);
        } catch (e) {
          console.warn('Directory load failed:', e);
          this._body.innerHTML = '<div class="fp-empty">Failed to load directory</div>';
        }
      },

      _renderBreadcrumb(path) {
        const parts = path.split('/').filter(Boolean);
        let html = '<a data-fp-nav="">🏠 Root</a>';
        parts.forEach((part, i) => {
          const partPath = parts.slice(0, i + 1).join('/');
          html += ' <span class="fp-sep">/</span> ';
          if (i === parts.length - 1) {
            html += '<span class="fp-current">' + this._esc(part) + '</span>';
          } else {
            html += '<a data-fp-nav="' + partPath + '">' + this._esc(part) + '</a>';
          }
        });
        this._breadcrumb.innerHTML = html;
        this._breadcrumb.querySelectorAll('a[data-fp-nav]').forEach(a => {
          a.addEventListener('click', (e) => {
            e.preventDefault();
            this._navigate(a.dataset.fpNav);
          });
        });
      },

      _renderFolders(folders) {
        if (folders.length === 0) {
          this._body.innerHTML = '<div class="fp-empty">No subfolders here</div>';
          return;
        }
        const ul = document.createElement('ul');
        ul.className = 'fp-folder-list';
        folders.forEach(f => {
          const li = document.createElement('li');
          li.className = 'fp-folder-item';
          li.innerHTML = '<span class="fp-icon">📁</span>' +
            '<span class="fp-name">' + this._esc(f.name) + '</span>' +
            '<span class="fp-arrow">›</span>';
          li.addEventListener('click', () => {
            const child = this._currentPath ? this._currentPath + '/' + f.name : f.name;
            this._navigate(child);
          });
          ul.appendChild(li);
        });
        this._body.innerHTML = '';
        this._body.appendChild(ul);
      },

      async _createFolder() {
      const name = await showDialog('Enter folder name:', 'New folder', { prompt: true, showCancel: true });
      if (!name?.trim()) return;
      const formData = new URLSearchParams();
      formData.append('parent', this._currentPath);
        formData.append('name', name.trim());
        try {
          const res = await fetch('/mkdir', {
            method: 'POST',
            headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() },
            body: formData.toString(),
          });
          if (res.ok) this._navigate(this._currentPath);
          else showDialog('Create folder failed: ' + (await res.text()), 'Error');
        } catch (e) {
          showDialog('Create folder failed: ' + e.message, 'Error');
        }
      },

      _esc(str) {
        const el = document.createElement('span');
        el.textContent = str;
        return el.innerHTML;
      }
    };

    async function bulkCopy() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const destDir = await FolderPicker.open('copy');
      if (destDir == null) return;
      let failed = 0;
      for (const path of paths) {
        const base = path.split('/').findLast(Boolean) ?? path;
        const fullDest = destDir ? destDir + '/' + base : base;
        const formData = new URLSearchParams();
        formData.append('path', path);
        formData.append('dest', fullDest);
        const res = await fetch('/copy', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
        if (!res.ok) failed++;
      }
      if (failed === 0) { SelectionStore.clear(); location.reload(); }
      else showDialog(failed + ' of ' + paths.length + ' copy operation(s) failed.', 'Copy');
    }

    async function bulkMove() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const destDir = await FolderPicker.open('move');
      if (destDir == null) return;
      let failed = 0;
      for (const path of paths) {
        const base = path.split('/').findLast(Boolean) ?? path;
        const fullDest = destDir ? destDir + '/' + base : base;
        const formData = new URLSearchParams();
        formData.append('path', path);
        formData.append('dest', fullDest);
        const res = await fetch('/move', { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded', 'X-XSRFToken': getXSRFToken() }, body: formData.toString() });
        if (!res.ok) failed++;
      }
      if (failed === 0) { SelectionStore.clear(); location.reload(); }
      else showDialog(failed + ' of ' + paths.length + ' move operation(s) failed.', 'Move');
    }

    async function bulkAddToShare() {
      const paths = getSelectedPaths();
      if (paths.length === 0) return;
      const selectEl = document.getElementById('sharePickerSelect');
      const modal = document.getElementById('sharePickerModal');
      if (!selectEl || !modal) return;
      selectEl.innerHTML = '<option value="">Loading...</option>';
      modal.classList.add('show');
      try {
        const res = await fetch('/share/list');
        const data = await res.json();
        if (data.error) { showDialog(data.error, 'Error'); modal.classList.remove('show'); return; }
        const shares = data.shares || {};
        selectEl.innerHTML = Object.keys(shares).length ? Object.entries(shares).map(([id, s]) => '<option value="' + id + '">' + id + (s.paths?.length ? ' (' + s.paths.length + ' path(s))' : '') + '</option>').join('') : '<option value="">No shares</option>';
      } catch (e) {
        console.warn('Failed to load shares:', e);
        selectEl.innerHTML = '<option value="">Error loading shares</option>';
      }
      const shareId = await new Promise((resolve) => {
        document.getElementById('sharePickerConfirm').onclick = () => { modal.classList.remove('show'); resolve(selectEl.value); };
        document.getElementById('sharePickerCancel').onclick = () => { modal.classList.remove('show'); resolve(null); };
      });
      if (!shareId) return;
      try {
        const res = await fetch('/api/bulk', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({ action: 'add_to_share', share_id: shareId, paths }),
        });
        const data = await res.json();
        if (data.ok) { SelectionStore.clear(); location.reload(); }
        else showDialog(data.results?.some(r => !r.ok) ? data.results.map(r => r.error).filter(Boolean).join('; ') : 'Add to share failed', 'Error');
      } catch (e) {
        showDialog('Add to share failed: ' + e.message, 'Error');
      }
    }

    function calculateSkipRows(allRows) {
      // Skip parent directory link if present (must stay first; never sort with file rows).
      if (allRows.length > 0 && allRows[0].classList.contains("parent-row")) {
        return 1;
      }
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
    function sortTable(columnIndex) {
      const table = document.getElementById("fileTable");
      const tbody = table.querySelector("tbody");
      const allRows = Array.from(tbody.querySelectorAll("tr"));
      const skipRows = calculateSkipRows(allRows);
      const rows = allRows.slice(skipRows);

      rows.sort((a, b) => {
        let aVal = a.children[columnIndex].textContent.trim();
        let bVal = b.children[columnIndex].textContent.trim();

        // Column indices: 0 = Name (checkbox+name), 1 = Size, 2 = Modified, 3 = Actions
        if (columnIndex === 1) {
          // Size column
          aVal = Number.parseInt(a.children[columnIndex].dataset.bytes, 10) || -1;
          bVal = Number.parseInt(b.children[columnIndex].dataset.bytes, 10) || -1;
          return bVal - aVal;
        } else if (columnIndex === 2) {
          // Modified column
          aVal = Number.parseInt(a.children[columnIndex].dataset.timestamp, 10) || 0;
          bVal = Number.parseInt(b.children[columnIndex].dataset.timestamp, 10) || 0;
          return bVal - aVal;
        }

        const nameCol = 0;
        const aIsDir = a.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";
        const bIsDir = b.children[nameCol]?.querySelector(".file-icon")?.textContent === "📁";

        if (aIsDir !== bIsDir) {
          return bIsDir ? 1 : -1;
        }

        return aVal.localeCompare(bVal);
      });

      rows.forEach((row) => tbody.appendChild(row));
    }

    document.addEventListener('DOMContentLoaded', function () {
      const table = document.getElementById('fileTable');
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
    function formatShareAccessLabel(share) {
      const users = share.allowed_users;
      if (!users) return 'Public Access';
      const n = users.length;
      let pluralSuffix = 's';
      if (n === 1) pluralSuffix = '';
      return 'Restricted (' + n + ' user' + pluralSuffix + ')';
    }

    function formatShareAllowedUsersHtml(share) {
      const users = share.allowed_users;
      if (!users) return '';
      const tags = users.map(function (u) {
        return '<span class="user-tag">' + u + '</span>';
      }).join('');
      return '<div class="share-users"><div class="share-users-title">Allowed Users:</div>' + tags + '</div>';
    }

    async function showShareDetails(filePath) {
      const popup = document.getElementById('sharePopup');
      const content = document.getElementById('sharePopupContent');

      // Show popup with loading state
      popup.classList.add('show');
      content.innerHTML = '<div class="loading">Loading share details...</div>';

      try {
        const response = await fetch(`/api/share/details?path=${encodeURIComponent(filePath)}`);
        const data = await response.json();

        if (data.error) {
          content.innerHTML = `<div style="color: red; text-align: center; padding: 20px;">Error: ${data.error}</div>`;
          return;
        }

        if (data.shares.length === 0) {
          content.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">This file is not currently shared.</div>';
          return;
        }

        // Update popup title with filename
        document.querySelector('.popup-title').textContent = `Share Details - ${filePath.split('/').pop()}`;

        // Render share details
        const origin = location.origin;
        content.innerHTML = data.shares.map(function (share) {
          const accessClass = share.allowed_users ? 'restricted' : 'public';
          const accessLabel = formatShareAccessLabel(share);
          const usersHtml = formatShareAllowedUsersHtml(share);
          return (
            '<div class="share-item">' +
            '<div class="share-id">' + share.id + '</div>' +
            '<div class="share-url">' +
            '<a href="' + share.url + '" target="_blank">' + origin + share.url + '</a>' +
            '</div>' +
            '<div class="share-access ' + accessClass + '">' + accessLabel + '</div>' +
            usersHtml +
            '<div class="share-actions">' +
            '<button class="btn" data-action="copyToClipboard" data-text="' + origin + share.url + '">Copy Link</button>' +
            '<button class="btn" data-action="openShare" data-url="' + share.url + '">Open Share</button>' +
            '<button class="btn" data-action="revokeShare" data-id="' + share.id + '">Revoke</button>' +
            '</div>' +
            '</div>'
          );
        }).join('');

      } catch (error) {
        console.error('Error loading share details:', error);
        content.innerHTML = '<div style="color: red; text-align: center; padding: 20px;">Failed to load share details</div>';
      }
    }

    function closeSharePopup() {
      const popup = document.getElementById('sharePopup');
      popup.classList.remove('show');
    }

    // Close popup when clicking outside
    document.addEventListener('click', function (event) {
      const popup = document.getElementById('sharePopup');
      if (event.target === popup) {
        closeSharePopup();
      }
    });

    // Close popup with Escape key
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        closeSharePopup();
      }
    });

    function _showCopiedFeedback(btn) {
      if (!btn) return;
      const originalText = btn.textContent;
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = originalText; }, 1500);
    }

    function copyToClipboard(text, btn) {
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text)
          .then(() => _showCopiedFeedback(btn))
          .catch(() => showDialog('Failed to copy to clipboard', 'Error'));
      } else {
        showDialog('Clipboard not available', 'Error');
      }
    }

    function openShare(url) {
      globalThis.open(url, '_blank');
    }

    async function revokeShare(shareId) {
      const confirmed = await showDialog('Are you sure you want to revoke this share?', 'Confirm Revoke', { showCancel: true });
      if (!confirmed) return;

      try {
        const formData = new URLSearchParams();
        formData.append('id', shareId);
        await fetch('/share/revoke', {
          method: 'POST',
          headers: {
            'X-XSRFToken': getXSRFToken()
          },
          body: formData
        });

        // Close popup and refresh the page to update share status
        closeSharePopup();
        location.reload();
      } catch (error) {
        console.error('Error revoking share:', error);
        showDialog('Failed to revoke share', 'Error');
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

      // Restore selections from sessionStorage
      document.querySelectorAll('.row-checkbox').forEach(function (cb) {
        if (SelectionStore.has(cb.dataset.path)) {
          cb.checked = true;
        }
      });
      updateBulkToolbar();

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
      const bulkDeleteBtn = document.getElementById('bulkDeleteBtn');
      if (bulkDeleteBtn) bulkDeleteBtn.addEventListener('click', bulkDelete);
      const bulkCopyBtn = document.getElementById('bulkCopyBtn');
      if (bulkCopyBtn) bulkCopyBtn.addEventListener('click', bulkCopy);
      const bulkMoveBtn = document.getElementById('bulkMoveBtn');
      if (bulkMoveBtn) bulkMoveBtn.addEventListener('click', bulkMove);
      const bulkAddToShareBtn = document.getElementById('bulkAddToShareBtn');
      if (bulkAddToShareBtn) bulkAddToShareBtn.addEventListener('click', bulkAddToShare);
      const clearSelectionBtn = document.getElementById('clearSelectionBtn');
      if (clearSelectionBtn) clearSelectionBtn.addEventListener('click', function () {
        SelectionStore.clear();
        document.querySelectorAll('.row-checkbox:checked').forEach(function (cb) { cb.checked = false; });
        const sa = document.getElementById('selectAllCheckbox');
        if (sa) sa.checked = false;
        updateBulkToolbar();
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

      // --- Favorites ---
      document.querySelectorAll('.fav-star').forEach(function(star) {
        star.addEventListener('click', async function(e) {
          e.preventDefault();
          e.stopPropagation();
          const path = star.dataset.favPath;
          try {
            const r = await fetch('/api/favorites/toggle', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
              body: JSON.stringify({ path })
            });
            const data = await r.json();
            if (data.favorited) {
              star.textContent = '\u2605';
              star.classList.add('fav-active');
            } else {
              star.textContent = '\u2606';
              star.classList.remove('fav-active');
            }
          } catch (e) {
            console.warn('Favorite toggle failed:', e);
          }
        });
      });

      // --- Keyboard Shortcuts ---
      let shortcutsOverlay = null;

      function showShortcutsHelp() {
        if (shortcutsOverlay) { hideShortcutsHelp(); return; }
        shortcutsOverlay = document.createElement('div');
        shortcutsOverlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center;';
        const card = document.createElement('div');
        card.style.cssText = 'background:#fff;border:2px solid #000;border-radius:8px;padding:24px 32px;max-width:420px;width:90%;font-family:monospace;font-size:13px;';
        if (document.documentElement.dataset.theme === 'dark') {
          card.style.background = '#1e1e2e';
          card.style.color = '#e0e0e0';
          card.style.borderColor = '#444';
        }
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
        const fpOverlay = document.getElementById('folderPickerOverlay');
        if (fpOverlay?.classList.contains('show')) { FolderPicker.close(null); return; }
        const popup = document.querySelector('.popup-overlay.show');
        if (popup) { popup.classList.remove('show'); return; }
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
    });

})();
