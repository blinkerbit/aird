"use strict";

import { SelectionStore } from '/static/js/browse/selection-store.js';
import {
  getCanTag,
  getTagColors,
  escapeHtml,
  escapeAttr,
  showDialog,
  pathBasename,
} from '/static/js/browse/util.js';
import {
  applyTagRules,
  deleteTagRuleIds,
  fetchAllTagRules,
  tagsOnPath,
} from '/static/js/browse/tag-api.js';

function tagChipStyleAttr(tagName) {
  const hex = getTagColors()[tagName];
  if (!hex || typeof hex !== 'string') return '';
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return '';
  const r = parseInt(m[1].slice(0, 2), 16);
  const g = parseInt(m[1].slice(2, 4), 16);
  const b = parseInt(m[1].slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const fg = lum > 0.55 ? '#111827' : '#f9fafb';
  return ' style="background:' + hex + ';color:' + fg + ';border-color:color-mix(in oklch, '
    + hex + ' 65%, transparent)"';
}

export function fileTagChipHtml(tagName) {
  // No inline onclick (CSP). Clicks are stopped via delegation in initTagsUi.
  return '<a href="/tagged/' + encodeURIComponent(tagName) + '" class="file-tag-chip"'
    + tagChipStyleAttr(tagName)
    + '>' + escapeHtml(tagName) + '</a>';
}

export function pickerTagChipHtml(t) {
  return '<span class="tag-picker-chip">'
    + escapeHtml(t)
    + '<button type="button" class="tag-picker-chip-remove" data-tag="' + escapeAttr(t) + '" '
    + 'aria-label="Remove ' + escapeAttr(t) + '">×</button>'
    + '</span>';
}

function renderTagChips(chipsEl, pendingTags, onRemove) {
  chipsEl.innerHTML = [...pendingTags].map(pickerTagChipHtml).join('');
  chipsEl.querySelectorAll('button[data-tag]').forEach(function (btn) {
    btn.addEventListener('click', function () { onRemove(btn.dataset.tag); });
  });
}

function commitTagInput(inputEl, pendingTags) {
  inputEl.value.split(',')
    .map(function (s) { return s.trim().toLowerCase().replaceAll(/\s+/g, '-'); })
    .filter(Boolean)
    .forEach(function (t) { pendingTags.add(t); });
  inputEl.value = '';
}

function renderTagSuggestions(inputEl, existingTagNames, pendingTags, onPick, suggestionsId = 'tagPickerSuggestions') {
  const sugId = suggestionsId;
  const q = inputEl.value.trim().toLowerCase();
  const matches = q
    ? existingTagNames.filter(function (n) { return n.includes(q) && !pendingTags.has(n); })
    : existingTagNames.filter(function (n) { return !pendingTags.has(n); }).slice(0, 8);
  let sug = document.getElementById(sugId);
  if (!sug) {
    sug = document.createElement('div');
    sug.id = sugId;
    sug.className = 'tag-picker-suggestions';
    inputEl.parentNode.classList.add('tag-picker-input-wrap');
    inputEl.after(sug);
  }
  sug.innerHTML = matches.map(function (n) {
    return '<div class="tag-picker-suggestion-item" data-sug="' + escapeAttr(n) + '">'
      + escapeHtml(n) + '</div>';
  }).join('');
  sug.querySelectorAll('[data-sug]').forEach(function (el) {
    el.addEventListener('mousedown', function (e) {
      e.preventDefault();
      onPick(el.dataset.sug);
      sug.innerHTML = '';
    });
  });
  if (sugId === 'rowTagPopoverSuggestions') scheduleRowTagPopoverPosition();
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
  let existingTagNames = [];
  try {
    const rules = await fetchAllTagRules();
    existingTagNames = [...new Set(rules.map(function (t) { return t.tag; }))].sort((a, b) => a.localeCompare(b));
  } catch { /* autocomplete is best-effort */ }

  const refresh = setupTagPickerListeners(inputEl, chipsEl, pendingTags, existingTagNames);
  refresh();
  modal.showModal();

  const tags = await awaitTagPickerClose(modal, inputEl, errEl, pendingTags, refresh);
  if (!tags) return;

  const { created, failed } = await applyTagRules(tags, paths);
  const msg = created + ' tag rule(s) created for [' + tags.join(', ') + '].'
    + (failed ? ' ' + failed + ' already existed or failed.' : '');
  showDialog(msg, 'Tags applied');
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

function renderTagsCellInner(tags, path) {
  let html = '';
  if (tags.length) {
    const overflow = tags.length > 1;
    html += '<span class="file-tag-list' + (overflow ? ' file-tag-list--overflow' : '') + '">';
    html += fileTagChipHtml(tags[0]);
    if (overflow) {
      html += '<button type="button" class="file-tag-more file-tag-more-trigger"'
        + ' aria-label="Show all ' + tags.length + ' tags" title="Show all tags">'
        + '+' + (tags.length - 1) + '</button>';
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
    if (sug) sug.remove();
    const wrap = document.getElementById('rowTagPopoverInput')?.parentNode;
    wrap?.classList.remove('tag-picker-input-wrap');
  }
  if (backdrop) backdrop.hidden = true;
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
  const allRules = await fetchAllTagRules();
  const tagsOnPathMap = tagsOnPath(allRules, path);
  const existingTagNames = [...new Set(allRules.map(function (t) { return t.tag; }))].sort(function (a, b) {
    return a.localeCompare(b);
  });

  function syncTagsCellFromMap() {
    updateTagsCell(path, [...tagsOnPathMap.keys()]);
  }

  function renderExisting() {
    renderExistingTagChips(existingEl, tagsOnPathMap, async function (tagName) {
      const ids = tagsOnPathMap.get(tagName) || [];
      if (!ids.length) return;
      applyBtn.disabled = true;
      const result = await deleteTagRuleIds(ids);
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
    });
  }
  renderExisting();

  const refresh = setupTagPickerListeners(
    inputEl, chipsEl, pendingTags, existingTagNames, 'rowTagPopoverSuggestions', signal
  );
  refresh();
  renderTagSuggestions(inputEl, existingTagNames, pendingTags, function (picked) {
    pendingTags.add(picked);
    inputEl.value = '';
    refresh();
  }, 'rowTagPopoverSuggestions');

  backdrop.hidden = false;
  positionRowTagPopover(anchorEl, pop);
  scheduleRowTagPopoverPosition();
  inputEl.focus();

  const onApply = async function () {
    commitTagInput(inputEl, pendingTags);
    refresh();
    if (!pendingTags.size) {
      closeRowTagPopover();
      return;
    }
    applyBtn.disabled = true;
    const tags = [...pendingTags];
    const { created, failed } = await applyTagRules(tags, [path]);
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
  // CSP-safe: keep chip navigation without bubbling into row handlers.
  document.getElementById('fileTable')?.addEventListener('click', function (e) {
    const chip = e.target.closest('a.file-tag-chip');
    if (chip) e.stopPropagation();
  });
  document.getElementById('fileTagsHoverPopover')?.addEventListener('click', function (e) {
    const chip = e.target.closest('a.file-tag-chip');
    if (chip) e.stopPropagation();
  });
}
