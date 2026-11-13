import { apiJson } from '../collab-api.js';
import { listDialog, sharePicker } from '../collab-ui.js';
import { hydrateTagPicker } from '../collab-tags.js';
import { tagChipsRow } from '../collab/tag-chips.js';
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

  const pinKey = 'aird-list-pins';
  let pins = { lists: [], tags: [] };
  try { pins = { lists: [], tags: [], ...JSON.parse(localStorage.getItem(pinKey) || '{}') }; } catch { pins = { lists: [], tags: [] }; }

  function savePins() {
    localStorage.setItem(pinKey, JSON.stringify(pins));
  }

  function listById(id) {
    return lists.find((row) => row.id === id);
  }

  function listButton(list) {
    const on = list.id === activeListId;
    const starred = pins.lists.includes(list.id);
    return `<div class="ub-list ${on ? 'is-active' : ''}" data-drop-list="${esc(list.id)}">
      <button type="button" class="ub-list-open" data-open-list="${esc(list.id)}"><span>${esc(list.title || 'List')}</span></button>
      <button type="button" class="ub-pin ${starred ? 'is-on' : ''}" data-pin-list="${esc(list.id)}" aria-label="${starred ? 'Unpin list' : 'Pin list'}">★</button>
    </div>`;
  }

  function renderRail() {
    if (!railEl) return;
    const pinnedLists = pins.lists.map(listById).filter(Boolean);
    const groupBlocks = groups.map((group) => {
      const rows = lists.filter((list) => (list.group_id || '') === group.id);
      return `<section class="ub-fold" data-drop-group="${esc(group.id)}">
        <header><span>${esc(group.name)}</span><span>${rows.length}</span>
          <button type="button" data-rename-group="${esc(group.id)}" aria-label="Rename">✎</button>
          <button type="button" data-remove-group="${esc(group.id)}" aria-label="Remove">✕</button>
        </header>
        ${rows.map(listButton).join('') || ''}
      </section>`;
    }).join('');
    const loose = lists.filter((list) => !list.group_id);
    const tagRows = pins.tags.map((tag) => `
      <div class="ub-tag">
        <button type="button" data-pin-filter="${esc(tag)}">#${esc(tag)}</button>
        <button type="button" data-unpin-tag="${esc(tag)}" aria-label="Unpin">✕</button>
      </div>`).join('');
    const nav = document.getElementById('listsRailNav') || railEl;
    nav.innerHTML = `
      <div class="ub-side-scroll">
        <button type="button" class="ub-nav ${selectedGroupId === '__all__' && !activeListId ? 'is-active' : ''}" data-group-id="__all__">All lists <span>${lists.length}</span></button>
        <div class="ub-label">Pinned lists</div>
        ${pinnedLists.length ? pinnedLists.map(listButton).join('') : '<p class="ub-empty">None pinned</p>'}
        <div class="ub-label">Groups</div>
        ${groupBlocks || '<p class="ub-empty">No groups</p>'}
        <section class="ub-fold" data-drop-group="">
          <header><span>Ungrouped</span><span>${loose.length}</span></header>
          ${loose.map(listButton).join('')}
        </section>
        <div class="ub-label">Pinned tags</div>
        ${tagRows || '<p class="ub-empty">Pin tags from the filter</p>'}
      </div>
      <button type="button" class="ub-new" data-side-new>+ New list</button>`;

    railEl.querySelector('[data-group-id="__all__"]')?.addEventListener('click', () => {
      selectedGroupId = '__all__';
      activeListId = '';
      detail = null;
      syncUrl();
      if (isMobile()) setMobileView('lists');
      paint();
    });
    railEl.querySelectorAll('[data-open-list]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openList));
      btn.addEventListener('dblclick', (event) => {
        event.preventDefault();
        event.stopPropagation();
        const list = listById(btn.dataset.openList);
        if (list) beginRename(list, btn);
      });
    });
    railEl.querySelectorAll('[data-pin-list]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const id = btn.dataset.pinList;
        pins.lists = pins.lists.includes(id) ? pins.lists.filter((row) => row !== id) : [...pins.lists, id];
        savePins();
        paintRail();
      });
    });
    railEl.querySelectorAll('[data-pin-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        editorEl?.querySelector('[data-block-root]')?._blocks?.toggleTag(btn.dataset.pinFilter);
      });
    });
    railEl.querySelectorAll('[data-unpin-tag]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const index = pins.tags.indexOf(btn.dataset.unpinTag);
        if (index >= 0) pins.tags.splice(index, 1);
        savePins();
        paintRail();
        editorEl?.querySelector('[data-block-root]')?._blocks?.refreshPins();
      });
    });
    railEl.querySelector('[data-side-new]')?.addEventListener('click', () => {
      document.getElementById('listsNewBtn')?.click();
    });
    railEl.querySelectorAll('.ub-list').forEach((row) => {
      row.draggable = true;
      row.addEventListener('dragstart', (event) => {
        event.dataTransfer.setData('text/list-id', row.dataset.dropList);
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

  let renameLock = false;

  function paintRail() {
    if (renameLock) return;
    renderRail();
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
      ? `<input class="input input-bordered input-sm" data-tag-filter placeholder="Type to add a tag" autocomplete="off"><div data-tag-choices></div>`
      : '<p class="collab-hint">No more tags. <a href="/tags">Create one</a>.</p>';
    document.body.appendChild(menu);
    const rect = anchor.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8))}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    tagMenu = menu;
    const paintChoices = () => {
      const box = menu.querySelector('[data-tag-choices]');
      const field = menu.querySelector('[data-tag-filter]');
      if (!box || !field) return;
      const q = field.value.trim().toLowerCase();
      const shown = choices.filter((tag) => !q || String(tag.name || '').toLowerCase().includes(q)).slice(0, 8);
      box.innerHTML = !q
        ? '<p class="collab-hint">Type to find a tag</p>'
        : shown.length
          ? shown.map((tag) => `<button type="button" data-pick-tag="${esc(String(tag.id))}">${esc(tag.name)}</button>`).join('')
          : '<p class="collab-hint">No matching tags</p>';
      box.querySelectorAll('[data-pick-tag]').forEach((btn) => {
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
    };
    paintChoices();
    menu.querySelector('[data-tag-filter]')?.addEventListener('input', paintChoices);
    menu.querySelector('[data-tag-filter]')?.focus();
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
      const filter = document.getElementById('listsTagFilter');
      if (filter) filter.innerHTML = '';
      closeInspector();
      paintMeta(null);
      return;
    }
    editorEl.classList.toggle('is-locked', list.can_edit === false);
    closeInspector();
    editorEl.innerHTML = `
      ${isMobile() ? '<button type="button" class="ub-back" data-back>Back</button>' : ''}
      <div class="ub-doc">
        <h1 class="ub-title${list.organizer === me ? ' is-editable' : ''}" title="${list.organizer === me ? 'Double-click to rename' : ''}">${esc(list.title || 'List')}</h1>
        <div data-block-root></div>
      </div>`;
    editorEl.querySelector('[data-back]')?.addEventListener('click', () => {
      activeListId = '';
      detail = null;
      syncUrl();
      setMobileView('lists');
      paint();
    });
    const title = editorEl.querySelector('.ub-title');
    if (title && list.organizer === me) {
      title.addEventListener('dblclick', () => beginRename(list, title));
    }
    paintMeta(list);
    const mount = editorEl.querySelector('[data-block-root]');
    mountBlockCanvas(mount, list, {
      me,
      pinnedTags: pins.tags,
      filterRoot: document.getElementById('listsTagFilter'),
      onChange: applyDetail,
      onError: showError,
      onPinTag: (name, on) => {
        const tag = String(name || '').replace(/^#/, '').toLowerCase();
        const index = pins.tags.indexOf(tag);
        if (on && index < 0) pins.tags.push(tag);
        if (!on && index >= 0) pins.tags.splice(index, 1);
        savePins();
        paintRail();
        mount._blocks?.refreshPins();
      },
      onInspect: openInspector,
    });
  }

  let metaGen = 0;

  function readMetaPeople(root) {
    return (root.querySelector('[data-meta-participants]')?.value || '')
      .split(',')
      .map((part) => part.trim())
      .filter(Boolean);
  }

  async function patchOpen(body) {
    if (!detail) return;
    try {
      applyDetail(await apiJson(`/api/lists/${detail.id}`, { method: 'PATCH', body }));
    } catch (err) {
      showError(err.message || 'Could not save');
      paintMeta(detail, true);
    }
  }

  function paintMeta(list, force = false) {
    const pane = document.getElementById('listsInspector');
    const body = document.getElementById('listsMetaBody');
    if (!pane || !body) return;
    if (!list) {
      body.dataset.listId = '';
      body.innerHTML = '<p class="lists-meta-empty">Select a list to set who can open it and which tags it uses.</p>';
      return;
    }
    if (!force && pane.contains(document.activeElement) && body.dataset.listId === list.id) return;
    const gen = ++metaGen;
    body.dataset.listId = list.id;
    const mine = list.organizer === me;
    const vis = list.visibility === 'private' ? 'private' : 'public';
    const people = (list.participants || []).filter((name) => name && name !== list.organizer).join(', ');
    const tags = list.tags || [];
    if (!mine) {
      body.innerHTML = `
        <section class="lists-meta-block">
          <h3>Who can open this</h3>
          <p>${esc(vis === 'private' ? 'Only invited people' : 'Anyone signed in')}</p>
          ${vis === 'private' ? `<p class="collab-hint">${esc(people || 'No one else is invited')}</p>` : ''}
        </section>
        <section class="lists-meta-block">
          <h3>Tags</h3>
          <div class="collab-tag-chips">${tags.length ? tags.map((tag) => `<span class="collab-tag-chip">${esc(tag.display_name || tag.name)}</span>`).join('') : '<p class="collab-hint">No tags</p>'}</div>
        </section>`;
      return;
    }
    body.innerHTML = `
      <section class="lists-meta-block">
        <h3>Who can open this</h3>
        <select class="select select-bordered select-sm" data-meta-vis>
          <option value="public" ${vis === 'public' ? 'selected' : ''}>Anyone signed in</option>
          <option value="private" ${vis === 'private' ? 'selected' : ''}>Only people you invite</option>
        </select>
        <label class="collab-field" data-meta-people ${vis === 'private' ? '' : 'hidden'}>
          <span>People</span>
          <input class="input input-bordered input-sm" data-meta-participants value="${esc(people)}" placeholder="usernames, comma separated" autocomplete="off">
        </label>
        <label class="collab-check"><input type="checkbox" data-meta-forward ${list.allow_forward ? 'checked' : ''}> Let people this list is shared with share it onward</label>
      </section>
      <section class="lists-meta-block" data-tag-picker>
        <span>Tags</span>
        <div class="collab-tag-chips" data-tag-chips></div>
        <input class="input input-bordered input-sm" data-tag-query placeholder="Type to add a tag" autocomplete="off">
        <div class="collab-tag-suggest" data-tag-suggest hidden></div>
      </section>`;
    const visEl = body.querySelector('[data-meta-vis]');
    const peopleWrap = body.querySelector('[data-meta-people]');
    visEl?.addEventListener('change', () => {
      const visibility = visEl.value;
      if (peopleWrap) peopleWrap.hidden = visibility !== 'private';
      const payload = { visibility };
      if (visibility === 'private') payload.participants = readMetaPeople(body);
      patchOpen(payload);
    });
    const peopleInput = body.querySelector('[data-meta-participants]');
    peopleInput?.addEventListener('change', () => {
      patchOpen({ visibility: 'private', participants: readMetaPeople(body) });
    });
    peopleInput?.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      peopleInput.blur();
    });
    body.querySelector('[data-meta-forward]')?.addEventListener('change', (event) => {
      patchOpen({ allow_forward: event.target.checked });
    });
    hydrateTagPicker(body, list.tag_ids || tags.map((tag) => tag.id), {
      onChange: (ids) => {
        if (gen !== metaGen) return;
        patchOpen({ tag_ids: ids });
      },
    }).catch(() => {});
  }

  function beginRename(list, el) {
    if (!list || list.organizer !== me || !el || renameLock) return;
    renameLock = true;
    const input = document.createElement('input');
    input.className = el.classList.contains('ub-title') ? 'ub-title-input' : 'ub-rename';
    input.value = list.title || '';
    input.setAttribute('aria-label', 'List name');
    input.dataset.renaming = '1';
    el.replaceWith(input);
    input.focus();
    input.select();
    let closed = false;
    const finish = async (save) => {
      if (closed) return;
      closed = true;
      const name = input.value.trim();
      if (!save || !name || name === (list.title || '')) {
        if (input.isConnected) input.replaceWith(el);
        renameLock = false;
        return;
      }
      try {
        const next = await apiJson(`/api/lists/${list.id}`, { method: 'PATCH', body: { title: name } });
        if (input.isConnected) input.replaceWith(el);
        if (el.classList.contains('ub-title')) el.textContent = next.title || name;
        renameLock = false;
        applyDetail(next);
      } catch (err) {
        if (input.isConnected) input.replaceWith(el);
        renameLock = false;
        showError(err.message || 'Could not rename');
      }
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        finish(true);
      }
      if (event.key === 'Escape') {
        event.preventDefault();
        finish(false);
      }
    });
    input.addEventListener('blur', () => finish(true));
  }

  function openInspector(item) {
    const slot = document.getElementById('listsItemDetails');
    if (!slot || !item) return;
    setMetaCollapsed(false);
    const when = (stamp) => (stamp ? new Date(stamp).toLocaleString() : '—');
    slot.hidden = false;
    slot.innerHTML = `
      <section class="lists-meta-block">
        <header><strong>Item</strong><button type="button" data-close-inspector aria-label="Close">✕</button></header>
        <dl>
          <dt>Type</dt><dd>${esc(item.type)}</dd>
          <dt>Depth</dt><dd>${item.depth}</dd>
          <dt>Created</dt><dd>${esc(when(item.createdAt))}</dd>
          <dt>Updated</dt><dd>${esc(when(item.updatedAt))}</dd>
          <dt>Tags</dt><dd>${esc((item.tags || []).map((tag) => `#${tag}`).join(' ') || 'None')}</dd>
        </dl>
      </section>`;
    slot.querySelector('[data-close-inspector]')?.addEventListener('click', closeInspector);
  }

  async function editOpenList() {
    if (!detail) return;
    const draft = await listDialog(detail);
    if (!draft) return;
    applyDetail(await apiJson(`/api/lists/${detail.id}`, { method: 'PATCH', body: draft }));
  }

  async function deleteOpenList() {
    if (!detail || !window.confirm('Delete this list?')) return;
    await apiJson(`/api/lists/${detail.id}`, { method: 'DELETE' });
    activeListId = '';
    detail = null;
    syncUrl();
    if (isMobile()) setMobileView('lists');
    closeInspector();
    refresh(false);
    paintEditor(null);
  }

  function applyDetail(next) {
    detailStamp += 1;
    detail = next;
    lists = lists.map((row) => (row.id === next.id ? { ...row, ...next, tags: next.tags || row.tags } : row));
    paintCrumbs();
    const renaming = document.activeElement?.classList?.contains('ub-rename')
      || document.activeElement?.classList?.contains('ub-title-input');
    if (!renaming) paintRail();
    const mount = editorEl?.querySelector('[data-block-root]');
    if (mount?._blocks && detail?.id === next.id) {
      const title = editorEl.querySelector('.ub-title');
      if (title) title.textContent = next.title || 'List';
      mount._blocks.setList(next);
      paintMeta(next);
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
    paintCrumbs();
    paintRail();
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

  function closeInspector() {
    const slot = document.getElementById('listsItemDetails');
    if (!slot) return;
    slot.hidden = true;
    slot.innerHTML = '';
  }

  function setMetaCollapsed(collapsed) {
    bodyEl?.classList.toggle('is-meta-collapsed', collapsed);
    try { localStorage.setItem('aird-lists-meta', collapsed ? '0' : '1'); } catch { /* ignore */ }
    const toggle = document.getElementById('listsMetaToggle');
    if (toggle) {
      toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      toggle.classList.toggle('is-on', !collapsed);
    }
  }

  function paintCrumbs() {
    const nav = document.getElementById('listsCrumbs');
    if (!nav) return;
    const groupName = detail?.group_id
      ? (groups.find((group) => group.id === detail.group_id)?.name || 'Group')
      : 'Lists';
    const canRename = detail?.organizer === me;
    nav.innerHTML = detail
      ? `<span>${esc(groupName)}</span><span aria-hidden="true">/</span><span${canRename ? ' class="is-editable" title="Double-click to rename" data-crumb-title' : ''}>${esc(detail.title || 'List')}</span>`
      : '<span>Lists</span>';
    nav.querySelector('[data-crumb-title]')?.addEventListener('dblclick', () => beginRename(detail, nav.querySelector('[data-crumb-title]')));
  }

  function paint() {
    if (!bodyEl) return;
    bodyEl.classList.toggle('is-mobile', isMobile());
    if (isMobile()) bodyEl.dataset.mobileView = activeListId ? 'detail' : 'lists';
    else delete bodyEl.dataset.mobileView;
    paintCrumbs();
    paintRail();
    const mount = editorEl?.querySelector('[data-block-root]');
    if (activeListId && detail?.id === activeListId) {
      if (!mount?._blocks) paintEditor(detail);
    } else if (!activeListId) paintEditor(null);
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

  document.getElementById('listsCollapse')?.addEventListener('click', () => {
    bodyEl?.classList.toggle('is-rail-collapsed');
  });

  try {
    if (localStorage.getItem('aird-lists-meta') === '0') setMetaCollapsed(true);
  } catch { /* ignore */ }

  document.getElementById('listsMetaToggle')?.addEventListener('click', () => {
    setMetaCollapsed(!bodyEl?.classList.contains('is-meta-collapsed'));
  });
  document.getElementById('listsMetaCollapse')?.addEventListener('click', () => {
    setMetaCollapsed(true);
  });

  function popMenu(anchor, rows) {
    document.querySelector('.ub-menu')?.remove();
    const node = document.createElement('div');
    node.className = 'ub-menu';
    node.innerHTML = rows.map((row, index) => `<button type="button" data-i="${index}">${esc(row.label)}</button>`).join('');
    document.body.appendChild(node);
    setTimeout(() => {
      const close = (event) => {
        if (node.contains(event.target)) return;
        node.remove();
        document.removeEventListener('pointerdown', close, true);
      };
      document.addEventListener('pointerdown', close, true);
    }, 0);
    const rect = anchor.getBoundingClientRect();
    node.style.top = `${rect.bottom + 4}px`;
    node.style.left = `${Math.max(8, rect.right - node.offsetWidth)}px`;
    node.querySelectorAll('button').forEach((button) => {
      button.addEventListener('click', () => {
        const row = rows[Number(button.dataset.i)];
        node.remove();
        row?.run();
      });
    });
  }

  document.getElementById('listsHelp')?.addEventListener('click', (event) => {
    event.stopPropagation();
    const existing = document.querySelector('.lists-help-pop');
    if (existing) {
      existing.remove();
      event.currentTarget.setAttribute('aria-expanded', 'false');
      return;
    }
    const pop = document.createElement('div');
    pop.className = 'lists-help-pop';
    pop.innerHTML = `
      <h3>Using a list</h3>
      <ul>
        <li>Double-click the list name, edit it, then press <kbd>Enter</kbd> to save. <kbd>Escape</kbd> cancels.</li>
        <li><kbd>Enter</kbd> adds the next item. <kbd>Shift</kbd>+<kbd>Enter</kbd> stays on the same line.</li>
        <li>Click the mark on a line to switch Checkbox, Bullet, or Text.</li>
        <li><kbd>Tab</kbd> indents. <kbd>Shift</kbd>+<kbd>Tab</kbd> outdents.</li>
        <li><kbd>Alt</kbd>+<kbd>↑</kbd> or <kbd>Alt</kbd>+<kbd>↓</kbd> moves a line.</li>
        <li><kbd>Ctrl</kbd>+<kbd>Enter</kbd> checks a task. On Mac, use <kbd>Cmd</kbd> instead of <kbd>Ctrl</kbd>.</li>
        <li><strong>Find</strong> under the title searches the words in items. Tags are filtered on the left.</li>
        <li><kbd>Ctrl</kbd>+<kbd>K</kbd> jumps to Find. <kbd>Ctrl</kbd>+<kbd>\\</kbd> hides the sidebar.</li>
        <li>The <strong>+</strong> under the items adds a line. Details on the right sets who can open the list and its tags.</li>
      </ul>`;
    document.body.appendChild(pop);
    const rect = event.currentTarget.getBoundingClientRect();
    pop.style.top = `${rect.bottom + 6}px`;
    pop.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - pop.offsetWidth - 8))}px`;
    event.currentTarget.setAttribute('aria-expanded', 'true');
    setTimeout(() => {
      const close = (pointer) => {
        if (pop.contains(pointer.target) || pointer.target === event.currentTarget) return;
        pop.remove();
        event.currentTarget.setAttribute('aria-expanded', 'false');
        document.removeEventListener('pointerdown', close, true);
      };
      document.addEventListener('pointerdown', close, true);
    }, 0);
  });

  document.getElementById('listsViewBtn')?.addEventListener('click', (event) => {
    const blocks = editorEl?.querySelector('[data-block-root]')?._blocks;
    popMenu(event.currentTarget, [
      {
        label: blocks?.hideDone?.() ? 'Show completed' : 'Hide completed',
        run: () => blocks?.setHideDone(!blocks.hideDone()),
      },
    ]);
  });

  document.getElementById('listsMoreBtn')?.addEventListener('click', (event) => {
    const blocks = editorEl?.querySelector('[data-block-root]')?._blocks;
    popMenu(event.currentTarget, [
      { label: 'New group', run: () => document.getElementById('listsGroupBtn')?.click() },
      { label: 'Edit list', run: () => editOpenList() },
      { label: 'Share', run: () => detail && sharePicker('list', detail.id).catch((err) => showError(err.message)) },
      { label: 'Export markdown', run: () => {
        const text = blocks?.exportMarkdown?.();
        if (!text) return;
        const blob = new Blob([text], { type: 'text/markdown' });
        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = `${detail?.title || 'list'}.md`;
        link.click();
        URL.revokeObjectURL(link.href);
      } },
      { label: 'Item details', run: () => openInspector(blocks?.activeItem()) },
      { label: 'Delete list', run: () => deleteOpenList() },
    ]);
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
    if (mount?._blocks && activeListId) mount._blocks.focusFind();
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
