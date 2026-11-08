import { apiJson } from '../collab-api.js';
import { initCalls, joinStandaloneCall } from '../chat-calls.js';

const root = document.querySelector('.meet-join');
const meet = globalThis.__AIRD_MEET__ || {};
const meetId = root?.dataset?.meetId || meet.id;

document.getElementById('meetJoinBtn')?.addEventListener('click', async () => {
  const session = await apiJson(`/api/meet/${meetId}/token`, { method: 'POST', body: {} });
  initCalls({
    me: document.cookie.match(/user=([^;]+)/)?.[1] || '',
    api: apiJson,
  });
  await joinStandaloneCall({ ...session, mode: 'video' }, { title: meet.title || 'Meet' });
});
