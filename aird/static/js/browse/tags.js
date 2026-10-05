"use strict";

import { SelectionStore } from './selection-store.js';
import {
  getCanTag,
  getTagColors,
  setTagColorLocal,
  escapeHtml,
  escapeAttr,
  showDialog,
  pathBasename,
  getRecentTags,
  noteRecentTag,
  listenTagColorSync,
} from './util.js';
import {
  applyTagRules,
  fetchTagCatalog,
  putTagColor,
  untagPath,
} from './tag-api.js';

function tagChipColors(hex) {
  if (!hex || typeof hex !== 'string') return null;
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const r = Number.parseInt(m[1].slice(0, 2), 16);
  const g = Number.parseInt(m[1].slice(2, 4), 16);
  const b = Number.parseInt(m[1].slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const fg = lum > 0.55 ? '#111827' : '#f9fafb';
  const norm = '#' + m[1].toLowerCase();
  return {
    bg: norm,
    fg: fg,
    border: 'color-mix(in oklch, ' + norm + ' 65%, transparent)',
  };
}

function tagChipStyleAttr(tagName) {
  const vars = tagChipColors(getTagColors()[tagName]);
  if (!vars) return '';
  return ' style="background:' + vars.bg + ';color:' + vars.fg + ';border-color:' + vars.border + '"';
}

function applyChipPaint(el, vars) {
  el.style.background = vars.bg;
  el.style.color = vars.fg;
  el.style.borderColor = vars.border;
}

/** Repaint every on-page chip for this tag. No reload. */
export function paintTagColor(tag, hex, opts) {
  if (!tag || !hex) return null;
  const vars = tagChipColors(hex);
  if (!vars) return null;
  const fromRemote = !!opts?.fromRemote;
  setTagColorLocal(tag, vars.bg, { broadcast: !fromRemote });
  const href = '/tagged/' + encodeURIComponent(tag);
  document.querySelectorAll('a.file-tag-chip').forEach(function (el) {
    if (el.dataset.tag ? el.dataset.tag !== tag : el.getAttribute('href') !== href) return;
    applyChipPaint(el, vars);
  });
  document.querySelectorAll('.tag-picker-chip').forEach(function (el) {
    const named = el.dataset.tag || el.querySelector('[data-tag]')?.dataset.tag;
    if (named !== tag) return;
    applyChipPaint(el, vars);
  });
  document.querySelectorAll('input.tag-color-btn-input').forEach(function (el) {
    if (el.dataset.tag !== tag) return;
    if (el.value.toLowerCase() !== vars.bg) el.value = vars.bg;
    el.closest('.tag-color-btn')?.style.setProperty('--tag-swatch', vars.bg);
  });
  document.querySelectorAll('.tag-picker-suggestion-item[data-sug="' + CSS.escape(tag)
    + '"] .tag-picker-suggestion-swatch').forEach(function (el) {
    el.style.background = vars.bg;
  });
  return vars;
}

export function fileTagChipHtml(tagName) {
  // No inline onclick (CSP). Clicks are stopped via delegation in initTagsUi.
  return '<a href="/tagged/' + encodeURIComponent(tagName) + '" class="file-tag-chip" data-tag="'
    + escapeAttr(tagName) + '"'
    + tagChipStyleAttr(tagName)
    + '>' + escapeHtml(tagName) + '</a>';
}

const TAG_COLOR_PALETTE_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
  + ' stroke-linecap="round" stroke-linejoin="round">'
  + '<circle cx="13.5" cy="6.5" r="0.5" fill="currentColor" />'
  + '<circle cx="17.5" cy="10.5" r="0.5" fill="currentColor" />'
  + '<circle cx="8.5" cy="7.5" r="0.5" fill="currentColor" />'
  + '<circle cx="6.5" cy="12.5" r="0.5" fill="currentColor" />'
  + '<path d="M12 2C6.5 2 2 6.5 2 12s4.5 10 10 10c.926 0 1.648-.746 1.648-1.688 0-.437-.18-.835-.437-1.125-.29-.289-.438-.652-.438-1.125a1.64 1.64 0 0 1 1.668-1.668h1.996c3.051 0 5.555-2.503 5.555-5.554C21.965 6.012 17.461 2 12 2z" />'
  + '</svg>';

function tagColorPickerBtnHtml(tag, hex) {
  return '<label class="tag-color-btn tag-color-btn--chip" data-tag="' + escapeAttr(tag)
    + '" style="--tag-swatch:' + escapeAttr(hex) + '" title="Change tag color">'
    + '<span class="tag-color-btn-icon" aria-hidden="true">' + TAG_COLOR_PALETTE_ICON + '</span>'
    + '<span class="tag-color-btn-dot"></span>'
    + '<input type="color" class="tag-color-btn-input" data-tag="' + escapeAttr(tag)
    + '" value="' + escapeAttr(hex) + '" aria-label="Color for ' + escapeAttr(tag) + '">'
    + '</label>';
}

export function pickerTagChipHtml(t, { colorable = false } = {}) {
  const hex = getTagColors()[t] || '#6366f1';
  return '<span class="tag-picker-chip" data-tag="' + escapeAttr(t) + '"' + tagChipStyleAttr(t) + '>'
    + escapeHtml(t)
    + (colorable ? tagColorPickerBtnHtml(t, hex) : '')
    + '<button type="button" class="tag-picker-chip-remove" data-tag="' + escapeAttr(t) + '" '
    + 'aria-label="Remove ' + escapeAttr(t) + '">×</button>'
    + '</span>';
}

function bindChipColorInputs(root, onCommit, onPreview) {
  root.querySelectorAll('input.tag-color-btn-input').forEach(function (input) {
    input.addEventListener('input', function () {
      paintTagColor(input.dataset.tag, input.value);
      if (onPreview) onPreview(input.dataset.tag, input.value);
    });
    input.addEventListener('change', function () {
      paintTagColor(input.dataset.tag, input.value);
      if (onCommit) onCommit(input.dataset.tag, input.value);
    });
  });
}

function renderTagChips(chipsEl, pendingTags, onRemove, colorable, onColor, onPreview) {
  chipsEl.innerHTML = [...pendingTags].map(function (t) {
    return pickerTagChipHtml(t, { colorable: !!colorable });
  }).join('');
  chipsEl.querySelectorAll('button[data-tag]').forEach(function (btn) {
    btn.addEventListener('click', function () { onRemove(btn.dataset.tag); });
  });
  bindChipColorInputs(chipsEl, onColor, onPreview);
}

function commitTagInput(inputEl, pendingTags) {
  inputEl.value.split(',')
    .map(function (s) { return s.trim().toLowerCase().replaceAll(/\s+/g, '-'); })
    .filter(Boolean)
    .forEach(function (t) {
      pendingTags.add(t);
      noteRecentTag(t);
    });
  inputEl.value = '';
}

function collectKnownTagNames() {
  const names = new Set();
  document.querySelectorAll('.tags-cell[data-tags]').forEach(function (el) {
    parseTagsAttr(el.dataset.tags).forEach(function (t) { names.add(t); });
  });
  Object.keys(getTagColors()).forEach(function (t) { if (t) names.add(t); });
  return [...names].sort(function (a, b) { return a.localeCompare(b); });
}

function mergeTagNames(target, extra) {
  const set = new Set(target);
  (extra || []).forEach(function (n) { if (n) set.add(n); });
  const sorted = [...set].sort(function (a, b) { return a.localeCompare(b); });
  target.length = 0;
  sorted.forEach(function (n) { target.push(n); });
}

function suggestionMatches(names, pendingTags, q, excludeSet) {
  return names.filter(function (n) {
    if (pendingTags.has(n) || excludeSet?.has(n)) return false;
    return !q || n.includes(q);
  });
}

function nextSuggestionIndex(idx, len, key) {
  if (key === 'ArrowDown') return (idx + 1) % len;
  if (idx <= 0) return len - 1;
  return idx - 1;
}

function sortTagSuggestions(matches) {
  const recent = getRecentTags();
  return matches.slice().sort(function (a, b) {
    const ai = recent.indexOf(a);
    const bi = recent.indexOf(b);
    if (ai !== -1 || bi !== -1) {
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    }
    return a.localeCompare(b);
  });
}

function renderTagSuggestions(inputEl, existingTagNames, pendingTags, onPick, suggestionsId = 'tagPickerSuggestions', excludeSet) {
  const sugId = suggestionsId;
  const q = inputEl.value.trim().toLowerCase();
  let matches = suggestionMatches(existingTagNames, pendingTags, q, excludeSet);
  if (q && !matches.length) {
    matches = suggestionMatches(existingTagNames, pendingTags, '', excludeSet);
  }
  matches = sortTagSuggestions(matches);
  let sug = document.getElementById(sugId);
  if (!sug) {
    sug = document.createElement('div');
    sug.id = sugId;
    sug.className = 'tag-picker-suggestions';
    inputEl.parentNode.classList.add('tag-picker-input-wrap');
    inputEl.after(sug);
  }
  sug.hidden = !matches.length;
  sug.innerHTML = matches.map(function (n) {
    const hex = getTagColors()[n];
    const swatch = hex
      ? '<span class="tag-picker-suggestion-swatch" style="background:' + escapeAttr(hex) + '"></span>'
      : '';
    return '<div class="tag-picker-suggestion-item" role="option" data-sug="' + escapeAttr(n) + '">'
      + swatch + escapeHtml(n) + '</div>';
  }).join('');
  sug.querySelectorAll('[data-sug]').forEach(function (el) {
    el.addEventListener('mousedown', function (e) {
      e.preventDefault();
      onPick(el.dataset.sug);
    });
  });
  if (sugId === 'rowTagPopoverSuggestions') scheduleRowTagPopoverPosition();
}


function setupTagPickerListeners(inputEl, chipsEl, pendingTags, existingTagNames, suggestionsId, signal, colorOpts, excludeSet) {
  const sugId = suggestionsId || 'tagPickerSuggestions';
  const opts = signal ? { signal } : undefined;
  const colorable = !!colorOpts?.colorable;
  const onColor = colorOpts?.onColor;
  const onPreview = colorOpts?.onPreview;

  function pick(tag) {
    pendingTags.add(tag);
    noteRecentTag(tag);
    inputEl.value = '';
    refresh();
  }

  function refresh() {
    renderTagChips(chipsEl, pendingTags, function (tag) {
      pendingTags.delete(tag);
      refresh();
    }, colorable, onColor, onPreview);
    renderTagSuggestions(inputEl, existingTagNames, pendingTags, pick, sugId, excludeSet);
  }

  inputEl.addEventListener('keydown', function (e) {
    const sug = document.getElementById(sugId);
    const items = sug ? [...sug.querySelectorAll('[data-sug]')] : [];
    const active = sug?.querySelector('.is-active');
    const idx = items.indexOf(active);
    if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && items.length) {
      e.preventDefault();
      const next = nextSuggestionIndex(idx, items.length, e.key);
      items.forEach(function (el) { el.classList.remove('is-active'); });
      items[next].classList.add('is-active');
      items[next].scrollIntoView({ block: 'nearest' });
      return;
    }
    if (e.key === 'Enter' && active?.dataset.sug) {
      e.preventDefault();
      pick(active.dataset.sug);
      return;
    }
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault();
      commitTagInput(inputEl, pendingTags);
      refresh();
    }
  }, opts);
  inputEl.addEventListener('input', function () {
    if (inputEl.value.includes(',')) {
      commitTagInput(inputEl, pendingTags);
      refresh();
      return;
    }
    renderTagSuggestions(inputEl, existingTagNames, pendingTags, pick, sugId, excludeSet);
  }, opts);
  inputEl.addEventListener('focus', function () {
    renderTagSuggestions(inputEl, existingTagNames, pendingTags, pick, sugId, excludeSet);
  }, opts);
  return refresh;
}

