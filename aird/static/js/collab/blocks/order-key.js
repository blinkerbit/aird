function toNum(key, fallback) {
  if (!key) return fallback;
  const head = String(key).split('#', 1)[0];
  const n = Number(head);
  return Number.isFinite(n) ? n : fallback;
}

export function midOrder(prev, next, actor = '') {
  const left = toNum(prev, 0);
  let right = toNum(next, left + 2);
  if (right <= left) right = left + 2;
  const mid = (left + right) / 2;
  let suffix = 0;
  for (let i = 0; i < actor.length; i += 1) suffix = (suffix + actor.charCodeAt(i) * (i + 1)) % 0xffff;
  if (!actor) suffix = Math.floor(Math.random() * 0xffff);
  return `${mid.toFixed(8)}#${suffix.toString(16).padStart(4, '0')}`;
}
