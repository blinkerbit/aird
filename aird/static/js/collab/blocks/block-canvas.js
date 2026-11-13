import { HybridClock } from './hlc.js';
import { listReducer, opsFromTransition } from './list-reducer.js';
import { parseMarkdown, toMarkdown } from './markdown-io.js';
import { saveBlocks } from './local-db.js';
import { enqueueAndSync, makeActorId, pushPull } from './sync-client.js';
import { fromApiList } from './types.js';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function tagName(value) {
  return String(value || '').trim().replace(/^#/, '').toLowerCase();
}

function childCount(items, index) {
  const depth = items[index].depth;
  let count = 0;
  for (let i = index + 1; i < items.length; i += 1) {
    if (items[i].depth <= depth) break;
    count += 1;
  }
  return count;
}

function hiddenByFold(items) {
  const hidden = new Set();
  let skip = null;
  items.forEach((item) => {
    if (skip !== null) {
      if (item.depth > skip) {
        hidden.add(item.id);
        return;
      }
      skip = null;
    }
    if (item.isCollapsed) skip = item.depth;
  });
  return hidden;
}

function caretColumn(field) {
  const pos = field.selectionStart || 0;
  const probe = document.createElement('span');
  const style = getComputedStyle(field);
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${style.font};letter-spacing:${style.letterSpacing}`;
  const line = field.value.slice(0, pos).split('\n').pop();
  probe.textContent = line || '';
  document.body.appendChild(probe);
  const width = probe.getBoundingClientRect().width;
  probe.remove();
  return width;
}

function offsetAtColumn(field, column, edge) {
  const style = getComputedStyle(field);
  const probe = document.createElement('span');
  probe.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font:${style.font};letter-spacing:${style.letterSpacing}`;
  document.body.appendChild(probe);
  const lines = field.value.split('\n');
  const lineIndex = edge === 'start' ? 0 : lines.length - 1;
  const text = lines[lineIndex] || '';
  let best = 0;
  for (let i = 0; i <= text.length; i += 1) {
    probe.textContent = text.slice(0, i);
    if (probe.getBoundingClientRect().width <= column) best = i;
    else break;
  }
  probe.remove();
  const lineStart = lines.slice(0, lineIndex).reduce((sum, line) => sum + line.length + 1, 0);
  return Math.min(field.value.length, lineStart + best);
}

export function mountBlockCanvas(root, list, hooks = {}) {
  const actorId = makeActorId(hooks.me || 'user');
  const clock = new HybridClock();
  let state = {
    items: fromApiList(list),
    activeItemId: null,
    cursorPosition: 0,
  };
  let filterTags = new Set((hooks.filterTags || []).map(tagName).filter(Boolean));
  let filterMode = hooks.filterMode === 'or' ? 'or' : 'and';
  let hideDone = false;
  let syncing = false;
  let undo = null;
  let undoTimer = 0;
  let selectMode = false;
  let selected = new Set();
  let preferredX = null;
  let tagQuery = '';
  let suggestOpen = false;
  let contentTimer = 0;
  const pinned = () => new Set((hooks.pinnedTags || []).map(tagName));

  const syncOps = (before) => {
    const ops = opsFromTransition(before, state.items, actorId, clock, list.id);
    if (!ops.length) return;
    saveBlocks(list.id, state.items).catch(() => {});
    enqueueAndSync(list.id, ops, { actorId, clock, items: state.items }).then((result) => {
      if (result?.list) adoptServer(result.list);
      else if (result?.items) hooks.onChange?.({ ...list, items: result.items.map(apiItem) });
    }).catch((err) => hooks.onError?.(err.message || 'Sync failed'));
  };

  const adoptServer = (nextList) => {
    const next = fromApiList(nextList);
    const focused = document.activeElement?.closest?.('.block-row')?.dataset.id;
    if (focused) {
      const local = state.items.find((item) => item.id === focused);
      const index = next.findIndex((item) => item.id === focused);
      if (local && index >= 0) next[index] = { ...next[index], content: local.content };
    }
    state = { ...state, items: next };
    if (!root.contains(document.activeElement)) paint();
    hooks.onChange?.(nextList);
  };

  const dispatch = (action, { quiet = false } = {}) => {
    const before = state.items;
    state = listReducer(state, action);
    if (action.type === 'UPDATE_CONTENT') {
      clearTimeout(contentTimer);
      contentTimer = setTimeout(() => syncOps(before), 350);
      return;
    }
    if (!quiet) paint();
    syncOps(before);
  };

  const tagStats = () => {
    const counts = new Map();
    state.items.forEach((item) => {
      (item.tags || []).forEach((tag) => {
        const name = tagName(tag);
        if (!name) return;
        counts.set(name, (counts.get(name) || 0) + 1);
      });
    });
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  };

  const visibleItems = () => {
    const folded = filterTags.size ? new Set() : hiddenByFold(state.items);
    return state.items.filter((item) => {
      if (folded.has(item.id)) return false;
      if (hideDone && item.type === 'checkbox' && item.isChecked) return false;
      if (!filterTags.size) return true;
      const tags = new Set((item.tags || []).map(tagName));
      const wanted = [...filterTags];
      return filterMode === 'or' ? wanted.some((tag) => tags.has(tag)) : wanted.every((tag) => tags.has(tag));
    });
  };

  const paint = () => {
    const visible = visibleItems();
    const stats = tagStats();
    const pins = pinned();
    root.innerHTML = `
      <div class="block-canvas" data-list="${esc(list.id)}">
        <div class="block-filter">
          <label class="block-search">
            <span aria-hidden="true">⌕</span>
            <input class="block-tag-search" data-tag-search placeholder="Filter tags" value="${esc(tagQuery)}" />
            ${tagQuery ? '<button type="button" data-clear-query aria-label="Clear">✕</button>' : ''}
          </label>
          <button type="button" class="block-logic" data-logic>${filterMode === 'or' ? 'OR' : 'AND'}</button>
          <div class="block-tag-chips" data-chips>
            ${stats.map(([tag]) => chipHtml(tag, filterTags.has(tag))).join('')}
          </div>
        </div>
        <div class="block-suggest" data-suggest hidden></div>
        <div class="block-rows" data-rows>
          ${visible.map((item, index) => rowHtml(item, visible[index - 1])).join('') || '<p class="collab-empty">Type or press Enter to add an item</p>'}
        </div>
        <button type="button" class="block-fab" data-add aria-label="Add block">+</button>
        <div class="block-keys" data-keys>
          <button type="button" data-morph="checkbox">Check</button>
          <button type="button" data-morph="bullet">Bullet</button>
          <button type="button" data-morph="paragraph">Text</button>
          <button type="button" data-outdent>Outdent</button>
          <button type="button" data-indent>Indent</button>
        </div>
        ${selectMode ? cabHtml() : ''}
        <div class="block-undo" data-undo ${undo ? '' : 'hidden'}>Block deleted. <button type="button" data-undo-btn>Undo</button></div>
      </div>`;
    wire();
    const active = root.querySelector(`[data-id="${CSS.escape(state.activeItemId || '')}"] .block-input`);
    if (active) {
      active.focus();
      const pos = Math.min(state.cursorPosition, active.value.length);
      active.setSelectionRange(pos, pos);
    }
    if (suggestOpen) paintSuggest();
  };

  const chipHtml = (tag, on) => `
    <button type="button" class="block-chip ${on ? 'is-on' : ''}" data-chip="${esc(tag)}">
      #${esc(tag)}${on ? '<span data-chip-x aria-hidden="true">✕</span>' : ''}
    </button>`;

  const rowHtml = (item, prev) => {
    const index = state.items.findIndex((row) => row.id === item.id);
    const kids = childCount(state.items, index);
    const gap = Boolean(prev) && (prev.type !== item.type || prev.type === 'paragraph' || item.type === 'paragraph');
    const picked = selected.has(item.id);
    return `
      <div class="block-row ${item.isChecked ? 'is-done' : ''} ${state.activeItemId === item.id ? 'is-active' : ''} ${picked ? 'is-selected' : ''} ${gap ? 'is-gap' : ''}"
           data-id="${esc(item.id)}" data-type="${esc(item.type)}" style="--depth:${item.depth}">
        ${kids ? `<button type="button" class="block-fold ${item.isCollapsed ? 'is-shut' : ''}" data-fold aria-label="${item.isCollapsed ? 'Expand' : 'Collapse'}"></button>` : '<span class="block-fold"></span>'}
        <button type="button" class="block-grip" data-grip draggable="true" aria-label="Drag">⋮⋮</button>
        <button type="button" class="block-glyph" data-glyph aria-label="${item.type === 'checkbox' ? 'Toggle task' : 'Change type'}">${glyphHtml(item)}</button>
        <div class="block-main">
          <textarea class="block-input" data-input rows="1">${esc(item.content)}</textarea>
          ${item.isCollapsed && kids ? `<span class="block-nested">${kids} nested item${kids === 1 ? '' : 's'}</span>` : ''}
        </div>
        <div class="block-actions">
          ${selectMode ? `<button type="button" class="block-pick ${picked ? 'is-on' : ''}" data-pick aria-label="Select"></button>` : ''}
          <button type="button" class="block-tag" data-tag aria-label="Tags">#</button>
          <button type="button" class="block-del" data-del aria-label="Delete">✕</button>
        </div>
      </div>`;
  };

  const glyphHtml = (item) => {
    if (item.type === 'checkbox') return `<span class="block-box ${item.isChecked ? 'is-on' : ''}"></span>`;
    if (item.type === 'bullet') return '<span class="block-dot"></span>';
    return '<span class="block-para">¶</span>';
  };

  const cabHtml = () => `
    <div class="block-cab">
      <strong>${selected.size} selected</strong>
      <button type="button" data-batch="done">Mark done</button>
      <button type="button" data-batch="checkbox">Checkboxes</button>
      <button type="button" data-batch="indent">Indent</button>
      <button type="button" data-batch="outdent">Outdent</button>
      <button type="button" data-batch="delete">Delete</button>
      <button type="button" data-batch="clear">Done</button>
    </div>`;

  const wire = () => {
    root.querySelectorAll('[data-input]').forEach((field) => {
      const row = field.closest('.block-row');
      const id = row.dataset.id;
      const resize = () => {
        field.style.height = 'auto';
        field.style.height = `${Math.max(28, field.scrollHeight)}px`;
      };
      resize();
      field.addEventListener('focus', () => {
        state = { ...state, activeItemId: id, cursorPosition: field.selectionStart || 0 };
        root.querySelector('.block-canvas')?.classList.add('is-editing');
        root.querySelectorAll('.block-row').forEach((el) => el.classList.toggle('is-active', el.dataset.id === id));
      });
      field.addEventListener('blur', () => {
        setTimeout(() => {
          if (!root.querySelector('.block-input:focus')) root.querySelector('.block-canvas')?.classList.remove('is-editing');
        }, 120);
      });
      field.addEventListener('input', () => {
        resize();
        state = { ...state, cursorPosition: field.selectionStart || 0 };
        dispatch({ type: 'UPDATE_CONTENT', payload: { itemId: id, content: field.value } });
      });
      field.addEventListener('keyup', () => {
        state = { ...state, cursorPosition: field.selectionStart || 0 };
      });
      field.addEventListener('keydown', (event) => onKey(event, id, field));
      field.addEventListener('paste', (event) => onPaste(event, id));
    });

    root.querySelectorAll('[data-glyph]').forEach((button) => {
      button.addEventListener('click', (event) => {
        const id = button.closest('.block-row').dataset.id;
        const item = state.items.find((row) => row.id === id);
        if (item?.type === 'checkbox' && !event.shiftKey && !event.altKey) {
          dispatch({ type: 'TOGGLE_CHECKBOX', payload: { itemId: id } });
          return;
        }
        openTypeMenu(button, id);
      });
      button.addEventListener('contextmenu', (event) => {
        event.preventDefault();
        openTypeMenu(button, button.closest('.block-row').dataset.id);
      });
    });
    root.querySelectorAll('[data-fold]').forEach((button) => {
      if (!button.dataset.fold && button.tagName !== 'BUTTON') return;
      button.addEventListener('click', () => {
        dispatch({ type: 'TOGGLE_COLLAPSE', payload: { itemId: button.closest('.block-row').dataset.id } });
      });
    });
    root.querySelectorAll('[data-del]').forEach((button) => {
      button.addEventListener('click', () => removeItem(button.closest('.block-row').dataset.id));
    });
    root.querySelectorAll('[data-tag]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        openTagEditor(button, button.closest('.block-row').dataset.id);
      });
    });
    root.querySelectorAll('[data-pick]').forEach((button) => {
      button.addEventListener('click', () => toggleSelect(button.closest('.block-row').dataset.id));
    });
    root.querySelectorAll('[data-chip]').forEach((button) => {
      button.addEventListener('click', (event) => {
        const tag = button.dataset.chip;
        if (event.target.closest('[data-chip-x]')) filterTags.delete(tag);
        else if (filterTags.has(tag)) filterTags.delete(tag);
        else filterTags.add(tag);
        paint();
      });
    });
    root.querySelector('[data-logic]')?.addEventListener('click', () => {
      filterMode = filterMode === 'and' ? 'or' : 'and';
      paint();
    });
    root.querySelector('[data-add]')?.addEventListener('click', () => {
      const afterId = state.activeItemId || state.items[state.items.length - 1]?.id || null;
      dispatch({ type: 'INSERT_EMPTY', payload: { afterId, type: 'paragraph', depth: 0 } });
    });
    root.querySelector('[data-morph]')?.parentElement?.querySelectorAll('[data-morph]').forEach((button) => {
      button.addEventListener('click', () => morph(button.dataset.morph));
    });
    root.querySelector('[data-indent]')?.addEventListener('click', () => morphDepth(1));
    root.querySelector('[data-outdent]')?.addEventListener('click', () => morphDepth(-1));
    root.querySelector('[data-undo-btn]')?.addEventListener('click', restoreUndo);
    root.querySelectorAll('[data-batch]').forEach((button) => {
      button.addEventListener('click', () => runBatch(button.dataset.batch));
    });

    const search = root.querySelector('[data-tag-search]');
    search?.addEventListener('input', () => {
      tagQuery = search.value;
      suggestOpen = true;
      paintSuggest();
    });
    search?.addEventListener('focus', () => {
      suggestOpen = true;
      paintSuggest();
    });
    search?.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        filterTags.clear();
        tagQuery = '';
        suggestOpen = false;
        paint();
        root.querySelector('.block-input')?.focus();
      }
    });
    root.querySelector('[data-clear-query]')?.addEventListener('click', () => {
      tagQuery = '';
      suggestOpen = false;
      paint();
    });

    wireDrag();
    wireSwipe();
  };

  const closeFloats = (event) => {
    if (event.target.closest?.('.block-float, .block-suggest, [data-tag-search], [data-tag], [data-glyph]')) return;
    document.querySelector('.block-float')?.remove();
    if (suggestOpen) {
      suggestOpen = false;
      root.querySelector('[data-suggest]')?.setAttribute('hidden', '');
    }
  };

  if (!root._floatCloser) {
    root._floatCloser = (event) => closeFloats(event);
    document.addEventListener('pointerdown', root._floatCloser, true);
  }

  const paintSuggest = () => {
    const box = root.querySelector('[data-suggest]');
    if (!box) return;
    const query = tagName(tagQuery);
    const stats = tagStats().filter(([tag]) => !query || tag.includes(query));
    const exact = stats.some(([tag]) => tag === query);
    const pins = pinned();
    box.hidden = !suggestOpen;
    box.innerHTML = `
      ${stats.slice(0, 8).map(([tag, count]) => `
        <div class="block-suggest-row">
          <button type="button" data-use-tag="${esc(tag)}">#${esc(tag)} <span>${count}</span></button>
          <button type="button" data-pin-tag="${esc(tag)}" aria-pressed="${pins.has(tag)}">${pins.has(tag) ? 'Pinned' : 'Pin'}</button>
        </div>`).join('')}
      ${query && !exact ? `<button type="button" data-create-tag="${esc(query)}">Create #${esc(query)}</button>` : ''}
      ${!stats.length && !query ? '<p class="collab-empty">No tags yet</p>' : ''}`;
    box.querySelectorAll('[data-use-tag]').forEach((button) => {
      button.addEventListener('click', () => {
        filterTags.add(button.dataset.useTag);
        tagQuery = '';
        suggestOpen = false;
        paint();
      });
    });
    box.querySelectorAll('[data-pin-tag]').forEach((button) => {
      button.addEventListener('click', (event) => {
        event.stopPropagation();
        hooks.onPinTag?.(button.dataset.pinTag, !pins.has(button.dataset.pinTag));
        paintSuggest();
      });
    });
    box.querySelector('[data-create-tag]')?.addEventListener('click', () => {
      const tag = root.querySelector('[data-create-tag]').dataset.createTag;
      const id = state.activeItemId || state.items[0]?.id;
      filterTags.add(tag);
      tagQuery = '';
      suggestOpen = false;
      if (id) {
        const item = state.items.find((row) => row.id === id);
        const tags = [...new Set([...(item?.tags || []).map(tagName), tag])];
        dispatch({ type: 'SET_TAGS', payload: { itemId: id, tags } });
      } else paint();
    });
  };

  const wireDrag = () => {
    root.querySelectorAll('[data-grip]').forEach((grip) => {
      grip.addEventListener('dragstart', (event) => {
        event.dataTransfer.setData('text/block-id', grip.closest('.block-row').dataset.id);
        grip.closest('.block-row').classList.add('is-lifting');
      });
      grip.addEventListener('dragend', () => {
        root.querySelectorAll('.is-lifting, .is-drop').forEach((el) => el.classList.remove('is-lifting', 'is-drop'));
      });
    });
    const rows = root.querySelector('[data-rows]');
    rows?.addEventListener('dragover', (event) => {
      event.preventDefault();
      const target = event.target.closest?.('.block-row');
      root.querySelectorAll('.is-drop').forEach((el) => el.classList.remove('is-drop'));
      target?.classList.add('is-drop');
    });
    rows?.addEventListener('drop', (event) => {
      const id = event.dataTransfer.getData('text/block-id');
      const target = event.target.closest?.('.block-row')?.dataset.id;
      moveBlock(id, target);
    });
  };

  const moveBlock = (id, target) => {
    if (!id || !target || id === target) return;
    const items = [...state.items];
    const from = items.findIndex((row) => row.id === id);
    const to = items.findIndex((row) => row.id === target);
    if (from < 0 || to < 0) return;
    const [moved] = items.splice(from, 1);
    items.splice(to, 0, moved);
    const before = state.items;
    state = { ...state, items: rekey(items), activeItemId: id };
    paint();
    syncOps(before);
  };

  const wireSwipe = () => {
    root.querySelectorAll('.block-row').forEach((row) => {
      let startX = 0;
      let startY = 0;
      let tracking = false;
      let timer = 0;
      let long = false;
      row.addEventListener('pointerdown', (event) => {
        if (event.button !== 0 || event.target.closest('textarea, input, button')) return;
        startX = event.clientX;
        startY = event.clientY;
        tracking = true;
        long = false;
        timer = window.setTimeout(() => {
          long = true;
          row.classList.add('is-lifting');
        }, 250);
      });
      row.addEventListener('pointermove', (event) => {
        if (!tracking) return;
        const dx = event.clientX - startX;
        const dy = event.clientY - startY;
        if (Math.abs(dx) > 8 || Math.abs(dy) > 8) window.clearTimeout(timer);
        if (!long && Math.abs(dx) > 12 && Math.abs(dx) > Math.abs(dy)) row.style.transform = `translateX(${dx}px)`;
      });
      const finish = (event) => {
        if (!tracking) return;
        tracking = false;
        window.clearTimeout(timer);
        const dx = event.clientX - startX;
        const dy = event.clientY - startY;
        row.style.transform = '';
        row.classList.remove('is-lifting');
        const id = row.dataset.id;
        if (long && Math.hypot(dx, dy) < 8) {
          selectMode = true;
          toggleSelect(id);
          return;
        }
        if (long && Math.abs(dy) > 24) return;
        if (dx > 40 && Math.abs(dx) > Math.abs(dy)) {
          dispatch({ type: 'INDENT_ITEM', payload: { itemId: id } });
          return;
        }
        if (dx < -72 && Math.abs(dx) > Math.abs(dy)) removeItem(id);
      };
      row.addEventListener('pointerup', finish);
      row.addEventListener('pointercancel', () => {
        tracking = false;
        window.clearTimeout(timer);
        row.style.transform = '';
        row.classList.remove('is-lifting');
      });
    });
  };

  const toggleSelect = (id) => {
    if (selected.has(id)) selected.delete(id);
    else selected.add(id);
    if (!selected.size) selectMode = false;
    paint();
  };

  const runBatch = (kind) => {
    const ids = [...selected];
    if (kind === 'clear') {
      selectMode = false;
      selected = new Set();
      paint();
      return;
    }
    const before = state.items;
    ids.forEach((id) => {
      if (kind === 'done') state = listReducer(state, { type: 'TOGGLE_CHECKBOX', payload: { itemId: id } });
      if (kind === 'checkbox') state = listReducer(state, { type: 'CONVERT_TYPE', payload: { itemId: id, newType: 'checkbox' } });
      if (kind === 'indent') state = listReducer(state, { type: 'INDENT_ITEM', payload: { itemId: id } });
      if (kind === 'outdent') state = listReducer(state, { type: 'OUTDENT_ITEM', payload: { itemId: id } });
      if (kind === 'delete') state = listReducer(state, { type: 'DELETE_ITEM', payload: { itemId: id } });
    });
    selectMode = false;
    selected = new Set();
    paint();
    syncOps(before);
  };

  const removeItem = (id) => {
    const index = state.items.findIndex((row) => row.id === id);
    if (index < 0) return;
    undo = { items: state.items, activeItemId: id, cursorPosition: state.items[index].content.length };
    clearTimeout(undoTimer);
    undoTimer = window.setTimeout(() => {
      undo = null;
      root.querySelector('[data-undo]')?.setAttribute('hidden', '');
    }, 5000);
    dispatch({ type: 'DELETE_ITEM', payload: { itemId: id } });
  };

  const restoreUndo = () => {
    if (!undo) return;
    clearTimeout(undoTimer);
    const before = state.items;
    state = { ...state, items: undo.items, activeItemId: undo.activeItemId, cursorPosition: undo.cursorPosition };
    undo = null;
    paint();
    syncOps(before);
  };

  const morph = (type) => {
    if (!state.activeItemId) return;
    dispatch({ type: 'CONVERT_TYPE', payload: { itemId: state.activeItemId, newType: type } });
  };

  const morphDepth = (dir) => {
    if (!state.activeItemId) return;
    dispatch({ type: dir > 0 ? 'INDENT_ITEM' : 'OUTDENT_ITEM', payload: { itemId: state.activeItemId } });
  };

  const onPaste = (event, id) => {
    const text = event.clipboardData?.getData('text/plain') || '';
    if (!text.includes('\n')) return;
    event.preventDefault();
    const parsed = parseMarkdown(text, list.id);
    if (!parsed.length) return;
    const before = state.items;
    let items = state.items.filter((row) => row.id !== id || row.content);
    const index = items.findIndex((row) => row.id === id);
    if (index >= 0) items.splice(index, 1, ...parsed);
    else items = items.concat(parsed);
    state = { ...state, items, activeItemId: parsed[parsed.length - 1].id, cursorPosition: 0 };
    paint();
    syncOps(before);
  };

  const onKey = (event, id, field) => {
    const item = state.items.find((row) => row.id === id);
    if (!item) return;
    const start = field.selectionStart || 0;
    const end = field.selectionEnd || 0;
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      root.querySelector('[data-tag-search]')?.focus();
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      state = { ...state, cursorPosition: start };
      if (!item.content && (item.type === 'checkbox' || item.type === 'bullet')) {
        dispatch({ type: 'CONVERT_TYPE', payload: { itemId: id, newType: 'paragraph', depth: 0 } });
        return;
      }
      dispatch({ type: 'SPLIT_ITEM', payload: { itemId: id, splitIndex: start } });
      return;
    }
    if (event.key === 'Backspace' && start === 0 && end === 0) {
      event.preventDefault();
      if (item.depth > 0) {
        dispatch({ type: 'OUTDENT_ITEM', payload: { itemId: id } });
        return;
      }
      if (item.type === 'checkbox' || item.type === 'bullet') {
        dispatch({ type: 'CONVERT_TYPE', payload: { itemId: id, newType: 'paragraph' } });
        return;
      }
      if (!item.content) {
        dispatch({ type: 'DELETE_ITEM', payload: { itemId: id } });
        return;
      }
      dispatch({ type: 'MERGE_PREV', payload: { itemId: id } });
      return;
    }
    if (event.key === 'Tab') {
      event.preventDefault();
      dispatch({ type: event.shiftKey ? 'OUTDENT_ITEM' : 'INDENT_ITEM', payload: { itemId: id } });
      return;
    }
    if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
      event.preventDefault();
      dispatch({ type: 'REORDER', payload: { itemId: id, direction: event.key === 'ArrowUp' ? 'up' : 'down' } });
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter' && item.type === 'checkbox') {
      event.preventDefault();
      dispatch({ type: 'TOGGLE_CHECKBOX', payload: { itemId: id } });
      return;
    }
    if ((event.metaKey || event.ctrlKey) && event.shiftKey && event.key.toLowerCase() === 'h') {
      event.preventDefault();
      hideDone = !hideDone;
      paint();
      return;
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const rows = [...root.querySelectorAll('.block-input')];
      const index = rows.indexOf(field);
      const next = rows[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (!next || start !== end) return;
      const atEdge = event.key === 'ArrowUp' ? start === 0 : start === field.value.length;
      if (!atEdge && field.value.includes('\n')) return;
      event.preventDefault();
      if (preferredX == null) preferredX = caretColumn(field);
      next.focus();
      const pos = offsetAtColumn(next, preferredX, event.key === 'ArrowDown' ? 'start' : 'end');
      next.setSelectionRange(pos, pos);
      state = { ...state, activeItemId: next.closest('.block-row').dataset.id, cursorPosition: pos };
      return;
    }
    preferredX = null;
  };

  const openTypeMenu = (anchor, id) => {
    document.querySelector('.block-float')?.remove();
    const node = document.createElement('div');
    node.className = 'block-float';
    node.innerHTML = [
      ['checkbox', 'Checkbox'],
      ['bullet', 'Bullet'],
      ['paragraph', 'Text'],
    ].map(([type, label]) => `<button type="button" data-type="${type}">${label}</button>`).join('')
      + '<button type="button" data-details>Details</button>';
    document.body.appendChild(node);
    const rect = anchor.getBoundingClientRect();
    node.style.left = `${rect.left}px`;
    node.style.top = `${rect.bottom + 4}px`;
    node.addEventListener('click', (event) => {
      const type = event.target.closest('[data-type]')?.dataset.type;
      if (type) {
        node.remove();
        dispatch({ type: 'CONVERT_TYPE', payload: { itemId: id, newType: type } });
        return;
      }
      if (event.target.closest('[data-details]')) {
        node.remove();
        const item = state.items.find((row) => row.id === id);
        if (item) hooks.onInspect?.(item);
      }
    });
  };

  const openTagEditor = (anchor, id) => {
    document.querySelector('.block-float')?.remove();
    const item = state.items.find((row) => row.id === id);
    if (!item) return;
    const node = document.createElement('div');
    node.className = 'block-float';
    const tags = (item.tags || []).map(tagName).filter(Boolean);
    node.innerHTML = `
      ${tags.map((tag) => `<button type="button" data-drop-tag="${esc(tag)}">#${esc(tag)} ✕</button>`).join('')}
      <input data-new-tag placeholder="Add tag" />
      <button type="button" data-details>Details</button>`;
    document.body.appendChild(node);
    const rect = anchor.getBoundingClientRect();
    node.style.left = `${Math.max(8, rect.right - 180)}px`;
    node.style.top = `${rect.bottom + 4}px`;
    const commit = (next) => {
      node.remove();
      dispatch({ type: 'SET_TAGS', payload: { itemId: id, tags: next } });
    };
    node.querySelectorAll('[data-drop-tag]').forEach((button) => {
      button.addEventListener('click', () => commit(tags.filter((tag) => tag !== button.dataset.dropTag)));
    });
    node.querySelector('[data-new-tag]')?.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      const name = tagName(event.target.value);
      if (!name) return;
      commit([...new Set([...tags, name])]);
    });
    node.querySelector('[data-details]')?.addEventListener('click', () => {
      node.remove();
      hooks.onInspect?.(item);
    });
    node.querySelector('[data-new-tag]')?.focus();
  };

  if (!state.items.length) {
    dispatch({ type: 'INSERT_EMPTY', payload: { afterId: null, type: 'paragraph', depth: 0 } });
  } else paint();

  root._blocks = {
    focusTagSearch() {
      suggestOpen = true;
      root.querySelector('[data-tag-search]')?.focus();
      paintSuggest();
    },
    toggleTag(name) {
      const tag = tagName(name);
      if (!tag) return;
      if (filterTags.has(tag)) filterTags.delete(tag);
      else filterTags.add(tag);
      paint();
    },
    setHideDone(next) {
      hideDone = Boolean(next);
      paint();
    },
    hideDone: () => hideDone,
    activeItem: () => state.items.find((row) => row.id === state.activeItemId) || null,
    async sync() {
      if (syncing) return;
      syncing = true;
      try {
        const result = await pushPull(list.id, { actorId, clock, items: state.items });
        if (result.list) adoptServer(result.list);
      } finally {
        syncing = false;
      }
    },
    exportMarkdown() { return toMarkdown(list.title, state.items); },
    setList(next) {
      list = next;
      state = { ...state, items: fromApiList(next) };
      if (!root.contains(document.activeElement)) paint();
    },
    refreshPins() { paintSuggest(); },
  };

  saveBlocks(list.id, state.items).catch(() => {});
  return root._blocks;
}

function rekey(items) {
  return items.map((item, index) => ({
    ...item,
    order: `${String(index + 1).padStart(6, '0')}#0000`,
    updatedAt: Date.now(),
  }));
}

function apiItem(item) {
  return {
    id: item.id,
    text: item.content,
    content: item.content,
    kind: item.type,
    type: item.type,
    done: item.isChecked,
    depth: item.depth,
    order_key: item.order,
    labels: item.tags,
    tags: item.tags,
    collapsed: item.isCollapsed,
  };
}