function awaitTagPickerClose(modal, inputEl, errEl, pendingTags, refresh) {
  return new Promise(function (resolve) {
    const apply = document.getElementById('tagPickerConfirm');
    const remove = document.getElementById('tagPickerRemove');
    const cancel = document.getElementById('tagPickerCancel');
    let settled = false;
    function done(result) {
      if (settled) return;
      settled = true;
      modal.close();
      resolve(result);
    }
    function collect(action) {
      commitTagInput(inputEl, pendingTags);
      refresh();
      if (!pendingTags.size) {
        errEl.textContent = 'Enter at least one tag.';
        errEl.classList.remove('hidden');
        return;
      }
      done({ tags: [...pendingTags], action: action });
    }
    apply.onclick = function () { collect('apply'); };
    if (remove) remove.onclick = function () { collect('remove'); };
    cancel.onclick = function () { done(null); };
    modal.addEventListener('close', function () {
      if (!settled) resolve(null);
    }, { once: true });
  });
}

async function mergeTagCatalogIntoNames(existingTagNames) {
  try {
    const catalog = await fetchTagCatalog();
    mergeTagNames(existingTagNames, catalog.names);
    if (globalThis.__BROWSE_CONFIG) {
      globalThis.__BROWSE_CONFIG.tagColors = {
        ...(globalThis.__BROWSE_CONFIG.tagColors || {}),
        ...(catalog.colors || {}),
      };
    }
  } catch { /* autocomplete is best-effort */ }
}

