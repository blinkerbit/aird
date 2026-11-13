import { apiJson } from '../collab-api.js';
import { listDialog, sharePicker } from '../collab-ui.js';
import { tagChipHtml, tagChipsRow } from '../collab/tag-chips.js';
import { fetchMyUserTags } from '../user-tag-api.js';
import { mountBlockCanvas } from '../collab/blocks/block-canvas.js';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
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
  const params = new URLSearchParams(location.search);
  let activeListId = params.get('id') || '';
  let noteId = params.get('note') || '';
  let noteMode = 'write';
  let focusMode = false;
  let selected = [];
  let armDraft = false;
  let tagFilter = '';
  let mobileView = noteId ? 'note' : activeListId ? 'detail' : 'lists';
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
    if (noteId) params.set('note', noteId);
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
        const input = document.createElement('input');
        input.className = 'list-rename';
        input.value = group.name;
        btn.replaceWith(input);
        input.focus();
        input.select();
        let closed = false;
        const finish = async (save) => {
          if (closed) return;
          closed = true;
          const name = input.value.trim();
          if (!save || !name || name === group.name) {
            paintRail();
            return;
          }
          await apiJson(`/api/list-groups/${group.id}`, { method: 'PATCH', body: { name } });
          await refresh(false);
        };
        input.addEventListener('keydown', (key) => {
          if (key.key === 'Enter') finish(true);
          if (key.key === 'Escape') finish(false);
        });
        input.addEventListener('blur', () => finish(true));
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

  function addTagButton(list) {
    if (list.organizer !== me) return '';
    return `<button type="button" class="lists-add-tag" data-add-tag="${esc(list.id)}" draggable="false" aria-label="Add tag" title="Add tag">+</button>`;
  }

  function listMeta(list) {
    const due = list.next_due ? dueHint(list.next_due) : '';
    const prog = progressLabel(list);
    const bits = [list.organizer || '', prog, due ? `Due ${due}` : ''].filter(Boolean);
    return bits.join(' · ');
  }

  function listCard(list) {
    const tags = list.tags || [];
    const preview = (list.preview || []).map((row) => (
      row.kind === 'note'
        ? `<button type="button" class="lists-card-line" data-list-id="${esc(list.id)}" data-open-note="${esc(row.id)}">${esc(row.title)}</button>`
        : `<span class="lists-card-line">${row.kind === 'bullet' ? '•' : '☐'} ${esc(row.title)}</span>`
    )).join('');
    return `<article class="lists-card ${list.id === activeListId ? 'is-active' : ''}" draggable="true" data-list-id="${esc(list.id)}">
      <button type="button" class="lists-card-open" data-open-list="${esc(list.id)}">
        <strong class="lists-card-title">${esc(list.title)}</strong>
        <span class="lists-card-aside">${esc(listMeta(list))}</span>
        ${tags.length ? tagChipsRow(tags) : ''}
      </button>
      ${preview ? `<div class="lists-card-preview">${preview}</div>` : ''}
      ${addTagButton(list)}
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
          ${rows.length ? rows.map((l) => `<div class="lists-row ${l.id === activeListId ? 'is-active' : ''}">
            <button type="button" class="lists-row-open" data-open-list="${esc(l.id)}">
              <span class="lists-row-title">${esc(l.title)}</span>
              <span class="lists-card-aside">${esc(listMeta(l))}</span>
              ${(l.tags || []).length ? tagChipsRow(l.tags || []) : ''}
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
      <header class="lists-board-head"><span>Lists</span><span class="lists-rail-count">${rows.length}</span></header>
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
      card.addEventListener('dragend', () => {
        card.classList.remove('is-dragging');
        if (pendingBoard) reloadBoard().catch(() => {});
      });
    });
    boardEl.querySelectorAll('[data-open-list]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openList));
    });
    boardEl.querySelectorAll('[data-open-note]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        openNote(btn.dataset.listId, btn.dataset.openNote);
      });
    });
    wireAddTag(boardEl);
  }

  function showError(message) {
    let node = document.getElementById('listsBanner');
    if (!node && editorEl) {
      node = document.createElement('p');
      node.id = 'listsBanner';
      node.className = 'list-banner';
      editorEl.before(node);
    }
    if (!node) return;
    node.hidden = !message;
    node.textContent = message || '';
  }

  function paintEditor(list) {
    if (!editorEl) return;
    detail = list;
    if (!list) {
      editorEl.classList.remove('is-locked');
      editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
      return;
    }
    editorEl.classList.toggle('is-locked', list.can_edit === false);
    editorEl.innerHTML = `
      <header class="block-canvas-head">
        ${isMobile() ? '<button type="button" data-back>Back</button>' : ''}
        <strong>${esc(list.title || 'List')}</strong>
        <button type="button" data-focus>${focusMode ? 'Show board' : 'Focus'}</button>
        <button type="button" data-edit>Edit</button>
        <button type="button" data-share>Share</button>
        <button type="button" data-delete-list>Delete</button>
      </header>
      <div data-block-root></div>`;
    editorEl.querySelector('[data-back]')?.addEventListener('click', () => {
      setMobileView('lists');
      paint();
    });
    editorEl.querySelector('[data-focus]')?.addEventListener('click', () => {
      focusMode = !focusMode;
      paint();
    });
    editorEl.querySelector('[data-edit]')?.addEventListener('click', async () => {
      const draft = await listDialog(detail);
      if (!draft) return;
      const next = await apiJson(`/api/lists/${detail.id}`, { method: 'PATCH', body: draft });
      applyDetail(next);
    });
    editorEl.querySelector('[data-share]')?.addEventListener('click', () => {
      sharePicker('list', list.id).catch((err) => showError(err.message));
    });
    editorEl.querySelector('[data-delete-list]')?.addEventListener('click', async () => {
      if (!window.confirm('Delete this list?')) return;
      await apiJson(`/api/lists/${list.id}`, { method: 'DELETE' });
      activeListId = '';
      detail = null;
      syncUrl();
      if (isMobile()) setMobileView('lists');
      editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
      refresh(false);
    });
    const mount = editorEl.querySelector('[data-block-root]');
    mountBlockCanvas(mount, list, {
      me,
      onChange: applyDetail,
      onError: showError,
    });
  }

  function applyDetail(next) {
    detailStamp += 1;
    detail = next;
    lists = lists.map((row) => (row.id === next.id ? { ...row, ...next, tags: next.tags || row.tags } : row));
    paintBoard();
    const mount = editorEl?.querySelector('[data-block-root]');
    if (mount?._blocks && detail?.id === next.id) {
      mount._blocks.setList(next);
      return;
    }
    paintEditor(next);
  }

  async function openList(id, keepNote = false) {
    if (!keepNote && id !== activeListId) noteId = '';
    activeListId = id;
    syncUrl();
    const list = await apiJson(`/api/lists/${id}`);
    if (isMobile()) setMobileView(noteId ? 'note' : 'detail');
    paintBoard();
    paintEditor(list);
  }

  async function openNote(listId, itemId) {
    noteId = itemId;
    noteMode = 'read';
    if (detail?.id !== listId) await openList(listId, true);
    else {
      syncUrl();
      if (isMobile()) setMobileView('note');
      paint();
    }
  }

  function paint() {
    if (!bodyEl) return;
    bodyEl.classList.toggle('is-mobile', isMobile());
    bodyEl.classList.toggle('is-focus', Boolean(focusMode || noteId));
    if (noteId && isMobile()) mobileView = 'note';
    if (isMobile()) bodyEl.dataset.mobileView = mobileView;
    else delete bodyEl.dataset.mobileView;
    paintRail();
    paintBoard();
    if (activeListId && detail?.id === activeListId) paintEditor(detail);
    else if (!activeListId) paintEditor(null);
  }

  let pendingLiveId = '';
  let pendingBoard = false;
  let liveGen = 0;
  let detailStamp = 0;

  function editorBusy() {
    if (document.body.classList.contains('lists-is-reordering')) return true;
    const el = document.activeElement;
    if (!editorEl || !el || !editorEl.contains(el)) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  async function reloadBoard() {
    const data = await apiJson('/api/lists');
    groups = data.groups || [];
    lists = data.lists || [];
    tagCatalog = data.tag_catalog || [];
    if (boardEl?.querySelector('.is-dragging') || document.body.classList.contains('lists-is-reordering')) {
      pendingBoard = true;
      return;
    }
    pendingBoard = false;
    paintRail();
    paintBoard();
  }

  async function pullOpen(id) {
    const stamp = detailStamp;
    const gen = ++liveGen;
    const list = await apiJson(`/api/lists/${id}`);
    if (gen !== liveGen || activeListId !== id) return;
    if (stamp !== detailStamp) {
      if (editorBusy()) pendingLiveId = id;
      else pullOpen(id).catch(() => {});
      return;
    }
    if (editorBusy()) {
      pendingLiveId = id;
      return;
    }
    pendingLiveId = '';
    applyDetail(list);
  }

  async function applyLive(id) {
    await reloadBoard();
    if (!id || activeListId !== id) return;
    if (editorBusy()) {
      pendingLiveId = id;
      return;
    }
    await pullOpen(id);
  }

  editorEl?.addEventListener('focusout', () => {
    setTimeout(() => {
      if (!pendingLiveId || pendingLiveId !== activeListId || editorBusy()) return;
      const id = pendingLiveId;
      pendingLiveId = '';
      pullOpen(id).catch(() => {});
    }, 0);
  });

  globalThis.AirdChatWS?.subscribe((data) => {
    if (data?.type !== 'collab_update' || data.kind !== 'list') return;
    applyLive(String(data.id || '')).catch(() => {});
  });

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

  document.getElementById('listsGroupBtn')?.addEventListener('click', () => {
    const button = document.getElementById('listsGroupBtn');
    const input = document.createElement('input');
    input.className = 'list-rename';
    input.placeholder = 'Group name';
    button.replaceWith(input);
    input.focus();
    let closed = false;
    const finish = async (save) => {
      if (closed) return;
      closed = true;
      const name = input.value.trim();
      input.replaceWith(button);
      if (!save || !name) return;
      await apiJson('/api/list-groups', { method: 'POST', body: { name } });
      await refresh();
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') finish(true);
      if (event.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', () => finish(true));
  });

  document.getElementById('listsNewBtn')?.addEventListener('click', async () => {
    const draft = await listDialog();
    if (!draft) return;
    const list = await apiJson('/api/lists', { method: 'POST', body: draft });
    armDraft = true;
    activeListId = list.id;
    detail = list;
    if (list.group_id) selectedGroupId = list.group_id;
    syncUrl();
    await refresh();
    await openList(list.id);
  });

  document.addEventListener('keydown', (event) => {
    if (!(event.metaKey || event.ctrlKey)) return;
    if (event.key === '\\') {
      event.preventDefault();
      bodyEl?.classList.toggle('is-rail-collapsed');
      return;
    }
    if (event.key.toLowerCase() !== 'k') return;
    event.preventDefault();
    const mount = editorEl?.querySelector('[data-block-root]');
    if (mount?._blocks && activeListId) mount._blocks.focusTagSearch();
    else openPalette();
  });

  function openPalette() {
    document.querySelector('.list-palette')?.remove();
    const node = document.createElement('div');
    node.className = 'list-palette';
    const rows = [];
    lists.forEach((list) => {
      rows.push({ label: list.title, hint: 'List', run: () => openList(list.id) });
      (list.notes || []).forEach((note) => {
        rows.push({ label: note.title, hint: list.title, run: () => openNote(list.id, note.id) });
      });
    });
    node.innerHTML = `<input class="list-palette-input" placeholder="Search lists and notes" />
      <div class="list-palette-rows"></div>`;
    document.body.appendChild(node);
    const input = node.querySelector('input');
    const box = node.querySelector('.list-palette-rows');
    let active = 0;
    const paintRows = () => {
      const query = input.value.trim().toLowerCase();
      const shown = rows.filter((row) => !query || row.label.toLowerCase().includes(query) || row.hint.toLowerCase().includes(query)).slice(0, 8);
      if (active >= shown.length) active = 0;
      box.innerHTML = shown.map((row, index) => `<button type="button" class="${index === active ? 'is-active' : ''}" data-index="${index}"><strong>${esc(row.label)}</strong><span>${esc(row.hint)}</span></button>`).join('')
        || '<p class="collab-empty">Nothing matches.</p>';
      box.querySelectorAll('button').forEach((button) => {
        button.addEventListener('click', () => {
          const pick = shown[Number(button.dataset.index)];
          node.remove();
          pick?.run();
        });
      });
      return shown;
    };
    let shown = paintRows();
    input.addEventListener('input', () => { active = 0; shown = paintRows(); });
    input.addEventListener('keydown', (key) => {
      if (key.key === 'Escape') {
        key.preventDefault();
        key.stopPropagation();
        node.remove();
      }
      if (key.key === 'ArrowDown' || key.key === 'ArrowUp') {
        key.preventDefault();
        const count = box.querySelectorAll('button').length;
        if (!count) return;
        active = (active + (key.key === 'ArrowDown' ? 1 : count - 1)) % count;
        shown = paintRows();
      }
      if (key.key === 'Enter') {
        key.preventDefault();
        shown[active]?.run();
        node.remove();
      }
    });
    input.focus();
  }

  refresh().catch((e) => {
    if (editorEl) editorEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`;
  });

  return { refresh };
}
