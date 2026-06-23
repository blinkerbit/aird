import {
  shareVars,
  selectedFiles,
  selectedFileMetadata,
  elements,
  selectedModifyUsers,
} from './state.js';
import { escapeHtml, findCheckboxByValue } from './utils.js';
import { updateSelectedModifyUsersDisplay } from './create-users.js';

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
      const isDir = file.endsWith('/') || shareVars.allFiles.some(f => f.name === fileName && f.is_dir);
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

export {
  buildFileChips,
  updateSelectedDisplay,
  updateConfigSelectedFiles,
  addToSelection,
  removeFromSelection,
  clearSelection,
  selectAllVisible,
};
