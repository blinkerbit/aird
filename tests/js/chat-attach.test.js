import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import {
  attachAllowed,
  bindDropZone,
  clipboardFiles,
  completePath,
  createBrowsePicker,
  createPendingStore,
  droppedFiles,
  filesApiUrl,
  formatRelPath,
  isFileDrag,
  joinRel,
  looksLikePath,
  matchFiles,
  normalizeRel,
  openDialog,
  parsePathInput,
  renderBrowseRows,
  takePendingBrowseAttach,
} from '/static/js/chat-attach.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

describe('chat attach path helpers', () => {
  it('normalizes browse-relative paths', () => {
    expect(normalizeRel('/docs/./a/../b/')).toBe('docs/b');
    expect(joinRel('docs', 'note.txt')).toBe('docs/note.txt');
    expect(parsePathInput('/docs/re')).toEqual({ dir: 'docs', prefix: 're' });
    expect(parsePathInput('/docs/')).toEqual({ dir: 'docs', prefix: '' });
    expect(formatRelPath('docs', 'note.txt', false)).toBe('/docs/note.txt');
    expect(filesApiUrl('')).toBe('/api/files/');
    expect(filesApiUrl('docs/a b')).toBe('/api/files/docs/a%20b');
  });

  it('completes a unique folder path', () => {
    const result = completePath('/do', [
      { name: 'docs', is_dir: true },
      { name: 'readme.txt', is_dir: false },
    ]);
    expect(result.unique).toBe(true);
    expect(result.value).toBe('/docs/');
  });

  it('filters and sorts browse hits', () => {
    const hits = matchFiles(
      [{ name: 'Zed' }, { name: 'alpha' }, { name: 'Beta' }],
      'b',
    );
    expect(hits.map((f) => f.name)).toEqual(['Beta']);
  });

  it('detects typed file paths', () => {
    expect(looksLikePath('/docs/a')).toBe(true);
    expect(looksLikePath('@bob')).toBe(false);
    expect(looksLikePath('https://x')).toBe(false);
  });
});

describe('chat attach gates', () => {
  it('blocks attach in enhanced chat and without a conversation', () => {
    expect(attachAllowed({ enhanced: true, convId: 'c1' }).ok).toBe(false);
    expect(attachAllowed({ enhanced: false, convId: '' }).ok).toBe(false);
    expect(attachAllowed({ enhanced: false, convId: 'c1' }).ok).toBe(true);
  });

  it('reads dropped files from files or items', () => {
    const file = new File(['x'], 'a.txt');
    expect(droppedFiles({ files: [file] })).toHaveLength(1);
    expect(droppedFiles({
      files: [],
      items: [{ kind: 'file', getAsFile: () => file }, { kind: 'string', getAsFile: () => null }],
    })).toEqual([file]);
    expect(isFileDrag({ dataTransfer: { types: ['Files'] } })).toBe(true);
    expect(isFileDrag({ dataTransfer: { types: ['text/plain'] } })).toBe(false);
  });

  it('reads pasted files from clipboard files or items', () => {
    const img = new File(['x'], 'shot.png', { type: 'image/png' });
    const txt = new File(['y'], 'note.txt', { type: 'text/plain' });
    expect(clipboardFiles({ files: [img, txt] }).map((f) => f.name)).toEqual(['shot.png', 'note.txt']);
    expect(clipboardFiles({
      files: [],
      items: [{ kind: 'file', getAsFile: () => img }, { kind: 'string', getAsFile: () => null }],
    })).toEqual([img]);
  });
});

describe('chat pending store', () => {
  it('stages files and folders up to the limit', () => {
    const store = createPendingStore({ limit: 2 });
    expect(store.stagePath('docs/a.txt').ok).toBe(true);
    expect(store.stagePath('docs/a.txt').duplicate).toBe(true);
    expect(store.stageBlob(new File(['x'], 'b.txt')).ok).toBe(true);
    expect(store.stageBlob(new File(['y'], 'c.txt')).ok).toBe(false);
    expect(store.items).toHaveLength(2);
    const taken = store.take();
    expect(taken).toHaveLength(2);
    expect(store.items).toHaveLength(0);
  });

  it('reads a pending browse attach from sessionStorage', () => {
    sessionStorage.setItem('aird.chat.pendingAttach', JSON.stringify({ path: 'docs/a.txt', isDir: false }));
    expect(takePendingBrowseAttach()).toEqual({ path: 'docs/a.txt', isDir: false });
    expect(sessionStorage.getItem('aird.chat.pendingAttach')).toBeNull();
  });
});

