import { apiJson, visibilityChip } from '../collab-api.js';
import { listDialog, sharePicker } from '../collab-ui.js';
import { tagChipHtml } from './tag-chips.js';

const GRIP = `<svg class="lists-grip" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="5" cy="3.2" r="1.15"/><circle cx="11" cy="3.2" r="1.15"/><circle cx="5" cy="8" r="1.15"/><circle cx="11" cy="8" r="1.15"/><circle cx="5" cy="12.8" r="1.15"/><circle cx="11" cy="12.8" r="1.15"/></svg>`;
const CHEVRON = `<svg class="lists-glance-chevron" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6 3.5 11 8 6 12.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function isoStamp(value) {
  if (!value) return '';
  const when = new Date(value);
  return Number.isNaN(when.getTime()) ? '' : when.toISOString();
}

function localStamp(iso) {
  if (!iso) return '';
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '';
  const pad = (n) => String(n).padStart(2, '0');
  return `${when.getFullYear()}-${pad(when.getMonth() + 1)}-${pad(when.getDate())}T${pad(when.getHours())}:${pad(when.getMinutes())}`;
}

function dueHint(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString();
}

function canEditList(list, me) {
  if (typeof list.can_edit === 'boolean') return list.can_edit;
  return list.organizer === me;
}

function readOnlyControl(list, me) {
  if (list.kind === 'bullet') return '';
  if (list.organizer === me) {
    return `<label class="lists-ro-switch"><input type="checkbox" data-act="readonly" ${list.read_only ? 'checked' : ''}><span>Read only</span></label>`;
  }
  if (list.read_only || list.can_edit === false) return '<span class="lists-ro-switch is-locked">Read only</span>';
  return '';
}

function gridItems(list, canEdit) {
  return (list.items || []).map((it) => `<div class="lists-item-row ${it.done ? 'is-done' : ''}" data-item-id="${esc(it.id)}">
    ${canEdit ? `<button type="button" class="lists-drag-handle" data-drag-handle aria-label="Drag to reorder">${GRIP}</button>` : '<span></span>'}
    <span class="lists-col-check">${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''} ${canEdit ? '' : 'disabled'}>` : ''}</span>
    <input class="input input-bordered input-xs" data-field="text" value="${esc(it.text)}" ${canEdit ? '' : 'readonly'}>
    <input class="input input-bordered input-xs" data-field="notes" value="${esc(it.notes || '')}" placeholder="Note" ${canEdit ? '' : 'readonly'}>
    <input class="input input-bordered input-xs" type="datetime-local" data-field="due" value="${esc(localStamp(it.due_at))}" ${canEdit ? '' : 'readonly'}>
    <input class="input input-bordered input-xs" data-field="labels" value="${esc((it.labels || []).join(', '))}" placeholder="buy, gift" title="Short words on this item. Separate from list tags." ${canEdit ? '' : 'readonly'}>
    <span class="lists-col-act">
      ${canEdit ? `<button type="button" class="btn btn-ghost btn-xs" data-save-item="${esc(it.id)}">Save</button>
      <button type="button" class="btn btn-ghost btn-xs" data-del-item="${esc(it.id)}">×</button>` : ''}
    </span>
  </div>`).join('');
}

