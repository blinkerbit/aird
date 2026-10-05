/**
 * Chat file attach: browse picker, drop zone, and pending chips.
 * Isolated so browse/drop cannot silently regress when chat UI changes.
 */

export const ATTACH_LIMIT = 10;

export function trimBrowsePath(path) {
  let s = String(path || '').trim().replaceAll('\\', '/');
  while (s.startsWith('/')) s = s.slice(1);
  while (s.endsWith('/')) s = s.slice(0, -1);
  return s;
}

export function normalizeRel(path) {
  const parts = [];
  for (const seg of trimBrowsePath(path).split('/')) {
    if (!seg || seg === '.') continue;
    if (seg === '..') {
      parts.pop();
      continue;
    }
    parts.push(seg);
  }
  return parts.join('/');
}

function trimSlashes(value) {
  let text = String(value || '');
  while (text.startsWith('/')) text = text.slice(1);
  while (text.endsWith('/')) text = text.slice(0, -1);
  return text;
}

export function joinRel(dir, name) {
  const d = normalizeRel(dir);
  const n = trimSlashes(name);
  if (!d) return n;
  if (!n) return d;
  return `${d}/${n}`;
}

export function parsePathInput(raw) {
  let s = String(raw || '').replaceAll('\\', '/').trim();
  if (s.startsWith('~/')) s = s.slice(2);
  while (s.startsWith('/')) s = s.slice(1);
  const dirEnds = !s || s.endsWith('/');
  const parts = s.split('/').filter(Boolean);
  if (dirEnds) return { dir: parts.join('/'), prefix: '' };
  return { dir: parts.slice(0, -1).join('/'), prefix: parts[parts.length - 1] || '' };
}

export function formatRelPath(dir, name, isDir) {
  const body = joinRel(dir, name);
  if (!body) return '/';
  return `/${body}${isDir ? '/' : ''}`;
}

export function sameBrowseValue(a, b) {
  const norm = (v) => {
    const s = String(v || '').replaceAll('\\', '/').trim();
    const slash = s.endsWith('/') && s !== '/';
    const rel = normalizeRel(s);
    return `${rel}${slash ? '/' : ''}`;
  };
  return norm(a) === norm(b);
}

export function restyleCompleted(token, value) {
  const body = String(value || '').replace(/^\/+/, '');
  if (token.startsWith('./')) return `./${body}`;
  if (token.startsWith('/')) return `/${body}`;
  return body;
}

export function commonPrefix(names) {
  if (!names.length) return '';
  let p = names[0];
  for (const n of names.slice(1)) {
    let i = 0;
    const max = Math.min(p.length, n.length);
    while (i < max && p[i].toLowerCase() === n[i].toLowerCase()) i += 1;
    p = p.slice(0, i);
  }
  return p;
}

export function matchFiles(files, prefix) {
  const q = String(prefix || '').toLowerCase();
  return files
    .filter((f) => !q || f.name.toLowerCase().startsWith(q))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
}

export function looksLikePath(token) {
  const t = String(token || '').trim();
  if (!t || t.startsWith('@') || /^[a-z]+:\/\//i.test(t) || t.startsWith('mailto:')) return false;
  if (t.includes('/') || t.includes('\\')) return true;
  if (t.startsWith('./') || t.startsWith('../')) return true;
  if (/^\.[A-Za-z0-9._-]+/.test(t)) return true;
  return false;
}

export function filesApiUrl(relPath) {
  const key = normalizeRel(relPath);
  if (!key) return '/api/files/';
  return `/api/files/${key.split('/').map(encodeURIComponent).join('/')}`;
}

export function attachAllowed({ enhanced = false, convId = '' } = {}) {
  if (enhanced) {
    return { ok: false, reason: 'Enhanced Secure Chat is text only. Open the normal chat to share files.' };
  }
  if (!convId) {
    return { ok: false, reason: 'Open a conversation before attaching files.' };
  }
  return { ok: true, reason: '' };
}

export function isFileDrag(event) {
  return [...(event?.dataTransfer?.types || [])].includes('Files');
}

export function droppedFiles(dataTransfer) {
  const files = [...(dataTransfer?.files || [])];
  if (files.length) return files;
  return [...(dataTransfer?.items || [])]
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean);
}

