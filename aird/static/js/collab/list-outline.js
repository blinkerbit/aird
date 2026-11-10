import { apiJson } from '../collab-api.js';
import { notePreview, noteTitle, renderMarkdown, wrapSelection } from './list-md.js';

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

export async function renderOutline(root, list, hooks = {}) {
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
  root.innerHTML = `
    <header class="list-outline-head">
      ${hooks.onBack ? '<button type="button" data-back>Back</button>' : ''}
      <strong>${esc(list.title || 'List')}</strong>
      ${hooks.onFocus ? `<button type="button" data-focus>${hooks.focus ? 'Show board' : 'Focus'}</button>` : ''}
      ${hooks.onShare ? '<button type="button" data-share>Share</button>' : ''}
      ${hooks.onEdit ? '<button type="button" data-edit>Edit</button>' : ''}
      ${hooks.onDeleteList ? '<button type="button" data-delete-list>Delete</button>' : ''}
    </header>
    <div class="list-outline" data-list="${esc(list.id)}">
      ${items.map((item) => rowHtml(item, selected.has(item.id), notes)).join('')}
      ${list.can_edit ? `<div class="list-line is-draft" data-kind="check">
        <button type="button" class="list-mark" data-draft-kind aria-label="Line type">☐</button>
        <input class="list-line-input" data-draft placeholder="Write, or type /" maxlength="300" />
      </div>` : ''}
    </div>
    ${selected.size ? `<div class="list-select-bar"><span>${selected.size} selected</span><button type="button" data-turn>Turn into</button><button type="button" data-delete-selected>Delete</button></div>` : ''}`;
  wireOutline(root, list, notes, hooks);
}