describe('chat browse picker', () => {
  it('opens the picker, lists files, and attaches on click', async () => {
    document.body.innerHTML = `
      <dialog id="picker"><div id="list"></div>
        <input id="path" /><button id="up"></button><button id="close"></button>
        <button id="attach"></button>
      </dialog>`;
    const attached = [];
    const picker = createBrowsePicker({
      modal: document.getElementById('picker'),
      list: document.getElementById('list'),
      pathInput: document.getElementById('path'),
      upBtn: document.getElementById('up'),
      closeBtn: document.getElementById('close'),
      attachBtn: document.getElementById('attach'),
      listDir: async (rel) => {
        if (!rel) return [{ name: 'docs', is_dir: true }, { name: 'hi.txt', is_dir: false }];
        if (rel === 'docs') return [{ name: 'inside.txt', is_dir: false }];
        return [];
      },
      onAttach: (p, isDir) => attached.push({ p, isDir }),
    });

    expect(await picker.open()).toBe(true);
    expect(document.getElementById('picker').open).toBe(true);
    expect(picker.hits.map((f) => f.name)).toEqual(['docs', 'hi.txt']);
    document.querySelector('[data-path="hi.txt"]').click();
    expect(attached).toEqual([{ p: 'hi.txt', isDir: false }]);
    expect(document.getElementById('picker').open).toBe(false);

    expect(await picker.open()).toBe(true);
    document.querySelector('[data-path="docs"]').click();
    expect(document.getElementById('path').value).toBe('/docs');
    document.querySelector('[data-path="docs"]').dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    await vi.waitFor(() => {
      expect(document.querySelector('[data-path="docs/inside.txt"]')).toBeTruthy();
    });
    expect(picker.path).toBe('docs');
    expect(renderBrowseRows(picker.hits, picker.path)[0].path).toBe('docs/inside.txt');
    document.getElementById('attach').click();
    await vi.waitFor(() => expect(attached.at(-1)).toEqual({ p: 'docs/inside.txt', isDir: false }));
  });

  it('surfaces a listDir failure instead of an empty success', async () => {
    document.body.innerHTML = `<dialog id="picker"><div id="list"></div><input id="path" /></dialog>`;
    const errors = [];
    const picker = createBrowsePicker({
      modal: document.getElementById('picker'),
      list: document.getElementById('list'),
      pathInput: document.getElementById('path'),
      listDir: async () => { throw new Error('HTTP 403'); },
      onError: (msg) => errors.push(msg),
    });
    expect(await picker.open()).toBe(false);
    expect(document.getElementById('list').textContent).toMatch(/403/);
    expect(errors[0]).toMatch(/403/);
  });
});

describe('chat drop zone', () => {
  it('stages files only when attach is allowed', () => {
    document.body.innerHTML = '<div id="zone"></div>';
    const zone = document.getElementById('zone');
    const received = [];
    const blocked = [];
    const unbind = bindDropZone(zone, {
      canAttach: () => attachAllowed({ enhanced: false, convId: 'c1' }),
      onFiles: (files) => received.push(...files),
      onBlocked: (msg) => blocked.push(msg),
    });
    const file = new File(['x'], 'drop.txt');
    const dt = { types: ['Files'], files: [file], dropEffect: 'none' };
    zone.dispatchEvent(Object.assign(new Event('dragover', { bubbles: true, cancelable: true }), { dataTransfer: dt }));
    expect(zone.classList.contains('chat-drop')).toBe(true);
    zone.dispatchEvent(Object.assign(new Event('drop', { bubbles: true, cancelable: true }), { dataTransfer: dt }));
    expect(received.map((f) => f.name)).toEqual(['drop.txt']);
    expect(zone.classList.contains('chat-drop')).toBe(false);
    unbind();
    expect(blocked).toEqual([]);
  });
});

describe('chat page attach contract', () => {
  it('keeps browse, upload, and picker markup wired for the attach module', () => {
    const html = readFileSync(path.join(root, 'aird/templates/chat.html'), 'utf8');
    const js = readFileSync(path.join(root, 'aird/static/js/pages/chat.js'), 'utf8');
    expect(html).toContain('id="chatBrowseBtn"');
    expect(html).toContain('id="chatFileInput"');
    expect(html).toContain('id="chatBrowseModal"');
    expect(html).toContain('id="chatBrowseList"');
    expect(html).toContain('id="chatBrowsePath"');
    expect(html).toContain('id="chatBrowseAttach"');
    expect(html).toContain('class="chat-file-picker"');
    expect(html).not.toContain('class="chat-attach-menu"');
    expect(html).toMatch(/type="module" src="\/static\/js\/pages\/chat\.js/);
    expect(js).toMatch(/from '\.\.\/chat-attach\.js/);
    expect(js).toContain('createBrowsePicker');
    expect(js).toContain('filesApiUrl');
    expect(js).toContain('if (!Array.isArray(data?.files)) throw');
    expect(js).toContain('filePicker.open()');
    expect(js).toContain('bindDropZone');
  });
});

describe('openDialog', () => {
  it('marks the picker open even without HTMLDialogElement.showModal', () => {
    const dialog = document.createElement('div');
    expect(openDialog(dialog)).toBe(true);
    expect(dialog.classList.contains('is-open')).toBe(true);
    expect(dialog.hasAttribute('open')).toBe(true);
  });
});