export function clipboardFiles(clipboardData) {
  if (!clipboardData) return [];
  const fromList = [...(clipboardData.files || [])].filter(Boolean);
  if (fromList.length) return fromList;
  return [...(clipboardData.items || [])]
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter(Boolean);
}

const PENDING_ATTACH_KEY = 'aird.chat.pendingAttach';
const PENDING_ATTACH_BATCH_KEY = 'aird.chat.pendingAttachBatch';

export function queueBrowseAttach(path, isDir = false) {
  queueBrowseAttachBatch([{ path, isDir }]);
}

export function queueBrowseAttachBatch(entries) {
  const list = (entries || [])
    .map(function (e) { return { path: normalizeRel(e.path), isDir: !!e.isDir }; })
    .filter(function (e) { return e.path; });
  if (!list.length) return;
  try {
    sessionStorage.setItem(PENDING_ATTACH_BATCH_KEY, JSON.stringify(list));
    sessionStorage.removeItem(PENDING_ATTACH_KEY);
  } catch { /* ignore */ }
  const q = new URLSearchParams();
  q.set('attachBatch', '1');
  const conv = new URLSearchParams(globalThis.location.search).get('c');
  if (conv) q.set('c', conv);
  globalThis.location.href = `/chat?${q}`;
}

export function takePendingBrowseAttachAll() {
  try {
    const batch = JSON.parse(sessionStorage.getItem(PENDING_ATTACH_BATCH_KEY) || 'null');
    if (Array.isArray(batch) && batch.length) {
      sessionStorage.removeItem(PENDING_ATTACH_BATCH_KEY);
      return batch.map(function (e) {
        return { path: normalizeRel(e.path), isDir: !!e.isDir };
      }).filter(function (e) { return e.path; });
    }
  } catch {
    /* ignore */
  }
  const params = new URLSearchParams(globalThis.location.search);
  const attach = params.get('attach');
  if (attach) {
    return [{ path: normalizeRel(attach), isDir: params.get('attachDir') === '1' }];
  }
  try {
    const stored = JSON.parse(sessionStorage.getItem(PENDING_ATTACH_KEY) || 'null');
    sessionStorage.removeItem(PENDING_ATTACH_KEY);
    if (stored?.path) {
      return [{ path: normalizeRel(stored.path), isDir: !!stored.isDir }];
    }
  } catch {
    /* ignore */
  }
  return [];
}

export function takePendingBrowseAttach() {
  const all = takePendingBrowseAttachAll();
  return all[0] || null;
}

export function createPendingStore({ limit = ATTACH_LIMIT } = {}) {
  const items = [];

  function stagePath(relativePath, opts = {}) {
    const path = normalizeRel(relativePath);
    if (!path) return { ok: false, reason: 'Choose a file or folder.' };
    if (items.some((p) => p.path === path)) return { ok: true, duplicate: true };
    if (items.length >= limit) return { ok: false, reason: `Up to ${limit} files per message` };
    const isDir = !!opts.isDir;
    const base = path.split('/').pop();
    items.push({ id: `p:${path}`, name: isDir ? `${base}/` : base, path, isDir });
    return { ok: true };
  }

  function stageBlob(file) {
    if (!file) return { ok: false, reason: 'No file.' };
    if (items.length >= limit) return { ok: false, reason: `Up to ${limit} files per message` };
    items.push({ id: `b:${Date.now()}:${file.name}:${items.length}`, name: file.name, file });
    return { ok: true };
  }

  function remove(id) {
    const i = items.findIndex((x) => x.id === id);
    if (i >= 0) items.splice(i, 1);
  }

  function clear() {
    items.splice(0, items.length);
  }

  return {
    items,
    stagePath,
    stageBlob,
    remove,
    clear,
    take() {
      const copy = items.slice();
      clear();
      return copy;
    },
  };
}

