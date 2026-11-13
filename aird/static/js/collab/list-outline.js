import { apiJson } from '../collab-api.js';
import { tagChipHtml } from './tag-chips.js';
import { notePreview, noteTitle, renderMarkdown, wrapSelection } from './list-md.js';

const CHECK = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 8.2 6.4 11 12.5 4.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
const LINE_TYPES = [
  ['check', 'Todo', 'A checkbox you can finish'],
  ['bullet', 'Line', 'A short note'],
  ['note', 'Page', 'A markdown page'],
];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function kindOf(item) {
  const raw = String(item?.kind || '').toLowerCase();
  if (raw === 'bullet' || raw === 'note') return raw;
  return 'check';
}

function dueLabel(value) {
  if (!value) return 'Due';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Due';
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = new Date(date);
  day.setHours(0, 0, 0, 0);
  const diff = Math.round((day - today) / 86400000);
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function stampFor(choice, custom) {
  const date = new Date();
  date.setHours(9, 0, 0, 0);
  if (choice === 'tomorrow') date.setDate(date.getDate() + 1);
  if (choice === 'week') date.setDate(date.getDate() + 7);
  if (choice === 'date' && custom) return new Date(`${custom}T09:00:00`).toISOString();
  if (choice === 'clear') return '';
  return date.toISOString();
}

function kindLabel(kind) {
  if (kind === 'journal') return 'Journal';
  if (kind === 'bullet') return 'Notes';
  return 'Todo';
}

function docHead(list, hooks) {
  const prog = list.progress || {};
  const remain = Math.max(0, (prog.total || 0) - (prog.done || 0));
  const count = list.kind === 'todo'
    ? `<p class="ln-remain">${prog.total ? (remain ? `${remain} to-do${remain === 1 ? '' : 's'}` : 'All done') : 'No to-dos yet'}</p>`
    : `<p class="ln-remain">${esc(kindLabel(list.kind))}${list.read_only ? ' · Read only' : ''}</p>`;
  const blurb = typeof list.notes === 'string' && list.notes.trim()
    ? `<p class="ln-blurb">${esc(list.notes)}</p>` : '';
  const tags = (list.tags || []).map((tag) => tagChipHtml(tag)).join('');
  const menu = [
    hooks.onShare ? '<button type="button" data-share>Share to chat</button>' : '',
    hooks.onEdit ? '<button type="button" data-edit>Settings</button>' : '',
    hooks.onDeleteList ? '<button type="button" data-delete-list>Delete</button>' : '',
  ].filter(Boolean).join('');
  return `<header class="ln-doc-head">
    ${hooks.onBack ? '<button type="button" class="ln-back" data-back>Back</button>' : ''}
    <div class="ln-doc-titles">
      <h1>${esc(list.title || 'Untitled')}</h1>
      ${count}
      ${blurb}
      ${tags ? `<div class="lists-tag-row">${tags}</div>` : ''}
    </div>
    ${menu ? `<details class="ln-more"><summary aria-label="List actions">···</summary><div>${menu}</div></details>` : ''}
  </header>`;
}

export async function renderOutline(root, list, hooks = {}) {
  if (list.kind === 'journal') {
    paintJournal(root, list, hooks);
    return;
  }
  const items = list.items || [];
  const notes = items.filter((item) => kindOf(item) === 'note').map((item) => ({
    id: item.id,
    title: noteTitle(item.text),
  }));
  const open = items.find((item) => item.id === hooks.noteId && kindOf(item) === 'note');
  if (open) {
    paintNote(root, list, open, notes, hooks);
    return;
  }
  const selected = new Set(hooks.selected || []);
  const draftKind = list.kind === 'bullet' ? 'bullet' : 'check';
  const openItems = items.filter((item) => kindOf(item) !== 'check' || !item.done);
  const doneItems = items.filter((item) => kindOf(item) === 'check' && item.done);
  const placeholder = list.kind === 'bullet' ? 'New note' : 'New to-do';
  root.innerHTML = `
    <div class="ln-project">
    ${docHead(list, hooks)}
    <div class="list-outline" data-list="${esc(list.id)}">
      ${openItems.map((item) => rowHtml(item, selected.has(item.id))).join('')}
      ${list.can_edit ? `<div class="list-line is-draft" data-kind="${draftKind}">
        <button type="button" class="list-mark ${draftKind === 'bullet' ? 'is-bullet' : ''}" data-draft-kind aria-label="${draftKind === 'bullet' ? 'Line' : 'To-do'}"></button>
        <input class="list-line-input" data-draft placeholder="${placeholder}" maxlength="300" />
      </div>` : ''}
      ${doneItems.length ? `<details class="ln-logged"><summary>Completed ${doneItems.length}</summary>${doneItems.map((item) => rowHtml(item, selected.has(item.id))).join('')}</details>` : ''}
    </div>
    ${selected.size ? `<div class="list-select-bar"><span>${selected.size} selected</span><button type="button" data-turn>Turn into</button><button type="button" data-delete-selected>Delete</button></div>` : ''}
    </div>`;
  wireOutline(root, list, notes, hooks);
}

function rowHtml(item, on) {
  const kind = kindOf(item);
  if (kind === 'note') {
    const preview = notePreview(item.text);
    return `<button type="button" class="list-note-card" data-id="${esc(item.id)}" data-open-note="${esc(item.id)}">
      <span class="list-note-kicker">Page</span>
      <strong>${esc(noteTitle(item.text))}</strong>
      ${preview ? `<span>${esc(preview)}</span>` : '<span>Empty page</span>'}
    </button>`;
  }
  const mark = kind === 'bullet'
    ? `<button type="button" class="list-mark is-bullet" data-mark aria-label="Change line type"></button>`
    : `<button type="button" class="list-mark" data-mark aria-pressed="${item.done ? 'true' : 'false'}" aria-label="${item.done ? 'Mark not done' : 'Mark done'}">${item.done ? CHECK : ''}</button>`;
  return `<div class="list-line ${on ? 'is-selected' : ''} ${item.done ? 'is-done' : ''}" data-id="${esc(item.id)}" data-kind="${esc(kind)}">
    ${on ? '' : '<span class="list-grip" draggable="true" data-grip aria-label="Drag to reorder"></span>'}
    ${mark}
    <input class="list-line-input" data-text value="${esc(item.text || '')}" maxlength="300" />
    ${kind === 'check' ? `<button type="button" class="list-due ${item.due_at ? 'is-set' : ''}" data-due>${esc(item.due_at ? dueLabel(item.due_at) : 'Due')}</button>` : ''}
  </div>`;
}

function closeFloats() {
  document.querySelectorAll('.list-float').forEach((node) => node.remove());
}

function placeFloat(anchor, node) {
  const rect = anchor.getBoundingClientRect();
  const below = window.innerHeight - rect.bottom > 180;
  node.style.left = `${Math.max(8, rect.left)}px`;
  node.style.top = below ? `${rect.bottom + 4}px` : 'auto';
  node.style.bottom = below ? 'auto' : `${window.innerHeight - rect.top + 4}px`;
}

function wireOutline(root, list, notes, hooks) {
  const outline = root.querySelector('.list-outline');
  const draft = root.querySelector('[data-draft]');
  const draftKindBtn = root.querySelector('[data-draft-kind]');
  const picked = new Set(hooks.selected || []);
  let draftKind = list.kind === 'bullet' ? 'bullet' : 'check';
  const setDraftKind = (kind) => {
    draftKind = kind === 'bullet' ? 'bullet' : 'check';
    const row = draft?.closest('.list-line');
    if (row) row.dataset.kind = draftKind;
    draftKindBtn?.classList.toggle('is-bullet', draftKind === 'bullet');
    draftKindBtn?.setAttribute('aria-label', draftKind === 'bullet' ? 'Line' : 'Todo');
  };
  outline?.addEventListener('scroll', () => closeFloats(), { passive: true });
  root.querySelector('[data-back]')?.addEventListener('click', () => hooks.onBack?.());
  root.querySelector('[data-focus]')?.addEventListener('click', () => hooks.onFocus?.());
  root.querySelector('[data-share]')?.addEventListener('click', () => hooks.onShare?.());
  root.querySelector('[data-edit]')?.addEventListener('click', () => hooks.onEdit?.());
  root.querySelector('[data-delete-list]')?.addEventListener('click', () => hooks.onDeleteList?.());
  outline?.addEventListener('dragstart', (event) => {
    const grip = event.target.closest?.('[data-grip]');
    const row = grip?.closest('.list-line');
    if (!row || picked.size) return;
    event.dataTransfer?.setData('text/item-id', row.dataset.id || '');
    event.dataTransfer?.setData('text/plain', row.dataset.id || '');
  });
  outline?.addEventListener('dragover', (event) => {
    if (picked.size) return;
    const row = event.target.closest?.('[data-id]');
    if (!row) return;
    event.preventDefault();
  });
  outline?.addEventListener('drop', async (event) => {
    const id = event.dataTransfer?.getData('text/item-id');
    const target = event.target.closest?.('[data-id]');
    if (!id || !target || picked.size) return;
    event.preventDefault();
    const ids = [...outline.querySelectorAll('[data-id]')].map((node) => node.dataset.id);
    const from = ids.indexOf(id);
    const to = ids.indexOf(target.dataset.id);
    if (from < 0 || to < 0 || from === to) return;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/order`, { method: 'POST', body: { ids } });
      hooks.onChange?.(next);
    } catch (err) {
      hooks.onError?.(err.message || 'Could not reorder');
    }
  });

  const saveText = async (item, value) => {
    const text = value.trim();
    if (!text || text === item.text) return true;
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {
        method: 'PATCH',
        body: { text },
      });
      hooks.onChange?.(next);
      return true;
    } catch (err) {
      hooks.onError?.(err.message || 'Could not save');
      return false;
    }
  };

  const patch = async (id, body) => {
    const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body,
    });
    hooks.onChange?.(next);
  };

  const addItem = async (kind, text) => {
    const body = text.trim();
    if (!body) return null;
    const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items`, {
      method: 'POST',
      body: { text: body, kind },
    });
    hooks.onChange?.(next);
    return next;
  };

  root.querySelectorAll('[data-text]').forEach((input) => {
    const row = input.closest('.list-line');
    const item = (list.items || []).find((rowItem) => rowItem.id === row?.dataset.id);
    if (!item) return;
    input.addEventListener('blur', () => { saveText(item, input.value); });
    input.addEventListener('keydown', async (event) => {
      if (!hooks.captureKeys) return;
      if (document.querySelector('.list-float')) return;
      if (event.shiftKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault();
        const lines = [...outline.querySelectorAll('.list-line')];
        const index = lines.indexOf(row);
        const step = event.key === 'ArrowDown' ? 1 : -1;
        const next = lines[index + step];
        hooks.onSelect?.(item.id, 'add');
        if (next?.dataset.id) hooks.onSelect?.(next.dataset.id, 'add');
        return;
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        if (!input.value.trim()) {
          const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
          hooks.onChange?.(next);
          return;
        }
        const ok = await saveText(item, input.value);
        if (!ok) return;
        const next = row.nextElementSibling?.querySelector('[data-text], [data-draft]');
        next?.focus();
      }
      if (event.key === 'Backspace' && input.selectionStart === 0 && !input.value.trim()) {
        event.preventDefault();
        const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, { method: 'DELETE' });
        hooks.onChange?.(next);
      }
    });
    input.addEventListener('input', () => {
      if (!hooks.captureKeys || root.querySelector('.list-float')) return;
      applyShortcut(input, (kind) => {
        input.value = input.value.replace(/^(\[\]|-|\*)\s/, '');
        patch(item.id, { kind, text: input.value.trim() || item.text }).catch((err) => hooks.onError?.(err.message));
      }, () => {
        input.value = item.text || '';
        openSlash(input, (kind) => turnOne(item, kind, item.text));
      });
    });
  });

  root.querySelectorAll('[data-mark]').forEach((button) => {
    button.addEventListener('click', (event) => {
      const row = button.closest('.list-line');
      const item = (list.items || []).find((rowItem) => rowItem.id === row?.dataset.id);
      if (!item) return;
      if (event.shiftKey) {
        hooks.onSelect?.(item.id, 'add');
        return;
      }
      if (kindOf(item) === 'check') {
        patch(item.id, { done: !item.done }).catch((err) => hooks.onError?.(err.message));
        return;
      }
      openType(button, item);
    });
  });

  root.querySelectorAll('[data-due]').forEach((button) => {
    button.addEventListener('click', () => {
      const item = (list.items || []).find((rowItem) => rowItem.id === button.closest('.list-line')?.dataset.id);
      if (item) openDue(button, item);
    });
  });

  root.querySelectorAll('[data-open-note]').forEach((card) => {
    card.addEventListener('click', async () => {
      const input = root.querySelector('.list-line-input:focus');
      const row = input?.closest('.list-line');
      const item = (list.items || []).find((rowItem) => rowItem.id === row?.dataset.id);
      if (item && input) {
        const ok = await saveText(item, input.value);
        if (!ok) return;
      }
      closeFloats(root);
      hooks.onOpenNote?.(card.dataset.openNote);
    });
  });

  draft?.addEventListener('keydown', async (event) => {
    if (!hooks.captureKeys) return;
    const menu = root.querySelector('.list-float');
    if (event.key === 'Enter') {
      event.preventDefault();
      if (document.querySelector('.list-float')) {
        document.querySelector('.list-float .is-active, .list-float button')?.click();
        return;
      }
      const text = draft.value.trim();
      if (!text) return;
      draft.value = '';
      try {
        await addItem(draftKind, text);
      } catch (err) {
        draft.value = text;
        hooks.onError?.(err.message);
      }
    }
    if (event.key === 'Backspace' && !draft.value) event.preventDefault();
  });
  draft?.addEventListener('input', () => {
    if (!hooks.captureKeys) return;
    applyShortcut(draft, (kind) => {
      draft.value = draft.value.replace(/^(\[\]|-|\*)\s/, '');
      setDraftKind(kind);
    }, () => {
      draft.value = '';
      openSlash(draft, async (kind) => {
        closeFloats();
        if (kind === 'note') {
          const created = await addItem('note', 'Untitled');
          const added = [...(created?.items || [])].reverse().find((row) => kindOf(row) === 'note');
          if (added) hooks.onOpenNote?.(added.id);
          return;
        }
        setDraftKind(kind);
      });
    });
  });
  draftKindBtn?.addEventListener('click', () => openSlash(draftKindBtn, (kind) => {
    closeFloats(root);
    if (kind === 'note') return;
    setDraftKind(kind);
  }));

  root.querySelector('[data-delete-selected]')?.addEventListener('click', async () => {
    let latest = null;
    for (const id of hooks.selected || []) {
      latest = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(id)}`, { method: 'DELETE' });
    }
    hooks.onSelect?.('');
    if (latest) hooks.onChange?.(latest);
  });
  root.querySelector('[data-turn]')?.addEventListener('click', (event) => {
    openMenu(event.currentTarget, LINE_TYPES, async (kind) => {
      let latest = null;
      for (const id of hooks.selected || []) {
        const item = (list.items || []).find((row) => row.id === id);
        if (!item || kindOf(item) === 'note') continue;
        const body = kind === 'note' ? item.text : { kind };
        latest = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(id)}`, {
          method: 'PATCH',
          body: kind === 'note' ? { kind: 'note', text: item.text } : body,
        });
      }
      hooks.onSelect?.('');
      if (latest) hooks.onChange?.(latest);
    });
  });

  if (hooks.focusDraft && draft) draft.focus();
  root.querySelectorAll('.ln-more button').forEach((button) => {
    button.addEventListener('click', () => {
      const menu = button.closest('details');
      if (menu) menu.open = false;
    });
  });

  function openType(anchor, item) {
    openMenu(anchor, LINE_TYPES, (kind) => turnOne(item, kind, item.text));
  }

  async function turnOne(item, kind, text) {
    closeFloats(root);
    if (kindOf(item) === 'note' && noteBody(item.text) && kind !== 'note') {
      hooks.onError?.('This note has a body. Open it and shorten it before turning it into a line.');
      return;
    }
    const body = { kind };
    if (text && text !== item.text) body.text = text;
    try {
      const next = await patch(item.id, body);
      if (kind === 'note') hooks.onOpenNote?.(item.id);
      return next;
    } catch (err) {
      hooks.onError?.(err.message);
    }
  }

  function openDue(anchor, item) {
    closeFloats(root);
    const node = document.createElement('div');
    node.className = 'list-float';
    node.innerHTML = `
      <button type="button" data-choice="today">Today</button>
      <button type="button" data-choice="tomorrow">Tomorrow</button>
      <button type="button" data-choice="week">Next week</button>
      <label>Date <input type="date" data-date /></label>
      <button type="button" data-choice="clear">Clear</button>`;
    document.body.appendChild(node);
    placeFloat(anchor, node);
    let active = 0;
    const choices = [...node.querySelectorAll('[data-choice]')];
    const mark = () => choices.forEach((choice, index) => choice.classList.toggle('is-active', index === active));
    mark();
    const apply = async (choice, custom) => {
      closeFloats(root);
      const due = stampFor(choice, custom);
      await patch(item.id, { due_at: due || null, remind_at: due || null });
    };
    node.addEventListener('click', (event) => {
      const choice = event.target.closest('[data-choice]')?.dataset.choice;
      if (choice) apply(choice).catch((err) => hooks.onError?.(err.message));
    });
    node.querySelector('[data-date]')?.addEventListener('change', (event) => {
      apply('date', event.target.value).catch((err) => hooks.onError?.(err.message));
    });
    const onKey = (event) => {
      if (!node.isConnected) {
        document.removeEventListener('keydown', onKey, true);
        return;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        active = (active + (event.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length;
        mark();
      }
      if (event.key === 'Enter') {
        event.preventDefault();
        event.stopPropagation();
        choices[active]?.click();
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeFloats(root);
      }
    };
    document.addEventListener('keydown', onKey, true);
  }

  function openSlash(anchor, onPick) {
    openMenu(anchor, LINE_TYPES, onPick);
  }

  function openMenu(anchor, rows, onPick) {
    closeFloats();
    const node = document.createElement('div');
    node.className = 'list-float';
    node.innerHTML = rows.map(([id, label, hint], index) => (
      `<button type="button" data-pick="${esc(id)}" class="${index === 0 ? 'is-active' : ''}"><strong>${esc(label)}</strong>${hint ? `<span>${esc(hint)}</span>` : ''}</button>`
    )).join('');
    document.body.appendChild(node);
    placeFloat(anchor, node);
    let active = 0;
    const choices = [...node.querySelectorAll('[data-pick]')];
    const mark = () => choices.forEach((choice, index) => choice.classList.toggle('is-active', index === active));
    const onKey = (event) => {
      if (!node.isConnected) {
        document.removeEventListener('keydown', onKey, true);
        return;
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopPropagation();
        active = (active + (event.key === 'ArrowDown' ? 1 : choices.length - 1)) % choices.length;
        mark();
      } else if (event.key === 'Enter') {
        event.preventDefault();
        event.stopPropagation();
        choices[active]?.click();
      } else if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        closeFloats();
      }
    };
    document.addEventListener('keydown', onKey, true);
    node.addEventListener('click', (event) => {
      const pick = event.target.closest('[data-pick]')?.dataset.pick;
      if (!pick) return;
      closeFloats();
      Promise.resolve(onPick(pick)).catch((err) => hooks.onError?.(err.message));
    });
  }
}

