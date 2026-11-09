import { apiJson, visibilityChip } from '../collab-api.js';
import { listDialog, sharePicker } from '../collab-ui.js';
import { tagChipHtml, tagChipsRow } from '../collab/tag-chips.js';
import { fetchMyUserTags } from '../user-tag-api.js';

const GRIP = `<svg class="lists-grip" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="5" cy="3.2" r="1.15"/><circle cx="11" cy="3.2" r="1.15"/><circle cx="5" cy="8" r="1.15"/><circle cx="11" cy="8" r="1.15"/><circle cx="5" cy="12.8" r="1.15"/><circle cx="11" cy="12.8" r="1.15"/></svg>`;

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

function remindersEnabled(root) {
  return root?.dataset?.reminders === '1';
}

function isMobile() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function listMatchesTag(list, tagName) {
  if (!tagName) return true;
  return (list.tags || []).some((t) => t.name === tagName);
}

function progressLabel(list) {
  const p = list.progress || {};
  if (!p.total) return '';
  return `${p.done || 0}/${p.total}`;
}

function dueHint(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString();
}

export function initListsHub(root) {
  const me = root?.dataset.me || '';
  const railEl = document.getElementById('listsRail');
  const boardEl = document.getElementById('listsBoard');
  const editorEl = document.getElementById('listsEditor');
  const bodyEl = document.getElementById('listsHubBody');

  let groups = [];
  let lists = [];
  let tagCatalog = [];
  let selectedGroupId = '';
  let activeListId = new URLSearchParams(location.search).get('id') || '';
  let tagFilter = '';
  let mobileView = 'lists';
  let selectedGroupIdInit = false;
  let detail = null;

  const mq = window.matchMedia('(max-width: 900px)');
  mq.addEventListener('change', () => paint());

  function filteredLists() {
    return lists.filter((list) => {
      if (!listMatchesTag(list, tagFilter)) return false;
      if (selectedGroupId === '__all__') return true;
      const gid = list.group_id || '';
      return gid === selectedGroupId;
    });
  }

  function setMobileView(view) {
    mobileView = view;
    if (bodyEl) bodyEl.dataset.mobileView = view;
  }

  function syncUrl() {
    const params = new URLSearchParams();
    if (activeListId) params.set('id', activeListId);
    const q = params.toString();
    history.replaceState(null, '', q ? `/lists?${q}` : '/lists');
  }

  function groupCount(groupId) {
    if (groupId === '__all__') return lists.length;
    return lists.filter((list) => (list.group_id || '') === groupId).length;
  }

  function renderRail() {
    if (!railEl) return;
    const groupBtns = groups
      .map(
        (g) => `<div class="lists-rail-group ${selectedGroupId === g.id ? 'is-active' : ''}" data-group-id="${esc(g.id)}" data-drop-group>
          <button type="button" class="lists-rail-select"><span class="lists-rail-label">${esc(g.name)}</span><span class="lists-rail-count">${groupCount(g.id)}</span></button>
          <button type="button" class="lists-rail-rename btn btn-ghost btn-xs" data-rename-group="${esc(g.id)}" aria-label="Rename">✎</button>
          <button type="button" class="lists-rail-remove btn btn-ghost btn-xs" data-remove-group="${esc(g.id)}" aria-label="Remove">×</button>
        </div>`,
      )
      .join('');
    const tags = tagCatalog
      .map(
        (tag) => `<button type="button" class="lists-tag-filter ${tagFilter === tag.name ? 'is-active' : ''}" data-tag-filter="${esc(tag.name)}">
          ${tagChipHtml(tag, { className: 'lists-tag-chip lists-tag-chip--btn', link: false })}
        </button>`,
      )
      .join('');
    railEl.innerHTML = `
      <div class="lists-rail-head">Groups</div>
      <div class="lists-rail-groups" role="list">
        <button type="button" class="lists-rail-group ${selectedGroupId === '__all__' ? 'is-active' : ''}" data-group-id="__all__"><span class="lists-rail-label">All lists</span><span class="lists-rail-count">${groupCount('__all__')}</span></button>
        ${groupBtns}
        <button type="button" class="lists-rail-group ${selectedGroupId === '' ? 'is-active' : ''}" data-group-id=""><span class="lists-rail-label">Ungrouped</span><span class="lists-rail-count">${groupCount('')}</span></button>
      </div>
      ${tagCatalog.length ? `<div class="lists-rail-tags"><div class="lists-rail-tags-label">Filter by tag</div>${tags}</div>` : ''}`;

    railEl.querySelectorAll('[data-group-id]').forEach((btn) => {
      btn.querySelector('.lists-rail-select')?.addEventListener('click', (event) => {
        event.stopPropagation();
        if (event.target.closest('[data-rename-group],[data-remove-group]')) return;
        selectedGroupId = btn.dataset.groupId;
        if (isMobile()) setMobileView('lists');
        paintBoard();
        paintRail();
      });
    });
    railEl.querySelectorAll('button[data-group-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectedGroupId = btn.dataset.groupId;
        if (isMobile()) setMobileView('lists');
        paintBoard();
        paintRail();
      });
    });
    railEl.querySelectorAll('[data-drop-group]').forEach((zone) => {
      zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('is-drop-target');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-drop-target'));
      zone.addEventListener('drop', async (e) => {
        e.preventDefault();
        zone.classList.remove('is-drop-target');
        const id = e.dataTransfer.getData('text/list-id');
        if (!id) return;
        const gid = zone.dataset.groupId;
        if (gid === '__all__') return;
        await apiJson(`/api/lists/${id}/group`, {
          method: 'POST',
          body: { group_id: gid },
        });
        await refresh(false);
      });
    });
    railEl.querySelectorAll('[data-tag-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const name = btn.dataset.tagFilter;
        tagFilter = tagFilter === name ? '' : name;
        paintBoard();
        paintRail();
      });
    });
    railEl.querySelectorAll('[data-remove-group]').forEach((btn) => {
      btn.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!window.confirm('Remove this group? Lists will become ungrouped for you.')) return;
        await apiJson(`/api/list-groups/${btn.dataset.removeGroup}`, { method: 'DELETE', body: {} });
        if (selectedGroupId === btn.dataset.removeGroup) selectedGroupId = '';
        await refresh(false);
      });
    });
    railEl.querySelectorAll('[data-rename-group]').forEach((btn) => {
      btn.addEventListener('click', async (event) => {
        event.stopPropagation();
        const group = groups.find((g) => g.id === btn.dataset.renameGroup);
        if (!group) return;
        const name = window.prompt('Group name', group.name);
        if (!name || !name.trim()) return;
        await apiJson(`/api/list-groups/${group.id}`, { method: 'PATCH', body: { name: name.trim() } });
        await refresh(false);
      });
    });
  }

  function renderMobileGroups() {
    if (!railEl) return;
    const chips = [
      `<button type="button" class="lists-chip ${selectedGroupId === '__all__' ? 'is-active' : ''}" data-group-id="__all__">All <span class="lists-rail-count">${groupCount('__all__')}</span></button>`,
      ...groups.map(
        (g) => `<button type="button" class="lists-chip ${selectedGroupId === g.id ? 'is-active' : ''}" data-group-id="${esc(g.id)}">${esc(g.name)} <span class="lists-rail-count">${groupCount(g.id)}</span></button>`,
      ),
      `<button type="button" class="lists-chip ${selectedGroupId === '' ? 'is-active' : ''}" data-group-id="">Ungrouped <span class="lists-rail-count">${groupCount('')}</span></button>`,
    ].join('');
    const tags = tagCatalog.length
      ? `<div class="lists-mobile-tags">${tagCatalog
          .map(
            (tag) => `<button type="button" class="lists-tag-filter ${tagFilter === tag.name ? 'is-active' : ''}" data-tag-filter="${esc(tag.name)}">${tagChipHtml(tag, { className: 'lists-tag-chip', link: false })}</button>`,
          )
          .join('')}</div>`
      : '';
    railEl.innerHTML = `<div class="lists-mobile-groups">${chips}</div>${tags}`;
    railEl.querySelectorAll('[data-group-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectedGroupId = btn.dataset.groupId;
        setMobileView('lists');
        paintBoard();
        paint();
      });
    });
    railEl.querySelectorAll('[data-tag-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const name = btn.dataset.tagFilter;
        tagFilter = tagFilter === name ? '' : name;
        paintBoard();
        paint();
      });
    });
  }

  function paintRail() {
    if (isMobile()) renderMobileGroups();
    else renderRail();
  }

  function addTagButton(list) {
    if (list.organizer !== me) return '';
    return `<button type="button" class="lists-add-tag" data-add-tag="${esc(list.id)}" draggable="false" aria-label="Add tag" title="Add tag">+</button>`;
  }

  function listCard(list) {
    const due = list.next_due ? dueHint(list.next_due) : '';
    const prog = progressLabel(list);
    return `<article class="lists-card ${list.id === activeListId ? 'is-active' : ''}" draggable="true" data-list-id="${esc(list.id)}">
      <button type="button" class="lists-card-open" data-open-list="${esc(list.id)}">
        <strong class="lists-card-title">${esc(list.title)}</strong>
        <span class="lists-author">Author ${esc(list.organizer || '')}</span>
        <span class="collab-hint">${esc(prog)}${due ? ` · Due ${esc(due)}` : ''}</span>
      </button>
      <div class="lists-card-meta">
        ${tagChipsRow(list.tags || [])}
        ${addTagButton(list)}
      </div>
    </article>`;
  }

  function paintBoard() {
    if (!boardEl) return;
    const rows = filteredLists();
    if (isMobile()) {
      const groupName =
        selectedGroupId === '__all__'
          ? 'All lists'
          : selectedGroupId
            ? groups.find((g) => g.id === selectedGroupId)?.name || 'Group'
            : 'Ungrouped';
      boardEl.innerHTML = `
        <header class="lists-mobile-bar">
          <span class="lists-mobile-title">${esc(groupName)}</span>
        </header>
        <div class="lists-mobile-rows">
          ${rows.length ? rows.map((l) => `<div class="lists-row">
            <button type="button" class="lists-row-open" data-open-list="${esc(l.id)}">
              <span class="lists-row-title">${esc(l.title)}</span>
              <span class="lists-author">Author ${esc(l.organizer || '')}</span>
              <span class="collab-hint">${esc(progressLabel(l))}</span>
              ${tagChipsRow(l.tags || [])}
            </button>
            ${addTagButton(l)}
          </div>`).join('') : '<div class="collab-empty">No lists here.</div>'}
        </div>`;
    boardEl.querySelectorAll('[data-open-list]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openList));
    });
    wireAddTag(boardEl);
    return;
  }
    boardEl.innerHTML = `
      <header class="lists-board-head"><h2 class="text-base font-semibold">Lists</h2></header>
      <div class="lists-card-grid">${rows.length ? rows.map(listCard).join('') : '<div class="collab-empty">No lists in this group.</div>'}</div>`;
    boardEl.querySelectorAll('.lists-card').forEach((card) => {
      card.addEventListener('dragstart', (e) => {
        if (e.target.closest('[data-add-tag], a')) {
          e.preventDefault();
          return;
        }
        e.dataTransfer.setData('text/list-id', card.dataset.listId);
        card.classList.add('is-dragging');
      });
      card.addEventListener('dragend', () => card.classList.remove('is-dragging'));
    });
    boardEl.querySelectorAll('[data-open-list]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openList));
    });
    wireAddTag(boardEl);
  }

  function paintEditor(list) {
    if (!editorEl) return;
    detail = list;
    const canEdit = !list || (typeof list.can_edit === 'boolean' ? list.can_edit : list.organizer === me);
    editorEl.classList.toggle('is-locked', Boolean(list) && !canEdit);
    if (!list) {
      editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
      return;
    }
    if (isMobile()) {
      paintEditorMobile(list);
      return;
    }
    paintEditorDesktop(list);
  }

  function paintEditorDesktop(list) {
    const mine = list.organizer === me;
    const prog = list.progress || {};
    const items = (list.items || [])
      .map(
        (it) => `<div class="lists-item-row ${it.done ? 'is-done' : ''}" data-item-id="${esc(it.id)}">
        <button type="button" class="lists-drag-handle" data-drag-handle aria-label="Drag to reorder">${GRIP}</button>
        <span class="lists-col-check">${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''}>` : ''}</span>
        <input class="input input-bordered input-xs" data-field="text" value="${esc(it.text)}">
        <input class="input input-bordered input-xs" data-field="notes" value="${esc(it.notes || '')}" placeholder="Note">
        <input class="input input-bordered input-xs" type="datetime-local" data-field="due" value="${esc(localStamp(it.due_at))}">
        <input class="input input-bordered input-xs" data-field="labels" value="${esc((it.labels || []).join(', '))}" placeholder="buy, gift" title="Short words on this item. Separate from list tags.">
        <span class="lists-col-act">
          <button type="button" class="btn btn-ghost btn-xs" data-save-item="${esc(it.id)}">Save</button>
          <button type="button" class="btn btn-ghost btn-xs" data-del-item="${esc(it.id)}">×</button>
        </span>
      </div>`,
      )
      .join('');
    editorEl.innerHTML = `
      <header class="lists-editor-head">
        <div>
          <h2 class="text-lg font-bold">${esc(list.title)}</h2>
          <p class="lists-author">Author ${esc(list.organizer || '')}</p>
          ${list.can_edit === false ? '<p class="collab-hint">Read only</p>' : ''}
          ${list.notes ? `<p class="collab-hint">${esc(list.notes)}</p>` : ''}
          <div class="lists-tag-row lists-editor-tags">${(list.tags || []).map((t) => tagChipHtml(t)).join('')}</div>
          <div class="collab-chips">
            <span class="collab-chip">${visibilityChip(list.visibility)}</span>
            <span class="collab-chip">${list.kind === 'bullet' ? 'Bullets' : 'Checklist'}</span>
            <span class="collab-chip">${prog.done || 0}/${prog.total || 0}</span>
          </div>
        </div>
        <div class="lists-editor-actions">
          ${mine ? '<button type="button" class="btn btn-ghost btn-sm" data-act="edit">List settings</button>' : ''}
          ${mine ? '<button type="button" class="btn btn-error btn-sm" data-act="delete">Delete</button>' : ''}
          ${(list.may_share || list.organizer === me) ? '<button type="button" class="btn btn-ghost btn-sm" data-act="share">Add to chat</button>' : ''}
        </div>
      </header>
      <div class="lists-table-wrap">
        <div class="lists-items-head" aria-hidden="true"><span></span><span></span><span>Item</span><span>Note</span><span>Due</span><span title="Short words on this item. Separate from list tags.">Labels</span><span></span></div>
        <div class="lists-item-list">${items || '<div class="collab-empty">No items yet.</div>'}</div>
      </div>
      <form class="collab-add lists-add-row" data-add>
        <input class="input input-bordered input-sm" name="text" placeholder="New item" required>
        <input class="input input-bordered input-sm" name="notes" placeholder="Note">
        <input class="input input-bordered input-sm" type="datetime-local" name="due">
        <input class="input input-bordered input-sm" name="labels" placeholder="Labels">
        <button class="btn btn-primary btn-sm" type="submit">Add</button>
      </form>`;
    wireListEditor(list, editorEl);
  }

  function paintEditorMobile(list) {
    const items = (list.items || [])
      .map(
        (it) => `<details class="lists-mobile-item ${it.done ? 'is-done' : ''}" data-item-id="${esc(it.id)}">
        <summary>
          <button type="button" class="lists-drag-handle" data-drag-handle aria-label="Drag to reorder">${GRIP}</button>
          ${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''}>` : '<span class="collab-bullet"></span>'}
          <span class="lists-mobile-item-text">${esc(it.text)}</span>
          ${it.due_at ? `<span class="collab-hint">${esc(dueHint(it.due_at))}</span>` : ''}
        </summary>
        ${it.notes ? `<p class="collab-hint">${esc(it.notes)}</p>` : ''}
        ${(it.labels || []).length ? `<div class="lists-tag-row">${(it.labels || []).map((lb) => `<span class="collab-chip">${esc(lb)}</span>`).join('')}</div>` : ''}
      </details>`,
      )
      .join('');
    editorEl.innerHTML = `
      <header class="lists-mobile-bar">
        <button type="button" class="btn btn-ghost btn-sm" data-mobile-back>Lists</button>
        <span class="lists-mobile-title">${esc(list.title)}</span>
        <span class="lists-author">Author ${esc(list.organizer || '')}</span>
        ${list.organizer === me ? '<button type="button" class="btn btn-error btn-sm" data-act="delete">Delete</button>' : ''}
      </header>
      <div class="lists-mobile-detail">
        ${tagChipsRow(list.tags || [])}
        <div class="lists-mobile-checklist">${items || '<p class="collab-empty">No items yet.</p>'}</div>
        <form class="collab-add" data-add>
          <input class="input input-bordered" name="text" placeholder="Add item" required>
          <button class="btn btn-primary btn-sm" type="submit">Add</button>
        </form>
      </div>`;
    editorEl.querySelector('[data-mobile-back]')?.addEventListener('click', () => {
      setMobileView('lists');
      paint();
    });
    wireListEditor(list, editorEl);
  }

  function readRowPayload(row) {
    const body = {
      text: row.querySelector('[data-field="text"]')?.value.trim() || '',
      notes: row.querySelector('[data-field="notes"]')?.value.trim() || '',
      due_at: isoStamp(row.querySelector('[data-field="due"]')?.value || ''),
      labels: String(row.querySelector('[data-field="labels"]')?.value || '')
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean),
    };
    if (remindersEnabled(root)) body.remind_at = body.due_at;
    return body;
  }

  let tagMenu = null;
  let tagMenuCloser = null;

  function closeTagMenu() {
    tagMenu?.remove();
    tagMenu = null;
    if (tagMenuCloser) {
      document.removeEventListener('pointerdown', tagMenuCloser, true);
      tagMenuCloser = null;
    }
  }

  async function openTagMenu(listId, anchor) {
    closeTagMenu();
    const list = lists.find((row) => row.id === listId);
    if (!list || list.organizer !== me) return;
    const { tags } = await fetchMyUserTags();
    const have = new Set((list.tags || []).map((tag) => Number(tag.id)));
    const choices = (tags || []).filter((tag) => !have.has(Number(tag.id)));
    const menu = document.createElement('div');
    menu.className = 'lists-tag-menu';
    menu.innerHTML = choices.length
      ? choices.map((tag) => `<button type="button" data-pick-tag="${esc(String(tag.id))}">${esc(tag.name)}</button>`).join('')
      : '<p class="collab-hint">No more tags. <a href="/tags">Create one</a>.</p>';
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    tagMenu = menu;
    menu.querySelectorAll('[data-pick-tag]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const ids = [...have, Number(btn.dataset.pickTag)];
        closeTagMenu();
        await apiJson(`/api/lists/${listId}`, { method: 'PATCH', body: { tag_ids: ids } });
        await refresh(false);
        if (activeListId === listId) {
          const next = await apiJson(`/api/lists/${listId}`);
          applyDetail(next);
        }
      });
    });
    tagMenuCloser = (event) => {
      if (menu.contains(event.target) || anchor.contains(event.target)) return;
      closeTagMenu();
    };
    document.addEventListener('pointerdown', tagMenuCloser, true);
  }

  function wireAddTag(scope) {
    scope.querySelectorAll('[data-add-tag]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        openTagMenu(btn.dataset.addTag, btn);
      });
    });
  }

  function wirePointerReorder(scope, list) {
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
          const scroller = parent.closest('.lists-editor');
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
          if (ids.join() === before) return;
          try {
            const next = await apiJson(`/api/lists/${list.id}/order`, { method: 'POST', body: { ids } });
            applyDetail(next);
          } catch (err) {
            window.alert(err.message || 'Could not reorder');
            paintEditor(detail);
          }
        };
        window.addEventListener('pointermove', move, { passive: false });
        window.addEventListener('pointerup', up);
        window.addEventListener('pointercancel', up);
      });
    });
  }

  function wireListEditor(list, root) {
    root.querySelectorAll('[data-done]').forEach((box) => {
      box.addEventListener('click', (e) => e.stopPropagation());
      box.addEventListener('change', async () => {
        const next = await apiJson(`/api/lists/${list.id}/items/${box.dataset.done}`, {
          method: 'PATCH',
          body: { done: box.checked },
        });
        applyDetail(next);
      });
    });
    root.querySelectorAll('[data-del-item]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const next = await apiJson(`/api/lists/${list.id}/items/${btn.dataset.delItem}`, { method: 'DELETE' });
        applyDetail(next);
      });
    });
    root.querySelectorAll('[data-save-item]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const row = btn.closest('[data-item-id]');
        const body = readRowPayload(row);
        if (!body.text) return;
        const next = await apiJson(`/api/lists/${list.id}/items/${btn.dataset.saveItem}`, { method: 'PATCH', body });
        applyDetail(next);
      });
    });
    wirePointerReorder(root, list);
    root.querySelector('[data-add]')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const fd = new FormData(event.currentTarget);
      const body = {
        text: String(fd.get('text') || ''),
        notes: String(fd.get('notes') || ''),
        due_at: isoStamp(String(fd.get('due') || '')),
        labels: String(fd.get('labels') || '').split(',').map((s) => s.trim()).filter(Boolean),
      };
      if (remindersEnabled(root)) body.remind_at = body.due_at;
      const next = await apiJson(`/api/lists/${list.id}/items`, { method: 'POST', body });
      applyDetail(next);
      event.currentTarget.reset();
    });
    root.querySelector('[data-act="edit"]')?.addEventListener('click', async () => {
      const draft = await listDialog(list);
      if (!draft) return;
      const next = await apiJson(`/api/lists/${list.id}`, { method: 'PATCH', body: draft });
      applyDetail(next);
    });
    root.querySelector('[data-act="share"]')?.addEventListener('click', () =>
      sharePicker('list', list.id).catch((e) => alert(e.message)),
    );
    root.querySelector('[data-act="delete"]')?.addEventListener('click', async () => {
      if (!window.confirm('Delete this list? This cannot be undone.')) return;
      await apiJson(`/api/lists/${list.id}`, { method: 'DELETE' });
      activeListId = '';
      detail = null;
      syncUrl();
      if (isMobile()) setMobileView('lists');
      editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
      await refresh(false);
    });
  }

  function applyDetail(next) {
    detail = next;
    lists = lists.map((row) => (row.id === next.id ? { ...row, ...next, tags: next.tags || row.tags } : row));
    paintBoard();
    paintEditor(next);
  }

  async function openList(id) {
    activeListId = id;
    syncUrl();
    const list = await apiJson(`/api/lists/${id}`);
    if (isMobile()) setMobileView('detail');
    paintBoard();
    paintEditor(list);
  }

  function paint() {
    if (!bodyEl) return;
    bodyEl.classList.toggle('is-mobile', isMobile());
    if (isMobile()) bodyEl.dataset.mobileView = mobileView;
    else delete bodyEl.dataset.mobileView;
    paintRail();
    paintBoard();
    if (activeListId && detail?.id === activeListId) paintEditor(detail);
    else if (!activeListId) paintEditor(null);
  }

  async function refresh(repaint = true) {
    const data = await apiJson('/api/lists');
    groups = data.groups || [];
    lists = data.lists || [];
    tagCatalog = data.tag_catalog || [];
    if (!selectedGroupIdInit) {
      selectedGroupId = groups.length ? groups[0].id : '__all__';
      selectedGroupIdInit = true;
    }
    if (!groups.some((g) => g.id === selectedGroupId) && selectedGroupId && selectedGroupId !== '__all__') {
      selectedGroupId = '__all__';
    }
    if (repaint) {
      paint();
      if (activeListId) await openList(activeListId).catch(() => {
        activeListId = '';
        detail = null;
        paintEditor(null);
      });
    } else {
      paintRail();
      paintBoard();
    }
  }

  document.getElementById('listsGroupBtn')?.addEventListener('click', async () => {
    const name = window.prompt('Group name');
    if (!name || !name.trim()) return;
    await apiJson('/api/list-groups', { method: 'POST', body: { name: name.trim() } });
    await refresh();
  });

  document.getElementById('listsNewBtn')?.addEventListener('click', async () => {
    const draft = await listDialog();
    if (!draft) return;
    const list = await apiJson('/api/lists', { method: 'POST', body: draft });
    activeListId = list.id;
    detail = list;
    if (list.group_id) selectedGroupId = list.group_id;
    syncUrl();
    await refresh();
    await openList(list.id);
  });

  refresh().catch((e) => {
    if (editorEl) editorEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`;
  });

  return { refresh };
}