function stackItems(list, canEdit) {
  return (list.items || []).map((it) => `<details class="lists-mobile-item ${it.done ? 'is-done' : ''}" data-item-id="${esc(it.id)}">
    <summary>
      ${canEdit ? `<button type="button" class="lists-drag-handle" data-drag-handle aria-label="Drag to reorder">${GRIP}</button>` : ''}
      ${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''} ${canEdit ? '' : 'disabled'}>` : '<span class="collab-bullet"></span>'}
      <span class="lists-mobile-item-text">${esc(it.text)}</span>
      ${it.due_at ? `<span class="collab-hint">${esc(dueHint(it.due_at))}</span>` : ''}
    </summary>
    ${it.notes ? `<p class="collab-hint">${esc(it.notes)}</p>` : ''}
    ${(it.labels || []).length ? `<div class="lists-tag-row">${(it.labels || []).map((lb) => `<span class="collab-chip">${esc(lb)}</span>`).join('')}</div>` : ''}
  </details>`).join('');
}

function itemFilled(it) {
  const notes = String(it.notes || '').trim();
  const labels = it.labels || [];
  return { notes, due: it.due_at || '', labels };
}

function glanceMeta(it) {
  const filled = itemFilled(it);
  const bits = [];
  if (filled.notes) bits.push(`<span class="lists-glance-note">${esc(filled.notes)}</span>`);
  if (filled.due) bits.push(`<span class="lists-glance-due">${esc(dueHint(filled.due))}</span>`);
  if (filled.labels.length) {
    bits.push(`<span class="lists-glance-labels">${filled.labels.map((lb) => `<span class="collab-chip">${esc(lb)}</span>`).join('')}</span>`);
  }
  return bits.length ? `<div class="lists-glance-meta">${bits.join('')}</div>` : '';
}

function glanceItems(list, canEdit, openIds) {
  return (list.items || []).map((it) => {
    const open = openIds.has(it.id);
    const toggle = canEdit
      ? `<button type="button" class="lists-glance-toggle" data-expand aria-expanded="${open ? 'true' : 'false'}" aria-label="${open ? 'Hide extra fields' : 'Edit all fields'}">${CHEVRON}</button>`
      : '';
    const editor = canEdit ? `<div class="lists-glance-edit" ${open ? '' : 'hidden'}>
      <input class="input input-bordered input-xs" data-field="text" value="${esc(it.text)}">
      <input class="input input-bordered input-xs" data-field="notes" value="${esc(it.notes || '')}" placeholder="Note">
      <input class="input input-bordered input-xs" type="datetime-local" data-field="due" value="${esc(localStamp(it.due_at))}">
      <input class="input input-bordered input-xs" data-field="labels" value="${esc((it.labels || []).join(', '))}" placeholder="buy, gift" title="Short words on this item. Separate from list tags.">
      <span class="lists-col-act">
        <button type="button" class="btn btn-ghost btn-xs" data-save-item="${esc(it.id)}">Save</button>
        <button type="button" class="btn btn-ghost btn-xs" data-del-item="${esc(it.id)}">×</button>
      </span>
    </div>` : '';
    return `<div class="lists-glance-item ${it.done ? 'is-done' : ''}" data-item-id="${esc(it.id)}">
      <div class="lists-glance-line">
        ${canEdit ? `<button type="button" class="lists-drag-handle" data-drag-handle aria-label="Drag to reorder">${GRIP}</button>` : ''}
        ${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''} ${canEdit ? '' : 'disabled'}>` : '<span class="collab-bullet"></span>'}
        <span class="lists-glance-text">${esc(it.text)}</span>
        ${toggle}
      </div>
      ${open ? '' : glanceMeta(it)}
      ${editor}
    </div>`;
  }).join('');
}

function glanceAdd(canEdit) {
  if (!canEdit) return '';
  return `<form class="lists-glance-add" data-add>
    <div class="lists-glance-line">
      <input class="input input-bordered input-sm" name="text" placeholder="Add item" required>
      <button type="button" class="lists-glance-toggle" data-add-more aria-expanded="false" aria-label="More fields">${CHEVRON}</button>
      <button class="btn btn-primary btn-sm" type="submit">Add</button>
    </div>
    <div class="lists-glance-edit" data-add-extra hidden>
      <input class="input input-bordered input-sm" name="notes" placeholder="Note">
      <input class="input input-bordered input-sm" type="datetime-local" name="due">
      <input class="input input-bordered input-sm" name="labels" placeholder="Labels">
    </div>
  </form>`;
}