async function bulkRemoveTagsFromPaths(paths, tags) {
  for (const path of paths) {
    const cell = document.querySelector('.tags-cell[data-path="' + CSS.escape(path) + '"]');
    const remain = tagsCellTags(cell).filter(function (t) { return !tags.includes(t); });
    for (const tag of tags) {
      await untagPath(tag, path);
    }
    updateTagsCell(path, remain);
  }
  showDialog('Removed [' + tags.join(', ') + '] from ' + paths.length + ' item(s).', 'Tags');
}

async function bulkApplyTagsToPaths(paths, tags, pendingColors) {
  const { created, failed } = await applyTagRules(tags, paths, { colors: pendingColors });
  tags.forEach(function (t) { noteRecentTag(t); });
  Object.entries(pendingColors).forEach(function (entry) { paintTagColor(entry[0], entry[1]); });
  for (const path of paths) {
    const cell = document.querySelector('.tags-cell[data-path="' + CSS.escape(path) + '"]');
    const merged = new Set(tagsCellTags(cell));
    tags.forEach(function (t) { merged.add(t); });
    updateTagsCell(path, [...merged]);
  }
  const msg = created + ' tag rule(s) created for [' + tags.join(', ') + '].'
    + (failed ? ' ' + failed + ' already existed or failed.' : '');
  showDialog(msg, 'Tags applied');
}

