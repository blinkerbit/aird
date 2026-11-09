import { apiJson } from '../collab-api.js';
import { listDialog } from '../collab-ui.js';
import { tagChipHtml, tagChipsRow } from '../collab/tag-chips.js';
import { fetchMyUserTags } from '../user-tag-api.js';
import { renderListEditor } from '../collab/list-editor.js';

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
    return `<article class="lists-card ${list.id === activeListId ? 'is-active' : ''}" draggable="true" data-list-id="${esc(list.id)}">
      <button type="button" class="lists-card-open" data-open-list="${esc(list.id)}">
        <strong class="lists-card-title">${esc(list.title)}</strong>
        <span class="lists-card-aside">${esc(listMeta(list))}</span>
        ${tags.length ? tagChipsRow(tags) : ''}
      </button>
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
    wireAddTag(boardEl);
  }

  function paintEditor(list) {
    if (!editorEl) return;
    detail = list;
    if (!list) {
      editorEl.classList.remove('is-locked');
      editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
      return;
    }
    renderListEditor(editorEl, list, {
      me,
      reminders: remindersEnabled(root),
      layout: isMobile() ? 'stack' : 'grid',
      onChange: applyDetail,
      onBack: isMobile() ? () => { setMobileView('lists'); paint(); } : null,
      onDelete() {
        activeListId = '';
        detail = null;
        syncUrl();
        if (isMobile()) setMobileView('lists');
        editorEl.classList.remove('is-locked');
        editorEl.innerHTML = '<div class="collab-empty">Select a list to edit items.</div>';
        refresh(false);
      },
      onRevert() { paintEditor(detail); },
      onSettled() {
        if (pendingBoard) reloadBoard().catch(() => {});
        if (pendingLiveId !== list.id) return;
        const id = pendingLiveId;
        pendingLiveId = '';
        pullOpen(id).catch(() => {});
      },
    });
  }

  function applyDetail(next) {
    detailStamp += 1;
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
