import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it, vi } from 'vitest';
import { fetchTagCatalog, pathToGlob, postTagRule } from '/static/js/browse/tag-api.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

describe('browse tag API', () => {
  it('maps a file path to the glob used when tagging', () => {
    expect(pathToGlob('docs/a.txt')).toBe('/docs/a.txt');
    expect(pathToGlob('/docs/a.txt')).toBe('/docs/a.txt');
  });

  it('loads the public catalog instead of the admin rule list', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ names: ['pii'], colors: { pii: '#111111' } }),
    });
    vi.stubGlobal('fetch', fetchMock);
    const catalog = await fetchTagCatalog();
    expect(fetchMock.mock.calls[0][0]).toBe('/api/tags');
    expect(catalog.names).toEqual(['pii']);
  });

  it('sends auto_color when creating a tag rule', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: async () => ({ id: 1, color: '#6366f1' }),
    });
    vi.stubGlobal('fetch', fetchMock);
    document.cookie = '_xsrf=tok';
    const result = await postTagRule('fresh', '/a.txt', { autoColor: true });
    expect(result.ok).toBe(true);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.auto_color).toBe(true);
    expect(body.tag).toBe('fresh');
  });
});

describe('row tag popover contract', () => {
  it('does not block opening on GET /admin/api/abac/tags', () => {
    const js = readFileSync(path.join(root, 'aird/static/js/browse/tags.js'), 'utf8');
    const html = readFileSync(path.join(root, 'aird/templates/browse.html'), 'utf8');
    expect(js).toContain('setupTagPickerListeners');
    expect(js).toContain('untagPath');
    expect(js).not.toMatch(/openRowTagPopover[\s\S]*fetchAllTagRules/);
    expect(html).toContain('data-tags=');
    expect(html).toContain('tagPickerRemove');
    expect(html).toContain('rowTagPopoverSuggestions');
    expect(html).toContain('tag-picker-input-wrap');
    expect(js).toContain('tag-color-btn--chip');
    expect(js).toContain('tagColorPickerBtnHtml');
    expect(js).toContain("action === 'remove'");
    expect(js).toContain('collectKnownTagNames');
    expect(js).toContain('mergeTagNames');
    expect(js).toContain('paintTagColor');
    expect(html).toContain('data-tag=');
    expect(js).not.toMatch(/existingTagNames = catalog\.names/);
  });
});

describe('paintTagColor', () => {
  it('recolors every listing chip for that tag without a reload', async () => {
    const { paintTagColor } = await import('/static/js/browse/tags.js');
    document.body.innerHTML = [
      '<a class="file-tag-chip" data-tag="yellow-tag" href="/tagged/yellow-tag" style="background:#111111">yellow-tag</a>',
      '<a class="file-tag-chip" data-tag="yellow-tag" href="/tagged/yellow-tag" style="background:#111111">yellow-tag</a>',
      '<a class="file-tag-chip" data-tag="green-tag" href="/tagged/green-tag" style="background:#00aa00">green-tag</a>',
    ].join('');
    paintTagColor('yellow-tag', '#ef4444');
    const yellow = [...document.querySelectorAll('a.file-tag-chip[data-tag="yellow-tag"]')];
    expect(yellow).toHaveLength(2);
    yellow.forEach((el) => {
      expect(el.style.background).toBe('#ef4444');
    });
    expect(document.querySelector('a.file-tag-chip[data-tag="green-tag"]').style.background)
      .toBe('#00aa00');
  });
});

describe('tagged page color picker', () => {
  it('lets admins change the current tag color in place', () => {
    const html = readFileSync(path.join(root, 'aird/templates/tagged_files.html'), 'utf8');
    const js = readFileSync(path.join(root, 'aird/static/js/pages/tagged.js'), 'utf8');
    expect(html).toContain('id="taggedPageColor"');
    expect(html).toContain('tag-color-btn');
    expect(html).toContain('pages/tagged.js');
    expect(js).toContain('/admin/api/abac/tag-colors');
    expect(js).toContain("addEventListener('input'");
    expect(js).toContain("addEventListener('change'");
  });
});

describe('pwa install banner', () => {
  it('defers beforeinstallprompt so the nav button can call prompt()', () => {
    const js = readFileSync(path.join(root, 'aird/static/js/pwa.js'), 'utf8');
    expect(js).toContain("addEventListener('beforeinstallprompt'");
    expect(js).toMatch(/beforeinstallprompt[\s\S]{0,250}preventDefault\(\)/);
    expect(js).toContain('deferredPrompt.prompt()');
  });
});