export async function bulkAddTags() {
  const paths = SelectionStore.getAll();
  if (!paths.length) { showDialog('No files selected.', 'Info'); return; }
  const modal = document.getElementById('tagPickerModal');
  const inputEl = document.getElementById('tagPickerInput');
  const chipsEl = document.getElementById('tagPickerChips');
  const descEl = document.getElementById('tagPickerDesc');
  const errEl = document.getElementById('tagPickerError');
  if (!modal || !inputEl) return;

  descEl.textContent = 'Will tag ' + paths.length + ' selected item(s).';
  inputEl.value = '';
  errEl.classList.add('hidden');

  const pendingTags = new Set();
  const pendingColors = {};
  const existingTagNames = collectKnownTagNames();
  await mergeTagCatalogIntoNames(existingTagNames);

  async function onPendingColor(tag, hex) {
    pendingColors[tag] = hex;
    paintTagColor(tag, hex);
  }

  const refresh = setupTagPickerListeners(
    inputEl, chipsEl, pendingTags, existingTagNames, 'tagPickerSuggestions', undefined,
    { colorable: true, onColor: onPendingColor, onPreview: onPendingColor }
  );
  refresh();
  modal.showModal();

  const result = await awaitTagPickerClose(modal, inputEl, errEl, pendingTags, refresh);
  if (!result?.tags?.length) return;

  if (result.action === 'remove') {
    await bulkRemoveTagsFromPaths(paths, result.tags);
    return;
  }

  await bulkApplyTagsToPaths(paths, result.tags, pendingColors);
}

let _rowTagPopoverAbort = null;
let _rowTagPopoverAnchor = null;
const ROW_TAG_POPOVER_MIN_W = 288;
const ROW_TAG_POPOVER_MIN_H = 140;

function ensureRowTagPopoverPortal(pop, backdrop) {
  if (pop && pop.parentNode !== document.body) document.body.appendChild(pop);
  if (backdrop && backdrop.parentNode !== document.body) document.body.appendChild(backdrop);
}

function scheduleRowTagPopoverPosition() {
  const pop = document.getElementById('rowTagPopover');
  if (!pop || !_rowTagPopoverAnchor?.isConnected) return;
  requestAnimationFrame(function () {
    if (!_rowTagPopoverAnchor?.isConnected) return;
    positionRowTagPopover(_rowTagPopoverAnchor, pop);
  });
}