function noteBody(text) {
  return String(text || '').split(/\r?\n/).map((row) => row.trim()).filter(Boolean).length > 1;
}

function applyShortcut(input, onKind, onSlash) {
  const value = input.value;
  if (/^\[\]\s/.test(value)) onKind('check');
  else if (/^(-|\*)\s/.test(value)) onKind('bullet');
  else if (value === '/') onSlash();
}

function formatBar() {
  return `<div class="list-format" data-format>
    <button type="button" data-wrap="# " aria-label="Heading">H1</button>
    <button type="button" data-wrap="## " aria-label="Subheading">H2</button>
    <button type="button" data-wrap="**" aria-label="Bold"><b>B</b></button>
    <button type="button" data-wrap="*" aria-label="Italic"><i>I</i></button>
    <button type="button" data-wrap="- " aria-label="List">List</button>
    <button type="button" data-wrap="> " aria-label="Quote">Quote</button>
    <button type="button" data-wrap="\`" aria-label="Code">Code</button>
    <button type="button" data-link aria-label="Link">Link</button>
  </div>`;
}

function bindSaver(field, list, item, hooks, status) {
  let saved = item.text || '';
  let timer = 0;
  const write = async (quiet) => {
    const text = field.value.trim();
    if (!text) {
      if (!quiet) {
        if (status) status.textContent = 'Add a title';
        hooks.onError?.('Add a title before leaving this page.');
      }
      return false;
    }
    if (text === saved) {
      if (status) status.textContent = 'Saved';
      return true;
    }
    if (status) status.textContent = 'Saving…';
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {
        method: 'PATCH',
        body: { text },
      });
      saved = text;
      if (status) status.textContent = 'Saved';
      if (quiet && hooks.onQuiet) hooks.onQuiet(next);
      else hooks.onChange?.(next);
      return true;
    } catch (err) {
      if (status) status.textContent = 'Not saved';
      hooks.onError?.(err.message || 'Could not save');
      return false;
    }
  };
  const poke = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => { write(true); }, 700);
  };
  field.addEventListener('input', poke);
  return { flush: () => write(true), poke };
}