function addForm(canEdit, compact) {
  if (!canEdit) return '';
  if (compact) {
    return `<form class="collab-add" data-add>
      <input class="input input-bordered" name="text" placeholder="Add item" required>
      <button class="btn btn-primary btn-sm" type="submit">Add</button>
    </form>`;
  }
  return `<form class="collab-add lists-add-row" data-add>
    <input class="input input-bordered input-sm" name="text" placeholder="New item" required>
    <input class="input input-bordered input-sm" name="notes" placeholder="Note">
    <input class="input input-bordered input-sm" type="datetime-local" name="due">
    <input class="input input-bordered input-sm" name="labels" placeholder="Labels">
    <button class="btn btn-primary btn-sm" type="submit">Add</button>
  </form>`;
}

function headerActions(list, me) {
  const mine = list.organizer === me;
  const prog = list.progress || {};
  return `
    ${readOnlyControl(list, me)}
    ${mine ? '<button type="button" class="btn btn-ghost btn-sm" data-act="edit">List settings</button>' : ''}
    ${mine ? '<button type="button" class="btn btn-error btn-sm" data-act="delete">Delete</button>' : ''}
    ${(list.may_share || mine) ? '<button type="button" class="btn btn-ghost btn-sm" data-act="share">Add to chat</button>' : ''}
    <span class="collab-chip">${visibilityChip(list.visibility)}</span>
    <span class="collab-chip">${list.kind === 'bullet' ? 'Bullets' : 'Checklist'}</span>
    <span class="collab-chip">${prog.done || 0}/${prog.total || 0}</span>`;
}

function paintGrid(root, list, me) {
  const canEdit = canEditList(list, me);
  const items = gridItems(list, canEdit);
  root.classList.toggle('is-locked', !canEdit);
  root.innerHTML = `
    <div class="lists-editor-body">
      <header class="lists-editor-head">
        <div>
          <h2 class="text-lg font-bold">${esc(list.title)}</h2>
          <p class="lists-author">Author ${esc(list.organizer || '')}</p>
          ${list.notes ? `<p class="collab-hint">${esc(list.notes)}</p>` : ''}
          <div class="lists-tag-row lists-editor-tags">${(list.tags || []).map((t) => tagChipHtml(t)).join('')}</div>
        </div>
        <div class="lists-editor-actions">${headerActions(list, me)}</div>
      </header>
      <div class="lists-table-wrap">
        <div class="lists-items-head" aria-hidden="true"><span></span><span></span><span>Item</span><span>Note</span><span>Due</span><span title="Short words on this item. Separate from list tags.">Labels</span><span></span></div>
        <div class="lists-item-list">${items || '<div class="collab-empty">No items yet.</div>'}</div>
      </div>
      ${addForm(canEdit, false)}
    </div>`;
}

function paintGlance(root, list, me) {
  const canEdit = canEditList(list, me);
  const openIds = root._glanceOpen || new Set();
  root.classList.toggle('is-locked', !canEdit);
  const tags = (list.tags || []).map((t) => tagChipHtml(t)).join('');
  root.innerHTML = `
    <div class="lists-editor-body lists-glance">
      <header class="lists-editor-head">
        <div>
          <h2 class="text-lg font-bold">${esc(list.title)}</h2>
          <p class="lists-author">Author ${esc(list.organizer || '')}</p>
          ${list.notes ? `<p class="collab-hint">${esc(list.notes)}</p>` : ''}
          ${tags ? `<div class="lists-tag-row lists-editor-tags">${tags}</div>` : ''}
        </div>
        <div class="lists-editor-actions">${headerActions(list, me)}</div>
      </header>
      <div class="lists-glance-list">${glanceItems(list, canEdit, openIds) || '<div class="collab-empty">No items yet.</div>'}</div>
      ${glanceAdd(canEdit)}
    </div>`;
}

function paintStack(root, list, me, onBack) {
  const canEdit = canEditList(list, me);
  root.classList.toggle('is-locked', !canEdit);
  root.innerHTML = `
    <div class="lists-editor-body">
      <header class="lists-mobile-bar">
        ${onBack ? '<button type="button" class="btn btn-ghost btn-sm" data-act="back">Lists</button>' : ''}
        <span class="lists-mobile-title">${esc(list.title)}</span>
        <span class="lists-author">Author ${esc(list.organizer || '')}</span>
      </header>
      <div class="lists-editor-actions lists-editor-actions--stack">${headerActions(list, me)}</div>
      <div class="lists-tag-row">${(list.tags || []).map((t) => tagChipHtml(t)).join('')}</div>
      <div class="lists-mobile-checklist">${stackItems(list, canEdit) || '<p class="collab-empty">No items yet.</p>'}</div>
      ${addForm(canEdit, true)}
    </div>`;
}

