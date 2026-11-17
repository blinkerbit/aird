function toNum(key, fallback) {
  if (!key) return fallback;
  const head = String(key).split('#', 1)[0];
  const n = Number(head);
  return Number.isFinite(n) ? n : fallback;
}

export function midOrder(prev, next) {
  const left = toNum(prev, 0);
  let right = toNum(next, left + 2);
  if (right <= left) right = left + 2;
  const mid = (left + right) / 2;
  const trim = (value) => value.toFixed(16).replace(/0+$/, '').replace(/\.$/, '') || '0';
  let text = trim(mid);
  if (text === trim(left)) text = `${trim(left)}5`;
  const suffix = Math.floor(Math.random() * 0xffffff).toString(16).padStart(6, '0');
  return `${text}#${suffix}`;
}