function bindFormat(root, field, paint, poke) {
  root.querySelectorAll('[data-wrap]').forEach((button) => {
    button.addEventListener('click', () => {
      const token = button.dataset.wrap || '';
      if (token.endsWith(' ')) {
        const start = field.selectionStart || 0;
        field.value = `${field.value.slice(0, start)}${token}${field.value.slice(start)}`;
        field.setSelectionRange(start + token.length, start + token.length);
        field.focus();
      } else wrapSelection(field, token, token === '`' ? '`' : token);
      paint();
      poke?.();
    });
  });
  root.querySelector('[data-link]')?.addEventListener('click', () => {
    wrapSelection(field, '[', '](https://)');
    paint();
    poke?.();
  });
}

function paintNote(root, list, item, notes, hooks) {
  const write = hooks.noteMode !== 'read';
  const words = String(item.text || '').trim() ? String(item.text || '').trim().split(/\s+/).length : 0;
  root.innerHTML = `
    <section class="ln-paper list-note-page ${write ? 'is-writing' : 'is-reading'}">
      <header class="ln-doc-head">
        <button type="button" class="ln-back" data-note-back>Back</button>
        <div class="ln-doc-titles">
          <h1>${esc(noteTitle(item.text))}</h1>
          <p class="ln-remain">Page</p>
        </div>
        <div class="ln-page-tools">
          <span class="list-save-state" data-save-state aria-live="polite">Saved</span>
          <span class="list-word-count">${words} ${words === 1 ? 'word' : 'words'}</span>
          <button type="button" data-note-mode>${write ? 'Preview' : 'Write'}</button>
        </div>
      </header>
      ${write && list.can_edit !== false ? formatBar() : ''}
      <div class="ln-sheet list-note-stage">
        <textarea class="list-note-write" maxlength="100000" placeholder="Write in Markdown. Link another page with [[." ${write ? '' : 'hidden'} ${list.can_edit === false ? 'readonly' : ''}>${esc(item.text || '')}</textarea>
        <article class="markdown-preview list-note-read" ${write ? 'hidden' : ''}>${renderMarkdown(item.text, notes)}</article>
      </div>
    </section>`;
  const field = root.querySelector('.list-note-write');
  const preview = root.querySelector('.list-note-read');
  const wordsEl = root.querySelector('.list-word-count');
  const paint = () => {
    if (preview) preview.innerHTML = renderMarkdown(field.value, notes);
    if (wordsEl) {
      const count = field.value.trim() ? field.value.trim().split(/\s+/).length : 0;
      wordsEl.textContent = `${count} ${count === 1 ? 'word' : 'words'}`;
    }
  };
  const saver = field ? bindSaver(field, list, item, hooks, root.querySelector('[data-save-state]')) : null;
  field?.addEventListener('input', () => {
    paint();
    const query = mentionQuery(field);
    if (query == null) closeFloats();
    else openMentions(root, field, notes, query);
  });
  field?.addEventListener('keydown', (event) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = event.key.toLowerCase();
    if (key === 'b') { event.preventDefault(); wrapSelection(field, '**'); paint(); saver?.poke(); }
    if (key === 'i') { event.preventDefault(); wrapSelection(field, '*'); paint(); saver?.poke(); }
    if (key === 's') { event.preventDefault(); saver?.flush(); }
  });
  root.querySelector('[data-note-back]')?.addEventListener('click', async () => {
    const ok = await (saver ? saver.flush() : true);
    if (ok) hooks.onCloseNote?.();
  });
  root.querySelector('[data-note-mode]')?.addEventListener('click', async () => {
    const ok = await (saver ? saver.flush() : true);
    if (ok) hooks.onNoteMode?.(write ? 'read' : 'write');
  });
  if (field) bindFormat(root, field, paint, () => saver?.poke());
  preview?.addEventListener('click', async (event) => {
    const mention = event.target.closest('[data-note]');
    if (!mention) return;
    const ok = await (saver ? saver.flush() : true);
    if (ok) hooks.onOpenNote?.(mention.dataset.note);
  });
  if (write && list.can_edit !== false) field?.focus();
}

