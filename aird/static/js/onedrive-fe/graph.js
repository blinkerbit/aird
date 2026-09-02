'use strict';

import { getConflictPolicy, graphConflictBehavior, getDefaultSave, setDefaultSave } from './settings.js';

const GRAPH = 'https://graph.microsoft.com/v1.0';

export async function graph(token, path, opts = {}) {
  const res = await fetch(`${GRAPH}${path}`, Object.assign({
    headers: Object.assign(
      { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      opts.headers || {},
    ),
  }, opts));
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error?.message || data.error || res.statusText);
  return data;
}

function encPath(p) {
  return String(p || '').split('/').filter(Boolean).map(encodeURIComponent).join('/');
}

export async function listChildren(token, itemId) {
  const path = itemId && itemId !== 'root'
    ? `/me/drive/items/${itemId}/children?$select=id,name,folder,file`
    : '/me/drive/root/children?$select=id,name,folder,file';
  const data = await graph(token, path);
  return data.value || [];
}

export async function listFolders(token, itemId) {
  return (await listChildren(token, itemId)).filter((item) => item.folder);
}

async function createFolder(token, parentId, name) {
  const url = parentId && parentId !== 'root'
    ? `/me/drive/items/${parentId}/children`
    : '/me/drive/root/children';
  return graph(token, url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, folder: {}, '@microsoft.graph.conflictBehavior': 'fail' }),
  });
}

export async function ensureFolderPath(token, parts) {
  let parentId = 'root';
  for (const name of parts) {
    const kids = await listChildren(token, parentId);
    let folder = kids.find((k) => k.folder && k.name === name);
    if (!folder) folder = await createFolder(token, parentId, name);
    parentId = folder.id;
  }
  return parentId;
}

export async function resolveDefaultFolderId(token) {
  const saved = getDefaultSave();
  if (saved.folderId) return saved.folderId;
  if (!saved.path) return null;
  const folderId = await ensureFolderPath(token, saved.path.split('/').filter(Boolean));
  setDefaultSave({ path: saved.path, folderId });
  return folderId;
}

