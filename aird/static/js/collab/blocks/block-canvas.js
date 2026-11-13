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

function glyph(item) {
  if (item.type === 'checkbox') return item.isChecked ? '☑' : '☐';
  if (item.type === 'bullet') return '•';
  return '';
}

export function mountBlockCanvas(root, list, hooks = {}) {
  const actorId = makeActorId(hooks.me || 'user');
  const clock = new HybridClock();
  let state = {
    items: fromApiList(list),
    activeItemId: null,
    cursorPosition: 0,
  };
  let filterTags = new Set(hooks.filterTags || []);
  let filterMode = hooks.filterMode === 'or' ? 'or' : 'and';
  let hideDone = false;
  let syncing = false;

  const dispatch = (action, { sync = true } = {}) => {
    const before = state.items;
    state = listReducer(state, action);
    paint();
    if (!sync) return;
    const ops = opsFromTransition(before, state.items, actorId, clock, list.id);
    if (!ops.length) return;
    saveBlocks(list.id, state.items).catch(() => {});
    enqueueAndSync(list.id, ops, { actorId, clock, items: state.items }).then((result) => {
      if (result?.list) {
        state = { ...state, items: fromApiList(result.list) };
        paint();
        hooks.onChange?.(result.list);
      } else if (result?.items) {
        hooks.onChange?.({ ...list, items: result.items.map(apiItem) });
      }
    }).catch((err) => hooks.onError?.(err.message || 'Sync failed'));
  };

  const paint = () => {
    const visible = state.items.filter((item) => {
      if (hideDone && item.type === 'checkbox' && item.isChecked) return false;
      if (!filterTags.size) return true;
      const tags = new Set((item.tags || []).map((t) => String(t).toLowerCase()));
      const wanted = [...filterTags];
      return filterMode === 'or'
        ? wanted.some((t) => tags.has(t))
        : wanted.every((t) => tags.has(t));
    });
    root.innerHTML = `
      <div class="block-canvas" data-list="${esc(list.id)}">
        <div class="block-filter">
          <input class="block-tag-search" data-tag-search placeholder="Filter tags" />
          <div class="block-tag-chips" data-chips></div>
          <label class="block-filter-mode"><input type="checkbox" data-or ${filterMode === 'or' ? 'checked' : ''}/> OR</label>
        </div>
        <div class="block-rows" data-rows>
          ${visible.map((item) => rowHtml(item)).join('') || '<p class="collab-empty">Empty list — start typing</p>'}
        </div>
      </div>`;
    wire();
    const active = root.querySelector(`[data-id="${CSS.escape(state.activeItemId || '')}"] .block-input`);
    if (active) {
      active.focus();
      const pos = Math.min(state.cursorPosition, active.value.length);
      active.setSelectionRange(pos, pos);
    }
  };

  const rowHtml = (item) => `
    <div class="block-row ${item.isChecked ? 'is-done' : ''} ${state.activeItemId === item.id ? 'is-active' : ''}"
         data-id="${esc(item.id)}" data-type="${esc(item.type)}" style="--depth:${item.depth}">
      <button type="button" class="block-grip" data-grip draggable="true" aria-label="Drag">⋮⋮</button>
      <button type="button" class="block-glyph" data-glyph aria-label="Change type">${glyph(item) || '¶'}</button>
      <textarea class="block-input" data-input rows="1">${esc(item.content)}</textarea>
      <button type="button" class="block-del" data-del aria-label="Delete">✕</button>
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
        root.querySelectorAll('.block-row').forEach((el) => el.classList.toggle('is-active', el.dataset.id === id));
      });
      field.addEventListener('input', () => {
        resize();
        dispatch({ type: 'UPDATE_CONTENT', payload: { itemId: id, content: field.value } });
      });
      field.addEventListener('keydown', (event) => onKey(event, id, field));
      field.addEventListener('paste', (event) => {
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
        const ops = opsFromTransition(before, state.items, actorId, clock, list.id);
        enqueueAndSync(list.id, ops, { actorId, clock, items: state.items }).then((result) => {
          if (result?.list) {
            state = { ...state, items: fromApiList(result.list) };
            paint();
            hooks.onChange?.(result.list);
          }
        });
      });
    });

    root.querySelectorAll('[data-glyph]').forEach((button) => {
      button.addEventListener('click', (event) => {
        const id = button.closest('.block-row').dataset.id;
        openTypeMenu(event.currentTarget, id);
      });
    });
    root.querySelectorAll('[data-del]').forEach((button) => {
      button.addEventListener('click', () => {
        dispatch({ type: 'DELETE_ITEM', payload: { itemId: button.closest('.block-row').dataset.id } });
      });
    });
    root.querySelectorAll('[data-grip]').forEach((grip) => {
      grip.addEventListener('dragstart', (event) => {
        event.dataTransfer.setData('text/block-id', grip.closest('.block-row').dataset.id);
      });
    });
    const rows = root.querySelector('[data-rows]');
    rows?.addEventListener('dragover', (event) => event.preventDefault());
    rows?.addEventListener('drop', (event) => {
      const id = event.dataTransfer.getData('text/block-id');
      const target = event.target.closest?.('.block-row')?.dataset.id;
      if (!id || !target || id === target) return;
      const items = [...state.items];
      const from = items.findIndex((row) => row.id === id);
      const to = items.findIndex((row) => row.id === target);
      if (from < 0 || to < 0) return;
      const [moved] = items.splice(from, 1);
      items.splice(to, 0, moved);
      const before = state.items;
      state = { ...state, items: rekey(items) };
      paint();
      const ops = opsFromTransition(before, state.items, actorId, clock, list.id);
      enqueueAndSync(list.id, ops, { actorId, clock, items: state.items }).then((result) => {
        if (result?.list) hooks.onChange?.(result.list);
      });
    });

    root.querySelector('[data-or]')?.addEventListener('change', (event) => {
      filterMode = event.target.checked ? 'or' : 'and';
      paint();
    });
    const search = root.querySelector('[data-tag-search]');
    search?.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        const tag = search.value.trim().toLowerCase();
        if (!tag) return;
        filterTags.add(tag);
        search.value = '';
        paint();
      }
      if (event.key === 'Escape') {
        filterTags.clear();
        paint();
        root.querySelector('.block-input')?.focus();
      }
    });
    paintChips();
  };

  const paintChips = () => {
    const box = root.querySelector('[data-chips]');
    if (!box) return;
    box.innerHTML = [...filterTags].map((tag) => (
      `<button type="button" data-clear-tag="${esc(tag)}">${esc(tag)} ✕</button>`
    )).join('');
    box.querySelectorAll('[data-clear-tag]').forEach((button) => {
      button.addEventListener('click', () => {
        filterTags.delete(button.dataset.clearTag);
        paint();
      });
    });
  };

  const onKey = (event, id, field) => {
    const item = state.items.find((row) => row.id === id);
    if (!item) return;
    const start = field.selectionStart || 0;
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!item.content.trim() && (item.type === 'checkbox' || item.type === 'bullet')) {
        dispatch({ type: 'CONVERT_TYPE', payload: { itemId: id, newType: 'paragraph' } });
        dispatch({ type: 'OUTDENT_ITEM', payload: { itemId: id } }, { sync: true });
        return;
      }
      dispatch({ type: 'SPLIT_ITEM', payload: { itemId: id, splitIndex: start } });
      return;
    }
    if (event.key === 'Backspace' && start === 0 && field.selectionEnd === 0) {
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
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      const rows = [...root.querySelectorAll('.block-input')];
      const index = rows.indexOf(field);
      const next = rows[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (!next) return;
      event.preventDefault();
      const col = field.selectionStart || 0;
      next.focus();
      const pos = Math.min(col, next.value.length);
      next.setSelectionRange(pos, pos);
    }
  };

  const openTypeMenu = (anchor, id) => {
    document.querySelector('.block-float')?.remove();
    const node = document.createElement('div');
    node.className = 'block-float';
    node.innerHTML = ['checkbox|To-Do', 'bullet|Bullet', 'paragraph|Text'].map((row) => {
      const [type, label] = row.split('|');
      return `<button type="button" data-type="${type}">${label}</button>`;
    }).join('');
    document.body.appendChild(node);
    const rect = anchor.getBoundingClientRect();
    node.style.left = `${rect.left}px`;
    node.style.top = `${rect.bottom + 4}px`;
    node.addEventListener('click', (event) => {
      const type = event.target.closest('[data-type]')?.dataset.type;
      if (!type) return;
      node.remove();
      dispatch({ type: 'CONVERT_TYPE', payload: { itemId: id, newType: type } });
    });
  };

  if (!state.items.length) {
    dispatch({ type: 'INSERT_EMPTY', payload: { afterId: null, type: 'paragraph', depth: 0 } }, { sync: true });
  } else paint();

  root._blocks = {
    focusTagSearch() { root.querySelector('[data-tag-search]')?.focus(); },
    async sync() {
      if (syncing) return;
      syncing = true;
      try {
        const result = await pushPull(list.id, { actorId, clock, items: state.items });
        if (result.list) {
          state = { ...state, items: fromApiList(result.list) };
          paint();
          hooks.onChange?.(result.list);
        }
      } finally {
        syncing = false;
      }
    },
    exportMarkdown() { return toMarkdown(list.title, state.items); },
    setList(next) {
      list = next;
      state = { ...state, items: fromApiList(next) };
      paint();
    },
  };

  saveBlocks(list.id, state.items).catch(() => {});
  return root._blocks;
}

function rekey(items) {
  return items.map((item, index, all) => ({
    ...item,
    order: item.order || `${(index + 1).toFixed(8)}#0000`,
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
  };
}
