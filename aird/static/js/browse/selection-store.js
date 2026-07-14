"use strict";

/** Session-persisted multi-select for browse (Set-backed for O(1) membership). */
export const SelectionStore = {
  KEY: 'aird_browse_selections',

  _read() {
    try {
      const arr = JSON.parse(sessionStorage.getItem(this.KEY)) || [];
      return Array.isArray(arr) ? arr : [];
    } catch {
      return [];
    }
  },

  _write(arr) {
    try {
      sessionStorage.setItem(this.KEY, JSON.stringify(arr));
    } catch {
      /* quota / private mode */
    }
  },

  getAll() {
    return this._read();
  },

  add(path) {
    const arr = this._read();
    if (!arr.includes(path)) {
      arr.push(path);
      this._write(arr);
    }
  },

  remove(path) {
    this._write(this._read().filter((p) => p !== path));
  },

  has(path) {
    return this._read().includes(path);
  },

  clear() {
    try {
      sessionStorage.removeItem(this.KEY);
    } catch {
      /* ignore */
    }
  },

  count() {
    return this._read().length;
  },

  addMany(paths) {
    const set = new Set(this._read());
    let changed = false;
    for (const p of paths) {
      if (!set.has(p)) {
        set.add(p);
        changed = true;
      }
    }
    if (changed) this._write([...set]);
  },

  removeMany(paths) {
    const drop = new Set(paths);
    this._write(this._read().filter((p) => !drop.has(p)));
  },
};
