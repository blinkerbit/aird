import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { bindPickDrop, dropEntries, folderRowFromEvent, initUploadUi, splitBySize } from '/static/js/browse/upload.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

describe('browse pick/drop contract', () => {
  it('uses a label so click-to-select is native HTML, not input.click()', () => {
    const html = readFileSync(path.join(root, 'aird/templates/browse.html'), 'utf8');
    const js = readFileSync(path.join(root, 'aird/static/js/browse/upload.js'), 'utf8');
    expect(html).toMatch(/<div[^>]*id="uploadZone"/);
    expect(html).toMatch(/<label[^>]*for="fileInput"/);
    expect(html).toMatch(/<input type="file" id="fileInput"/);
    expect(html).not.toMatch(/webkitdirectory/);
    expect(html).not.toMatch(/folderUploadBtn/);
    expect(html).not.toMatch(/<label[^>]*id="uploadZone"/);
    const zone = html.slice(html.indexOf('id="uploadZone"'), html.indexOf('id="newFolderBtn"'));
    expect(zone).toContain('id="fileInput"');
    expect(zone).toContain('Drop files or folders');
    expect(html).toMatch(/type="module" src="\/static\/js\/browse\/upload\.js/);
    expect(js).not.toMatch(/fileInput\.click\(/);
    expect(js).not.toMatch(/folderInput\.click\(/);
    expect(js).toContain('bindPickDrop');
    expect(js).toContain('onEntries');
  });
});

describe('initUploadUi', () => {
  it('binds once and records that pick/drop is live', () => {
    document.body.innerHTML = `
      <label id="uploadZone" for="fileInput"><input id="fileInput" type="file" /></label>
      <input id="currentPath" value="" />`;
    globalThis.AirdTransferManager = {
      enqueueUpload() { return {}; },
      pumpUploads() {},
    };
    initUploadUi();
    expect(document.getElementById('uploadZone').dataset.pickBound).toBe('1');
    initUploadUi();
    expect(document.getElementById('uploadZone').dataset.pickBound).toBe('1');
  });
});

describe('splitBySize', () => {
  it('keeps files at the limit and rejects oversize', () => {
    const ok = new File(['a'], 'ok.txt');
    Object.defineProperty(ok, 'size', { value: 10 });
    const big = new File(['b'], 'big.bin');
    Object.defineProperty(big, 'size', { value: 11 });
    expect(splitBySize([ok, big], 10)).toEqual({
      accepted: [ok],
      rejected: ['big.bin'],
    });
  });
});

describe('bindPickDrop', () => {
  it('opens via the native label control, not a parent click handler', () => {
    document.body.innerHTML = `
      <label id="zone" for="fileInput">pick
        <input id="fileInput" type="file" />
      </label>`;
    const zone = document.getElementById('zone');
    const input = document.getElementById('fileInput');
    const received = [];
    bindPickDrop(zone, input, { onFiles: (files) => received.push(...files) });
    zone.click();
    expect(received).toEqual([]);
    expect(input.labels[0]).toBe(zone);
    expect(zone.htmlFor).toBe('fileInput');
  });

  it('enqueues files from the input change event', () => {
    document.body.innerHTML = `
      <label id="zone" for="fileInput">
        <input id="fileInput" type="file" />
      </label>`;
    const received = [];
    const input = document.getElementById('fileInput');
    bindPickDrop(document.getElementById('zone'), input, {
      onFiles: (files) => received.push(...files),
    });
    const file = new File(['x'], 'picked.txt');
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event('change'));
    expect(received.map((f) => f.name)).toEqual(['picked.txt']);
    expect(input.files).toHaveLength(0);
  });

  it('enqueues dropped files', () => {
    document.body.innerHTML = `<label id="zone" for="fileInput"><input id="fileInput" type="file" /></label>`;
    const received = [];
    const zone = document.getElementById('zone');
    bindPickDrop(zone, document.getElementById('fileInput'), {
      onFiles: (files) => received.push(...files),
    });
    const file = new File(['x'], 'drop.txt');
    const dt = { files: [file], items: [] };
    zone.dispatchEvent(Object.assign(new Event('drop', { bubbles: true, cancelable: true }), { dataTransfer: dt }));
    expect(received.map((f) => f.name)).toEqual(['drop.txt']);
    expect(zone.classList.contains('dragover')).toBe(false);
  });
});

describe('dropEntries', () => {
  it('reads webkit entries when present', () => {
    const entry = { isDirectory: true, name: 'docs' };
    expect(dropEntries({
      items: [{ webkitGetAsEntry: () => entry }, { webkitGetAsEntry: () => null }],
    })).toEqual([entry]);
  });
});

describe('folder row drop', () => {
  it('only treats directory rows as drop targets', () => {
    document.body.innerHTML = `
      <table>
        <tr class="file-row" data-path="docs" data-is-dir="1"><td><input class="row-checkbox" data-is-dir="1" /></td></tr>
        <tr class="file-row" data-path="a.txt" data-is-dir="0"><td><input class="row-checkbox" data-is-dir="0" /></td></tr>
      </table>`;
    const dirCell = document.querySelector('[data-path="docs"] td');
    const fileCell = document.querySelector('[data-path="a.txt"] td');
    expect(folderRowFromEvent(dirCell)?.dataset.path).toBe('docs');
    expect(folderRowFromEvent(fileCell)).toBeNull();
  });
});