function mentionQuery(field) {
  const before = field.value.slice(0, field.selectionStart || 0);
  const match = before.match(/\[\[([^\]]*)$/);
  return match ? match[1].toLowerCase() : null;
}

function openMentions(root, field, notes, query) {
  closeFloats();
  const rows = notes.filter((note) => note.title.toLowerCase().includes(query)).slice(0, 6);
  if (!rows.length) return;
  const node = document.createElement('div');
  node.className = 'list-float';
  node.innerHTML = rows.map((note) => `<button type="button" data-mention="${esc(note.id)}">${esc(note.title)}</button>`).join('');
  document.body.appendChild(node);
  const rect = field.getBoundingClientRect();
  node.style.left = `${rect.left}px`;
  node.style.bottom = `${window.innerHeight - rect.top + 4}px`;
  node.addEventListener('click', (event) => {
    const id = event.target.closest('[data-mention]')?.dataset.mention;
    if (!id) return;
    const end = field.selectionStart || 0;
    const start = field.value.slice(0, end).lastIndexOf('[[');
    field.value = `${field.value.slice(0, start)}[[${id}]] ${field.value.slice(end)}`;
    node.remove();
    field.focus();
  });
}

function paintJournal(root, list, hooks) {
  const items = list.items || [];
  const pages = items.filter((item) => kindOf(item) === 'note');
  const lines = items.filter((item) => kindOf(item) !== 'note');
  const notes = pages.map((item) => ({ id: item.id, title: noteTitle(item.text) }));
  const active = pages.find((item) => item.id === hooks.noteId) || pages[pages.length - 1] || null;
  const canEdit = list.can_edit !== false;
  const write = hooks.noteMode !== 'read';
  root.innerHTML = `
    <section class="ln-paper list-journal ${write ? 'is-writing' : 'is-reading'}">
      ${docHead(list, hooks)}
      <div class="ln-pagebar">
        ${canEdit ? '<button type="button" class="list-entry-new" data-new-entry>New page</button>' : ''}
        <div class="ln-pages" role="tablist" aria-label="Pages">
          ${pages.map((item) => `<button type="button" class="list-entry ${item.id === active?.id ? 'is-active' : ''}" data-open-note="${esc(item.id)}" role="tab" aria-selected="${item.id === active?.id ? 'true' : 'false'}">
            <strong>${esc(noteTitle(item.text))}</strong>
          </button>`).join('') || '<p class="list-outline-hint">No pages yet.</p>'}
        </div>
        <div class="ln-page-tools">
          <span class="list-save-state" data-save-state aria-live="polite">Saved</span>
          <span class="list-word-count"></span>
          <button type="button" data-note-mode>${write ? 'Preview' : 'Write'}</button>
          ${canEdit && pages.length > 1 ? '<button type="button" data-delete-entry>Delete page</button>' : ''}
        </div>
      </div>
      ${lines.length ? `<div class="list-journal-lines">${lines.map((item) => (
        kindOf(item) === 'check'
          ? `<label class="list-journal-line"><input type="checkbox" data-line="${esc(item.id)}" ${item.done ? 'checked' : ''} ${canEdit ? '' : 'disabled'}> <span>${esc(item.text || '')}</span></label>`
          : `<p class="list-journal-line"><span>• ${esc(item.text || '')}</span></p>`
      )).join('')}</div>` : ''}
      ${active ? `${canEdit && write ? formatBar() : ''}
        <div class="ln-sheet list-note-stage">
          <textarea class="list-note-write" maxlength="100000" placeholder="Start writing. The first line is the page title." ${write ? '' : 'hidden'} ${canEdit ? '' : 'readonly'}>${esc(active.text || '')}</textarea>
          <article class="markdown-preview list-note-read" ${write ? 'hidden' : ''}>${renderMarkdown(active.text, notes)}</article>
        </div>` : '<div class="ln-empty"><h2>Start a page</h2><p>A page is free-form Markdown.</p></div>'}
    </section>`;
  wireJournal(root, list, notes, active, canEdit, write, hooks);
}

function wireJournal(root, list, notes, active, canEdit, write, hooks) {
  const field = root.querySelector('.list-note-write');
  const preview = root.querySelector('.list-note-read');
  const wordsEl = root.querySelector('.list-word-count');
  const paint = () => {
    if (!field) return;
    if (preview) preview.innerHTML = renderMarkdown(field.value, notes);
    if (wordsEl) {
      const count = field.value.trim() ? field.value.trim().split(/\s+/).length : 0;
      wordsEl.textContent = `${count} ${count === 1 ? 'word' : 'words'}`;
    }
  };
  paint();
  const saver = field && active ? bindSaver(field, list, active, hooks, root.querySelector('[data-save-state]')) : null;
  field?.addEventListener('input', () => {
    paint();
    const current = root.querySelector('.list-entry.is-active strong');
    if (current) current.textContent = noteTitle(field.value);
    const query = mentionQuery(field);
    if (query == null) closeFloats();
    else openMentions(root, field, notes, query);
  });
  field?.addEventListener('keydown', (event) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    const key = event.key.toLowerCase();
    if (key === 'b') { event.preventDefault(); wrapSelection(field, '**'); paint(); saver?.poke(); }
    if (key === 'i') { event.preventDefault(); wrapSelection(field, '*'); paint(); saver?.poke(); }
    if (key === 's') { event.preventDefault(); saver?.flush(); }
  });
  if (field && canEdit) bindFormat(root, field, paint, () => saver?.poke());
  const leave = async (done) => {
    const ok = saver ? await saver.flush() : true;
    if (ok) done();
  };
  root.querySelector('[data-back]')?.addEventListener('click', () => leave(() => hooks.onBack?.()));
  root.querySelector('[data-focus]')?.addEventListener('click', () => leave(() => hooks.onFocus?.()));
  root.querySelector('[data-share]')?.addEventListener('click', () => leave(() => hooks.onShare?.()));
  root.querySelector('[data-edit]')?.addEventListener('click', () => leave(() => hooks.onEdit?.()));
  root.querySelector('[data-delete-list]')?.addEventListener('click', () => leave(() => hooks.onDeleteList?.()));
  root.querySelector('[data-note-mode]')?.addEventListener('click', () => leave(() => hooks.onNoteMode?.(write ? 'read' : 'write')));
  root.querySelectorAll('[data-open-note]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.dataset.openNote === active?.id) return;
      const ok = saver ? await saver.flush() : true;
      if (ok) hooks.onOpenNote?.(button.dataset.openNote);
    });
  });
  root.querySelector('[data-new-entry]')?.addEventListener('click', async () => {
    const ok = saver ? await saver.flush() : true;
    if (!ok) return;
    const stamp = new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' });
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items`, {
        method: 'POST',
        body: { text: `# ${stamp}`, kind: 'note' },
      });
      const added = [...(next?.items || [])].reverse().find((row) => kindOf(row) === 'note');
      hooks.onChange?.(next);
      if (added) hooks.onOpenNote?.(added.id);
    } catch (err) {
      hooks.onError?.(err.message || 'Could not add a page');
    }
  });
  root.querySelector('[data-delete-entry]')?.addEventListener('click', async () => {
    if (!active || !window.confirm('Delete this page?')) return;
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(active.id)}`, { method: 'DELETE' });
      const rest = (next?.items || []).filter((row) => kindOf(row) === 'note');
      hooks.onChange?.(next);
      hooks.onOpenNote?.(rest[rest.length - 1]?.id || '');
    } catch (err) {
      hooks.onError?.(err.message || 'Could not delete the page');
    }
  });
  root.querySelectorAll('[data-line]').forEach((box) => {
    box.addEventListener('change', async () => {
      const item = (list.items || []).find((row) => row.id === box.dataset.line);
      if (!item || kindOf(item) !== 'check') return;
      try {
        const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {
          method: 'PATCH',
          body: { done: box.checked },
        });
        hooks.onChange?.(next);
      } catch (err) {
        hooks.onError?.(err.message || 'Could not save');
      }
    });
  });
  if (write && canEdit) field?.focus();
  root.querySelectorAll('.ln-more button').forEach((button) => {
    button.addEventListener('click', () => {
      const menu = button.closest('details');
      if (menu) menu.open = false;
    });
  });
}

export function renderChatList(root, list, hooks = {}) {
  const items = list.items || [];
  root.innerHTML = `<div class="list-chat">
    ${items.map((item) => {
      const kind = kindOf(item);
      if (kind === 'note') {
        return `<a class="list-note-card" href="/lists?id=${esc(list.id)}&note=${esc(item.id)}"><strong>${esc(noteTitle(item.text))}</strong><span>${esc(notePreview(item.text))}</span></a>`;
      }
      const mark = kind === 'bullet' ? '•' : (item.done ? '☑' : '☐');
      return `<div class="list-line ${item.done ? 'is-done' : ''}"><button type="button" class="list-mark" data-chat-check="${esc(item.id)}" ${kind === 'check' ? '' : 'disabled'}>${mark}</button><span>${esc(item.text || '')}</span></div>`;
    }).join('') || '<p class="collab-empty">No items yet.</p>'}
    <a class="btn btn-ghost btn-sm list-chat-more" href="/lists?id=${esc(list.id)}">View in lists</a>
  </div>`;
  root.querySelectorAll('[data-chat-check]').forEach((button) => {
    button.addEventListener('click', async () => {
      const item = items.find((row) => row.id === button.dataset.chatCheck);
      if (!item || kindOf(item) !== 'check') return;
      try {
        const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {
          method: 'PATCH',
          body: { done: !item.done },
        });
        hooks.onChange?.(next);
      } catch (err) {
        hooks.onError?.(err.message || 'Could not save');
      }
    });
  });
}
