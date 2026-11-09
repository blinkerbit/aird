import { apiJson } from '../collab-api.js';
import { meetDialog, mountMeet } from '../collab-ui.js';

const root = document.querySelector('[data-hub="meet"]');
const me = root?.dataset.me || '';
const indexEl = document.getElementById('meetIndex');
const detailEl = document.getElementById('meetDetail');
let activeId = new URLSearchParams(location.search).get('id') || '';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function renderIndex(meets) {
  if (!meets.length) {
    indexEl.innerHTML = '<div class="collab-empty">No meetings yet. Start one — the link stays live for 48 hours.</div>';
    return;
  }
  indexEl.innerHTML = meets.map((m) => (
    `<button type="button" data-id="${esc(m.id)}" class="${m.id === activeId ? 'is-active' : ''}">
      <strong>${esc(m.title)}</strong>
      <span class="collab-hint">${esc(m.slug ? `//${m.slug}` : '')}</span>
      <span class="collab-hint">${esc(m.live ? 'Live' : 'Ended')}</span>
    </button>`
  )).join('');
  indexEl.querySelectorAll('button[data-id]').forEach((btn) => {
    btn.addEventListener('click', () => openMeet(btn.dataset.id));
  });
}

async function openMeet(id) {
  activeId = id;
  const meet = await apiJson(`/api/meet/${id}`);
  history.replaceState(null, '', `/meet?id=${encodeURIComponent(id)}`);
  renderIndex((await apiJson('/api/meet')).meets || []);
  mountMeet(detailEl, meet, {
    me,
    onChange: () => openMeet(id),
    onDelete() {
      activeId = '';
      history.replaceState(null, '', '/meet');
      detailEl.innerHTML = '<div class="collab-empty">Create or select a meeting.</div>';
      refresh();
    },
  });
}

async function refresh() {
  const data = await apiJson('/api/meet');
  renderIndex(data.meets || []);
  if (activeId) openMeet(activeId).catch(() => {});
}

document.getElementById('meetNewBtn')?.addEventListener('click', async () => {
  const draft = await meetDialog();
  if (!draft) return;
  const meet = await apiJson('/api/meet', { method: 'POST', body: draft });
  activeId = meet.id;
  await refresh();
});

refresh().catch((e) => { detailEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`; });
