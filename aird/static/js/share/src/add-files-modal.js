import { addFilesModalData, shareVars } from './state.js';
import { getXSRFToken, showDialog, escapeHtml, escapeAttr } from './utils.js';
import { getFileIcon } from './file-icons.js';
import { addToSelection } from './selection.js';
import { renderShareManagementModal } from './management.js';
import { openShareConfigModal } from './create-share.js';
import { loadActiveShares } from './shares-list.js';

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
    const base = shareVars.currentShareData.paths ? [...shareVars.currentShareData.paths] : [];
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
          share_id: shareVars.currentShareData.id,
          paths: merged
        })
      });
      const data = await response.json();
      if (data.error) {
        showDialog('Error: ' + data.error, 'Error');
        return;
      }
      shareVars.currentShareData.paths = data.updated_paths || merged;
      showDialog('Files added to share.', 'Success');
      renderShareManagementModal(shareVars.currentShareData);
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

export {
  showAddFilesModal,
  closeAddFilesModal,
  loadFilesForAddModal,
  renderFilesForAddModal,
  toggleFileSelection,
  updateSelectedFilesPreview,
  removeFromAddModalSelection,
  navigateUp,
  navigateToDirectory,
  addSelectedFilesToShare,
};
