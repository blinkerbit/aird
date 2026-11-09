import { apiJson } from '../collab-api.js';
import { listDialog, mountList } from '../collab-ui.js';

const root = document.querySelector('[data-hub="lists"]');
const me = root?.dataset.me || '';
const indexEl = document.getElementById('listsIndex');
const detailEl = document.getElementById('listsDetail');
let activeId = new URLSearchParams(location.search).get('id') || '';
let groups = [];

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function listButton(list) {
  const due = list.next_due ? new Date(list.next_due).toLocaleString() : '';
  const prog = list.progress || {};
  return `<button type="button" data-id="${esc(list.id)}" class="${list.id === activeId ? 'is-active' : ''}">
      <strong>${esc(list.title)}</strong>
      <span class="collab-hint">${esc(list.kind === 'bullet' ? 'Bullets' : 'Checklist')}${prog.total ? ` · ${prog.done || 0}/${prog.total}` : ''}${due ? ` · ${due}` : ''}</span>
    </button>`;
}

function renderIndex(lists) {
  if (!lists.length && !groups.length) {
    indexEl.innerHTML = '<div class="collab-empty">No lists yet. Create a checklist or a bullet list.</div>';
    return;
  }
  const sections = groups.map((group) => ({
    id: group.id,
    name: group.name,
    lists: lists.filter((list) => list.group_id === group.id),
  }));
  sections.push({ id: '', name: 'Ungrouped', lists: lists.filter((list) => !list.group_id) });
  indexEl.innerHTML = sections.map((section) => `
    <div class="collab-group">
      <div class="collab-hint" style="display:flex;justify-content:space-between;padding:.45rem .85rem">
        <span>${esc(section.name)}</span>
        ${section.id ? `<button type="button" data-drop-group="${esc(section.id)}">Remove</button>` : ''}
      </div>
      ${section.lists.map(listButton).join('') || '<div class="collab-empty">Empty</div>'}
    </div>`).join('');
  indexEl.querySelectorAll('button[data-id]').forEach((btn) => {
    btn.addEventListener('click', () => openList(btn.dataset.id));
  });
  indexEl.querySelectorAll('[data-drop-group]').forEach((btn) => {
    btn.addEventListener('click', async (event) => {
      event.stopPropagation();
      await apiJson(`/api/list-groups/${btn.dataset.dropGroup}`, { method: 'DELETE', body: {} });
      await refresh();
    });
  });
}

async function openList(id) {
  activeId = id;
  const list = await apiJson(`/api/lists/${id}`);
  history.replaceState(null, '', `/lists?id=${encodeURIComponent(id)}`);
  renderIndex((await apiJson('/api/lists')).lists || []);
  mountList(detailEl, list, { me, groups, onChange: () => openList(id) });
}

async function refresh() {
  const data = await apiJson('/api/lists');
  groups = data.groups || [];
  renderIndex(data.lists || []);
  if (activeId) openList(activeId).catch(() => {});
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
  activeId = list.id;
  await refresh();
});

refresh().catch((e) => { detailEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`; });