function readRowPayload(row, reminders) {
  const body = {
    text: row.querySelector('[data-field="text"]')?.value.trim() || '',
    notes: row.querySelector('[data-field="notes"]')?.value.trim() || '',
    due_at: isoStamp(row.querySelector('[data-field="due"]')?.value || ''),
    labels: String(row.querySelector('[data-field="labels"]')?.value || '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean),
  };
  if (reminders) body.remind_at = body.due_at;
  return body;
}

function wireReorder(scope, list, hooks) {
  scope.querySelectorAll('[data-drag-handle]').forEach((handle) => {
    handle.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    handle.addEventListener('pointerdown', (event) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      const row = handle.closest('[data-item-id]');
      const parent = row?.parentElement;
      if (!row || !parent) return;
      const itemIds = () => [...parent.querySelectorAll(':scope > [data-item-id]')].map((el) => el.dataset.itemId);
      const before = itemIds().join();
      const start = row.getBoundingClientRect();
      const grabY = event.clientY - start.top;
      const placeholder = document.createElement('div');
      placeholder.className = 'lists-item-placeholder';
      placeholder.style.height = `${start.height}px`;
      parent.insertBefore(placeholder, row);
      row.classList.add('is-dragging');
      row.style.width = `${start.width}px`;
      row.style.left = `${start.left}px`;
      row.style.top = `${start.top}px`;
      document.body.classList.add('lists-is-reordering');
      const slideInto = (pointerY) => {
        const others = [...parent.querySelectorAll(':scope > [data-item-id]')].filter((el) => el !== row);
        let anchor = null;
        for (const el of others) {
          const box = el.getBoundingClientRect();
          if (pointerY < box.top + box.height / 2) {
            anchor = el;
            break;
          }
        }
        if (placeholder.nextSibling === anchor) return;
        const tops = new Map(others.map((el) => [el, el.getBoundingClientRect().top]));
        parent.insertBefore(placeholder, anchor);
        others.forEach((el) => {
          const dy = tops.get(el) - el.getBoundingClientRect().top;
          if (!dy) return;
          el.animate(
            [{ transform: `translateY(${dy}px)` }, { transform: 'translateY(0)' }],
            { duration: 180, easing: 'cubic-bezier(.2,.7,.2,1)' },
          );
        });
      };
      const move = (ev) => {
        ev.preventDefault();
        row.style.top = `${ev.clientY - grabY}px`;
        slideInto(ev.clientY);
        const scroller = parent.closest('.lists-editor, .collab-dialog');
        if (!scroller) return;
        const edge = scroller.getBoundingClientRect();
        if (ev.clientY < edge.top + 36) scroller.scrollTop -= 14;
        else if (ev.clientY > edge.bottom - 36) scroller.scrollTop += 14;
      };
      const up = async () => {
        window.removeEventListener('pointermove', move);
        window.removeEventListener('pointerup', up);
        window.removeEventListener('pointercancel', up);
        parent.insertBefore(row, placeholder);
        placeholder.remove();
        row.classList.remove('is-dragging');
        row.style.width = '';
        row.style.left = '';
        row.style.top = '';
        document.body.classList.remove('lists-is-reordering');
        const ids = itemIds();
        try {
          if (ids.join() !== before) {
            const next = await apiJson(`/api/lists/${list.id}/order`, { method: 'POST', body: { ids } });
            hooks.onChange?.(next);
          }
        } catch (err) {
          window.alert(err.message || 'Could not reorder');
          hooks.onRevert?.();
        }
        hooks.onSettled?.();
      };
      window.addEventListener('pointermove', move, { passive: false });
      window.addEventListener('pointerup', up);
      window.addEventListener('pointercancel', up);
    });
  });
}

