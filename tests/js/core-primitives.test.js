import { afterEach, describe, expect, it, vi } from 'vitest';
import { placeNearAnchor } from '/static/js/core/anchor.js';
import { copyToClipboard } from '/static/js/core/clipboard.js';
import { showDialog, cancelActiveDialog } from '/static/js/core/dialog.js';
import { escapeCssAttrValue, escapeHtml, queryByDataFileId } from '/static/js/core/dom-safe.js';
import { formatBytes } from '/static/js/core/format.js';
import { apiFetch } from '/static/js/core/http.js';
import { getXSRFToken, xsrfHeaders } from '/static/js/core/xsrf.js';

describe('formatBytes', () => {
  it('formats units and rejects junk', () => {
    expect(formatBytes(0)).toBe('0 B');
    expect(formatBytes(-1)).toBe('0 B');
    expect(formatBytes(Number.NaN)).toBe('0 B');
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(1048576)).toBe('1.0 MB');
  });
});

describe('escapeHtml', () => {
  it('escapes text and attributes', () => {
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(`a<b>&"'`)).toBe('a&lt;b&gt;&amp;&quot;&#39;');
  });

  it('escapes css attribute values when CSS.escape is absent', () => {
    const css = globalThis.CSS;
    globalThis.CSS = undefined;
    expect(escapeCssAttrValue('a"b\\c')).toBe('a\\22 b\\\\c');
    globalThis.CSS = css;
  });

  it('finds a row by data-file-id', () => {
    document.body.innerHTML = '<button class="available-file-btn" data-file-id="report.pdf"></button>';
    const el = document.querySelector('.available-file-btn');
    expect(queryByDataFileId('.available-file-btn', 'report.pdf')).toBe(el);
  });
});

describe('xsrf and apiFetch', () => {
  afterEach(() => {
    document.cookie = '_xsrf=; Max-Age=0';
    vi.unstubAllGlobals();
  });

  it('reads the cookie and merges headers', () => {
    document.cookie = '_xsrf=a%2Fb';
    expect(getXSRFToken()).toBe('a/b');
    expect(xsrfHeaders({ Accept: 'application/json' })).toEqual({
      Accept: 'application/json',
      'X-XSRFToken': 'a/b',
    });
  });

  it('sets the token unless the caller already did', async () => {
    document.cookie = '_xsrf=tok';
    const fetchMock = vi.fn(async () => new Response('ok'));
    vi.stubGlobal('fetch', fetchMock);
    await apiFetch('/api/x', { method: 'POST' });
    await apiFetch('/api/y', { headers: { 'X-XSRFToken': 'own' } });
    expect(fetchMock.mock.calls[0][1].headers.get('X-XSRFToken')).toBe('tok');
    expect(fetchMock.mock.calls[1][1].headers.get('X-XSRFToken')).toBe('own');
  });
});

describe('copyToClipboard', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('flashes the button only after a successful write', async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(async () => {});
    vi.stubGlobal('navigator', { clipboard: { writeText } });
    const btn = document.createElement('button');
    btn.textContent = 'Copy';
    await expect(copyToClipboard('path', btn)).resolves.toBe(true);
    expect(writeText).toHaveBeenCalledWith('path');
    expect(btn.textContent).toBe('Copied!');
    vi.advanceTimersByTime(1500);
    expect(btn.textContent).toBe('Copy');
    vi.useRealTimers();
  });

  it('reports failure when the clipboard is missing', async () => {
    vi.stubGlobal('navigator', {});
    const onDone = vi.fn();
    await expect(copyToClipboard('x', { onDone })).resolves.toBe(false);
    expect(onDone).toHaveBeenCalledWith(false);
  });
});

describe('showDialog', () => {
  it('resolves a prompt value and cancel', async () => {
    const prompt = showDialog('Name?', 'Rename', { prompt: true, showCancel: true, defaultValue: 'a' });
    const input = document.getElementById('dialogInput');
    expect(input.value).toBe('a');
    input.value = 'b';
    document.getElementById('dialogConfirmBtn').click();
    await expect(prompt).resolves.toBe('b');

    const cancelled = showDialog('Sure?', 'Delete', { showCancel: true });
    expect(cancelActiveDialog()).toBe(true);
    await expect(cancelled).resolves.toBe(false);
    expect(cancelActiveDialog()).toBe(false);
  });
});
