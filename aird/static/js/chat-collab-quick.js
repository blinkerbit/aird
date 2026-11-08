import { apiJson } from './collab-api.js';
import { listDialog, meetDialog, pollDialog } from './collab-ui.js';

async function createAndPin(convId, kind, path, draft) {
  const created = await apiJson(path, { method: 'POST', body: draft });
  const linked = await apiJson(`/api/chat/conversations/${convId}/refs`, {
    method: 'POST',
    body: { kind, id: created.id },
  });
  return linked.ref;
}

export async function quickPoll(convId) {
  const draft = await pollDialog();
  if (!draft) return null;
  return createAndPin(convId, 'poll', '/api/polls', draft);
}

export async function quickList(convId) {
  const draft = await listDialog();
  if (!draft) return null;
  return createAndPin(convId, 'list', '/api/lists', draft);
}

export async function quickMeet(convId) {
  const draft = await meetDialog();
  if (!draft) return null;
  return createAndPin(convId, 'meet', '/api/meet', draft);
}