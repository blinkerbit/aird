import {
  cloudElements,
  cloudState,
  selectedFiles,
} from './state.js';
import { getXSRFToken, formatFileSize, showDialog } from './utils.js';
import { addToSelection, removeFromSelection } from './selection.js';

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

function wireCloudEvents() {
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
}

export {
  openCloudBrowser,
  closeCloudBrowser,
  switchCloudProvider,
  cloudNavigateUp,
  handleCloudUpload,
  wireCloudEvents,
};
