export class HybridClock {
  constructor() {
    this.wall = 0;
    this.logical = 0;
  }

  tick() {
    const now = Date.now();
    if (now > this.wall) {
      this.wall = now;
      this.logical = 0;
    } else this.logical += 1;
    return this.format();
  }

  receive(remote) {
    const [wall, logical] = parseHlc(remote);
    const now = Date.now();
    if (now > this.wall && now > wall) {
      this.wall = now;
      this.logical = 0;
    } else if (wall > this.wall) {
      this.wall = wall;
      this.logical = logical + 1;
    } else if (wall === this.wall) {
      this.logical = Math.max(this.logical, logical) + 1;
    } else this.logical += 1;
    return this.format();
  }

  format() {
    return `${String(this.wall).padStart(13, '0')}:${String(this.logical).padStart(6, '0')}`;
  }
}

export function parseHlc(value) {
  const raw = String(value || '');
  if (!raw.includes(':')) return [0, 0];
  const [a, b] = raw.split(':', 2);
  return [Number(a) || 0, Number(b) || 0];
}

export function hlcCmp(a, b) {
  const [aw, al] = parseHlc(a);
  const [bw, bl] = parseHlc(b);
  if (aw !== bw) return aw < bw ? -1 : 1;
  if (al !== bl) return al < bl ? -1 : 1;
  return 0;
}

export function newId() {
  if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
  return `b_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 10)}`;
}