function rowHtml(item, on, notes) {
  const kind = kindOf(item);
  if (kind === 'note') {
    const preview = notePreview(item.text);
    return `<button type="button" class="list-note-card" data-id="${esc(item.id)}" data-open-note="${esc(item.id)}">
      <strong>${esc(noteTitle(item.text))}</strong>
      ${preview ? `<span>${esc(preview)}</span>` : ''}
    </button>`;
  }
  const mark = kind === 'bullet' ? '•' : (item.done ? '☑' : '☐');
  return `<div class="list-line ${on ? 'is-selected' : ''} ${item.done ? 'is-done' : ''}" data-id="${esc(item.id)}" data-kind="${esc(kind)}">
    ${on ? '' : '<span class="list-grip" draggable="true" data-grip aria-hidden="true">⋮</span>'}
    <button type="button" class="list-mark" data-mark>${mark}</button>
    <input class="list-line-input" data-text value="${esc(item.text || '')}" maxlength="300" />
    ${kind === 'check' ? `<button type="button" class="list-due ${item.due_at ? 'is-set' : ''}" data-due>${esc(dueLabel(item.due_at))}</button>` : ''}
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
  let draftKind = 'check';
  const setDraftKind = (kind) => {
    draftKind = kind === 'bullet' ? 'bullet' : 'check';
    if (draftKindBtn) draftKindBtn.textContent = draftKind === 'bullet' ? '•' : '☐';
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
    if (!row || selected.size) return;
    event.dataTransfer?.setData('text/item-id', row.dataset.id || '');
    event.dataTransfer?.setData('text/plain', row.dataset.id || '');
  });
  outline?.addEventListener('dragover', (event) => {
    if (selected.size) return;
    const row = event.target.closest?.('[data-id]');
    if (!row) return;
    event.preventDefault();
  });
  outline?.addEventListener('drop', async (event) => {
    const id = event.dataTransfer?.getData('text/item-id');
    const target = event.target.closest?.('[data-id]');
    if (!id || !target || selected.size) return;
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
    openMenu(event.currentTarget, [
      ['check', 'Check'],
      ['bullet', 'Bullet'],
      ['note', 'Note'],
    ], async (kind) => {
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

  function openType(anchor, item) {
    openMenu(anchor, [
      ['check', 'Check'],
      ['bullet', 'Bullet'],
      ['note', 'Note'],
    ], (kind) => turnOne(item, kind, item.text));
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
    openMenu(anchor, [
      ['check', 'Check'],
      ['bullet', 'Bullet'],
      ['note', 'Note'],
    ], onPick);
  }

  function openMenu(anchor, rows, onPick) {
    closeFloats();
    const node = document.createElement('div');
    node.className = 'list-float';
    node.innerHTML = rows.map(([id, label], index) => (
      `<button type="button" data-pick="${esc(id)}" class="${index === 0 ? 'is-active' : ''}">${esc(label)}</button>`
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

function paintNote(root, list, item, notes, hooks) {
  const wide = window.matchMedia('(min-width: 901px)').matches;
  const write = wide || hooks.noteMode !== 'read';
  root.innerHTML = `
    <section class="list-note-page ${wide ? 'is-wide' : ''} ${write ? 'is-writing' : 'is-reading'}">
      <header>
        <button type="button" data-note-back>Back</button>
        ${wide ? '' : `<button type="button" data-note-mode>${write ? 'Preview' : 'Write'}</button>`}
      </header>
      <div class="list-note-stage">
        <textarea class="list-note-write" maxlength="100000" ${write ? '' : 'hidden'}>${esc(item.text || '')}</textarea>
        <article class="markdown-preview list-note-read" ${write ? 'hidden' : ''}>${renderMarkdown(item.text, notes)}</article>
      </div>
      ${write ? `<div class="list-format" data-format>
        <button type="button" data-wrap="# ">H</button>
        <button type="button" data-wrap="**">B</button>
        <button type="button" data-wrap="- ">List</button>
        <button type="button" data-wrap="\`">Code</button>
        <button type="button" data-link>Link</button>
      </div>` : ''}
    </section>`;
  const field = root.querySelector('.list-note-write');
  const preview = root.querySelector('.list-note-read');
  if (wide && preview) preview.hidden = false;
  const paint = () => { preview.innerHTML = renderMarkdown(field.value, notes); };
  field?.addEventListener('input', () => {
    paint();
    const query = mentionQuery(field);
    if (query == null) closeFloats();
    else openMentions(root, field, notes, query);
  });
  root.querySelector('[data-note-back]')?.addEventListener('click', () => saveThen(field, () => hooks.onCloseNote?.()));
  root.querySelector('[data-note-mode]')?.addEventListener('click', () => {
    hooks.onNoteMode?.(write ? 'read' : 'write');
  });
  root.querySelectorAll('[data-wrap]').forEach((button) => {
    button.addEventListener('click', () => {
      const token = button.dataset.wrap;
      if (token === '- ' || token === '# ') {
        const start = field.selectionStart || 0;
        field.value = `${field.value.slice(0, start)}${token}${field.value.slice(start)}`;
        field.focus();
      } else wrapSelection(field, token, token === '`' ? '`' : token);
      paint();
    });
  });
  root.querySelector('[data-link]')?.addEventListener('click', () => {
    wrapSelection(field, '[', '](https://)');
    paint();
  });
  preview?.addEventListener('click', (event) => {
    const mention = event.target.closest('[data-note]');
    if (!mention) return;
    saveThen(field, () => hooks.onOpenNote?.(mention.dataset.note));
  });
  if (write) field?.focus();

  async function saveThen(source, done) {
    if (!source) {
      done();
      return;
    }
    const text = source.value.trim();
    if (!text) {
      hooks.onError?.('Add a title before leaving this note.');
      return;
    }
    if (text === item.text) {
      done();
      return;
    }
    try {
      const next = await apiJson(`/api/lists/${encodeURIComponent(list.id)}/items/${encodeURIComponent(item.id)}`, {
        method: 'PATCH',
        body: { text },
      });
      hooks.onChange?.(next);
      done();
    } catch (err) {
      hooks.onError?.(err.message || 'Could not save');
    }
  }
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
    <a class="list-chat-more" href="/lists?id=${esc(list.id)}">Open list</a>
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
