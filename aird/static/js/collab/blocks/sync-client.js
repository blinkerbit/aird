import { apiJson } from '../../collab-api.js';
import { HybridClock } from './hlc.js';
import { clearPending, getTip, pendingOps, queueOps, saveBlocks, setTip } from './local-db.js';
import { fromApiList } from './types.js';

export function makeActorId(username = 'user') {
  const key = 'aird-list-actor';
  let id = localStorage.getItem(key);
  if (!id) {
    id = `${username}:${Math.random().toString(36).slice(2, 10)}`;
    localStorage.setItem(key, id);
  }
  return id;
}

export async function pushPull(listId, { actorId, clock, items } = {}) {
  const actor = actorId || makeActorId();
  const hlc = clock || new HybridClock();
  const since = await getTip(listId);
  const queued = await pendingOps(listId);
  const body = {
    actor_id: actor,
    since_hlc: since,
    ops: queued.map(({ pending, ...op }) => op),
  };
  const result = await apiJson(`/api/lists/${encodeURIComponent(listId)}/sync`, {
    method: 'POST',
    body,
  });
  await clearPending(queued.map((op) => op.opId));
  if (result.tip_hlc) await setTip(listId, result.tip_hlc);
  if (result.list) {
    const next = fromApiList(result.list);
    await saveBlocks(listId, next);
    return { ...result, items: next, tipHlc: result.tip_hlc, clock: hlc };
  }
  return { ...result, items: items || [], tipHlc: result.tip_hlc, clock: hlc };
}

export async function enqueueAndSync(listId, ops, extras = {}) {
  await queueOps(ops.map((op) => ({ ...op, listId })));
  try {
    return await pushPull(listId, extras);
  } catch (err) {
    return { offline: true, error: err, items: extras.items || null };
  }
}