export function completePath(raw, files) {
  const parsed = parsePathInput(raw);
  const dir = normalizeRel(parsed.dir);
  const hits = matchFiles(files || [], parsed.prefix);
  if (!hits.length) return { dir, prefix: parsed.prefix, hits, value: raw, unique: false };
  const labels = hits.map((f) => `${f.name}${f.is_dir ? '/' : ''}`);
  let filled = parsed.prefix;
  if (hits.length === 1) filled = labels[0];
  else {
    const common = commonPrefix(labels);
    if (common.length > parsed.prefix.length) filled = common;
  }
  const dirSlash = filled.endsWith('/');
  let filledPath = filled;
  while (filledPath.endsWith('/')) filledPath = filledPath.slice(0, -1);
  const body = joinRel(dir, filledPath);
  let value = '/';
  if (body) value = `/${body}${dirSlash ? '/' : ''}`;
  return { dir, prefix: parsed.prefix, hits, value, unique: hits.length === 1 };
}

export function renderBrowseRows(files, dir, activeName) {
  const rows = files || [];
  const active = activeName || (rows[0] && rows[0].name) || '';
  return rows.map((f) => {
    const full = joinRel(dir, f.name);
    return {
      name: f.name,
      path: full,
      isDir: !!f.is_dir,
      active: f.name === active,
    };
  });
}

export function openDialog(dialog) {
  if (!dialog) return false;
  dialog.classList.add('is-open');
  if (typeof dialog.showModal === 'function') {
    try {
      if (!dialog.open) dialog.showModal();
    } catch (err) {
      console.debug('showModal failed', err);
      dialog.setAttribute('open', '');
    }
  } else {
    dialog.setAttribute('open', '');
  }
  return !!(dialog.open || dialog.hasAttribute('open'));
}

export function closeDialog(dialog) {
  if (!dialog) return;
  dialog.classList.remove('is-open');
  if (typeof dialog.close === 'function' && dialog.open) {
    try { dialog.close(); } catch (err) {
      console.debug('dialog.close failed', err);
      dialog.removeAttribute('open');
    }
    return;
  }
  dialog.removeAttribute('open');
}

export function bindDropZone(target, {
  canAttach,
  onFiles,
  onBlocked,
} = {}) {
  if (!target) return () => {};
  let depth = 0;

  function clear() {
    depth = 0;
    target.classList.remove('chat-drop');
  }

  function onEnter(e) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    depth += 1;
    target.classList.add('chat-drop');
  }

  function onOver(e) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    target.classList.add('chat-drop');
  }

  function onLeave() {
    if (!depth) return;
    depth = Math.max(0, depth - 1);
    if (!depth) target.classList.remove('chat-drop');
  }

  function onDrop(e) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    clear();
    const gate = canAttach ? canAttach() : { ok: true };
    if (!gate.ok) {
      onBlocked?.(gate.reason || 'Cannot attach files here.');
      return;
    }
    const files = droppedFiles(e.dataTransfer);
    if (!files.length) {
      onBlocked?.('No supported files were found in the drop.');
      return;
    }
    onFiles?.(files);
  }

  target.addEventListener('dragenter', onEnter);
  target.addEventListener('dragover', onOver);
  target.addEventListener('dragleave', onLeave);
  target.addEventListener('drop', onDrop);
  const onDocEnd = () => clear();
  document.addEventListener('dragend', onDocEnd);

  return () => {
    target.removeEventListener('dragenter', onEnter);
    target.removeEventListener('dragover', onOver);
    target.removeEventListener('dragleave', onLeave);
    target.removeEventListener('drop', onDrop);
    document.removeEventListener('dragend', onDocEnd);
    clear();
  };
}

