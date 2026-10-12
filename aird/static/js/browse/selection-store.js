"use strict";

/** Session-persisted multi-select. Inject storage in tests; the page uses sessionStorage. */
export function createSelectionStore(storage, key = "aird_browse_selections") {
  let items = null;

  function read() {
    if (items) return items;
    try {
      const arr = JSON.parse(storage.getItem(key)) || [];
      items = Array.isArray(arr) ? arr : [];
    } catch {
      items = [];
    }
    return items;
  }

  function write(next) {
    items = next;
    try {
      storage.setItem(key, JSON.stringify(next));
    } catch {
      /* quota / private mode */
    }
  }

  return {
    KEY: key,

    getAll() {
      return read().slice();
    },

    add(path) {
      const arr = read();
      if (!arr.includes(path)) write(arr.concat(path));
    },

    remove(path) {
      write(read().filter((p) => p !== path));
    },

    has(path) {
      return read().includes(path);
    },

    clear() {
      items = [];
      try {
        storage.removeItem(key);
      } catch {
        /* ignore */
      }
    },

    count() {
      return read().length;
    },

    addMany(paths) {
      const set = new Set(read());
      let changed = false;
      for (const p of paths) {
        if (!set.has(p)) {
          set.add(p);
          changed = true;
        }
      }
      if (changed) write([...set]);
    },

    removeMany(paths) {
      const drop = new Set(paths);
      write(read().filter((p) => !drop.has(p)));
    },
  };
}

export const SelectionStore = createSelectionStore(sessionStorage);