export function isRowTagPopoverOpen() {
  return !!_rowTagPopoverAnchor;
}

function parseTagsAttr(raw) {
  if (!raw) return [];
  return raw.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
}

function tagsCellTags(tagsCell) {
  return parseTagsAttr(tagsCell?.dataset.tags || '');
}

const TAG_FILTER_PARAM = 'tag';
const TAG_CHIP_MAX_VISIBLE = 2;
let _activeTagFilter = null;

export function setBrowseTagFilter(tag, options) {
  const opts = options || {};
  _activeTagFilter = tag ? String(tag).trim() : null;
  const table = document.getElementById('fileTable');
  if (table) {
    table.querySelectorAll('tr.file-row').forEach(function (row) {
      if (!_activeTagFilter) {
        row.hidden = false;
        return;
      }
      const cell = row.querySelector('.tags-cell');
      row.hidden = !tagsCellTags(cell).includes(_activeTagFilter);
    });
  }
  const bar = document.getElementById('browseTagFilterBar');
  if (bar) {
    bar.hidden = !_activeTagFilter;
    const chip = bar.querySelector('.browse-tag-filter-chip');
    if (chip && _activeTagFilter) {
      chip.textContent = _activeTagFilter;
      chip.dataset.tag = _activeTagFilter;
      const vars = tagChipColors(getTagColors()[_activeTagFilter]);
      if (vars) applyChipPaint(chip, vars);
    }
  }
  if (opts.updateUrl !== false) {
    const url = new URL(globalThis.location.href);
    if (_activeTagFilter) url.searchParams.set(TAG_FILTER_PARAM, _activeTagFilter);
    else url.searchParams.delete(TAG_FILTER_PARAM);
    globalThis.history.replaceState(null, '', url.pathname + url.search + url.hash);
  }
}

export function initBrowseTagFilter() {
  const param = new URLSearchParams(globalThis.location.search).get(TAG_FILTER_PARAM);
  if (param) setBrowseTagFilter(param, { updateUrl: false });
  document.getElementById('browseTagFilterClear')?.addEventListener('click', function () {
    setBrowseTagFilter(null);
  });
}

function handleTagChipClick(e, chip) {
  if (!chip?.dataset?.tag) return;
  if (e.ctrlKey || e.metaKey || e.button === 1) return;
  e.preventDefault();
  setBrowseTagFilter(chip.dataset.tag);
}

function renderTagsCellInner(tags, path) {
  let html = '';
  if (tags.length) {
    const visible = tags.slice(0, TAG_CHIP_MAX_VISIBLE);
    const overflow = tags.length > TAG_CHIP_MAX_VISIBLE;
    html += '<span class="file-tag-list' + (overflow ? ' file-tag-list--overflow' : '') + '">';
    visible.forEach(function (t) { html += fileTagChipHtml(t); });
    if (overflow) {
      html += '<button type="button" class="file-tag-more file-tag-more-trigger"'
        + ' aria-label="Show all ' + tags.length + ' tags" title="Show all tags">'
        + '+' + (tags.length - TAG_CHIP_MAX_VISIBLE) + '</button>';
    }
    html += '</span>';
  }
  if (getCanTag()) {
    html += '<button type="button" class="row-tag-add-btn" data-path="' + escapeAttr(path) + '"'
      + ' title="Add tag" aria-label="Add tag to ' + escapeAttr(pathBasename(path)) + '">'
      + '<span aria-hidden="true">+</span></button>';
  } else if (!tags.length) {
    html = '<span class="tags-cell-empty">—</span>';
  }
  return html;
}

function updateTagsCell(path, tags) {
  const row = document.querySelector('tr.file-row[data-path="' + CSS.escape(path) + '"]');
  const tagsCell = row?.querySelector('.tags-cell');
  if (!tagsCell) return;
  const sorted = [...new Set(tags)].sort(function (a, b) { return a.localeCompare(b); });
  tagsCell.dataset.tags = sorted.join(',');
  const inner = tagsCell.querySelector('.tags-cell-inner');
  if (inner) inner.innerHTML = renderTagsCellInner(sorted, path);
}

