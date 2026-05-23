/**
 * Folder picker overlay for bulk copy/move. Requires #folderPickerOverlay markup.
 * @global AirdFolderPicker
 */
(function attachFolderPicker(global) {
  'use strict';

  const core = global.AirdCore;

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
    _abort: null,

    init() {
      this._overlay = document.getElementById('folderPickerOverlay');
      this._body = document.getElementById('fpBody');
      this._breadcrumb = document.getElementById('fpBreadcrumb');
      this._destDisplay = document.getElementById('fpDestDisplay');
      this._confirmBtn = document.getElementById('fpConfirmBtn');
      this._titleEl = document.getElementById('fpTitle');
      if (!this._overlay) return;

      document.getElementById('fpCloseBtn')?.addEventListener('click', () => this.close(null));
      document.getElementById('fpCancelBtn')?.addEventListener('click', () => this.close(null));
      this._confirmBtn?.addEventListener('click', () => this.close(this._currentPath));
      this._overlay.addEventListener('click', (e) => {
        if (e.target === this._overlay) this.close(null);
      });
      document.getElementById('fpNewFolderBtn')?.addEventListener('click', () => this._createFolder());
    },

    open(mode) {
      this._mode = mode;
      if (this._titleEl) {
        this._titleEl.textContent = mode === 'copy' ? 'Copy to...' : 'Move to...';
      }
      if (this._confirmBtn) {
        this._confirmBtn.textContent = mode === 'copy' ? 'Paste here' : 'Move here';
      }
      this._overlay?.classList.add('show');
      const startPath = document.getElementById('currentPath')?.value ?? '';
      this._navigate(startPath);
      return new Promise((resolve) => { this._resolve = resolve; });
    },

    close(result) {
      this._overlay?.classList.remove('show');
      if (this._resolve) {
        this._resolve(result);
        this._resolve = null;
      }
    },

    async _navigate(path) {
      this._currentPath = path;
      this._renderBreadcrumb(path);
      if (this._destDisplay) {
        this._destDisplay.textContent = '/' + (path || '');
      }
      if (this._body) {
        this._body.innerHTML = '<div class="fp-loading">Loading...</div>';
      }

      if (this._abort) this._abort.abort();
      const controller = new AbortController();
      this._abort = controller;

      try {
        const res = await fetch('/api/files/' + encodeURI(path), { signal: controller.signal });
        if (controller.signal.aborted) return;
        if (!res.ok) throw new Error('Failed to load');
        const data = await res.json();
        if (controller.signal.aborted) return;
        const folders = (data.files || []).filter((f) => f.is_dir);
        this._renderFolders(folders);
      } catch (e) {
        if (e?.name === 'AbortError') return;
        console.warn('Directory load failed:', e);
        if (this._body) {
          this._body.innerHTML = '<div class="fp-empty">Failed to load directory</div>';
        }
      }
    },

    _renderBreadcrumb(path) {
      if (!this._breadcrumb) return;
      const parts = path.split('/').filter(Boolean);
      let html = '<a data-fp-nav="">🏠 Root</a>';
      parts.forEach((part, i) => {
        const partPath = parts.slice(0, i + 1).join('/');
        html += ' <span class="fp-sep">/</span> ';
        if (i === parts.length - 1) {
          html += '<span class="fp-current">' + core.escapeHtml(part) + '</span>';
        } else {
          html += '<a data-fp-nav="' + core.escapeAttr(partPath) + '">' + core.escapeHtml(part) + '</a>';
        }
      });
      this._breadcrumb.innerHTML = html;
      this._breadcrumb.querySelectorAll('a[data-fp-nav]').forEach((a) => {
        a.addEventListener('click', (e) => {
          e.preventDefault();
          this._navigate(a.dataset.fpNav);
        });
      });
    },

    _renderFolders(folders) {
      if (!this._body) return;
      if (folders.length === 0) {
        this._body.innerHTML = '<div class="fp-empty">No subfolders here</div>';
        return;
      }
      const ul = document.createElement('ul');
      ul.className = 'fp-folder-list';
      folders.forEach((f) => {
        const li = document.createElement('li');
        li.className = 'fp-folder-item';
        li.innerHTML = '<span class="fp-icon">📁</span>'
          + '<span class="fp-name">' + core.escapeHtml(f.name) + '</span>'
          + '<span class="fp-arrow">›</span>';
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
      const name = await core.showDialog('Enter folder name:', 'New folder', { prompt: true, showCancel: true });
      if (!name?.trim()) return;
      const formData = new URLSearchParams();
      formData.append('parent', this._currentPath);
      formData.append('name', name.trim());
      try {
        const res = await fetch('/mkdir', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-XSRFToken': core.getXSRFToken(),
          },
          body: formData.toString(),
        });
        if (res.ok) this._navigate(this._currentPath);
        else core.showDialog('Create folder failed: ' + (await res.text()), 'Error');
      } catch (e) {
        core.showDialog('Create folder failed: ' + e.message, 'Error');
      }
    },
  };

  global.AirdFolderPicker = FolderPicker;
}(globalThis));
