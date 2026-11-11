import { apiJson } from '../collab-api.js';
import { listDialog, sharePicker } from '../collab-ui.js';
import { tagChipHtml, tagChipsRow } from '../collab/tag-chips.js';
import { fetchMyUserTags } from '../user-tag-api.js';
import { renderOutline } from '../collab/list-outline.js';

const VIEWS = [
  ['today', 'Today'],
  ['upcoming', 'Upcoming'],
  ['anytime', 'Anytime'],
  ['logbook', 'Logbook'],
];

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function isMobile() {
  return window.matchMedia('(max-width: 900px)').matches;
}

function dayDiff(iso) {
  if (!iso) return null;
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const day = new Date(when);
  day.setHours(0, 0, 0, 0);
  return Math.round((day - today) / 86400000);
}

function whenLabel(iso) {
  const diff = dayDiff(iso);
  if (diff === null) return '';
  if (diff < 0) return 'Overdue';
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  return new Date(iso).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function updatedLabel(iso) {
  const diff = dayDiff(iso);
  if (diff === null) return '';
  if (diff === 0) return 'Today';
  if (diff === -1) return 'Yesterday';
  if (diff === 1) return 'Tomorrow';
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function noteCards(list) {
  if (Array.isArray(list?.items)) {
    return list.items.filter((item) => item.kind === 'note').map((item) => ({
      id: item.id,
      title: String(item.text || '').split(/\r?\n/).map((row) => row.trim()).find(Boolean)?.replace(/^#{1,3}\s+/, '') || 'Untitled',
    }));
  }
  if (Array.isArray(list?.notes)) return list.notes;
  return [];
}

function previewFrom(list) {
  if (!Array.isArray(list?.items)) return list?.preview || [];
  const rows = [];
  list.items.forEach((item) => {
    if (rows.length >= 3) return;
    const title = String(item.text || '').split(/\r?\n/).map((row) => row.trim()).find(Boolean)?.replace(/^#{1,3}\s+/, '') || '';
    if (!title) return;
    rows.push({ id: item.id, kind: item.kind || 'check', title: title.slice(0, 80) });
  });
  return rows;
}

function mergeList(row, next) {
  const source = next.items ? next : row;
  return {
    ...row,
    ...next,
    tags: next.tags || row.tags,
    notes: noteCards(source),
    preview: previewFrom(source),
    group_id: next.group_id ?? row.group_id,
  };
}

function taskBucket(task) {
  const diff = dayDiff(task.due_at);
  if (diff === null) return 'anytime';
  if (diff <= 0) return 'today';
  return 'upcoming';
}

export function initListsHub(root) {
  const me = root?.dataset.me || '';
  const sideEl = document.getElementById('listsSideNav');
  const boardEl = document.getElementById('listsBoard');
  const editorEl = document.getElementById('listsEditor');
  const bodyEl = document.getElementById('listsHubBody');
  const searchEl = document.getElementById('listsSearch');

  let groups = [];
  let lists = [];
  let tasks = [];
  let logbook = [];
  let tagCatalog = [];
  const params = new URLSearchParams(location.search);
  let view = params.get('id') ? 'library' : (params.get('view') || 'today');
  if (!['today', 'upcoming', 'anytime', 'logbook', 'library'].includes(view)) view = 'today';
  let folder = '__all__';
  let kindFilter = '';
  let tagFilter = '';
  let query = '';
  let activeListId = params.get('id') || '';
  let noteId = params.get('note') || '';
  let noteMode = 'write';
  let selected = [];
  let armDraft = false;
  let detail = null;
  let screen = activeListId ? 'doc' : (view === 'library' ? 'index' : 'doc');

  const mq = window.matchMedia('(max-width: 900px)');
  mq.addEventListener('change', () => paint());

  function setScreen(next) {
    screen = next;
    root.dataset.screen = next;
  }

  function syncUrl() {
    const next = new URLSearchParams();
    if (view !== 'today') next.set('view', view);
    if (view === 'library' && activeListId) next.set('id', activeListId);
    if (view === 'library' && noteId) next.set('note', noteId);
    const q = next.toString();
    history.replaceState(null, '', q ? `/lists?${q}` : '/lists');
  }

  function haystack(list) {
    return [
      list.title,
      ...noteCards(list).map((note) => note.title),
      ...(list.preview || []).map((row) => row.title),
    ].join('\n').toLowerCase();
  }

  function matchesQuery(text) {
    const needle = query.trim().toLowerCase();
    return !needle || text.toLowerCase().includes(needle);
  }

  function visibleLists() {
    return lists.filter((list) => {
      if (tagFilter && !(list.tags || []).some((tag) => tag.name === tagFilter)) return false;
      if (kindFilter && list.kind !== kindFilter) return false;
      if (folder !== '__all__' && (list.group_id || '') !== folder) return false;
      return matchesQuery(haystack(list));
    });
  }

  function visibleTasks() {
    const source = view === 'logbook' ? logbook : tasks;
    return source.filter((task) => {
      if (view !== 'logbook' && taskBucket(task) !== view) return false;
      return matchesQuery(`${task.text} ${task.list_title}`);
    });
  }

  function countBucket(name) {
    if (name === 'logbook') return logbook.filter((task) => matchesQuery(`${task.text} ${task.list_title}`)).length;
    return tasks.filter((task) => taskBucket(task) === name && matchesQuery(`${task.text} ${task.list_title}`)).length;
  }

  function viewTitle() {
    if (view !== 'library') return VIEWS.find((row) => row[0] === view)?.[1] || 'Today';
    if (kindFilter === 'todo') return 'To-dos';
    if (kindFilter === 'bullet') return 'Notes';
    if (kindFilter === 'journal') return 'Journals';
    if (folder && folder !== '__all__') return groups.find((group) => group.id === folder)?.name || 'Group';
    if (folder === '') return 'Ungrouped';
    return 'All lists';
  }

  function navButton(id, label, count, active) {
    return `<button type="button" class="ln-nav ${active ? 'is-active' : ''}" data-view="${esc(id)}"><span>${esc(label)}</span><span class="ln-nav-count">${count}</span></button>`;
  }

  function paintSide() {
    if (!sideEl) return;
    const library = [
      ['library', 'All lists', lists.filter((list) => matchesQuery(haystack(list))).length, view === 'library' && folder === '__all__' && !kindFilter],
      ['todo', 'To-dos', lists.filter((list) => list.kind === 'todo' && matchesQuery(haystack(list))).length, view === 'library' && kindFilter === 'todo'],
      ['bullet', 'Notes', lists.filter((list) => list.kind === 'bullet' && matchesQuery(haystack(list))).length, view === 'library' && kindFilter === 'bullet'],
      ['journal', 'Journals', lists.filter((list) => list.kind === 'journal' && matchesQuery(haystack(list))).length, view === 'library' && kindFilter === 'journal'],
    ];
    const groupRows = groups.map((group) => `<div class="ln-group ${view === 'library' && folder === group.id ? 'is-active' : ''}" data-drop-group data-group-id="${esc(group.id)}">
      <button type="button" class="ln-nav" data-folder="${esc(group.id)}"><span>${esc(group.name)}</span></button>
        <button type="button" class="ln-icon" data-rename-group="${esc(group.id)}" aria-label="Rename ${esc(group.name)}">Rename</button>
      <button type="button" class="ln-icon" data-remove-group="${esc(group.id)}" aria-label="Remove ${esc(group.name)}">Remove</button>
    </div>`).join('');
    const tags = tagCatalog.map((tag) => `<button type="button" class="ln-tag ${tagFilter === tag.name ? 'is-active' : ''}" data-tag-filter="${esc(tag.name)}">${tagChipHtml(tag, { className: 'lists-tag-chip', link: false })}</button>`).join('');
    sideEl.innerHTML = `
      <div class="ln-new">
        <button type="button" class="ln-new-btn" id="listsNewBtn" aria-expanded="false" aria-controls="listsNewMenu">New</button>
        <div class="ln-new-menu" id="listsNewMenu" role="menu" hidden>
          <button type="button" role="menuitem" data-new-kind="todo"><strong>To-do</strong><span>A checklist with due dates</span></button>
          <button type="button" role="menuitem" data-new-kind="bullet"><strong>Notes</strong><span>Short lines in one place</span></button>
          <button type="button" role="menuitem" data-new-kind="journal"><strong>Journal</strong><span>Markdown pages</span></button>
        </div>
      </div>
      <div class="ln-nav-block">${VIEWS.map(([id, label]) => navButton(id, label, countBucket(id), view === id)).join('')}</div>
      <div class="ln-side-label">Library</div>
      <div class="ln-nav-block">${library.map(([id, label, count, active]) => navButton(id, label, count, active)).join('')}</div>
      <div class="ln-side-label">Groups</div>
      <div class="ln-nav-block">
        <button type="button" class="ln-nav ${view === 'library' && folder === '' && !kindFilter ? 'is-active' : ''}" data-folder=""><span>Ungrouped</span></button>
        ${groupRows}
        <button type="button" class="ln-nav ln-add" data-add-group><span>New group</span></button>
      </div>
      ${tags ? `<div class="ln-side-label">Tags</div><div class="ln-tags">${tags}</div>` : ''}`;
    wireSide();
  }

  function wireSide() {
    const newBtn = document.getElementById('listsNewBtn');
    const newMenu = document.getElementById('listsNewMenu');
    const closeNew = () => {
      if (!newMenu) return;
      newMenu.hidden = true;
      newBtn?.setAttribute('aria-expanded', 'false');
    };
    newBtn?.addEventListener('click', () => {
      const willOpen = Boolean(newMenu?.hidden);
      if (newMenu) newMenu.hidden = !willOpen;
      newBtn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });
    newMenu?.querySelectorAll('[data-new-kind]').forEach((btn) => {
      btn.addEventListener('click', () => {
        closeNew();
        startList(btn.dataset.newKind);
      });
    });
    sideEl.querySelectorAll('[data-view]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = btn.dataset.view;
        if (id === 'library' || id === 'todo' || id === 'bullet' || id === 'journal') {
          view = 'library';
          kindFilter = id === 'library' ? '' : id;
          folder = '__all__';
          if (isMobile()) setScreen('index');
        } else {
          view = id;
          kindFilter = '';
          if (isMobile()) setScreen('doc');
        }
        syncUrl();
        paint();
      });
    });
    sideEl.querySelectorAll('[data-folder]').forEach((btn) => {
      btn.addEventListener('click', () => {
        view = 'library';
        kindFilter = '';
        folder = btn.dataset.folder;
        if (isMobile()) setScreen('index');
        syncUrl();
        paint();
      });
    });
    sideEl.querySelectorAll('[data-drop-group]').forEach((zone) => {
      zone.addEventListener('dragover', (event) => {
        event.preventDefault();
        zone.classList.add('is-drop');
      });
      zone.addEventListener('dragleave', () => zone.classList.remove('is-drop'));
      zone.addEventListener('drop', async (event) => {
        event.preventDefault();
        zone.classList.remove('is-drop');
        const id = event.dataTransfer.getData('text/list-id');
        if (!id) return;
        await apiJson(`/api/lists/${id}/group`, { method: 'POST', body: { group_id: zone.dataset.groupId } });
        await refresh(false);
      });
    });
    sideEl.querySelectorAll('[data-tag-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        tagFilter = tagFilter === btn.dataset.tagFilter ? '' : btn.dataset.tagFilter;
        view = 'library';
        paint();
      });
    });
    sideEl.querySelector('[data-add-group]')?.addEventListener('click', (event) => beginGroup(event.currentTarget));
    sideEl.querySelectorAll('[data-remove-group]').forEach((btn) => {
      btn.addEventListener('click', async (event) => {
        event.stopPropagation();
        if (!window.confirm('Remove this group? Lists stay, ungrouped.')) return;
        await apiJson(`/api/list-groups/${btn.dataset.removeGroup}`, { method: 'DELETE', body: {} });
        if (folder === btn.dataset.removeGroup) folder = '__all__';
        await refresh(false);
      });
    });
    sideEl.querySelectorAll('[data-rename-group]').forEach((btn) => {
      btn.addEventListener('click', (event) => {
        event.stopPropagation();
        const group = groups.find((row) => row.id === btn.dataset.renameGroup);
        if (!group) return;
        const input = document.createElement('input');
        input.className = 'ln-rename';
        input.value = group.name;
        input.setAttribute('aria-label', 'Group name');
        btn.replaceWith(input);
        input.focus();
        input.select();
        let closed = false;
        const finish = async (save) => {
          if (closed) return;
          closed = true;
          const name = input.value.trim();
          if (!save || !name || name === group.name) {
            paintSide();
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

  function beginGroup(button) {
    const input = document.createElement('input');
    input.className = 'ln-rename';
    input.placeholder = 'Group name';
    input.setAttribute('aria-label', 'Group name');
    button.replaceWith(input);
    input.focus();
    let closed = false;
    const finish = async (save) => {
      if (closed) return;
      closed = true;
      const name = input.value.trim();
      if (!save || !name) {
        paintSide();
        return;
      }
      await apiJson('/api/list-groups', { method: 'POST', body: { name } });
      await refresh(false);
    };
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') finish(true);
      if (event.key === 'Escape') finish(false);
    });
    input.addEventListener('blur', () => finish(true));
  }

  function snippet(list) {
    const line = (list.preview || [])[0];
    if (line?.title) return line.title;
    if (list.kind === 'journal') return 'Empty journal';
    if (list.kind === 'bullet') return 'No notes yet';
    return 'No to-dos yet';
  }

  function listMeta(list) {
    if (list.kind === 'journal') {
      const count = noteCards(list).length;
      return `Journal · ${count} ${count === 1 ? 'page' : 'pages'}`;
    }
    if (list.kind === 'bullet') return 'Notes';
    const prog = list.progress || {};
    if (!prog.total) return 'To-do';
    const left = (prog.total || 0) - (prog.done || 0);
    return left ? `To-do · ${left} left` : 'To-do · Done';
  }

  function paintIndex() {
    if (!boardEl) return;
    if (view !== 'library') {
      boardEl.innerHTML = '';
      return;
    }
    const rows = visibleLists();
    boardEl.innerHTML = `
      <header class="ln-index-head">
        <button type="button" class="ln-views-btn" data-show-side>Views</button>
        <h1>${esc(viewTitle())}</h1>
        <span>${rows.length}</span>
      </header>
      <div class="ln-rows">
        ${rows.map((list) => `<article class="ln-row-wrap ${list.id === activeListId ? 'is-active' : ''}" draggable="true" data-list-id="${esc(list.id)}">
          <button type="button" class="ln-row" data-open-list="${esc(list.id)}">
            <span class="ln-row-title">${esc(list.title)}</span>
            <time class="ln-row-date">${esc(updatedLabel(list.updated_at))}</time>
            <span class="ln-row-snip">${esc(listMeta(list))} · ${esc(snippet(list))}</span>
            ${(list.tags || []).length ? tagChipsRow(list.tags) : ''}
          </button>
          ${list.organizer === me ? `<button type="button" class="ln-add-tag" data-add-tag="${esc(list.id)}" aria-label="Add tag">+</button>` : ''}
        </article>`).join('') || '<p class="ln-empty">Nothing in this folder.</p>'}
      </div>`;
    boardEl.querySelector('[data-show-side]')?.addEventListener('click', () => setScreen('side'));
    boardEl.querySelectorAll('[data-open-list]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openList));
    });
    boardEl.querySelectorAll('.ln-row-wrap').forEach((card) => {
      card.addEventListener('dragstart', (event) => {
        if (event.target.closest('[data-add-tag]')) {
          event.preventDefault();
          return;
        }
        event.dataTransfer.setData('text/list-id', card.dataset.listId);
      });
    });
    wireAddTag(boardEl);
  }

  function groupTasks(items) {
    const groups = new Map();
    items.forEach((task) => {
      const key = view === 'upcoming' ? (whenLabel(task.due_at) || 'Upcoming') : (task.list_title || 'List');
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(task);
    });
    return groups;
  }

  function paintAgenda() {
    if (!editorEl) return;
    const items = visibleTasks();
    const grouped = groupTasks(items);
    const sections = [...grouped.entries()].map(([title, rows]) => `<section class="ln-agenda-group">
      <h2>${esc(title)}</h2>
      ${rows.map((task) => `<div class="ln-task ${view === 'logbook' ? 'is-done' : ''}">
        <button type="button" class="ln-check ${view === 'logbook' ? 'is-done' : ''} ${whenLabel(task.due_at) === 'Overdue' ? 'is-overdue' : ''}" data-check="${esc(task.item_id)}" data-list="${esc(task.list_id)}" aria-pressed="${view === 'logbook' ? 'true' : 'false'}" aria-label="${view === 'logbook' ? 'Mark not done' : 'Mark done'}"></button>
        <button type="button" class="ln-task-open" data-open-task="${esc(task.list_id)}">
          <strong>${esc(task.text || 'Untitled')}</strong>
          ${view === 'upcoming' ? '' : `<span>${esc(task.list_title || '')}</span>`}
        </button>
        ${task.due_at ? `<span class="ln-when ${whenLabel(task.due_at) === 'Overdue' ? 'is-overdue' : ''} ${whenLabel(task.due_at) === 'Today' ? 'is-today' : ''}">${esc(whenLabel(task.due_at))}</span>` : ''}
      </div>`).join('')}
    </section>`).join('');
    editorEl.innerHTML = `
      <div class="ln-agenda">
        <header class="ln-agenda-head">
          <button type="button" class="ln-views-btn" data-show-side>Views</button>
          <div>
            <h1>${esc(viewTitle())}</h1>
            <p>${items.length} ${items.length === 1 ? 'to-do' : 'to-dos'}</p>
          </div>
        </header>
        ${sections || `<p class="ln-empty">${view === 'today' ? 'Nothing due today.' : view === 'upcoming' ? 'Nothing scheduled.' : view === 'logbook' ? 'Completed to-dos show up here.' : 'To-dos without a date show up here.'}</p>`}
      </div>`;
    editorEl.querySelector('[data-show-side]')?.addEventListener('click', () => setScreen('side'));
    editorEl.querySelectorAll('[data-check]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const task = [...tasks, ...logbook].find((row) => row.item_id === btn.dataset.check);
        if (!task) return;
        try {
          await apiJson(`/api/lists/${task.list_id}/items/${task.item_id}`, {
            method: 'PATCH',
            body: { done: view !== 'logbook' },
          });
          await refresh(false);
          paintDoc();
        } catch (err) {
          showError(err.message || 'Could not update that to-do');
        }
      });
    });
    editorEl.querySelectorAll('[data-open-task]').forEach((btn) => {
      btn.addEventListener('click', () => openList(btn.dataset.openTask));
    });
  }

  function paintWelcome() {
    if (!editorEl) return;
    editorEl.innerHTML = `<div class="ln-welcome">
      <h1>${esc(viewTitle())}</h1>
      <p>Pick a list, or start a to-do, a note list, or a journal.</p>
      <div class="ln-welcome-actions">
        <button type="button" data-empty-new="todo">New to-do</button>
        <button type="button" data-empty-new="bullet">New notes</button>
        <button type="button" data-empty-new="journal">New journal</button>
      </div>
    </div>`;
    editorEl.querySelectorAll('[data-empty-new]').forEach((btn) => {
      btn.addEventListener('click', () => startList(btn.dataset.emptyNew));
    });
  }

  function paintDoc() {
    if (bodyEl) bodyEl.dataset.view = view;
    if (view !== 'library') {
      paintAgenda();
      return;
    }
    if (!detail || detail.id !== activeListId) {
      paintWelcome();
      return;
    }
    const showBack = isMobile() || (noteId && detail.kind !== 'journal');
    renderOutline(editorEl, detail, {
      captureKeys: true,
      noteId,
      noteMode,
      selected,
      focusDraft: armDraft,
      onQuiet(next) {
        detailStamp += 1;
        detail = next;
        lists = lists.map((row) => (row.id === next.id ? mergeList(row, next) : row));
        paintIndex();
      },
      onBack: showBack ? () => {
        if (noteId && detail?.kind !== 'journal') {
          noteId = '';
          noteMode = 'write';
          syncUrl();
          paint();
          return;
        }
        setScreen('index');
      } : null,
      onChange: applyDetail,
      onOpenNote(id) {
        noteId = id || '';
        noteMode = 'write';
        selected = [];
        syncUrl();
        paint();
      },
      onCloseNote() {
        noteId = '';
        noteMode = 'write';
        syncUrl();
        paint();
      },
      onNoteMode(mode) {
        noteMode = mode;
        paintDoc();
      },
      onSelect(id, mode) {
        if (!id) selected = [];
        else if (mode === 'add') selected = [...new Set([...selected, id])];
        else selected = selected.includes(id) ? selected.filter((row) => row !== id) : [...selected, id];
        paintDoc();
      },
      onError: showError,
      onEdit: async () => {
        const draft = await listDialog(detail);
        if (!draft) return;
        applyDetail(await apiJson(`/api/lists/${detail.id}`, { method: 'PATCH', body: draft }));
      },
      onShare: () => sharePicker('list', detail.id).catch((err) => showError(err.message)),
      onDeleteList: async () => {
        if (!window.confirm('Delete this list?')) return;
        await apiJson(`/api/lists/${detail.id}`, { method: 'DELETE' });
        activeListId = '';
        noteId = '';
        detail = null;
        syncUrl();
        if (isMobile()) setScreen('index');
        refresh(false);
      },
    });
    armDraft = false;
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

  function applyDetail(next) {
    detailStamp += 1;
    detail = next;
    lists = lists.map((row) => (row.id === next.id ? mergeList(row, next) : row));
    paintIndex();
    paintDoc();
  }

  async function openList(id, keepNote = false) {
    if (!keepNote && id !== activeListId) noteId = '';
    activeListId = id;
    view = 'library';
    syncUrl();
    const list = await apiJson(`/api/lists/${id}`);
    if (list.kind === 'journal' && !noteId) {
      const page = [...(list.items || [])].reverse().find((item) => item.kind === 'note');
      if (page) noteId = page.id;
      syncUrl();
    }
    detail = list;
    if (isMobile()) setScreen('doc');
    paintSide();
    paintIndex();
    paintDoc();
  }

  function paint() {
    setScreen(isMobile() ? screen : 'index');
    paintSide();
    paintIndex();
    paintDoc();
  }

  let pendingLiveId = '';
  let liveGen = 0;
  let detailStamp = 0;

  function editorBusy() {
    const el = document.activeElement;
    if (!editorEl || !el || !editorEl.contains(el)) return false;
    const tag = el.tagName;
    return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
  }

  async function pullOpen(id) {
    const stamp = detailStamp;
    const gen = ++liveGen;
    const list = await apiJson(`/api/lists/${id}`);
    if (gen !== liveGen || activeListId !== id || view !== 'library') return;
    if (stamp !== detailStamp || editorBusy()) {
      pendingLiveId = id;
      return;
    }
    pendingLiveId = '';
    applyDetail(list);
  }

  async function refresh(openCurrent = true) {
    const data = await apiJson('/api/lists');
    groups = data.groups || [];
    lists = data.lists || [];
    tasks = data.tasks || [];
    logbook = data.logbook || [];
    tagCatalog = data.tag_catalog || [];
    paintSide();
    paintIndex();
    if (openCurrent && view === 'library' && activeListId) {
      await openList(activeListId, true).catch(() => {
        activeListId = '';
        detail = null;
        paintDoc();
      });
      return;
    }
    paintDoc();
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
    refresh(false).then(() => {
      const id = String(data.id || '');
      if (!id || activeListId !== id || view !== 'library') return;
      if (editorBusy()) pendingLiveId = id;
      else pullOpen(id).catch(() => {});
    }).catch(() => {});
  });

  let tagMenu = null;
  function closeTagMenu() {
    tagMenu?.remove();
    tagMenu = null;
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
    menu.style.left = `${Math.max(8, Math.min(rect.left, window.innerWidth - 180))}px`;
    menu.style.top = `${rect.bottom + 4}px`;
    tagMenu = menu;
    menu.querySelectorAll('[data-pick-tag]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        closeTagMenu();
        await apiJson(`/api/lists/${listId}`, { method: 'PATCH', body: { tag_ids: [...have, Number(btn.dataset.pickTag)] } });
        await refresh(false);
        if (activeListId === listId) applyDetail(await apiJson(`/api/lists/${listId}`));
      });
    });
    setTimeout(() => {
      document.addEventListener('pointerdown', function close(event) {
        if (menu.contains(event.target)) return;
        document.removeEventListener('pointerdown', close, true);
        closeTagMenu();
      }, true);
    }, 0);
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

  async function startList(kind) {
    let draft;
    try {
      draft = await listDialog(null, { kind });
    } catch (err) {
      showError(err.message || 'Could not open the form');
      return;
    }
    if (!draft) return;
    if (folder && folder !== '__all__') draft.group_id = folder;
    let list;
    try {
      list = await apiJson('/api/lists', { method: 'POST', body: draft });
    } catch (err) {
      showError(err.message || 'Could not create the list');
      return;
    }
    armDraft = list.kind !== 'journal';
    activeListId = list.id;
    detail = list;
    noteId = list.kind === 'journal' ? ((list.items || []).find((item) => item.kind === 'note')?.id || '') : '';
    view = 'library';
    kindFilter = '';
    if (list.group_id) folder = list.group_id;
    syncUrl();
    await refresh();
  }

  searchEl?.addEventListener('input', () => {
    query = searchEl.value;
    paintSide();
    paintIndex();
    if (view !== 'library') paintDoc();
  });

  document.addEventListener('pointerdown', (event) => {
    const menu = document.getElementById('listsNewMenu');
    const button = document.getElementById('listsNewBtn');
    if (!menu || menu.hidden) return;
    if (menu.contains(event.target) || button?.contains(event.target)) return;
    menu.hidden = true;
    button?.setAttribute('aria-expanded', 'false');
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      const menu = document.getElementById('listsNewMenu');
      if (menu && !menu.hidden) {
        menu.hidden = true;
        document.getElementById('listsNewBtn')?.setAttribute('aria-expanded', 'false');
      }
    }
    if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== 'k') return;
    const target = event.target;
    if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA')) return;
    event.preventDefault();
    openPalette();
  });

  function openPalette() {
    document.querySelector('.list-palette')?.remove();
    const node = document.createElement('div');
    node.className = 'list-palette';
    const rows = [];
    lists.forEach((list) => {
      rows.push({ label: list.title, hint: 'List', run: () => openList(list.id) });
      noteCards(list).forEach((note) => rows.push({ label: note.title, hint: list.title, run: () => openNote(list.id, note.id) }));
    });
    node.innerHTML = `<input class="list-palette-input" placeholder="Search lists and pages" />
      <div class="list-palette-rows"></div>`;
    document.body.appendChild(node);
    const input = node.querySelector('input');
    const box = node.querySelector('.list-palette-rows');
    let active = 0;
    const paintRows = () => {
      const needle = input.value.trim().toLowerCase();
      const shown = rows.filter((row) => !needle || row.label.toLowerCase().includes(needle) || row.hint.toLowerCase().includes(needle)).slice(0, 8);
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

  async function openNote(listId, itemId) {
    noteId = itemId;
    noteMode = 'write';
    if (detail?.id !== listId || view !== 'library') await openList(listId, true);
    else {
      syncUrl();
      paintDoc();
    }
  }

  setScreen(screen);
  refresh().catch((err) => {
    if (editorEl) editorEl.innerHTML = `<div class="ln-empty">${esc(err.message)}</div>`;
  });

  return { refresh };
}