function positionFileTagsHoverPopover(anchorEl, pop) {
  if (!anchorEl || !pop) return;
  pop.removeAttribute('hidden');
  pop.style.display = 'block';
  pop.style.visibility = 'hidden';
  pop.style.left = '0';
  pop.style.top = '0';

  const rect = anchorEl.getBoundingClientRect();
  const margin = 8;
  const gap = 6;
  const popW = pop.offsetWidth;
  const popH = pop.offsetHeight;

  let left = rect.left;
  let top = rect.bottom + gap;
  if (left + popW > window.innerWidth - margin) {
    left = Math.max(margin, window.innerWidth - margin - popW);
  }
  if (top + popH > window.innerHeight - margin) {
    const above = rect.top - gap - popH;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
  }

  pop.style.left = Math.round(left) + 'px';
  pop.style.top = Math.round(top) + 'px';
  pop.style.visibility = 'visible';
}

function tagsFromEventTarget(target) {
  const trigger = target.closest('.file-tag-list--overflow, .file-tag-more-trigger');
  if (!trigger) return null;
  const list = trigger.classList.contains('file-tag-list')
    ? trigger
    : trigger.closest('.file-tag-list');
  const cell = (list || trigger).closest('.tags-cell');
  if (!cell) return null;
  const tags = tagsCellTags(cell);
  if (tags.length <= 1) return null;
  return { anchor: list || trigger, tags: tags };
}

function initFileTagsHoverPopover() {
  const pop = document.getElementById('fileTagsHoverPopover');
  const listEl = pop?.querySelector('.file-tags-hover-popover-list');
  const table = document.getElementById('fileTable');
  if (!pop || !listEl || !table) return;

  let hideTimer = null;
  let activeAnchor = null;

  function hidePopover() {
    pop.hidden = true;
    pop.style.display = '';
    pop.style.visibility = '';
    pop.style.left = '';
    pop.style.top = '';
    activeAnchor = null;
  }

  function scheduleHide() {
    clearTimeout(hideTimer);
    hideTimer = setTimeout(hidePopover, 130);
  }

  function cancelHide() {
    clearTimeout(hideTimer);
  }

  function showPopover(anchorEl, tags) {
    if (!tags.length) return;
    cancelHide();
    activeAnchor = anchorEl;
    listEl.innerHTML = tags.map(function (t) {
      return fileTagChipHtml(t);
    }).join('');
    positionFileTagsHoverPopover(anchorEl, pop);
  }

  table.addEventListener('mouseover', function (e) {
    const info = tagsFromEventTarget(e.target);
    if (!info) return;
    showPopover(info.anchor, info.tags);
  });

  table.addEventListener('mouseout', function (e) {
    const info = tagsFromEventTarget(e.target);
    if (!info) return;
    const related = e.relatedTarget;
    if (related && (info.anchor.contains(related) || pop.contains(related))) return;
    scheduleHide();
  });

  pop.addEventListener('mouseenter', cancelHide);
  pop.addEventListener('mouseleave', scheduleHide);

  table.addEventListener('focusin', function (e) {
    const btn = e.target.closest('.file-tag-more-trigger');
    if (!btn) return;
    const cell = btn.closest('.tags-cell');
    const tags = tagsCellTags(cell);
    if (tags.length <= 1) return;
    const list = btn.closest('.file-tag-list') || btn;
    showPopover(list, tags);
  });

  table.addEventListener('focusout', function (e) {
    if (!e.target.closest('.file-tag-more-trigger')) return;
    scheduleHide();
  });

  document.addEventListener('scroll', function () {
    if (!activeAnchor || pop.hidden) return;
    positionFileTagsHoverPopover(activeAnchor, pop);
  }, true);

  window.addEventListener('resize', function () {
    if (!activeAnchor || pop.hidden) return;
    positionFileTagsHoverPopover(activeAnchor, pop);
  });
}

