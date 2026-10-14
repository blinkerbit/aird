"use strict";

import { createStore } from '../core/store.js';

/** Session-persisted multi-select. Inject storage in tests; the page uses sessionStorage. */
export function createSelectionStore(storage, key = "aird_browse_selections") {
  // Observable store holds the loaded list (`null` = not yet read from storage).
  const store = createStore({ items: null });

  function read() {
    const { items } = store.getState();
    if (items) return items;
    let loaded = [];
    try {
      const arr = JSON.parse(storage.getItem(key)) || [];
      loaded = Array.isArray(arr) ? arr : [];
    } catch {
      loaded = [];
    }
    store.setState({ items: loaded });
    return loaded;
  }

  function write(next) {
    store.setState({ items: next });
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
      store.setState({ items: [] });
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

    /** Subscribe to selection changes (Observer via the shared store). */
    subscribe(listener) {
      return store.select((s) => s.items, (items) => listener(items ? items.slice() : []));
    },
  };
}

export const SelectionStore = createSelectionStore(sessionStorage);