export async function uploadBlob(token, parentId, filename, blob, onProgress) {
  const policy = graphConflictBehavior(getConflictPolicy());
  const safe = encodeURIComponent(filename);
  const target = parentId && parentId !== 'root'
    ? `/me/drive/items/${parentId}:/${safe}:/content`
    : `/me/drive/root:/${safe}:/content`;
  if (onProgress) onProgress({ file: filename, phase: 'uploading' });
  const res = await fetch(`${GRAPH}${target}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': blob.type || 'application/octet-stream',
    },
    body: blob,
  });
  if (res.status === 409 && policy === 'fail') return { skipped: true, name: filename };
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    if (policy === 'rename' && res.status === 409) {
      const alt = `${filename.replace(/(\.[^.]+)?$/, '')}-${Date.now()}${(filename.match(/\.[^.]+$/) || [''])[0]}`;
      return uploadBlob(token, parentId, alt, blob, onProgress);
    }
    throw new Error(data.error?.message || 'Upload failed');
  }
  if (onProgress) onProgress({ file: filename, phase: 'done' });
  return { skipped: false, name: filename };
}

function downloadUrl(relPath) {
  return `/files/${encPath(relPath)}?download=1`;
}

async function fetchAirdBlob(relPath) {
  const res = await fetch(downloadUrl(relPath), { credentials: 'same-origin' });
  if (!res.ok) throw new Error('Could not read file from Aird');
  return res.blob();
}

async function listAirdDir(relPath) {
  const res = await fetch(`/api/files/${encPath(relPath)}`, { credentials: 'same-origin' });
  if (!res.ok) throw new Error('Could not list folder');
  const data = await res.json();
  return data.files || [];
}

export async function uploadTree(token, parentId, relPath, isDir, onProgress) {
  const name = relPath.split('/').filter(Boolean).pop() || relPath;
  if (!isDir) {
    const blob = await fetchAirdBlob(relPath);
    await uploadBlob(token, parentId, name, blob, onProgress);
    return 1;
  }
  const folder = await createFolder(token, parentId, name).catch(async () => {
    const kids = await listFolders(token, parentId);
    return kids.find((k) => k.name === name);
  });
  if (!folder?.id) throw new Error('Could not create OneDrive folder');
  let count = 0;
  const children = await listAirdDir(relPath);
  for (const child of children) {
    const childPath = relPath ? `${relPath}/${child.name}` : child.name;
    count += await uploadTree(token, folder.id, childPath, !!child.is_dir, onProgress);
  }
  return count;
}

export function showFolderPicker({ token, title, onPick }) {
  return new Promise((resolve, reject) => {
    const overlay = document.createElement('div');
    overlay.className = 'od-fe-picker-overlay';
    overlay.innerHTML = `
      <div class="od-fe-picker-panel">
        <div class="od-fe-picker-head">
          <strong>${title || 'Choose OneDrive folder'}</strong>
          <button type="button" class="btn btn-ghost btn-xs od-fe-pick-close">✕</button>
        </div>
        <div class="od-fe-pick-crumbs text-xs px-4 py-2 opacity-70"></div>
        <div class="od-fe-pick-list"></div>
        <div class="od-fe-picker-foot">
          <button type="button" class="btn btn-ghost btn-sm od-fe-pick-new">New folder</button>
          <button type="button" class="btn btn-primary btn-sm od-fe-pick-save">Use this folder</button>
        </div>
        <p class="od-fe-pick-msg text-xs px-4 pb-3"></p>
      </div>`;
    document.body.appendChild(overlay);
    const stack = [{ id: 'root', name: 'OneDrive' }];
    const listEl = overlay.querySelector('.od-fe-pick-list');
    const crumbEl = overlay.querySelector('.od-fe-pick-crumbs');
    const msgEl = overlay.querySelector('.od-fe-pick-msg');

    function say(text, err) {
      msgEl.textContent = text || '';
      msgEl.style.color = err ? 'var(--color-error,#b91c1c)' : '';
    }

    async function render() {
      crumbEl.textContent = stack.map((s) => s.name).join(' / ');
      listEl.textContent = 'Loading…';
      try {
        const folders = await listFolders(token, stack.at(-1).id);
        listEl.innerHTML = '';
        if (stack.length > 1) {
          const up = document.createElement('button');
          up.type = 'button';
          up.className = 'btn btn-ghost btn-sm w-full justify-start';
          up.textContent = '← Up';
          up.addEventListener('click', () => { stack.pop(); render(); });
          listEl.append(up);
        }
        folders.forEach((folder) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-ghost btn-sm w-full justify-start';
          btn.textContent = `📁 ${folder.name}`;
          btn.addEventListener('click', () => { stack.push({ id: folder.id, name: folder.name }); render(); });
          listEl.append(btn);
        });
        if (!folders.length) {
          const empty = document.createElement('p');
          empty.className = 'text-sm opacity-60 px-2';
          empty.textContent = 'No subfolders.';
          listEl.append(empty);
        }
      } catch (err) {
        listEl.textContent = '';
        say(err.message, true);
      }
    }

    overlay.querySelector('.od-fe-pick-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    overlay.querySelector('.od-fe-pick-new').addEventListener('click', async () => {
      const name = globalThis.prompt('New folder name');
      if (!name?.trim()) return;
      try {
        const created = await createFolder(token, stack.at(-1).id, name.trim());
        stack.push({ id: created.id, name: created.name || name.trim() });
        await render();
      } catch (err) {
        say(err.message, true);
      }
    });
    overlay.querySelector('.od-fe-pick-save').addEventListener('click', async () => {
      try {
        say('Working…');
        const result = await onPick(stack.at(-1).id, stack.map((s) => s.name));
        overlay.remove();
        resolve(result);
      } catch (err) {
        say(err.message, true);
        reject(err);
      }
    });
    render();
  });
}

export async function downloadItemText(token, itemId) {
  const res = await fetch(`${GRAPH}/me/drive/items/${itemId}/content`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.error?.message || 'Download failed');
  }
  return res.text();
}

export function showFilePicker({ token, title, filter, onPick }) {
  return new Promise((resolve, reject) => {
    const overlay = document.createElement('div');
    overlay.className = 'od-fe-picker-overlay';
    overlay.innerHTML = `
      <div class="od-fe-picker-panel">
        <div class="od-fe-picker-head">
          <strong>${title || 'Open from OneDrive'}</strong>
          <button type="button" class="btn btn-ghost btn-xs od-fe-pick-close">✕</button>
        </div>
        <div class="od-fe-pick-crumbs text-xs px-4 py-2 opacity-70"></div>
        <div class="od-fe-pick-list"></div>
        <p class="od-fe-pick-msg text-xs px-4 pb-3"></p>
      </div>`;
    document.body.appendChild(overlay);
    const stack = [{ id: 'root', name: 'OneDrive' }];
    const listEl = overlay.querySelector('.od-fe-pick-list');
    const crumbEl = overlay.querySelector('.od-fe-pick-crumbs');
    const msgEl = overlay.querySelector('.od-fe-pick-msg');

    function say(text, err) {
      msgEl.textContent = text || '';
      msgEl.style.color = err ? 'var(--color-error,#b91c1c)' : '';
    }

    async function render() {
      crumbEl.textContent = stack.map((s) => s.name).join(' / ');
      listEl.textContent = 'Loading…';
      try {
        const items = await listChildren(token, stack.at(-1).id);
        const folders = items.filter((item) => item.folder);
        const files = items.filter((item) => item.file && (!filter || filter(item)));
        listEl.innerHTML = '';
        if (stack.length > 1) {
          const up = document.createElement('button');
          up.type = 'button';
          up.className = 'btn btn-ghost btn-sm w-full justify-start';
          up.textContent = '← Up';
          up.addEventListener('click', () => { stack.pop(); render(); });
          listEl.append(up);
        }
        folders.forEach((folder) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-ghost btn-sm w-full justify-start';
          btn.textContent = `📁 ${folder.name}`;
          btn.addEventListener('click', () => { stack.push({ id: folder.id, name: folder.name }); render(); });
          listEl.append(btn);
        });
        files.forEach((file) => {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'btn btn-ghost btn-sm w-full justify-start';
          btn.textContent = `📄 ${file.name}`;
          btn.addEventListener('click', async () => {
            try {
              say('Loading…');
              await onPick(file);
              overlay.remove();
              resolve();
            } catch (err) {
              say(err.message, true);
              reject(err);
            }
          });
          listEl.append(btn);
        });
        if (!folders.length && !files.length) {
          const empty = document.createElement('p');
          empty.className = 'text-sm opacity-60 px-2';
          empty.textContent = 'This folder is empty.';
          listEl.append(empty);
        }
      } catch (err) {
        listEl.textContent = '';
        say(err.message, true);
      }
    }

    overlay.querySelector('.od-fe-pick-close').addEventListener('click', () => overlay.remove());
    overlay.addEventListener('click', (e) => { if (e.target === overlay) overlay.remove(); });
    render();
  });
}