function wire(root, list, hooks) {
  const reminders = Boolean(hooks.reminders);
  root.querySelectorAll('[data-done]').forEach((box) => {
    box.addEventListener('click', (event) => event.stopPropagation());
    box.addEventListener('change', async () => {
      const next = await apiJson(`/api/lists/${list.id}/items/${box.dataset.done}`, {
        method: 'PATCH',
        body: { done: box.checked },
      });
      hooks.onChange?.(next);
    });
  });
  root.querySelectorAll('[data-del-item]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const next = await apiJson(`/api/lists/${list.id}/items/${btn.dataset.delItem}`, { method: 'DELETE' });
      hooks.onChange?.(next);
    });
  });
  root.querySelectorAll('[data-save-item]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const row = btn.closest('[data-item-id]');
      const body = readRowPayload(row, reminders);
      if (!body.text) return;
      const next = await apiJson(`/api/lists/${list.id}/items/${btn.dataset.saveItem}`, { method: 'PATCH', body });
      hooks.onChange?.(next);
    });
  });
  wireReorder(root, list, hooks);
  root.querySelector('[data-add]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    const body = {
      text: String(fd.get('text') || ''),
      notes: String(fd.get('notes') || ''),
      due_at: isoStamp(String(fd.get('due') || '')),
      labels: String(fd.get('labels') || '').split(',').map((s) => s.trim()).filter(Boolean),
    };
    if (reminders) body.remind_at = body.due_at;
    const next = await apiJson(`/api/lists/${list.id}/items`, { method: 'POST', body });
    hooks.onChange?.(next);
  });
  root.querySelector('[data-act="edit"]')?.addEventListener('click', async () => {
    const draft = await listDialog(list);
    if (!draft) return;
    const next = await apiJson(`/api/lists/${list.id}`, { method: 'PATCH', body: draft });
    hooks.onChange?.(next);
  });
  root.querySelector('[data-act="share"]')?.addEventListener('click', () => {
    sharePicker('list', list.id).catch((err) => window.alert(err.message));
  });
  root.querySelector('[data-act="delete"]')?.addEventListener('click', async () => {
    if (!window.confirm('Delete this list? This cannot be undone.')) return;
    await apiJson(`/api/lists/${list.id}`, { method: 'DELETE' });
    hooks.onDelete?.();
  });
  root.querySelector('[data-act="readonly"]')?.addEventListener('change', async (event) => {
    const next = await apiJson(`/api/lists/${list.id}`, {
      method: 'PATCH',
      body: { read_only: event.target.checked },
    });
    hooks.onChange?.(next);
  });
  root.querySelector('[data-act="back"]')?.addEventListener('click', () => hooks.onBack?.());
  root.querySelectorAll('[data-expand]').forEach((btn) => {
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      const id = btn.closest('[data-item-id]')?.dataset.itemId;
      if (!id) return;
      const open = root._glanceOpen || new Set();
      if (open.has(id)) open.delete(id);
      else open.add(id);
      root._glanceOpen = open;
      renderListEditor(root, list, hooks);
    });
  });
  root.querySelector('[data-add-more]')?.addEventListener('click', (event) => {
    event.preventDefault();
    const extra = root.querySelector('[data-add-extra]');
    if (!extra) return;
    const show = extra.hidden;
    extra.hidden = !show;
    event.currentTarget.setAttribute('aria-expanded', show ? 'true' : 'false');
  });
}

export function renderListEditor(root, list, hooks = {}) {
  if (!root || !list) return;
  root.classList.add('lists-editor-mount');
  const layout = hooks.layout === 'stack' ? 'stack' : hooks.layout === 'glance' ? 'glance' : 'grid';
  if (layout === 'stack') paintStack(root, list, hooks.me || '', hooks.onBack);
  else if (layout === 'glance') paintGlance(root, list, hooks.me || '');
  else paintGrid(root, list, hooks.me || '');
  wire(root, list, hooks);
}