function positionRowTagPopover(anchorEl, pop) {
  if (!anchorEl || !pop) return;

  pop.removeAttribute('hidden');
  pop.style.display = 'block';
  pop.style.position = 'fixed';
  pop.style.right = 'auto';
  pop.style.bottom = 'auto';
  pop.style.margin = '0';
  pop.style.visibility = 'hidden';
  pop.style.left = '0';
  pop.style.top = '0';

  const rect = anchorEl.getBoundingClientRect();
  const margin = 8;
  const gap = 6;
  const popW = Math.max(pop.offsetWidth || 0, ROW_TAG_POPOVER_MIN_W);
  const popH = Math.max(pop.offsetHeight || 0, ROW_TAG_POPOVER_MIN_H);

  let left = rect.left;
  let top = rect.bottom + gap;

  if (left + popW > window.innerWidth - margin) {
    left = Math.max(margin, window.innerWidth - margin - popW);
  }
  if (left < margin) left = margin;

  if (top + popH > window.innerHeight - margin) {
    const above = rect.top - gap - popH;
    top = above >= margin ? above : Math.max(margin, window.innerHeight - margin - popH);
  }

  pop.style.left = Math.round(left) + 'px';
  pop.style.top = Math.round(top) + 'px';
  pop.style.visibility = 'visible';
}

export function closeRowTagPopover() {
  const pop = document.getElementById('rowTagPopover');
  const backdrop = document.getElementById('rowTagPopoverBackdrop');
  if (_rowTagPopoverAbort) {
    _rowTagPopoverAbort.abort();
    _rowTagPopoverAbort = null;
  }
  _rowTagPopoverAnchor = null;
  if (pop) {
    pop.hidden = true;
    pop.style.display = '';
    pop.style.visibility = '';
    pop.style.left = '';
    pop.style.top = '';
    const sug = document.getElementById('rowTagPopoverSuggestions');
    if (sug) {
      sug.innerHTML = '';
      sug.hidden = true;
    }
  }
  if (backdrop) backdrop.hidden = true;
}


function renderExistingTagChips(existingEl, tagsOnPathMap, onRemove, onColor) {
  const tags = [...tagsOnPathMap.keys()];
  if (!tags.length) {
    existingEl.innerHTML = '<span class="text-xs opacity-50">None yet</span>';
    return;
  }
  existingEl.innerHTML = tags.map(function (t) {
    return pickerTagChipHtml(t, { colorable: true });
  }).join('');
  existingEl.querySelectorAll('button[data-tag]').forEach(function (btn) {
    btn.addEventListener('click', function () { onRemove(btn.dataset.tag); });
  });
  bindChipColorInputs(existingEl, onColor);
}

