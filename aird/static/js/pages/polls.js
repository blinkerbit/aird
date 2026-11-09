import { apiJson } from '../collab-api.js';
import { mountPoll, pollDialog } from '../collab-ui.js';

const root = document.querySelector('[data-hub="polls"]');
const me = root?.dataset.me || '';
const indexEl = document.getElementById('pollsIndex');
const detailEl = document.getElementById('pollsDetail');
let activeId = new URLSearchParams(location.search).get('id') || '';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function renderIndex(polls) {
  if (!polls.length) {
    indexEl.innerHTML = '<div class="collab-empty">No polls yet. Create one to start collecting votes.</div>';
    return;
  }
  indexEl.innerHTML = polls.map((p) => (
    `<button type="button" data-id="${esc(p.id)}" class="${p.id === activeId ? 'is-active' : ''}">
      <strong>${esc(p.question)}</strong>
      <span class="collab-hint">${esc(p.slug ? `$$${p.slug}` : '')}</span>
      <span class="collab-hint">${esc(p.visibility === 'private' ? 'Private' : 'Public')}</span>
    </button>`
  )).join('');
  indexEl.querySelectorAll('button[data-id]').forEach((btn) => {
    btn.addEventListener('click', () => openPoll(btn.dataset.id));
  });
}

async function openPoll(id) {
  activeId = id;
  const poll = await apiJson(`/api/polls/${id}`);
  history.replaceState(null, '', `/polls?id=${encodeURIComponent(id)}`);
  renderIndex((await apiJson('/api/polls')).polls || []);
  mountPoll(detailEl, poll, {
    me,
    onChange: () => openPoll(id),
    onDelete() {
      activeId = '';
      history.replaceState(null, '', '/polls');
      detailEl.innerHTML = '<div class="collab-empty">Create or select a poll.</div>';
      refresh();
    },
  });
}

async function refresh() {
  const data = await apiJson('/api/polls');
  renderIndex(data.polls || []);
  if (activeId) openPoll(activeId).catch(() => {});
}

document.getElementById('pollsNewBtn')?.addEventListener('click', async () => {
  const draft = await pollDialog();
  if (!draft) return;
  const poll = await apiJson('/api/polls', { method: 'POST', body: draft });
  activeId = poll.id;
  await refresh();
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'n' && !event.metaKey && !event.ctrlKey && document.activeElement === document.body) {
    document.getElementById('pollsNewBtn')?.click();
  }
});

refresh().catch((e) => { detailEl.innerHTML = `<div class="collab-empty">${esc(e.message)}</div>`; });
