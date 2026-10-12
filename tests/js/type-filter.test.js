import { describe, expect, it } from 'vitest';
import {
  emptyTypeFilter,
  extensionFromName,
  isFiltering,
  parseExtensions,
  rowMatchesFilter,
  toggleCategory,
  typeFilterFromSearch,
} from '/static/js/browse/type-filter-model.js';
import { applyBrowseTypeFilter, initBrowseTypeFilter } from '/static/js/browse/type-filter.js';

describe('type filter model', () => {
  it('parses extensions and ignores junk', () => {
    expect(parseExtensions('PDF, .png, a/b, .a-b')).toEqual(['.pdf', '.png']);
    expect(extensionFromName('archive.tar.gz')).toBe('.gz');
    expect(extensionFromName('.hidden')).toBe('');
  });

  it('toggles categories and matches files', () => {
    let state = toggleCategory(emptyTypeFilter(), 'images');
    state = toggleCategory(state, 'nope');
    expect(isFiltering(state)).toBe(true);
    expect(rowMatchesFilter(state, { isDir: true, name: 'pics' })).toBe(true);
    expect(rowMatchesFilter(state, { isDir: false, name: 'a.PNG' })).toBe(true);
    expect(rowMatchesFilter(state, { isDir: false, name: 'a.pdf' })).toBe(false);
    state = toggleCategory(state, 'images');
    expect(isFiltering(state)).toBe(false);
  });

  it('prefers the query string over an empty filter', () => {
    expect(typeFilterFromSearch('code,images', 'py md')).toEqual({
      categories: ['code', 'images'],
      extensions: ['.py', '.md'],
    });
    expect(typeFilterFromSearch(null, null)).toBeNull();
  });
});

describe('type filter dom', () => {
  it('hides rows that miss the selected category', () => {
    document.body.innerHTML = `
      <div id="browseTypeFilter">
        <button type="button" data-type-filter="images"></button>
        <input id="browseTypeFilterExt">
        <button type="button" id="browseTypeFilterClear"></button>
        <span id="browseTypeFilterCount"></span>
      </div>
      <table id="fileTable">
        <tr class="file-row" data-is-dir="1"><td class="file-link">pics</td></tr>
        <tr class="file-row"><td class="file-link">a.png</td></tr>
        <tr class="file-row"><td class="file-link">notes.pdf</td></tr>
      </table>`;
    window.history.replaceState(null, '', '/browse?type=images');
    initBrowseTypeFilter();
    applyBrowseTypeFilter();
    const rows = [...document.querySelectorAll('#fileTable tr.file-row')];
    expect(rows.map((row) => row.hidden)).toEqual([false, false, true]);
    expect(document.getElementById('browseTypeFilterCount').textContent).toBe('1 of 2 files shown');
    document.getElementById('browseTypeFilterClear').click();
    expect(rows[2].hidden).toBe(false);
  });
});
