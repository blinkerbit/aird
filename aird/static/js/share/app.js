let currentPath = '';
    let selectedFiles = new Set();
    let allFiles = [];
    let selectedUsers = new Set();
    let selectedModifyUsers = new Set();

    const elements = {
      currentPath: document.getElementById('currentPath'),
      selectedCount: document.getElementById('selectedCount'),
      selectedFilesDiv: document.getElementById('selectedFiles'),
      generateLink: document.getElementById('generateLink'),
      clearSelection: document.getElementById('clearSelection'),
      selectAllVisible: document.getElementById('selectAllVisible'),
      shareResult: document.getElementById('shareResult'),
      fileTableBody: document.getElementById('fileTableBody'),
      sharesTableBody: document.getElementById('sharesTableBody'),
      activeSharesCount: document.getElementById('activeSharesCount'),
      sharedWithMeSection: document.getElementById('sharedWithMeSection'),
      sharedWithMeTableBody: document.getElementById('sharedWithMeTableBody'),
      sharedWithMeCount: document.getElementById('sharedWithMeCount')
    };

    const selectedFileMetadata = new Map();

    const cloudElements = {
      modal: document.getElementById('cloudBrowserModal'),
      providerSelect: document.getElementById('cloudProviderSelect'),
      pathDisplay: document.getElementById('cloudPathDisplay'),
      statusMessage: document.getElementById('cloudStatusMessage'),
      tableBody: document.getElementById('cloudFilesTableBody'),
      upButton: document.getElementById('cloudUpButton'),
      uploadInput: document.getElementById('cloudUploadInput'),
      uploadButton: document.getElementById('cloudUploadButton'),
      uploadStatus: document.getElementById('cloudUploadStatus')
    };

    const cloudState = {
      providers: [],
      currentProvider: null,
      currentFolder: null,
      currentFiles: [],
      pathStack: [],
      loading: false,
      uploading: false
    };

    let filePickerLoaded = false;

    function getXSRFToken() {
      return globalThis.AirdCore.getXSRFToken();
    }

    const showDialog = (...args) => globalThis.AirdCore.showDialog(...args);

    function getFileIcon(filename) {
      const ext = filename.toLowerCase().split('.').pop();
      const lowerFilename = filename.toLowerCase();

      // Special files by name - IDE-style
      if (['readme', 'readme.md', 'readme.txt'].includes(lowerFilename)) return '📖';
      if (['license', 'licence', 'copying'].includes(lowerFilename)) return '📜';
      if (['makefile', 'cmake', 'cmakelists.txt'].includes(lowerFilename)) return '🔨';
      if (['dockerfile', 'docker-compose.yml', 'docker-compose.yaml'].includes(lowerFilename)) return '🐳';
      if (['.gitignore', '.gitattributes', '.gitmodules'].includes(lowerFilename)) return '🔧';
      if (lowerFilename.startsWith('.env')) return '🔐';

      const iconMap = {
        // Document files - IDE-style
        'txt': '📄', 'md': '📄', 'rst': '📄', 'text': '📄',
        'doc': '📝', 'docx': '📝', 'rtf': '📝', 'odt': '📝',
        'pdf': '📕',
        'xls': '📊', 'xlsx': '📊', 'ods': '📊', 'csv': '📊',
        'ppt': '📋', 'pptx': '📋', 'odp': '📋',

        // Image files - IDE-style
        'jpg': '🖼️', 'jpeg': '🖼️', 'png': '🖼️', 'gif': '🖼️', 'bmp': '🖼️', 'webp': '🖼️', 'tiff': '🖼️', 'tif': '🖼️',
        'svg': '🎨', 'ico': '🎨',
        'psd': '🎭', 'ai': '🎭', 'sketch': '🎭',

        // Programming files - IDE-style (VS Code inspired)
        'py': '🐍', 'pyw': '🐍', 'pyc': '🐍', 'pyo': '🐍',
        'js': '🟨', 'jsx': '🟨', 'ts': '🟨', 'tsx': '🟨', 'mjs': '🟨',
        'java': '☕', 'class': '☕', 'jar': '☕',
        'cpp': '⚙️', 'cxx': '⚙️', 'cc': '⚙️', 'c': '⚙️', 'h': '⚙️', 'hpp': '⚙️',
        'cs': '🔷', 'vb': '🔷', 'fs': '🔷',
        'php': '🐘', 'phtml': '🐘',
        'rb': '💎', 'rake': '💎', 'gem': '💎',
        'go': '🐹',
        'rs': '🦀',
        'swift': '🦉',
        'kt': '🟣', 'kts': '🟣',
        'scala': '🔴',
        'r': '📊', 'rmd': '📊',
        'm': '🍎', 'mm': '🍎',
        'pl': '🐪', 'pm': '🐪',
        'sh': '📟', 'bash': '📟', 'zsh': '📟', 'fish': '📟', 'bat': '📟', 'cmd': '📟', 'ps1': '📟',
        'lua': '🌙',
        'dart': '🎯',

        // Web files - IDE-style
        'html': '🌐', 'htm': '🌐', 'xhtml': '🌐',
        'css': '🎨', 'scss': '🎨', 'sass': '🎨', 'less': '🎨',
        'xml': '📰', 'xsl': '📰', 'xsd': '📰',
        'json': '📋', 'jsonl': '📋',
        'yaml': '📄', 'yml': '📄',
        'toml': '⚙️', 'ini': '⚙️', 'cfg': '⚙️', 'conf': '⚙️',

        // Archive files - IDE-style
        'zip': '🗜️', 'rar': '🗜️', '7z': '🗜️', 'tar': '🗜️', 'gz': '🗜️', 'bz2': '🗜️', 'xz': '🗜️', 'lz': '🗜️', 'lzma': '🗜️',
        'deb': '📦', 'rpm': '📦', 'pkg': '📦', 'dmg': '📦', 'msi': '📦', 'exe': '📦',

        // Video files - IDE-style
        'mp4': '🎬', 'avi': '🎬', 'mkv': '🎬', 'mov': '🎬', 'wmv': '🎬', 'flv': '🎬', 'webm': '🎬', 'm4v': '🎬', '3gp': '🎬', 'ogv': '🎬', 'mpg': '🎬', 'mpeg': '🎬',

        // Audio files - IDE-style
        'mp3': '🎵', 'wav': '🎵', 'flac': '🎵', 'aac': '🎵', 'ogg': '🎵', 'm4a': '🎵', 'wma': '🎵', 'opus': '🎵', 'aiff': '🎵',

        // Font files - IDE-style
        'ttf': '🔤', 'otf': '🔤', 'woff': '🔤', 'woff2': '🔤', 'eot': '🔤',

        // Database files - IDE-style
        'db': '🗃️', 'sqlite': '🗃️', 'sqlite3': '🗃️', 'mdb': '🗃️', 'accdb': '🗃️',

        // Log files - IDE-style
        'log': '📜', 'out': '📜', 'err': '📜',

        // Data files - IDE-style
        'sql': '🗄️',
        'parquet': '📊', 'avro': '📊', 'orc': '📊',

        // Notebook files - IDE-style
        'ipynb': '📓'
      };

      return iconMap[ext] || '📦';
    }

    function formatFileSize(bytes) {
      return globalThis.AirdCore.formatBytes(bytes);
    }

    function escapeHtml(text) {
      return globalThis.AirdCore.escapeHtml(text);
    }

    function escapeAttr(text) {
      return globalThis.AirdCore.escapeAttr(text);
    }

    function findCheckboxByValue(scopeRoot, value) {
      if (!scopeRoot) return null;
      for (const el of scopeRoot.querySelectorAll('input[type="checkbox"]')) {
        if (el.value === value) return el;
      }
      return null;
    }

    function cloudSelectionKey(providerName, fileId) {
      return `cloud:${providerName}:${fileId}`;
    }

    function isCloudFileSelected(providerName, fileId) {
      return selectedFiles.has(cloudSelectionKey(providerName, fileId));
    }

    function setCloudStatus(message, isError = false) {
      if (!cloudElements.statusMessage) return;
      cloudElements.statusMessage.textContent = message || '';
      cloudElements.statusMessage.classList.toggle('error', Boolean(isError) && Boolean(message));
    }

    function clearCloudStatus() {
      setCloudStatus('');
    }

    function setCloudUploadStatus(message, isError = false) {
      if (!cloudElements.uploadStatus) return;
      cloudElements.uploadStatus.textContent = message || '';
      cloudElements.uploadStatus.classList.toggle('error', Boolean(isError) && Boolean(message));
    }

    function clearCloudUploadStatus() {
      setCloudUploadStatus('');
    }

    function updateCloudPathDisplay() {
      if (!cloudElements.pathDisplay) return;
      if (!cloudState.currentProvider) {
        cloudElements.pathDisplay.textContent = '';
        return;
      }

      const providerLabel = cloudState.currentProvider.label || cloudState.currentProvider.name;
      if (!cloudState.pathStack.length) {
        cloudElements.pathDisplay.textContent = providerLabel;
        return;
      }

      const parts = cloudState.pathStack.map(entry => entry.name).filter(Boolean);
      cloudElements.pathDisplay.textContent = `${providerLabel} / ${parts.join(' / ')}`;
    }

    function updateCloudNavigationState() {
      if (cloudElements.upButton) {
        const disableUp = cloudState.loading || cloudState.uploading || cloudState.pathStack.length === 0;
        cloudElements.upButton.disabled = disableUp;
      }
      if (cloudElements.uploadButton) {
        const disableUpload = cloudState.loading || cloudState.uploading || !cloudState.currentProvider;
        cloudElements.uploadButton.disabled = disableUpload;
      }
      if (cloudElements.providerSelect) {
        cloudElements.providerSelect.disabled = cloudState.uploading;
      }
    }

    function renderCloudProviders(providers) {
      if (!cloudElements.providerSelect) return;

      cloudElements.providerSelect.innerHTML = '';

      if (!providers || providers.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = 'No providers available';
        option.disabled = true;
        option.selected = true;
        cloudElements.providerSelect.appendChild(option);
        setCloudStatus('No cloud providers configured.', true);
        return;
      }

      providers.forEach((provider, index) => {
        const option = document.createElement('option');
        option.value = provider.name;
        option.textContent = provider.label || provider.name;
        if (index === 0) {
          option.selected = true;
        }
        cloudElements.providerSelect.appendChild(option);
      });

      clearCloudStatus();
    }

    async function loadCloudProviders(forceReload = false) {
      if (!cloudElements.providerSelect) return;

      clearCloudUploadStatus();

      if (!forceReload && cloudState.providers.length) {
        renderCloudProviders(cloudState.providers);
        if (!cloudState.currentProvider && cloudState.providers.length) {
          switchCloudProvider(cloudState.providers[0].name);
        }
        return;
      }

      try {
        setCloudStatus('Loading providers...');
        const response = await fetch('/api/cloud/providers');
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        cloudState.providers = Array.isArray(payload.providers) ? payload.providers : [];
        renderCloudProviders(cloudState.providers);

        if (cloudState.providers.length) {
          switchCloudProvider(cloudState.providers[0].name);
        } else {
          cloudState.currentProvider = null;
          cloudState.currentFiles = [];
          cloudState.pathStack = [];
          if (cloudElements.tableBody) {
            cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-base-content/50 italic text-sm">Connect a cloud provider to browse files.</td></tr>';
          }
        }
      } catch (error) {
        console.error('Failed to load cloud providers:', error);
        setCloudStatus('Unable to load cloud providers.', true);
        if (cloudElements.tableBody) {
          cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-error/70 text-sm">Error loading providers</td></tr>';
        }
      }
    }

    function switchCloudProvider(providerName) {
      if (!providerName) return;
      const provider = cloudState.providers.find(p => p.name === providerName);
      if (!provider) {
        setCloudStatus('Selected provider is not configured.', true);
        return;
      }

      cloudState.currentProvider = provider;
      cloudState.currentFolder = provider.root || 'root';
      cloudState.currentFiles = [];
      cloudState.pathStack = [];

      if (cloudElements.uploadInput) {
        cloudElements.uploadInput.value = '';
      }
      clearCloudUploadStatus();

      if (cloudElements.providerSelect && cloudElements.providerSelect.value !== provider.name) {
        cloudElements.providerSelect.value = provider.name;
      }

      updateCloudPathDisplay();
      updateCloudNavigationState();
      loadCloudFolder(cloudState.currentFolder, { reset: true });
    }

    function openCloudBrowser() {
      const shareTypeRadio = document.querySelector('input[name="shareType"]:checked');
      if (shareTypeRadio?.value === 'dynamic') {
        showDialog('Cloud files are not supported when creating a dynamic share.', 'Cloud Files');
        return;
      }

      clearCloudUploadStatus();
      if (cloudElements.uploadInput) {
        cloudElements.uploadInput.value = '';
        cloudElements.uploadInput.disabled = false;
      }
      cloudState.uploading = false;
      updateCloudNavigationState();

      cloudElements.modal?.showModal();

      if (cloudElements.tableBody) {
        cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading…</span></td></tr>';
      }

      loadCloudProviders();
    }

    function closeCloudBrowser() {
      cloudElements.modal?.close();
      clearCloudStatus();
      clearCloudUploadStatus();
    }

    async function loadCloudFolder(folderId, options = {}) {
      if (!cloudState.currentProvider) return;

      const provider = cloudState.currentProvider;
      const targetFolder = folderId || provider.root || 'root';

      if (cloudElements.tableBody) {
        cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading…</span></td></tr>';
      }
      setCloudStatus('Loading files...');
      cloudState.loading = true;
      updateCloudNavigationState();

      const previousFolder = cloudState.currentFolder;
      const previousStack = cloudState.pathStack.slice();

      try {
        const params = new URLSearchParams();
        if (targetFolder) {
          params.set('folder', targetFolder);
        }
        const query = params.toString();
        const url = query ? `/api/cloud/${provider.name}/files?${query}` : `/api/cloud/${provider.name}/files`;
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const payload = await response.json();
        const files = Array.isArray(payload.files) ? payload.files : [];

        if (options.reset) {
          cloudState.pathStack = [];
        } else if (options.pop) {
          cloudState.pathStack = cloudState.pathStack.slice(0, -1);
        } else if (options.pushEntry) {
          cloudState.pathStack = cloudState.pathStack.concat(options.pushEntry);
        }

        cloudState.currentFolder = targetFolder;
        cloudState.currentFiles = files;

        renderCloudFiles(files);
        clearCloudStatus();
      } catch (error) {
        console.error('Failed to load cloud files:', error);
        cloudState.currentFolder = previousFolder;
        cloudState.pathStack = previousStack;
        setCloudStatus('Failed to load cloud files.', true);
        if (cloudElements.tableBody) {
          cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-error/70 text-sm">Error loading files</td></tr>';
        }
      } finally {
        cloudState.loading = false;
        updateCloudPathDisplay();
        updateCloudNavigationState();
      }
    }

    function cloudNavigateUp() {
      if (!cloudState.currentProvider || cloudState.pathStack.length === 0 || cloudState.loading) {
        return;
      }

      const parentEntry = cloudState.pathStack.length > 1 ? cloudState.pathStack.at(-2) : null;
      const parentFolder = parentEntry ? parentEntry.id : (cloudState.currentProvider.root || 'root');
      loadCloudFolder(parentFolder, { pop: true });
    }

    function handleCloudFolderNavigation(file) {
      if (!file?.is_dir) return;
      const entry = { id: file.id, name: file.name || 'Folder' };
      loadCloudFolder(file.id, { pushEntry: entry });
    }

    function renderCloudFiles(files) {
      if (!cloudElements.tableBody) return;

      cloudElements.tableBody.innerHTML = '';

      if (!files || files.length === 0) {
        cloudElements.tableBody.innerHTML = '<tr><td colspan="4" class="p-6 text-center text-base-content/50 italic text-sm">No items found in this folder.</td></tr>';
        return;
      }

      files.forEach(file => {
        const row = document.createElement('tr');

        const nameCell = document.createElement('td');
        nameCell.className = 'name-col';

        const iconSpan = document.createElement('span');
        iconSpan.className = 'file-icon';
        iconSpan.textContent = file.is_dir ? '📁' : '📄';

        const nameText = document.createTextNode(file.name || 'Unnamed');

        if (file.is_dir) {
          const link = document.createElement('a');
          link.href = '#';
          link.className = 'file-link';
          link.appendChild(iconSpan);
          link.appendChild(nameText);
          link.addEventListener('click', event => {
            event.preventDefault();
            handleCloudFolderNavigation(file);
          });
          nameCell.appendChild(link);
        } else {
          nameCell.appendChild(iconSpan);
          nameCell.appendChild(nameText);
        }

        const sizeCell = document.createElement('td');
        sizeCell.className = 'size-col';
        if (file.is_dir || typeof file.size !== 'number') {
          sizeCell.textContent = '-';
        } else {
          sizeCell.textContent = formatFileSize(file.size);
        }

        const modifiedCell = document.createElement('td');
        modifiedCell.className = 'modified-col';
        modifiedCell.textContent = file.modified ? file.modified : '-';

        const actionCell = document.createElement('td');
        actionCell.className = 'actions-col';

        if (file.is_dir) {
          const openBtn = document.createElement('button');
          openBtn.className = 'btn';
          openBtn.type = 'button';
          openBtn.textContent = 'Open';
          openBtn.addEventListener('click', () => handleCloudFolderNavigation(file));
          actionCell.appendChild(openBtn);
        } else {
          const key = cloudSelectionKey(cloudState.currentProvider.name, file.id);
          const button = document.createElement('button');
          button.className = isCloudFileSelected(cloudState.currentProvider.name, file.id) ? 'btn' : 'btn primary';
          button.type = 'button';
          button.textContent = isCloudFileSelected(cloudState.currentProvider.name, file.id) ? 'Remove' : 'Select';
          button.addEventListener('click', () => {
            if (isCloudFileSelected(cloudState.currentProvider.name, file.id)) {
              removeCloudSelection(key);
            } else {
              selectCloudFile(file);
            }
          });
          actionCell.appendChild(button);

          if (isCloudFileSelected(cloudState.currentProvider.name, file.id)) {
            row.classList.add('selected');
          }
        }

        row.appendChild(nameCell);
        row.appendChild(sizeCell);
        row.appendChild(modifiedCell);
        row.appendChild(actionCell);

        cloudElements.tableBody.appendChild(row);
      });
    }

    function buildCloudFormData(file) {
      const formData = new FormData();
      formData.append('file', file);
      const targetFolder = cloudState.currentFolder || cloudState.currentProvider.root;
      if (targetFolder) {
        formData.append('parent_id', targetFolder);
      }
      return formData;
    }

    async function handleCloudUpload() {
      if (!cloudState.currentProvider) {
        setCloudUploadStatus('Select a cloud provider before uploading.', true);
        return;
      }

      const uploadInput = cloudElements.uploadInput;
      if (!uploadInput?.files?.length) {
        setCloudUploadStatus('Choose a file to upload.', true);
        return;
      }

      const file = uploadInput.files[0];
      const formData = buildCloudFormData(file);

      cloudState.uploading = true;
      setCloudUploadStatus(`Uploading ${file.name || 'file'}...`);
      updateCloudNavigationState();
      uploadInput.disabled = true;

      try {
        const xsrfHeaders = {};
        const xsrf = getXSRFToken();
        if (xsrf) {
          xsrfHeaders['X-XSRFToken'] = xsrf;
        }
        const response = await fetch(`/api/cloud/${cloudState.currentProvider.name}/upload`, {
          method: 'POST',
          headers: xsrfHeaders,
          body: formData
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          throw new Error(payload.error || `Upload failed (HTTP ${response.status})`);
        }

        const uploadedName = payload.file?.name ?? (file.name || 'file');
        setCloudUploadStatus(`Uploaded ${uploadedName}.`);
        uploadInput.value = '';

        await loadCloudFolder(cloudState.currentFolder, {});
        setCloudStatus(`Uploaded ${uploadedName} to ${cloudState.currentProvider?.label ?? cloudState.currentProvider.name}.`);
      } catch (error) {
        console.error('Cloud upload failed:', error);
        setCloudUploadStatus(error?.message ?? 'Upload failed.', true);
      } finally {
        cloudState.uploading = false;
        uploadInput.disabled = false;
        updateCloudNavigationState();
      }
    }

    function selectCloudFile(file) {
      if (!cloudState.currentProvider || !file || file.is_dir) return;

      const key = cloudSelectionKey(cloudState.currentProvider.name, file.id);
      const metadata = {
        type: 'cloud',
        provider: cloudState.currentProvider.name,
        id: file.id,
        name: file.name,
        is_dir: !!file.is_dir
      };

      addToSelection(key, metadata);
        setCloudStatus(`Added ${file.name || 'Unnamed file'} from ${cloudState.currentProvider?.label ?? cloudState.currentProvider.name}.`);
      renderCloudFiles(cloudState.currentFiles);
    }

    function removeCloudSelection(key) {
      removeFromSelection(key);
      renderCloudFiles(cloudState.currentFiles);
    }

    const openCloudButton = document.getElementById('openCloudBrowser');
    if (openCloudButton) {
      openCloudButton.addEventListener('click', openCloudBrowser);
    }

    if (cloudElements.providerSelect) {
      cloudElements.providerSelect.addEventListener('change', event => {
        switchCloudProvider(event.target.value);
      });
    }

    if (cloudElements.upButton) {
      cloudElements.upButton.addEventListener('click', cloudNavigateUp);
    }

    if (cloudElements.uploadButton) {
      cloudElements.uploadButton.addEventListener('click', handleCloudUpload);
    }

    if (cloudElements.uploadInput) {
      cloudElements.uploadInput.addEventListener('change', () => {
        clearCloudUploadStatus();
      });
    }

    if (cloudElements.modal) {
      cloudElements.modal.addEventListener('click', event => {
        if (event.target === cloudElements.modal) {
          closeCloudBrowser();
        }
      });
    }

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape' && cloudElements.modal?.open) {
        closeCloudBrowser();
      }
    });

    function buildFileChips(files) {
      return Array.from(files).map(file => {
        const meta = selectedFileMetadata.get(file);
        let icon;
        let label = '';
        if (meta?.type === 'cloud') {
          const providerLabel = meta.provider?.toUpperCase() ?? 'CLOUD';
          icon = '☁️';
          label = `${providerLabel} · ${meta.name || 'Unnamed'}`;
        } else {
          const fileName = file.split('/').pop();
          const isDir = file.endsWith('/') || allFiles.some(f => f.name === fileName && f.is_dir);
          icon = isDir ? '📁' : '📄';
          label = fileName;
        }
        return { icon, label, file };
      });
    }

    function updateSelectedDisplay() {
      elements.selectedCount.textContent = selectedFiles.size;
      elements.generateLink.disabled = selectedFiles.size === 0;

      const selectionBar = document.getElementById('selectionBar');
      if (selectedFiles.size === 0) {
        elements.selectedFilesDiv.innerHTML = '';
        selectionBar.classList.remove('visible');
        document.body.classList.remove('has-selection');
      } else {
        elements.selectedFilesDiv.innerHTML = '';
        const chips = buildFileChips(selectedFiles);
        for (const c of chips) {
          const chip = document.createElement('span');
          chip.className = 'selected-file';
          chip.dataset.action = 'removeFromSelection';
          chip.dataset.path = c.file;
          chip.textContent = `${c.icon} ${c.label} ×`;
          elements.selectedFilesDiv.appendChild(chip);
        }
        selectionBar.classList.add('visible');
        document.body.classList.add('has-selection');
      }

      document.querySelectorAll('#fileTableBody tr').forEach(row => {
        const checkbox = row.querySelector('input[type="checkbox"]');
        if (checkbox) {
          row.classList.toggle('selected', checkbox.checked);
        }
      });
    }

    function updateConfigSelectedFiles() {
      const container = document.getElementById('configSelectedFiles');
      if (!container) return;
      if (selectedFiles.size === 0) {
        container.innerHTML = '<em style="font-size:12px; color:var(--ds-text-subtle);">No files selected</em>';
        return;
      }
      const chips = buildFileChips(selectedFiles);
      container.innerHTML = chips
        .map(c => `<span class="config-file-chip">${c.icon} ${escapeHtml(c.label)}</span>`)
        .join('');
    }

    function addToSelection(filePath, metadata = null) {
      selectedFiles.add(filePath);
      if (metadata) {
        selectedFileMetadata.set(filePath, metadata);
      }
      updateSelectedDisplay();
    }

    function removeFromSelection(filePath) {
      selectedFiles.delete(filePath);
      if (selectedFileMetadata.has(filePath)) {
        selectedFileMetadata.delete(filePath);
      }
      const checkbox = findCheckboxByValue(document.getElementById('fileTableBody'), filePath);
      if (checkbox) checkbox.checked = false;
      updateSelectedDisplay();
    }

    function clearSelection() {
      selectedFiles.clear();
      selectedFileMetadata.clear();
      selectedModifyUsers.clear();
      document.querySelectorAll('#fileTableBody input[type="checkbox"]').forEach(cb => cb.checked = false);
      updateSelectedDisplay();
      updateSelectedModifyUsersDisplay();
    }
    function selectAllVisible() {
      const visibleFiles = document.querySelectorAll('#fileTableBody input[type="checkbox"]');
      visibleFiles.forEach(checkbox => {
        checkbox.checked = true;
        selectedFiles.add(checkbox.value);
      });
      updateSelectedDisplay();
    }

    function updateBreadcrumb(path) {
      const homeIcon = '<svg class="ico w-4 h-4 inline-block align-middle" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 11l9-8 9 8"/><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10"/></svg>';
      if (!path) {
        elements.currentPath.innerHTML = `<span class="inline-flex items-center gap-1">${homeIcon}<span>Home</span></span>`;
        return;
      }
      const parts = path.split('/').filter(Boolean);
      const crumbs = [`<a href="#" data-action="loadDirectory" data-path="" class="inline-flex items-center gap-1 hover:text-primary">${homeIcon}<span>Home</span></a>`];
      parts.forEach((part, i) => {
        const partPath = parts.slice(0, i + 1).join('/');
        if (i === parts.length - 1) {
          crumbs.push(`<span class="opacity-30" aria-hidden="true">/</span><strong>${escapeHtml(part)}</strong>`);
        } else {
          crumbs.push(`<span class="opacity-30" aria-hidden="true">/</span><a href="#" data-action="loadDirectory" data-path="${escapeAttr(partPath)}" class="hover:text-primary">${escapeHtml(part)}</a>`);
        }
      });
      elements.currentPath.innerHTML = crumbs.join(' ');
    }

    function openShareFilePicker() {
      const section = document.getElementById('shareFilePickerSection');
      const startBtn = document.getElementById('startCreateShareBtn');
      if (!section) return;
      section.classList.remove('share-file-picker-hidden');
      section.setAttribute('aria-hidden', 'false');
      if (startBtn) startBtn.setAttribute('aria-expanded', 'true');
      if (!filePickerLoaded) {
        filePickerLoaded = true;
        loadDirectory();
      }
      section.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function closeShareFilePicker() {
      const section = document.getElementById('shareFilePickerSection');
      const startBtn = document.getElementById('startCreateShareBtn');
      if (!section) return;
      section.classList.add('share-file-picker-hidden');
      section.setAttribute('aria-hidden', 'true');
      if (startBtn) startBtn.setAttribute('aria-expanded', 'false');
      clearSelection();
    }

    async function loadDirectory(path = '') {
      try {
        currentPath = path;
        updateBreadcrumb(path);
        elements.fileTableBody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading…</span></td></tr>';

        const response = await fetch(`/api/files/${path}`);

        if (response.ok) {
          const data = await response.json();
          allFiles = data.files;
          renderFiles();
          updateSelectedDisplay();
        } else {
          const errorText = await response.text();
          console.error('API error:', errorText);
          throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

      } catch (error) {
        console.error('Error loading directory:', error);
        elements.fileTableBody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-error/70 text-sm">Error loading files</td></tr>';
      }
    }

    function _makeCell(content, className) {
      const td = document.createElement('td');
      if (className) td.className = className;
      if (content instanceof Node) td.appendChild(content);
      else if (content != null) td.textContent = content;
      return td;
    }

    function _makeCheckboxCell(filePath, isSelected) {
      const td = document.createElement('td');
      td.className = 'select-col px-4 py-3';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = filePath;
      cb.checked = !!isSelected;
      cb.dataset.action = 'toggleSelection';
      cb.dataset.path = filePath;
      td.appendChild(cb);
      return td;
    }

    function _makeNameCell({ isDir, iconText, name, filePath, isShared, navPath }) {
      const td = document.createElement('td');
      const wrap = document.createElement('div');
      wrap.className = 'name-cell-contents';

      const link = document.createElement(isDir ? 'a' : 'span');
      if (isDir) link.href = '#';
      link.className = isDir ? 'file-link' : 'file-link file-link--preview';
      link.dataset.action = isDir ? 'loadDirectory' : 'previewFile';
      link.dataset.path = isDir ? navPath : filePath;
      const icon = document.createElement('span');
      icon.className = 'file-icon';
      icon.textContent = iconText;
      link.appendChild(icon);
      link.appendChild(document.createTextNode(name));

      if (isShared) {
        const share = document.createElement('span');
        share.className = 'shared-icon';
        share.title = 'Click to view share details';
        share.dataset.action = 'showShareDetails';
        share.dataset.path = filePath;
        share.textContent = '🔗';
        link.appendChild(share);
      }

      wrap.appendChild(link);

      td.appendChild(wrap);
      return td;
    }

    function _makeActionButton(label, action, path) {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.dataset.action = action;
      btn.dataset.path = path;
      btn.textContent = label;
      return btn;
    }

    function renderEmptyDirectoryRow() {
      elements.fileTableBody.innerHTML = '';
      if (!currentPath) {
        const row = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.className = 'p-6 text-center text-base-content/50 italic text-sm';
        td.textContent = 'No files in this directory';
        row.appendChild(td);
        elements.fileTableBody.appendChild(row);
        return;
      }
      const row = document.createElement('tr');
      row.appendChild(_makeCheckboxCell(currentPath, selectedFiles.has(currentPath)));
      row.appendChild(_makeNameCell({
        isDir: true,
        iconText: '📁',
        name: '(this folder)',
        filePath: currentPath,
        isShared: false,
        navPath: currentPath,
      }));
      row.appendChild(_makeCell('-'));
      row.appendChild(_makeCell('-'));
      const hintTd = document.createElement('td');
      hintTd.className = 'text-base-content/50 text-xs';
      hintTd.textContent = 'Empty — select to share this folder';
      row.appendChild(hintTd);
      if (selectedFiles.has(currentPath)) row.classList.add('selected');
      elements.fileTableBody.appendChild(row);
    }

    function renderFiles() {
      if (allFiles.length === 0) {
        renderEmptyDirectoryRow();
        return;
      }

      elements.fileTableBody.innerHTML = '';

      if (currentPath) {
        const parentPath = currentPath.split('/').filter(Boolean).slice(0, -1).join('/');
        const row = document.createElement('tr');
        row.appendChild(_makeCell(''));
        const nameTd = document.createElement('td');
        const parentLink = document.createElement('a');
        parentLink.href = '#';
        parentLink.className = 'file-link';
        parentLink.dataset.action = 'loadDirectory';
        parentLink.dataset.path = parentPath;
        const icon = document.createElement('span');
        icon.className = 'file-icon';
        icon.textContent = '📁';
        parentLink.appendChild(icon);
        parentLink.appendChild(document.createTextNode('..'));
        nameTd.appendChild(parentLink);
        row.appendChild(nameTd);
        row.appendChild(_makeCell('-'));
        row.appendChild(_makeCell('-'));
        row.appendChild(_makeCell('-'));
        elements.fileTableBody.appendChild(row);
      }

      allFiles.forEach(file => {
        const row = document.createElement('tr');
        const filePath = currentPath ? `${currentPath}/${file.name}` : file.name;
        const isSelected = selectedFiles.has(filePath);

        row.appendChild(_makeCheckboxCell(filePath, isSelected));
        row.appendChild(_makeNameCell({
          isDir: !!file.is_dir,
          iconText: file.is_dir ? '📁' : getFileIcon(file.name),
          name: file.name,
          filePath,
          isShared: !!file.is_shared,
          navPath: filePath,
        }));
        row.appendChild(_makeCell(file.is_dir ? '-' : (file.size_str || '-')));
        row.appendChild(_makeCell(file.modified || '-'));

        const actionTd = document.createElement('td');
        actionTd.appendChild(file.is_dir
          ? _makeActionButton('Open', 'loadDirectory', filePath)
          : _makeActionButton('View', 'previewFile', filePath));
        row.appendChild(actionTd);

        if (isSelected) row.classList.add('selected');
        elements.fileTableBody.appendChild(row);
      });
    }

    // Share management functions
    let currentShareData = null;

    async function manageShare(shareId) {
      const modal = document.getElementById('shareManagementModal');
      const body = document.getElementById('shareManagementBody');

      modal.showModal();
      body.innerHTML = '<div class="flex flex-col items-center py-10"><span class="loading loading-spinner loading-lg text-primary"></span><div class="mt-4 text-base-content/60">Loading share details...</div></div>';

      try {
        const detailsResponse = await fetch(`/api/share/details_by_id?id=${encodeURIComponent(shareId)}`, {
          method: 'GET',
          headers: {
            'Accept': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          cache: 'no-store'
        });

        if (!detailsResponse.ok) {
          const errorText = await detailsResponse.text();
          let errorMessage = `Server error: ${detailsResponse.status}`;
          try {
            const errorJson = JSON.parse(errorText);
            errorMessage = errorJson.error || errorMessage;
          } catch (parseErr) {
            console.debug('Share details error body was not JSON', parseErr);
          }
          throw new Error(errorMessage);
        }

        const detailsData = await detailsResponse.json();
        currentShareData = detailsData.share;
        renderShareManagementModal(currentShareData);

      } catch (error) {
        console.error('Error loading share details:', error);
        body.innerHTML = `
          <div class="alert alert-error shadow-lg">
            <svg xmlns="http://www.w3.org/2000/svg" class="stroke-current shrink-0 h-6 w-6" fill="none" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            <span>Failed to load share details: ${escapeHtml(error.message)}</span>
          </div>
        `;
      }
    }

    function buildShareUsersHtml(allowedUsers) {
      if (!allowedUsers || allowedUsers.length === 0) {
        return '<div class="share-empty-msg">No users specified (public access)</div>';
      }
      return allowedUsers.map(user => `
                  <span class="access-user-tag">
                    ${escapeHtml(user)}
                    <button class="access-user-remove" data-action="removeUserFromShare" data-user="${escapeAttr(user)}">&times;</button>
                  </span>`).join('');
    }

    function buildModifyUsersHtml(modifyUsers) {
      if (!modifyUsers || modifyUsers.length === 0) {
        return '<div class="share-empty-msg">No modify users (read-only share)</div>';
      }
      return modifyUsers.map(user => `
                  <span class="modify-user-tag">
                    ✏️ ${escapeHtml(user)}
                    <button class="modify-user-remove" data-action="removeModifyUserFromShare" data-user="${escapeAttr(user)}">&times;</button>
                  </span>`).join('');
    }

    function toLocalDatetimeInput(isoStr) {
      if (!isoStr) return '';
      const normalized = isoStr.endsWith('Z') ? isoStr : isoStr + 'Z';
      const d = new Date(normalized);
      if (Number.isNaN(d.getTime())) return '';
      const pad = n => String(n).padStart(2, '0');
      return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()) + 'T' + pad(d.getHours()) + ':' + pad(d.getMinutes());
    }

    function setupTokenEditCheckboxes() {
      const disableTokenCheckbox = document.getElementById('disableTokenEdit');
      const enableTokenCheckbox = document.getElementById('enableTokenEdit');
      if (!disableTokenCheckbox || !enableTokenCheckbox) return;
      disableTokenCheckbox.addEventListener('change', function () {
        if (this.checked) enableTokenCheckbox.checked = false;
      });
      enableTokenCheckbox.addEventListener('change', function () {
        if (this.checked) disableTokenCheckbox.checked = false;
      });
    }

    function _buildPathsSection(share, isTag) {
      if (isTag) {
        return {
          title: 'Tag-based listing',
          inner: '<div class="text-xs p-3 bg-base-200 rounded-lg">Files are defined by the resource tag <code class="font-mono">'
            + escapeHtml(share.tag_name || '') + '</code>. Path lists are not used; update tag rules in Admin instead.</div>',
          addBtn: '',
        };
      }
      const pathCount = (share.paths || []).length;
      const inner = pathCount > 0
        ? (share.paths || []).map(p =>
            '<div class="flex items-center justify-between p-1.5 bg-base-200 rounded text-xs group">'
            + '<span class="font-mono truncate flex-grow">' + escapeHtml(p) + '</span>'
            + '<button class="btn btn-ghost btn-xs text-error opacity-0 group-hover:opacity-100" data-action="removeFileFromShare" data-path="' + escapeAttr(p) + '">✕</button>'
            + '</div>'
          ).join('')
        : '<div class="text-center py-4 opacity-40 text-xs italic">No files in this share</div>';
      return {
        title: 'Shared Files (' + pathCount + ')',
        inner,
        addBtn: '<button class="btn btn-sm btn-outline w-full mt-3" data-action="showAddFilesModalInManagement">+ Add More Files</button>',
      };
    }

    function _buildTokenDisplayHtml(share, hasSecret) {
      if (share.secret_token) {
        return '<div class="bg-base-200 p-3 rounded-lg flex items-center gap-2">'
          + '<code class="text-xs font-mono flex-grow truncate">' + escapeHtml(share.secret_token) + '</code>'
          + '<button class="btn btn-xs btn-ghost" data-action="copyToClipboard" data-text="' + escapeAttr(share.secret_token) + '">Copy</button>'
          + '</div>';
      }
      if (hasSecret) {
        return '<p class="text-xs text-base-content/70">A secret token is enabled.</p>';
      }
      return '';
    }

    function _buildShareTypeEditBlock(share, isTag, isStatic) {
      if (isTag) {
        return '<div class="form-control"><p class="text-sm">Type: <strong>tag</strong> — membership follows Admin tag / glob rules for <code class="text-xs font-mono">'
          + escapeHtml(share.tag_name || '')
          + '</code>.</p></div>';
      }
      const staticChecked = isStatic ? ' checked' : '';
      const dynamicChecked = isStatic ? '' : ' checked';
      return '<div class="form-control">'
        + '<label class="label pb-1"><span class="label-text font-bold text-sm">Share Type</span></label>'
        + '<div class="flex gap-3">'
        + '<label class="label cursor-pointer justify-start gap-2 bg-base-200 px-3 py-2 rounded-lg flex-1">'
        + '<input type="radio" name="shareTypeEdit" value="static" class="radio radio-primary radio-sm"' + staticChecked + '>'
        + '<span class="label-text font-semibold text-sm">Static</span>'
        + '</label>'
        + '<label class="label cursor-pointer justify-start gap-2 bg-base-200 px-3 py-2 rounded-lg flex-1">'
        + '<input type="radio" name="shareTypeEdit" value="dynamic" class="radio radio-primary radio-sm"' + dynamicChecked + '>'
        + '<span class="label-text font-semibold text-sm">Dynamic</span>'
        + '</label>'
        + '</div></div>';
    }

    function renderShareManagementModal(share) {
      const body = document.getElementById('shareManagementBody');
      const editorOnly = Boolean(share.can_edit_paths && !share.is_owner);
      const isTag = (share.share_type || 'static') === 'tag';
      const isStatic = !isTag && (share.share_type || 'static') === 'static';
      const expiryValue = toLocalDatetimeInput(share.expiry_date);
      const hasSecret = Boolean(share.secret_token || share.has_token);
      const disableTokenInitially = !hasSecret;
      const enableTokenInitially = hasSecret;
      const tokenHtml = _buildTokenDisplayHtml(share, hasSecret);
      const pathsSection = _buildPathsSection(share, isTag);
      const shareTypeBlock = _buildShareTypeEditBlock(share, isTag, isStatic);

      body.innerHTML = `
        <div class="space-y-4 pb-4">
          <div class="card bg-base-200 shadow-inner">
            <div class="card-body p-4">
              <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div class="form-control">
                  <label class="label pt-0"><span class="label-text font-bold text-xs uppercase opacity-60">Share ID</span></label>
                  <div class="flex items-center gap-2">
                    <code class="bg-base-300 px-2 py-1 rounded text-primary font-mono text-xs flex-grow overflow-hidden text-ellipsis">${escapeHtml(share.id)}</code>
                    <button class="btn btn-ghost btn-xs btn-square" data-action="copyToClipboard" data-text="${escapeAttr(share.id)}" title="Copy ID">⎘</button>
                  </div>
                </div>
                <div class="form-control">
                  <label class="label pt-0"><span class="label-text font-bold text-xs uppercase opacity-60">Link</span></label>
                  <div class="flex items-center gap-2">
                    <a href="${escapeAttr(share.url)}" target="_blank" class="link link-primary text-xs truncate flex-grow">${escapeHtml(globalThis.location.origin + share.url)}</a>
                    <button class="btn btn-ghost btn-xs btn-square" data-action="copyToClipboard" data-text="${escapeAttr(globalThis.location.origin + share.url)}" title="Copy">⎘</button>
                  </div>
                </div>
              </div>
              <div class="flex flex-wrap gap-3 mt-2 text-xs">
                <span><span class="opacity-60">Downloads:</span> <span class="badge badge-ghost badge-sm">${share.download_count || 0}</span></span>
                <span><span class="opacity-60">Access:</span> <span class="badge ${share.allowed_users && share.allowed_users.length > 0 ? 'badge-warning' : 'badge-success'} badge-sm">${share.allowed_users && share.allowed_users.length > 0 ? 'Restricted' : 'Public'}</span></span>
                <span><span class="opacity-60">Type:</span> <span class="badge badge-info badge-sm capitalize">${escapeHtml(share.share_type || 'static')}</span></span>
              </div>
            </div>
          </div>

          <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>
            <input type="radio" name="mgmt-accordion" checked="checked" />
            <div class="collapse-title font-semibold text-sm">General Settings</div>
            <div class="collapse-content space-y-4">
              ${shareTypeBlock}
              <div class="form-control">
                <label class="label pb-1" for="expiryDateEdit"><span class="label-text font-bold text-sm">Expiration Date</span></label>
                <input type="datetime-local" id="expiryDateEdit" class="input input-bordered input-sm w-full" value="${escapeAttr(expiryValue)}">
              </div>
              <div class="form-control">
                <label class="label pb-1"><span class="label-text font-bold text-sm">Filter Rules</span></label>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label class="label-text text-xs opacity-60 mb-1 block">Allow List</label>
                    <textarea id="allowListEdit" class="textarea textarea-bordered textarea-sm w-full h-16" placeholder="*.txt, *.pdf">${escapeHtml((share.allow_list || []).join(', '))}</textarea>
                  </div>
                  <div>
                    <label class="label-text text-xs opacity-60 mb-1 block">Avoid List</label>
                    <textarea id="avoidListEdit" class="textarea textarea-bordered textarea-sm w-full h-16" placeholder="*.tmp, .git/**">${escapeHtml((share.avoid_list || []).join(', '))}</textarea>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>
            <input type="radio" name="mgmt-accordion" />
            <div class="collapse-title font-semibold text-sm">Security & Token</div>
            <div class="collapse-content space-y-3">
              <label class="label cursor-pointer justify-start gap-3">
                <input type="checkbox" id="disableTokenEdit" class="checkbox checkbox-error checkbox-sm" ${disableTokenInitially ? 'checked' : ''}>
                <span class="label-text text-sm">Disable Secret Token (Public Access)</span>
              </label>
              <label class="label cursor-pointer justify-start gap-3">
                <input type="checkbox" id="enableTokenEdit" class="checkbox checkbox-primary checkbox-sm" ${enableTokenInitially ? 'checked' : ''}>
                <span class="label-text text-sm">Enable / Rotate Secret Token</span>
              </label>
              ${(share.secret_token || share.has_token) ? `
              <label class="label cursor-pointer justify-start gap-3">
                <input type="checkbox" id="rotateTokenEdit" class="checkbox checkbox-secondary checkbox-sm">
                <span class="label-text text-sm">Regenerate secret token on save (invalidates old links)</span>
              </label>` : ''}
              ${tokenHtml}
            </div>
          </div>

          <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box">
            <input type="radio" name="mgmt-accordion" />
            <div class="collapse-title font-semibold text-sm">${pathsSection.title}</div>
            <div class="collapse-content">
              <div class="max-h-48 overflow-y-auto space-y-1 mt-1" id="manageSharePathsList">
                ${pathsSection.inner}
              </div>
              ${pathsSection.addBtn}
            </div>
          </div>

          <div class="collapse collapse-arrow bg-base-100 border border-base-300 rounded-box" data-owner-only>
            <input type="radio" name="mgmt-accordion" />
            <div class="collapse-title font-semibold text-sm">Access Control</div>
            <div class="collapse-content space-y-4">
              <div>
                <label class="label-text font-semibold text-sm mb-2 block">Allowed Viewers</label>
                <div id="accessUsersList" class="flex flex-wrap gap-1.5 min-h-8 p-2 bg-base-200 rounded-lg mb-2">
                  ${buildShareUsersHtml(share.allowed_users)}
                </div>
                <div class="join w-full">
                  <input type="text" id="newUserInput" placeholder="Enter username" class="input input-bordered input-xs join-item flex-grow">
                  <button class="btn btn-xs btn-primary join-item" data-action="addUserToShare">Add</button>
                </div>
              </div>
              <div class="divider my-1"></div>
              <div>
                <label class="label-text font-semibold text-sm mb-2 block">Authorized Editors</label>
                <div id="modifyUsersList" class="flex flex-wrap gap-1.5 min-h-8 p-2 bg-base-200 rounded-lg mb-2">
                  ${buildModifyUsersHtml(share.modify_users)}
                </div>
                <div class="join w-full">
                  <input type="text" id="newModifyUserInput" placeholder="Enter username" class="input input-bordered input-xs join-item flex-grow">
                  <button class="btn btn-xs btn-secondary join-item" data-action="addModifyUserToShare">Add</button>
                </div>
              </div>
            </div>
          </div>
        </div>
      `;

      currentShareData = share;
      document.querySelector('.share-management-title').textContent = editorOnly
        ? 'Edit shared files'
        : 'Manage Share';
      const updateBtn = document.getElementById('updateShareBtn');
      if (updateBtn) updateBtn.classList.toggle('hidden', editorOnly);
      if (editorOnly) {
        body.querySelectorAll('[data-owner-only]').forEach((el) => el.classList.add('hidden'));
        const pathsCollapse = body.querySelector('#manageSharePathsList')?.closest('.collapse');
        if (pathsCollapse) {
          pathsCollapse.classList.remove('hidden');
          const radio = pathsCollapse.querySelector('input[type="radio"]');
          if (radio) radio.checked = true;
        }
      }
      setupTokenEditCheckboxes();
    }

    async function updateShare() {
      if (!currentShareData) {
        showDialog('No share data available', 'Error');
        return;
      }

      try {
        const isTagShare = (currentShareData.share_type || 'static') === 'tag';
        const typeRadio = document.querySelector('input[name="shareTypeEdit"]:checked');
        let shareType;
        if (isTagShare) {
          shareType = 'tag';
        } else if (typeRadio) {
          shareType = typeRadio.value;
        } else {
          shareType = currentShareData.share_type || 'static';
        }
        const disableToken = document.getElementById('disableTokenEdit').checked;
        const enableToken = document.getElementById('enableTokenEdit').checked;
        const allowListText = document.getElementById('allowListEdit').value.trim();
        const avoidListText = document.getElementById('avoidListEdit').value.trim();

        const hasSecret = Boolean(currentShareData.secret_token || currentShareData.has_token);

        let tokenDisabled;
        if (disableToken) {
          tokenDisabled = true;
        } else if (enableToken) {
          tokenDisabled = false;
        } else {
          tokenDisabled = !hasSecret;
        }

        const allowList = allowListText ? allowListText.split(',').map(s => s.trim()).filter(Boolean) : [];
        const avoidList = avoidListText ? avoidListText.split(',').map(s => s.trim()).filter(Boolean) : [];

        // Get expiry date (store as system time)
        const expiryDateInput = document.getElementById('expiryDateEdit').value;
        const expiryDate = expiryDateInput ? new Date(expiryDateInput).toISOString().replace('Z', '') : null;

        const response = await fetch('/share/update', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: JSON.stringify({
            share_id: currentShareData.id,
            share_type: shareType,
            disable_token: tokenDisabled,
            allow_list: allowList,
            avoid_list: avoidList,
            expiry_date: expiryDate,
            allowed_users: currentShareData.allowed_users || [],
            modify_users: currentShareData.modify_users || [],
            rotate_token: Boolean(document.getElementById('rotateTokenEdit')?.checked)
          })
        });

        const data = await response.json();

        if (data.error) {
          showDialog('Error: ' + data.error, 'Error');
        } else {
          let message = 'Share updated successfully!';
          if (data.new_token) {
            message += `\n\nNew secret token: ${data.new_token}\n\nPlease save this token - it won't be shown again!`;
          }
          showDialog(message, 'Success');
          closeShareManagementModal();
          loadActiveShares();
        }
      } catch (error) {
        console.error('Error updating share:', error);
        showDialog('Failed to update share', 'Error');
      }
    }

    function closeShareManagementModal() {
      const modal = document.getElementById('shareManagementModal');
      modal.close();
      currentShareData = null;
    }

    // Add Files Modal Functions
    let addFilesModalData = {
      currentPath: '',
      selectedFiles: new Set(),
      allFiles: []
    };

    function showAddFilesModal() {
      const modal = document.getElementById('addFilesModal');
      modal.showModal();
      addFilesModalData.currentPath = '';
      addFilesModalData.selectedFiles.clear();
      loadFilesForAddModal();
    }

    function closeAddFilesModal() {
      const modal = document.getElementById('addFilesModal');
      modal.close();
      addFilesModalData.selectedFiles.clear();
      updateSelectedFilesPreview();
    }

    async function loadFilesForAddModal() {
      const content = document.getElementById('fileBrowserContent');
      const pathDisplay = document.getElementById('currentBrowsePath');

      content.innerHTML = '<div class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading…</span></div>';
      pathDisplay.textContent = addFilesModalData.currentPath || 'Root Directory';

      try {
        const apiPath = addFilesModalData.currentPath || '';
        const response = await fetch(`/api/files/${apiPath}`);

        if (response.ok) {
          const data = await response.json();
          // Transform the API response to include full paths
          addFilesModalData.allFiles = data.files.map(file => ({
            ...file,
            path: addFilesModalData.currentPath ? `${addFilesModalData.currentPath}/${file.name}` : file.name
          }));

          renderFilesForAddModal();
        } else {
          const errorText = await response.text();
          console.error('API error:', response.status, errorText);
          content.innerHTML = '';
          const errDiv = document.createElement('div');
          errDiv.style.color = 'red';
          errDiv.style.textAlign = 'center';
          errDiv.style.padding = '20px';
          errDiv.textContent = `Error loading files: ${response.status}`;
          content.appendChild(errDiv);
        }
      } catch (error) {
        console.error('Error loading files:', error);
        content.innerHTML = '<div class="p-4 text-center text-error/70 text-sm">Error loading files</div>';
      }
    }

    function renderFilesForAddModal() {
      const content = document.getElementById('fileBrowserContent');
      const files = addFilesModalData.allFiles;

      content.innerHTML = '';
      if (!files || files.length === 0) {
        if (addFilesModalData.currentPath) {
          const folderPath = addFilesModalData.currentPath;
          const isSelected = addFilesModalData.selectedFiles.has(folderPath);
          const itemDiv = document.createElement('div');
          itemDiv.className = `file-browser-item ${isSelected ? 'selected' : ''}`;
          itemDiv.dataset.action = 'toggleFileSelection';
          itemDiv.dataset.path = folderPath;
          const checkbox = document.createElement('input');
          checkbox.type = 'checkbox';
          checkbox.checked = isSelected;
          checkbox.dataset.action = 'toggleFileSelection';
          checkbox.dataset.path = folderPath;
          checkbox.addEventListener('click', (e) => e.stopPropagation());
          itemDiv.appendChild(checkbox);
          const span = document.createElement('span');
          span.textContent = ' 📁 (this folder) — empty';
          itemDiv.appendChild(span);
          content.appendChild(itemDiv);
        } else {
          content.innerHTML = '<div class="share-empty-msg">No files in this directory</div>';
        }
        return;
      }

      files.forEach(file => {
        const isSelected = addFilesModalData.selectedFiles.has(file.path);
        const icon = file.is_dir ? '📁' : getFileIcon(file.name);

        const itemDiv = document.createElement('div');
        itemDiv.className = `file-browser-item ${isSelected ? 'selected' : ''}`;
        itemDiv.dataset.action = file.is_dir ? 'navigateToDirectory' : 'toggleFileSelection';
        itemDiv.dataset.path = file.path;

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = isSelected;
        checkbox.dataset.action = 'toggleFileSelection';
        checkbox.dataset.path = file.path;
        checkbox.addEventListener('click', (e) => e.stopPropagation());
        itemDiv.appendChild(checkbox);

        const span = document.createElement('span');
        span.textContent = ` ${icon} ${file.name}`;
        itemDiv.appendChild(span);

        content.appendChild(itemDiv);
      });
    }

    function toggleFileSelection(filePath) {
      if (addFilesModalData.selectedFiles.has(filePath)) {
        addFilesModalData.selectedFiles.delete(filePath);
      } else {
        addFilesModalData.selectedFiles.add(filePath);
      }
      renderFilesForAddModal();
      updateSelectedFilesPreview();
    }

    function updateSelectedFilesPreview() {
      const preview = document.getElementById('selectedFilesPreview');
      const selectedFiles = Array.from(addFilesModalData.selectedFiles);

      if (selectedFiles.length === 0) {
        preview.innerHTML = '<div class="share-empty-msg">No files selected</div>';
        return;
      }

      preview.innerHTML = selectedFiles.map(filePath => {
        const fileName = filePath.split('/').pop();
        return `
            <div class="selected-file-item">
              <span>${escapeHtml(fileName)}</span>
              <button type="button" class="remove-btn" data-action="removeFromAddModalSelection" data-path="${escapeAttr(filePath)}">&times;</button>
            </div>
          `;
      }).join('');
    }

    function removeFromAddModalSelection(filePath) {
      addFilesModalData.selectedFiles.delete(filePath);
      renderFilesForAddModal();
      updateSelectedFilesPreview();
    }

    function navigateUp() {
      if (addFilesModalData.currentPath) {
        const pathParts = addFilesModalData.currentPath.split('/').filter(part => part.length > 0);
        pathParts.pop();
        addFilesModalData.currentPath = pathParts.join('/');
        loadFilesForAddModal();
      }
    }

    function navigateToDirectory(dirPath) {
      addFilesModalData.currentPath = dirPath;
      loadFilesForAddModal();
    }

    async function addSelectedFilesToShare() {
      const modal = document.getElementById('addFilesModal');
      const isManagement = modal.dataset.mode === 'management';

      const newPaths = Array.from(addFilesModalData.selectedFiles);
      if (newPaths.length === 0) {
        closeAddFilesModal();
        return;
      }

      if (isManagement) {
        const base = currentShareData.paths ? [...currentShareData.paths] : [];
        const merged = [...base];
        newPaths.forEach(p => {
          if (!merged.includes(p)) merged.push(p);
        });
        try {
          const response = await fetch('/share/update', {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              'X-XSRFToken': getXSRFToken()
            },
            body: JSON.stringify({
              share_id: currentShareData.id,
              paths: merged
            })
          });
          const data = await response.json();
          if (data.error) {
            showDialog('Error: ' + data.error, 'Error');
            return;
          }
          currentShareData.paths = data.updated_paths || merged;
          showDialog('Files added to share.', 'Success');
          renderShareManagementModal(currentShareData);
          closeAddFilesModal();
          modal.dataset.mode = '';
          loadActiveShares();
        } catch (error) {
          console.error('Error adding files to share:', error);
          showDialog('Failed to add files to share', 'Error');
        }
      } else {
        // Normal creation mode - prefill the share creation form
        newPaths.forEach(p => addToSelection(p));
        closeAddFilesModal();
        openShareConfigModal();
      }
    }


    async function removeFileFromShare(filePath) {
      if (!currentShareData) return;

      const confirmed = await showDialog(`Remove "${filePath}" from this share?`, 'Confirm Removal', { showCancel: true });
      if (!confirmed) return;

      try {
        const response = await fetch('/share/update', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: JSON.stringify({
            share_id: currentShareData.id,
            remove_files: [filePath]
          })
        });

        const data = await response.json();

        if (data.error) {
          showDialog('Error removing file: ' + data.error, 'Error');
        } else {
          showDialog('File removed successfully!', 'Success');
          // Refresh the modal
          manageShare(currentShareData.id);
        }
      } catch (error) {
        console.error('Error removing file:', error);
        showDialog('Failed to remove file', 'Error');
      }
    }

    async function addUserToShare() {
      const input = document.getElementById('newUserInput');
      const user = input.value.trim();
      if (!user) return;

      const currentUsers = currentShareData.allowed_users || [];
      if (currentUsers.includes(user)) { input.value = ''; return; }
      const updatedUsers = [...currentUsers, user];
      try {
        const response = await fetch('/share/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({ share_id: currentShareData.id, allowed_users: updatedUsers })
        });
        const data = await response.json();
        if (data.error) {
          showDialog('Error adding user: ' + data.error, 'Error');
        } else {
          input.value = '';
          manageShare(currentShareData.id);
        }
      } catch (error) {
        console.error('Error adding user:', error);
        showDialog('Failed to add user', 'Error');
      }
    }


    async function removeUserFromShare(username) {
      if (!currentShareData) return;

      const confirmed = await showDialog(`Remove access for "${username}"?`, 'Confirm Removal', { showCancel: true });
      if (!confirmed) return;

      const currentUsers = currentShareData.allowed_users || [];
      const updatedUsers = currentUsers.filter(u => u !== username);

      try {
        const response = await fetch('/share/update', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: JSON.stringify({
            share_id: currentShareData.id,
            allowed_users: updatedUsers
          })
        });

        const data = await response.json();

        if (data.error) {
          showDialog('Error removing user: ' + data.error, 'Error');
        } else {
          showDialog('User access removed successfully!', 'Success');
          manageShare(currentShareData.id);
        }
      } catch (error) {
        console.error('Error removing user:', error);
        showDialog('Failed to remove user', 'Error');
      }
    }

    async function addModifyUserToShare() {
      const input = document.getElementById('newModifyUserInput');
      const user = input.value.trim();
      if (!user) return;

      const currentUsers = currentShareData.modify_users || [];
      if (currentUsers.includes(user)) { input.value = ''; return; }
      const updatedUsers = [...currentUsers, user];
      try {
        const response = await fetch('/share/update', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
          body: JSON.stringify({ share_id: currentShareData.id, modify_users: updatedUsers })
        });
        const data = await response.json();
        if (data.error) {
          showDialog('Error adding modifier: ' + data.error, 'Error');
        } else {
          input.value = '';
          manageShare(currentShareData.id);
        }
      } catch (error) {
        console.error('Error adding modify user:', error);
        showDialog('Failed to add modifier', 'Error');
      }
    }

    async function removeModifyUserFromShare(username) {
      if (!currentShareData) return;
      const confirmed = await showDialog(`Remove modify access for "${username}"?`, 'Confirm Removal', { showCancel: true });
      if (!confirmed) return;
      const currentModifyUsers = currentShareData.modify_users || [];
      const updatedUsers = currentModifyUsers.filter(u => u !== username);
      try {
        const response = await fetch('/share/update', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: JSON.stringify({
            share_id: currentShareData.id,
            modify_users: updatedUsers
          })
        });
        const data = await response.json();
        if (data.error) {
          showDialog('Error removing modify access: ' + data.error, 'Error');
        } else {
          showDialog('Modify access removed!', 'Success');
          manageShare(currentShareData.id);
        }
      } catch (error) {
        console.error('Error removing modify user:', error);
        showDialog('Failed to remove modify user', 'Error');
      }
    }

    // Close modal when clicking outside
    document.addEventListener('click', function (event) {
      const modal = document.getElementById('shareManagementModal');
      if (event.target === modal) {
        closeShareManagementModal();
      }
    });

    // Close modals with Escape key
    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        closeShareManagementModal();
        closeShareConfigModal();
      }
    });

    // User management functions
    let searchTimeout = null;

    function toggleUserSelectionPanel() {
      const accessType = document.querySelector('input[name="accessType"]:checked').value;
      const userSelection = document.getElementById('userSelection');

      if (accessType === 'restricted') {
        userSelection.style.display = 'block';
        // Clear previous search and selections
        document.getElementById('userSearchInput').value = '';
        document.getElementById('userList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
        updateSelectedUsersDisplay();
      } else {
        userSelection.style.display = 'none';
        selectedUsers.clear();
        updateSelectedUsersDisplay();
      }
    }

    function toggleShareTypeInfo() {
      const shareType = document.querySelector('input[name="shareType"]:checked').value;
      const shareTypeInfo = document.getElementById('shareTypeInfo');

      if (shareType === 'static') {
        shareTypeInfo.textContent = 'Static share creates a snapshot. New files added later won\'t appear.';
      } else {
        shareTypeInfo.textContent = 'Dynamic share is live. New files added to the folder will appear automatically.';
      }
    }

    function toggleTokenInfo() {
      const disableToken = document.getElementById('disableToken').checked;
      const tokenInfo = document.getElementById('tokenInfo');

      if (disableToken) {
        tokenInfo.textContent = 'Public access — anyone with the link can view without a token.';
      } else {
        tokenInfo.textContent = 'Token enabled — users need a secret token to access shared files.';
      }
    }

    function setupUserSearch() {
      const searchInput = document.getElementById('userSearchInput');
      searchInput.addEventListener('input', function () {
        const query = this.value.trim();

        // Clear previous timeout
        if (searchTimeout) {
          clearTimeout(searchTimeout);
        }

        if (query.length < 1) {
          document.getElementById('userList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
          return;
        }

        // Debounce search requests
        searchTimeout = setTimeout(() => {
          searchUsers(query);
        }, 300);
      });
    }

    async function searchUsers(query) {
      try {
        const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`);
        if (response.ok) {
          const data = await response.json();
          renderUserList(data.users || []);
        } else {
          document.getElementById('userList').innerHTML = '<em class="share-error-text">Error searching users</em>';
        }
      } catch (error) {
        console.error('Error searching users:', error);
        document.getElementById('userList').innerHTML = '<em class="share-error-text">Error searching users</em>';
      }
    }

    function renderUserList(users) {
      const userList = document.getElementById('userList');

      if (users.length === 0) {
        userList.innerHTML = '<em class="share-hint">No users found</em>';
        return;
      }

      userList.innerHTML = '';
      users.forEach(user => {
        const userDiv = document.createElement('div');
        userDiv.style.marginBottom = '5px';
        const isSelected = selectedUsers.has(user.username);
        const label = document.createElement('label');
        label.style.display = 'flex';
        label.style.alignItems = 'center';
        label.style.gap = '8px';
        label.style.cursor = 'pointer';

        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = user.username;
        checkbox.checked = isSelected;
        checkbox.dataset.action = 'toggleUserSelection';
        checkbox.dataset.user = user.username;

        const span = document.createElement('span');
        span.textContent = `${user.username} ${user.role === 'admin' ? '(Admin)' : ''}`;

        label.appendChild(checkbox);
        label.appendChild(span);
        userDiv.appendChild(label);
        userList.appendChild(userDiv);
      });
    }

    function toggleUserSelection(username) {
      if (selectedUsers.has(username)) {
        selectedUsers.delete(username);
      } else {
        selectedUsers.add(username);
      }
      updateSelectedUsersDisplay();
    }

    function updateSelectedUsersDisplay() {
      const selectedUsersList = document.getElementById('selectedUsersList');

      if (selectedUsers.size === 0) {
        selectedUsersList.innerHTML = '<em class="share-hint">No users selected</em>';
        return;
      }

      selectedUsersList.innerHTML = Array.from(selectedUsers)
        .map(username => `<span class="config-tag access" data-action="removeSelectedUser" data-user="${escapeAttr(username)}">${escapeHtml(username)} ×</span>`)
        .join('');
    }

    function removeSelectedUser(username) {
      selectedUsers.delete(username);
      updateSelectedUsersDisplay();

      // Update checkbox in search results if visible
      const checkbox = findCheckboxByValue(document.getElementById('userList'), username);
      if (checkbox) {
        checkbox.checked = false;
      }
    }

    let modifySearchTimeout = null;

    function setupModifyUserSearch() {
      const searchInput = document.getElementById('modifyUserSearchInput');
      if (!searchInput) return;
      searchInput.addEventListener('input', function () {
        const query = this.value.trim();
        if (modifySearchTimeout) clearTimeout(modifySearchTimeout);
        if (query.length < 1) {
          document.getElementById('modifyUserList').innerHTML = '<em class="share-hint">Type to search for users...</em>';
          return;
        }
        modifySearchTimeout = setTimeout(() => searchModifyUsers(query), 300);
      });
    }

    async function searchModifyUsers(query) {
      try {
        const response = await fetch(`/api/users/search?q=${encodeURIComponent(query)}`);
        if (response.ok) {
          const data = await response.json();
          renderModifyUserList(data.users || []);
        } else {
          document.getElementById('modifyUserList').innerHTML = '<em class="share-error-text">Error searching users</em>';
        }
      } catch (error) {
        console.error('Error searching modify users:', error);
        document.getElementById('modifyUserList').innerHTML = '<em class="share-error-text">Error searching users</em>';
      }
    }

    function renderModifyUserList(users) {
      const userList = document.getElementById('modifyUserList');
      if (users.length === 0) {
        userList.innerHTML = '<em class="share-hint">No users found</em>';
        return;
      }
      userList.innerHTML = '';
      users.forEach(user => {
        const userDiv = document.createElement('div');
        userDiv.style.marginBottom = '5px';
        const isSelected = selectedModifyUsers.has(user.username);
        const label = document.createElement('label');
        label.style.display = 'flex';
        label.style.alignItems = 'center';
        label.style.gap = '8px';
        label.style.cursor = 'pointer';
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.value = user.username;
        checkbox.checked = isSelected;
        checkbox.dataset.action = 'toggleModifyUserSelection';
        checkbox.dataset.user = user.username;
        const span = document.createElement('span');
        span.textContent = `${user.username} ${user.role === 'admin' ? '(Admin)' : ''}`;
        label.appendChild(checkbox);
        label.appendChild(span);
        userDiv.appendChild(label);
        userList.appendChild(userDiv);
      });
    }

    function toggleModifyUserSelection(username) {
      if (selectedModifyUsers.has(username)) {
        selectedModifyUsers.delete(username);
      } else {
        selectedModifyUsers.add(username);
      }
      updateSelectedModifyUsersDisplay();
    }

    function updateSelectedModifyUsersDisplay() {
      const list = document.getElementById('selectedModifyUsersList');
      if (selectedModifyUsers.size === 0) {
        list.innerHTML = '<em class="share-hint">No modify users (read-only)</em>';
        return;
      }
      list.innerHTML = Array.from(selectedModifyUsers)
        .map(u => `<span class="config-tag editor" data-action="removeSelectedModifyUser" data-user="${escapeAttr(u)}">✏️ ${escapeHtml(u)} ×</span>`)
        .join('');
    }

    function removeSelectedModifyUser(username) {
      selectedModifyUsers.delete(username);
      updateSelectedModifyUsersDisplay();
      const checkbox = findCheckboxByValue(document.getElementById('modifyUserList'), username);
      if (checkbox) checkbox.checked = false;
    }

    function buildPathsPayload(files, metadataMap) {
      return Array.from(files).map(item => {
        const meta = metadataMap.get(item);
        if (meta?.type === 'cloud') {
          return { type: 'cloud', provider: meta.provider, id: meta.id, name: meta.name, is_dir: !!meta.is_dir };
        }
        return item;
      });
    }

    function buildShareResultHtml(data, fullUrl, accessType, allowedUsers, shareType, disableToken) {
      const modifyUsers = Array.from(selectedModifyUsers);
      const accessInfo = accessType === 'restricted'
        ? `<p class="text-sm opacity-90">Access restricted to: ${allowedUsers.map((u) => escapeHtml(u)).join(', ') || 'No users selected'}</p>`
        : '<p class="text-sm opacity-90">Public access (anyone with link)</p>';
      const modifyInfo = modifyUsers.length > 0
        ? `<p class="text-sm opacity-90">✏️ Modify access: ${modifyUsers.map((u) => escapeHtml(u)).join(', ')}</p>`
        : '<p class="text-sm opacity-90">📖 Read-only share</p>';
      const shareTypeInfo = shareType === 'dynamic'
        ? '<p class="text-sm opacity-90">🔄 Dynamic share (live folder)</p>'
        : '<p class="text-sm opacity-90">📸 Static share (snapshot)</p>';
      const tokenInfo = disableToken
        ? '<p class="text-sm opacity-90">🌐 Public access (no token required)</p>'
        : '<p class="text-sm opacity-90">🔐 Token required</p>';
      const tokenSection = disableToken ? '' : `
            <div class="flex flex-col gap-2 mt-1">
              <span class="font-semibold">🔐 Secret Token</span>
              <div class="flex flex-wrap items-center gap-2">
                <code class="break-all text-xs font-mono bg-base-200 px-2 py-1 rounded flex-1 min-w-0">${escapeHtml(data.secret_token)}</code>
                <button type="button" class="btn btn-sm shrink-0" data-action="copyToClipboard" data-text="${escapeAttr(data.secret_token)}">Copy Token</button>
              </div>
              <div class="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm mt-1">
                <strong>⚠️ Important:</strong> Share this secret token with users who need access. They will need to enter this token to view the shared files.
              </div>
            </div>`;
      return `
          <div class="flex flex-col gap-3 w-full min-w-0">
            <p class="font-bold text-base">Share link created</p>
            <div class="flex flex-wrap items-center gap-2 gap-y-2">
              <a class="link link-hover break-all text-sm flex-1 min-w-0" href="${escapeAttr(data.url)}">${escapeHtml(data.url)}</a>
              <button type="button" class="btn btn-sm shrink-0" data-action="copyToClipboard" data-text="${escapeAttr(fullUrl)}">Copy Link</button>
            </div>
            ${accessInfo}${modifyInfo}${shareTypeInfo}${tokenInfo}
            ${tokenSection}
          </div>`;
    }

    async function generateShareLink() {
      if (selectedFiles.size === 0) return;

      const createBtn = document.getElementById('createShareBtn');
      createBtn.disabled = true;
      createBtn.textContent = 'Creating...';

      try {
        const accessType = document.querySelector('input[name="accessType"]:checked').value;
        const shareType = document.querySelector('input[name="shareType"]:checked').value;
        const allowedUsers = accessType === 'restricted' ? Array.from(selectedUsers) : [];
        const modifyUsers = Array.from(selectedModifyUsers);
        const disableToken = document.getElementById('disableToken').checked;

        const allowListText = document.getElementById('allowList').value.trim();
        const avoidListText = document.getElementById('avoidList').value.trim();
        const allowList = allowListText ? allowListText.split(',').map(s => s.trim()).filter(Boolean) : [];
        const avoidList = avoidListText ? avoidListText.split(',').map(s => s.trim()).filter(Boolean) : [];

        const expiryDateInput = document.getElementById('expiryDate').value;
        const expiryDate = expiryDateInput ? new Date(expiryDateInput).toISOString().replace('Z', '') : null;

        const pathsPayload = buildPathsPayload(selectedFiles, selectedFileMetadata);

        const response = await fetch('/share/create', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: JSON.stringify({
            paths: pathsPayload,
            allowed_users: allowedUsers,
            modify_users: modifyUsers,
            share_type: shareType,
            allow_list: allowList,
            avoid_list: avoidList,
            disable_token: disableToken,
            expiry_date: expiryDate
          })
        });

        const data = await response.json();

        if (data.error) {
          showDialog('Error: ' + data.error, 'Error');
        } else {
          closeShareConfigModal();
          const fullUrl = `${globalThis.location.origin}${data.url}`;
          elements.shareResult.className =
            'alert alert-success shadow-sm mb-6 flex flex-col items-stretch text-start gap-0';
          elements.shareResult.innerHTML = buildShareResultHtml(
            data, fullUrl, accessType, allowedUsers, shareType, disableToken
          );
          clearSelection();
          loadActiveShares();
          elements.shareResult.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
      } catch (error) {
        console.error('Error generating share link:', error);
        showDialog('Failed to generate share link', 'Error');
      } finally {
        createBtn.disabled = false;
        createBtn.textContent = 'Create Share Link';
      }
    }

    function _buildAccessInfo(share) {
      const au = share.allowed_users;
      let accessInfo;
      if (Array.isArray(au) && au.length > 0) {
        const suffix = au.length === 1 ? '' : 's';
        accessInfo = `Restricted (${au.length} user${suffix})`;
      } else {
        accessInfo = 'Public';
      }
      const modifyCount = share.modify_users?.length ?? 0;
      if (modifyCount > 0) {
        accessInfo += ` <span class="permission-badge editor">${modifyCount} editor${modifyCount === 1 ? '' : 's'}</span>`;
      }
      return accessInfo;
    }

    function _buildShareRow(share, { showOwner = false, allowManage = true, allowRevoke = true } = {}) {
      const accessInfo = _buildAccessInfo(share);

      const createdDate = share.created ? new Date(share.created).toLocaleString() : 'Just now';
      const rawShareId = String(share.id);
      const sidAttr = escapeAttr(rawShareId);
      const shareLink = escapeAttr(`${globalThis.location.origin}/shared/${rawShareId}`);
      const sharePath = escapeAttr(`/shared/${rawShareId}`);
      const idPreviewEsc = escapeHtml(rawShareId.length > 8 ? `${rawShareId.substring(0, 8)}...` : rawShareId);
      const pathFileCount = share.paths ? share.paths.length : 0;
      const shareFileCount = share.count || pathFileCount;
      const filePlural = shareFileCount === 1 ? '' : 's';
      const secretToken = share.secret_token;
      const copyTokenBtn = secretToken
        ? `<button type="button" class="btn btn-sm btn-ghost gap-1" data-action="copyToClipboard" data-text="${escapeAttr(String(secretToken))}" title="Copy secret token">
            <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z" /></svg>
            <span class="hidden sm:inline text-xs">Token</span>
          </button>`
        : '';

      const ownerCell = showOwner
        ? `<td class="align-middle text-sm opacity-70">${escapeHtml(share.created_by || '—')}</td>`
        : '';
      const manageLabel = share.can_edit_paths && !share.is_owner ? 'Edit files' : 'Manage';
      const manageBtn = allowManage ? `<button class="btn btn-sm btn-primary btn-outline" data-action="manageShare" data-id="${sidAttr}">${manageLabel}</button>` : '';
      const revokeBtn = allowRevoke
        ? `<button class="btn btn-sm btn-error btn-ghost btn-square" data-action="revokeShare" data-id="${sidAttr}" title="Revoke">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" /></svg>
        </button>`
        : '';

      const row = document.createElement('tr');
      row.innerHTML = `
        <td class="align-middle"><code class="text-xs font-mono opacity-70">${idPreviewEsc}</code></td>
        <td class="align-middle">
          <div class="flex items-center gap-2">
            <span class="badge badge-sm badge-ghost font-bold">${shareFileCount}</span>
            <span class="text-sm">file${filePlural}</span>
          </div>
        </td>
        ${ownerCell}
        <td class="align-middle">${accessInfo}</td>
        <td class="align-middle text-sm opacity-60">${escapeHtml(createdDate)}</td>
        <td class="align-middle">
          <div class="flex flex-nowrap gap-2 items-center justify-end">
            <button class="btn btn-sm btn-ghost" data-action="copyToClipboard" data-text="${shareLink}" title="Copy link">
              <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3" /></svg>
            </button>
            ${copyTokenBtn}
            <button class="btn btn-sm btn-ghost" data-action="openShare" data-url="${sharePath}" title="Open Share">
               <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" /></svg>
            </button>
            ${manageBtn}
            ${revokeBtn}
          </div>
        </td>
      `;
      return row;
    }

    async function loadActiveShares() {
      const refreshBtn = document.getElementById('refreshSharesBtn');
      if (refreshBtn) refreshBtn.classList.add('loading');
      try {
        const response = await fetch('/share/list');
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`);
        }
        const data = await response.json();

        // --- My Shares ---
        const sharesArray = data.shares ? Object.keys(data.shares).map(id => ({
          id: id,
          ...data.shares[id],
          count: data.shares[id].paths?.length ?? 0
        })) : [];

        sharesArray.sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));

        elements.activeSharesCount.textContent = sharesArray.length;

        if (sharesArray.length === 0) {
          elements.sharesTableBody.innerHTML =
            '<tr><td colspan="5" class="p-8 text-center text-base-content/50">No active shares</td></tr>';
        } else {
          elements.sharesTableBody.innerHTML = '';
          sharesArray.forEach(share => {
            elements.sharesTableBody.appendChild(_buildShareRow(share, {
              showOwner: false,
              allowManage: Boolean(share.can_manage || share.can_edit_paths),
              allowRevoke: Boolean(share.can_revoke),
            }));
          });
        }

        // --- Shared with Me ---
        const sharedWithMe = Array.isArray(data.shared_with_me) ? data.shared_with_me : [];
        sharedWithMe.sort((a, b) => new Date(b.created || 0) - new Date(a.created || 0));

        if (elements.sharedWithMeCount) {
          elements.sharedWithMeCount.textContent = sharedWithMe.length;
        }
        if (elements.sharedWithMeTableBody) {
          if (sharedWithMe.length === 0) {
            elements.sharedWithMeTableBody.innerHTML =
              '<tr><td colspan="6" class="p-8 text-center text-base-content/50">No shares from others</td></tr>';
          } else {
            elements.sharedWithMeTableBody.innerHTML = '';
            sharedWithMe.forEach(share => {
              elements.sharedWithMeTableBody.appendChild(_buildShareRow(share, {
                showOwner: true,
                allowManage: Boolean(share.can_edit_paths),
                allowRevoke: Boolean(share.can_revoke),
              }));
            });
          }
        }

      } catch (error) {
        console.error('Error loading active shares:', error);
        elements.sharesTableBody.innerHTML = '<tr><td colspan="5" class="p-10 text-center"><div class="alert alert-error">Error loading shares</div></td></tr>';
        if (elements.sharedWithMeTableBody) {
          elements.sharedWithMeTableBody.innerHTML =
            '<tr><td colspan="6" class="p-10 text-center"><div class="alert alert-error">Error loading shares</div></td></tr>';
        }
      } finally {
        if (refreshBtn) refreshBtn.classList.remove('loading');
      }
    }

    function copyToClipboard(text, btn) {
      globalThis.AirdCore.copyToClipboard(text, btn);
    }

    function openShare(url) {
      globalThis.location.href = url;
    }

    async function revokeShare(shareId) {
      const confirmed = await showDialog('Are you sure you want to revoke this share?', 'Confirm Revocation', { showCancel: true });
      if (!confirmed) return;

      try {
        const formData = new URLSearchParams();
        formData.append('id', shareId);
        const res = await fetch('/share/revoke', {
          method: 'POST',
          headers: {
            'Accept': 'application/json',
            'X-XSRFToken': getXSRFToken()
          },
          body: formData
        });
        const text = await res.text();
        if (!res.ok) {
          let detail = text || `HTTP ${res.status}`;
          try {
            const j = JSON.parse(text);
            if (j.error) detail = j.error;
          } catch { /* use raw text */ }
          showDialog('Failed to revoke share: ' + detail, 'Error');
          return;
        }

        loadActiveShares();
      } catch (error) {
        console.error('Error revoking share:', error);
        showDialog('Failed to revoke share', 'Error');
      }
    }

    function previewFile(filePath) {
      globalThis.location.href = `/files/${filePath}`;
    }

    // Share popup functions
    async function showShareDetails(filePath) {
      const popup = document.getElementById('sharePopup');
      const content = document.getElementById('sharePopupContent');

      // Show popup with loading state
      popup.showModal();
      content.innerHTML = '<div class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading share details…</span></div>';

      try {
        const response = await fetch(`/api/share/details?path=${encodeURIComponent(filePath)}`);
        const data = await response.json();

        if (data.error) {
          content.innerHTML = `<div class="share-error-msg">Error: ${escapeHtml(data.error)}</div>`;
          return;
        }

        if (data.shares.length === 0) {
          content.innerHTML = '<div class="share-empty-msg">This file is not currently shared.</div>';
          return;
        }

        // Update popup title with filename
        const titleEl = document.querySelector('.popup-title');
        if (titleEl) titleEl.textContent = 'Share Details - ' + (filePath.split('/').pop() || filePath);

        // Render share details
        content.innerHTML = data.shares.map((share) => {
          const allowed = share.allowed_users;
          const isRestricted = allowed && allowed.length > 0;
          let shareAccessInfo;
          if (isRestricted) {
            const suffix = allowed.length === 1 ? '' : 's';
            shareAccessInfo = `Restricted (${allowed.length} user${suffix})`;
          } else {
            shareAccessInfo = 'Public Access';
          }
          const fullShareUrl = globalThis.location.origin + share.url;

          let secretBlock = '';
          const token = share.secret_token;
          if (token) {
            secretBlock = `
              <div class="share-token-block">
                <strong>🔐 Secret Token:</strong><br>
                <code class="share-token-code">${escapeHtml(token)}</code>
                <button class="btn btn-sm" data-action="copyToClipboard" data-text="${escapeAttr(token)}">Copy</button>
              </div>`;
          }

          let allowedBlock = '';
          if (isRestricted) {
            allowedBlock = `
              <div class="share-users">
                <div class="share-users-title">Allowed Users:</div>
                ${allowed.map((username) => `<span class="user-tag">${escapeHtml(username)}</span>`).join('')}
              </div>`;
          }

          let modifyBlock = '';
          const modifyUsers = share.modify_users;
          if (modifyUsers?.length) {
            modifyBlock = `
              <div class="share-users" style="margin-top:6px;">
                <div class="share-users-title">Modify Users:</div>
                ${modifyUsers.map((username) => `<span class="user-tag modify-user-tag">✏️ ${escapeHtml(username)}</span>`).join('')}
              </div>`;
          }

          return `
          <div class="share-item">
            <div class="share-id">${escapeHtml(share.id)}</div>
            <div class="share-url">
              <a href="${escapeAttr(share.url)}">${escapeHtml(fullShareUrl)}</a>
            </div>
            <div class="share-access ${isRestricted ? 'restricted' : 'public'}">
              ${shareAccessInfo}
            </div>
            ${secretBlock}
            ${allowedBlock}
            ${modifyBlock}
            <div class="share-actions">
              <button class="btn" data-action="copyToClipboard" data-text="${escapeAttr(fullShareUrl)}">Copy Link</button>
              <button class="btn" data-action="openShare" data-url="${escapeAttr(share.url)}">Open Share</button>
              <button class="btn" data-action="revokeShare" data-id="${escapeAttr(share.id)}">Revoke</button>
            </div>
          </div>
        `;
        }).join('');

      } catch (error) {
        console.error('Error loading share details:', error);
        content.innerHTML = '<div class="share-error-msg">Failed to load share details</div>';
      }
    }

    function closeSharePopup() {
      const popup = document.getElementById('sharePopup');
      popup.close();
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

    function openShareConfigModal() {
      if (selectedFiles.size === 0) return;
      updateConfigSelectedFiles();
      document.getElementById('shareConfigModal').showModal();
    }

    function closeShareConfigModal() {
      document.getElementById('shareConfigModal').close();
    }

    // Event listeners
    elements.generateLink.onclick = openShareConfigModal;
    elements.clearSelection.onclick = clearSelection;
    elements.selectAllVisible.onclick = selectAllVisible;

    // If the browse page handed us a selection via sessionStorage,
    // pre-populate it on load and open the configure modal so the user
    // lands straight on the configuration step.
    function consumeShareCreatePrefill() {
      let raw;
      try {
        raw = sessionStorage.getItem('airdShareCreatePrefill');
      } catch (e) {
        console.debug('sessionStorage unavailable for share prefill', e);
        return;
      }
      if (!raw) return;
      try {
        sessionStorage.removeItem('airdShareCreatePrefill');
      } catch (e) {
        console.debug('sessionStorage removeItem failed', e);
      }
      let parsed;
      try {
        parsed = JSON.parse(raw);
      } catch (e) {
        console.warn('Invalid share prefill payload:', e);
        return;
      }
      const paths = Array.isArray(parsed?.paths) ? parsed.paths : [];
      if (paths.length === 0) return;
      // Drop anything older than 10 minutes so stale tabs don't hijack
      // a fresh Share page visit.
      if (parsed.created_at && (Date.now() - parsed.created_at) > 10 * 60 * 1000) return;
      paths.forEach((p) => {
        if (typeof p === 'string' && p.length > 0) addToSelection(p);
      });
      if (selectedFiles.size > 0) {
        openShareFilePicker();
        openShareConfigModal();
      }
    }

    // Initialize
    document.addEventListener('DOMContentLoaded', () => {
      loadActiveShares();
      setupUserSearch();
      setupModifyUserSearch();
      consumeShareCreatePrefill();

      document.getElementById('refreshSharesBtn')?.addEventListener('click', loadActiveShares);
      document.getElementById('startCreateShareBtn')?.addEventListener('click', openShareFilePicker);
      document.getElementById('cancelCreateShareBtn')?.addEventListener('click', closeShareFilePicker);

      // Event listeners for CSP compliance - static elements
      document.getElementById('cloudBrowserClose')?.addEventListener('click', closeCloudBrowser);
      document.getElementById('sharePopupClose')?.addEventListener('click', closeSharePopup);
      document.getElementById('shareManagementClose')?.addEventListener('click', closeShareManagementModal);
      document.getElementById('shareManagementCancel')?.addEventListener('click', closeShareManagementModal);
      document.getElementById('updateShareBtn')?.addEventListener('click', updateShare);
      document.getElementById('addFilesClose')?.addEventListener('click', closeAddFilesModal);
      document.getElementById('navigateUpBtn')?.addEventListener('click', navigateUp);
      document.getElementById('addSelectedFilesBtn')?.addEventListener('click', addSelectedFilesToShare);
      document.getElementById('addFilesCancel')?.addEventListener('click', closeAddFilesModal);

      // Share config modal
      document.getElementById('shareConfigClose')?.addEventListener('click', closeShareConfigModal);
      document.getElementById('cancelShareConfig')?.addEventListener('click', closeShareConfigModal);
      document.getElementById('createShareBtn')?.addEventListener('click', generateShareLink);

      // Close share config modal when clicking overlay
      document.getElementById('shareConfigModal')?.addEventListener('click', function (e) {
        if (e.target === this) closeShareConfigModal();
      });

      // Static form controls
      document.querySelectorAll('input[name="accessType"]').forEach(r => r.addEventListener('change', toggleUserSelectionPanel));
      document.querySelectorAll('input[name="shareType"]').forEach(r => r.addEventListener('change', toggleShareTypeInfo));
      document.getElementById('disableToken')?.addEventListener('change', toggleTokenInfo);

      // Global event delegation for all data-action elements
      document.addEventListener('click', function (e) {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        const action = el.dataset.action;

        switch (action) {
          case 'removeFromSelection':
            e.preventDefault();
            removeFromSelection(el.dataset.path);
            break;
          case 'loadDirectory':
            e.preventDefault();
            loadDirectory(el.dataset.path);
            break;
          case 'previewFile':
            e.preventDefault();
            previewFile(el.dataset.path);
            break;
          case 'showShareDetails':
            e.preventDefault();
            e.stopPropagation();
            showShareDetails(el.dataset.path);
            break;
          case 'copyToClipboard':
            e.preventDefault();
            copyToClipboard(el.dataset.text, el);
            break;
          case 'openShare':
            e.preventDefault();
            openShare(el.dataset.url);
            break;
          case 'manageShare':
            e.preventDefault();
            manageShare(el.dataset.id);
            break;
          case 'showAddFilesModalInManagement':
            e.preventDefault();
            showAddFilesModal();
            // Flag that we are in management mode
            document.getElementById('addFilesModal').dataset.mode = 'management';
            break;
          case 'revokeShare':
            e.preventDefault();
            revokeShare(el.dataset.id);
            break;
          case 'removeUserFromShare':
            e.preventDefault();
            removeUserFromShare(el.dataset.user);
            break;
          case 'addUserToShare':
            e.preventDefault();
            addUserToShare();
            break;
          case 'removeFileFromShare':
            e.preventDefault();
            removeFileFromShare(el.dataset.path);
            break;
          case 'showAddFilesModal':
            e.preventDefault();
            showAddFilesModal();
            break;
          case 'navigateToDirectory':
            e.preventDefault();
            navigateToDirectory(el.dataset.path);
            break;
          case 'toggleFileSelection':
            toggleFileSelection(el.dataset.path);
            break;
          case 'removeFromAddModalSelection':
            e.preventDefault();
            removeFromAddModalSelection(el.dataset.path);
            break;
          case 'removeSelectedUser':
            e.preventDefault();
            removeSelectedUser(el.dataset.user);
            break;
          case 'addModifyUserToShare':
            e.preventDefault();
            addModifyUserToShare();
            break;
          case 'removeModifyUserFromShare':
            e.preventDefault();
            removeModifyUserFromShare(el.dataset.user);
            break;
          case 'removeSelectedModifyUser':
            e.preventDefault();
            removeSelectedModifyUser(el.dataset.user);
            break;
        }
      });

      // Separate change handler for checkboxes with data-action
      document.addEventListener('change', function (e) {
        const el = e.target.closest('[data-action]');
        if (!el) return;
        const action = el.dataset.action;

        switch (action) {
          case 'toggleSelection':
            if (el.checked) {
              addToSelection(el.dataset.path);
            } else {
              removeFromSelection(el.dataset.path);
            }
            break;
          case 'toggleUserSelection':
            toggleUserSelection(el.dataset.user);
            break;
          case 'toggleModifyUserSelection':
            toggleModifyUserSelection(el.dataset.user);
            break;
          case 'toggleFileSelection':
            toggleFileSelection(el.dataset.path);
            break;
        }
      });
    });