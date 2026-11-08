import { apiJson } from '../collab-api.js';
import { listDialog, mountList } from '../collab-ui.js';

const root = document.querySelector('[data-hub="lists"]');
const me = root?.dataset.me || '';
const indexEl = document.getElementById('listsIndex');
const detailEl = document.getElementById('listsDetail');
let activeId = new URLSearchParams(location.search).get('id') || '';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function renderIndex(lists) {
  if (!lists.length) {
    indexEl.innerHTML = '<div class="collab-empty">No lists yet. Create a checklist or a bullet list.</div>';
    return;
  }
  indexEl.innerHTML = lists.map((l) => (
    `<button type="button" data-id="${esc(l.id)}" class="${l.id === activeId ? 'is-active' : ''}">
      <strong>${esc(l.title)}</strong>
      <span class="collab-hint">${esc(l.slug ? `#${l.slug}` : '')}</span>
      <span class="collab-hint">${esc(l.kind === 'bullet' ? 'Bullets' : 'Checklist')}</span>
    </button>`
  )).join('');
  indexEl.querySelectorAll('button[data-id]').forEach((btn) => {
    btn.addEventListener('click', () => openList(btn.dataset.id));
  });
}

async function openList(id) {
  activeId = id;
  const list = await apiJson(`/api/lists/${id}`);
  history.replaceState(null, '', `/lists?id=${encodeURIComponent(id)}`);
  renderIndex((await apiJson('/api/lists')).lists || []);
  mountList(detailEl, list, { me, onChange: () => openList(id) });
}

async function refresh() {
  const data = await apiJson('/api/lists');
  renderIndex(data.lists || []);
  if (activeId) openList(activeId).catch(() => {});
}

document.getElementById('listsNewBtn')?.addEventListener('click', async () => {
  const draft = await listDialog();
  if (!draft) return;
  const list = await apiJson('/api/lists', { method: 'POST', body: draft });
  activeId = list.id;
  await refresh();
});

refresh().catch((e) => { detailEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`; });