export async function openRowTagPopover(path, anchorEl) {
  if (!getCanTag()) return;
  closeRowTagPopover();

  const pop = document.getElementById('rowTagPopover');
  const backdrop = document.getElementById('rowTagPopoverBackdrop');
  const inputEl = document.getElementById('rowTagPopoverInput');
  const chipsEl = document.getElementById('rowTagPopoverChips');
  const existingEl = document.getElementById('rowTagPopoverExisting');
  const fileEl = document.getElementById('rowTagPopoverFile');
  const errEl = document.getElementById('rowTagPopoverError');
  const applyBtn = document.getElementById('rowTagPopoverApply');
  const cancelBtn = document.getElementById('rowTagPopoverCancel');
  if (!pop || !inputEl || !chipsEl || !existingEl || !applyBtn || !cancelBtn) return;

  _rowTagPopoverAnchor = anchorEl;
  _rowTagPopoverAbort = new AbortController();
  const signal = _rowTagPopoverAbort.signal;
  ensureRowTagPopoverPortal(pop, backdrop);

  inputEl.value = '';
  chipsEl.innerHTML = '';
  existingEl.innerHTML = '';
  errEl.classList.add('hidden');
  errEl.textContent = '';
  fileEl.textContent = pathBasename(path);

  const pendingTags = new Set();
  const pendingColors = {};
  const tagsOnPathMap = new Map();
  const cell = document.querySelector('.tags-cell[data-path="' + CSS.escape(path) + '"]');
  tagsCellTags(cell).forEach(function (tag) { tagsOnPathMap.set(tag, []); });

  function syncTagsCellFromMap() {
    updateTagsCell(path, [...tagsOnPathMap.keys()]);
  }

  async function onColor(tag, hex) {
    pendingColors[tag] = hex;
    paintTagColor(tag, hex);
    const saved = await putTagColor(tag, hex);
    if (!saved.ok) {
      errEl.textContent = 'Could not save color for "' + tag + '".';
      errEl.classList.remove('hidden');
      return;
    }
    errEl.classList.add('hidden');
    paintTagColor(tag, saved.color || hex);
  }

  function renderExisting() {
    renderExistingTagChips(existingEl, tagsOnPathMap, async function (tagName) {
      applyBtn.disabled = true;
      const result = await untagPath(tagName, path);
      applyBtn.disabled = false;
      if (!result.ok) {
        errEl.textContent = 'Could not remove tag "' + tagName + '".';
        errEl.classList.remove('hidden');
        return;
      }
      errEl.classList.add('hidden');
      tagsOnPathMap.delete(tagName);
      renderExisting();
      syncTagsCellFromMap();
      scheduleRowTagPopoverPosition();
    }, onColor);
  }
  renderExisting();

  const existingTagNames = collectKnownTagNames();
  const refresh = setupTagPickerListeners(
    inputEl, chipsEl, pendingTags, existingTagNames, 'rowTagPopoverSuggestions', signal,
    {
      colorable: true,
      onPreview: function (tag, hex) {
        pendingColors[tag] = hex;
      },
      onColor: function (tag, hex) {
        pendingColors[tag] = hex;
        paintTagColor(tag, hex);
      },
    },
    tagsOnPathMap
  );
  refresh();

  backdrop.hidden = false;
  positionRowTagPopover(anchorEl, pop);
  scheduleRowTagPopoverPosition();
  inputEl.focus();

  (async function loadRowTagCatalog() {
    try {
      const catalog = await fetchTagCatalog();
      if (signal.aborted) return;
      mergeTagNames(existingTagNames, catalog.names);
      if (globalThis.__BROWSE_CONFIG) {
        globalThis.__BROWSE_CONFIG.tagColors = {
          ...(globalThis.__BROWSE_CONFIG.tagColors || {}),
          ...(catalog.colors || {}),
        };
      }
      refresh();
    } catch { /* autocomplete is best-effort */ }
  })();

  const onApply = async function () {
    commitTagInput(inputEl, pendingTags);
    refresh();
    if (!pendingTags.size) {
      closeRowTagPopover();
      return;
    }
    applyBtn.disabled = true;
    const tags = [...pendingTags];
    tags.forEach(function (t) { noteRecentTag(t); });
    const { created, failed } = await applyTagRules(tags, [path], { colors: pendingColors });
    Object.entries(pendingColors).forEach(function (entry) { paintTagColor(entry[0], entry[1]); });
    applyBtn.disabled = false;
    if (created === 0 && failed > 0) {
      errEl.textContent = 'Could not add tag(s). They may already exist.';
      errEl.classList.remove('hidden');
      return;
    }
    for (const tag of tags) {
      if (!tagsOnPathMap.has(tag)) tagsOnPathMap.set(tag, []);
    }
    syncTagsCellFromMap();
    closeRowTagPopover();
    if (failed > 0) {
      showDialog(created + ' tag rule(s) added. ' + failed + ' already existed or failed.', 'Tags');
    }
  };

  applyBtn.addEventListener('click', onApply, { signal: signal });
  cancelBtn.addEventListener('click', closeRowTagPopover, { signal: signal });
  backdrop.addEventListener('click', closeRowTagPopover, { signal: signal });
  document.addEventListener('keydown', function rowPopEsc(e) {
    if (e.key === 'Escape' && isRowTagPopoverOpen()) {
      e.preventDefault();
      e.stopPropagation();
      closeRowTagPopover();
    }
  }, { signal: signal });
  window.addEventListener('resize', scheduleRowTagPopoverPosition, { signal: signal });
  document.addEventListener('scroll', scheduleRowTagPopoverPosition, { signal: signal, capture: true });
  inputEl.addEventListener('input', scheduleRowTagPopoverPosition, { signal: signal });
}

export function initTagsUi() {
  initFileTagsHoverPopover();
  listenTagColorSync(function (tag, hex, opts) {
    paintTagColor(tag, hex, opts);
  });
  initBrowseTagFilter();
  document.getElementById('fileTable')?.addEventListener('click', function (e) {
    const chip = e.target.closest('a.file-tag-chip');
    if (chip) {
      handleTagChipClick(e, chip);
      e.stopPropagation();
    }
  });
  document.getElementById('fileTagsHoverPopover')?.addEventListener('click', function (e) {
    const chip = e.target.closest('a.file-tag-chip');
    if (chip) {
      handleTagChipClick(e, chip);
      e.stopPropagation();
    }
  });
}
