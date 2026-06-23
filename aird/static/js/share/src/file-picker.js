import { shareVars, selectedFiles, elements } from './state.js';
import { escapeHtml, escapeAttr } from './utils.js';
import { getFileIcon } from './file-icons.js';
import { updateSelectedDisplay, clearSelection } from './selection.js';

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
  if (!shareVars.filePickerLoaded) {
    shareVars.filePickerLoaded = true;
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
    shareVars.currentPath = path;
    updateBreadcrumb(path);
    elements.fileTableBody.innerHTML = '<tr><td colspan="5" class="p-6 text-center text-base-content/50"><span class="loading loading-spinner loading-sm align-middle mr-2"></span><span class="align-middle">Loading…</span></td></tr>';

    const response = await fetch(`/api/files/${path}`);

    if (response.ok) {
      const data = await response.json();
      shareVars.allFiles = data.files;
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
  if (!shareVars.currentPath) {
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
  row.appendChild(_makeCheckboxCell(shareVars.currentPath, selectedFiles.has(shareVars.currentPath)));
  row.appendChild(_makeNameCell({
    isDir: true,
    iconText: '📁',
    name: '(this folder)',
    filePath: shareVars.currentPath,
    isShared: false,
    navPath: shareVars.currentPath,
  }));
  row.appendChild(_makeCell('-'));
  row.appendChild(_makeCell('-'));
  const hintTd = document.createElement('td');
  hintTd.className = 'text-base-content/50 text-xs';
  hintTd.textContent = 'Empty — select to share this folder';
  row.appendChild(hintTd);
  if (selectedFiles.has(shareVars.currentPath)) row.classList.add('selected');
  elements.fileTableBody.appendChild(row);
}

function renderFiles() {
  if (shareVars.allFiles.length === 0) {
    renderEmptyDirectoryRow();
    return;
  }

  elements.fileTableBody.innerHTML = '';

  if (shareVars.currentPath) {
    const parentPath = shareVars.currentPath.split('/').filter(Boolean).slice(0, -1).join('/');
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

  shareVars.allFiles.forEach(file => {
    const row = document.createElement('tr');
    const filePath = shareVars.currentPath ? `${shareVars.currentPath}/${file.name}` : file.name;
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

export {
  updateBreadcrumb,
  openShareFilePicker,
  closeShareFilePicker,
  loadDirectory,
  renderFiles,
};