export function createBrowsePicker({
  modal,
  list,
  pathInput,
  upBtn,
  closeBtn,
  attachBtn,
  listDir,
  onAttach,
  onError,
} = {}) {
  let browsePath = '';
  let browseHits = [];
  let browseActiveName = '';
  const dirCache = new Map();

  async function filesFor(relPath, { cache = true } = {}) {
    const key = normalizeRel(relPath);
    const hit = dirCache.get(key);
    if (cache && hit && Date.now() - hit.ts < 8000) return hit.files;
    const files = await listDir(key);
    if (!Array.isArray(files)) throw new Error('Browse list is invalid');
    dirCache.set(key, { files, ts: Date.now() });
    return files;
  }

  function onBrowseShareClick(e) {
    e.preventDefault();
    e.stopPropagation();
    const btn = e.currentTarget;
    attach(btn.dataset.path || '', true);
  }

  function onBrowseRowClick(e) {
    const row = e.currentTarget;
    if (e.target.closest('.chat-browse-share')) return;
    const p = row.dataset.path || '';
    if (row.dataset.action === 'dir') {
      const name = p.split('/').filter(Boolean).pop() || '';
      browseActiveName = name;
      list.querySelectorAll('.chat-browse-row').forEach((el) => {
        el.classList.toggle('is-active', el === row);
      });
      if (pathInput) pathInput.value = formatRelPath(browsePath, name, false);
      return;
    }
    attach(p, false);
  }

  function onBrowseRowDblClick(e) {
    const row = e.currentTarget;
    if (e.target.closest('.chat-browse-share')) return;
    if (row.dataset.action === 'dir') {
      load(row.dataset.path || '').catch((err) => onError?.(err.message));
    }
  }

  function wireBrowseRow(row) {
    row.addEventListener('click', onBrowseRowClick);
    row.addEventListener('dblclick', onBrowseRowDblClick);
  }

  function paint(files, dir, activeName) {
    browseHits = files.slice();
    const rows = renderBrowseRows(files, dir, activeName);
    browseActiveName = rows.find((r) => r.active)?.name || '';
    if (!list) return;
    if (!rows.length) {
      list.innerHTML = '<p class="chat-file-picker-empty">No matches</p>';
      return;
    }
    list.innerHTML = rows.map((r) => {
      const share = r.isDir
        ? `<button type="button" class="chat-browse-share" data-path="${escapeAttr(r.path)}" data-dir="1">Share</button>`
        : '';
      return `<div role="option" class="chat-browse-row${r.active ? ' is-active' : ''}" data-action="${r.isDir ? 'dir' : 'file'}" data-path="${escapeAttr(r.path)}"><span>${r.isDir ? '📁' : '📄'}</span><span class="truncate">${escapeText(r.name)}${r.isDir ? '/' : ''}</span>${share}</div>`;
    }).join('');
    list.querySelectorAll('.chat-browse-row').forEach(wireBrowseRow);
    list.querySelectorAll('.chat-browse-share').forEach((btn) => {
      btn.addEventListener('click', onBrowseShareClick);
    });
  }

  function attach(path, isDir) {
    const rel = normalizeRel(path);
    if (!rel) {
      onError?.('Choose a file or folder.');
      return false;
    }
    onAttach?.(rel, !!isDir);
    close();
    return true;
  }

  async function load(path, opts = {}) {
    browsePath = normalizeRel(path);
    if (!opts.keepInput && pathInput) pathInput.value = formatRelPath(browsePath, '', true);
    const files = await filesFor(browsePath);
    const hits = matchFiles(files, opts.filter || '');
    paint(hits, browsePath, opts.activeName || (hits[0] && hits[0].name) || '');
    return browseHits;
  }

  async function open() {
    dirCache.clear();
    if (!openDialog(modal)) {
      onError?.('Could not open the file picker.');
      return false;
    }
    try {
      await load('');
      pathInput?.focus();
      pathInput?.select();
      return true;
    } catch (err) {
      if (list) list.innerHTML = `<p class="chat-file-picker-empty">${escapeText(err.message || 'Could not list files')}</p>`;
      onError?.(err.message || 'Could not list files');
      return false;
    }
  }

  function close() {
    closeDialog(modal);
  }

  async function onTab(shift) {
    if (!pathInput) return;
    const raw = pathInput.value;
    const files = await filesFor(parsePathInput(raw).dir);
    const result = completePath(raw, files);
    if (!result.hits.length) return false;
    if (result.unique) {
      const hit = result.hits[0];
      const full = joinRel(result.dir, hit.name);
      if (hit.is_dir) {
        await load(full);
      } else {
        pathInput.value = result.value;
        await load(result.dir, { keepInput: true, filter: hit.name });
      }
      return true;
    }
    if (sameBrowseValue(raw, result.value)) {
      const delta = shift ? -1 : 1;
      let i = result.hits.findIndex((f) => formatRelPath(result.dir, f.name, false) === formatRelPath(result.dir, browseActiveName, false));
      if (i < 0) i = 0;
      i = (i + delta + result.hits.length) % result.hits.length;
      const hit = result.hits[i];
      pathInput.value = formatRelPath(result.dir, hit.name, false);
      paint(result.hits, result.dir, hit.name);
      return true;
    }
    pathInput.value = result.value;
    await load(result.dir, { keepInput: true, filter: parsePathInput(result.value).prefix });
    return true;
  }

  function move(delta) {
    if (!browseHits.length) return;
    let i = browseHits.findIndex((f) => f.name === browseActiveName);
    if (i < 0) i = 0;
    i = (i + delta + browseHits.length) % browseHits.length;
    const hit = browseHits[i];
    paint(browseHits, browsePath, hit.name);
    if (pathInput) pathInput.value = formatRelPath(browsePath, hit.name, false);
  }

  async function confirm() {
    const active = list?.querySelector('.chat-browse-row.is-active');
    if (active?.dataset.path) {
      return attach(active.dataset.path, active.dataset.action === 'dir');
    }
    if (!pathInput) return false;
    const parsed = parsePathInput(pathInput.value);
    const dir = normalizeRel(parsed.dir);
    const files = await filesFor(dir);
    const exact = parsed.prefix
      ? files.find((f) => f.name.toLowerCase() === parsed.prefix.toLowerCase())
      : null;
    const hits = matchFiles(files, parsed.prefix);
    const pick = exact || hits[0];
    if (!pick) return false;
    return attach(joinRel(dir, pick.name), !!pick.is_dir);
  }

  function goUp() {
    if (!browsePath) return Promise.resolve();
    const parts = browsePath.split('/').filter(Boolean);
    parts.pop();
    return load(parts.join('/'));
  }

  pathInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      onTab(e.shiftKey).catch((err) => onError?.(err.message));
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      move(1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      move(-1);
    } else if (e.key === 'Enter' || e.key === 'NumpadEnter') {
      e.preventDefault();
      confirm().catch((err) => onError?.(err.message));
    } else if (e.key === 'Escape') {
      close();
    }
  });
  pathInput?.addEventListener('input', () => {
    const parsed = parsePathInput(pathInput.value);
    load(parsed.dir, { keepInput: true, filter: parsed.prefix }).catch((err) => onError?.(err.message));
  });
  upBtn?.addEventListener('click', () => goUp().catch((err) => onError?.(err.message)));
  closeBtn?.addEventListener('click', close);
  attachBtn?.addEventListener('click', () => confirm().catch((err) => onError?.(err.message)));

  return {
    open,
    close,
    load,
    confirm,
    goUp,
    move,
    onTab,
    get path() { return browsePath; },
    get hits() { return browseHits.slice(); },
  };
}

function escapeText(s) {
  return String(s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
}

function escapeAttr(s) {
  return escapeText(s).replaceAll('"', '&quot;');
}

const AirdChatAttach = {
  ATTACH_LIMIT,
  trimBrowsePath,
  normalizeRel,
  joinRel,
  parsePathInput,
  formatRelPath,
  sameBrowseValue,
  restyleCompleted,
  commonPrefix,
  matchFiles,
  looksLikePath,
  filesApiUrl,
  attachAllowed,
  isFileDrag,
  droppedFiles,
  createPendingStore,
  completePath,
  renderBrowseRows,
  openDialog,
  closeDialog,
  bindDropZone,
  createBrowsePicker,
};

if (typeof globalThis !== 'undefined') globalThis.AirdChatAttach = AirdChatAttach;

export { AirdChatAttach };
