import { describe, expect, it } from 'vitest';
import { createSelectionStore } from '/static/js/browse/selection-store.js';

function memoryStorage() {
  const data = new Map();
  return {
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => data.set(key, String(value)),
    removeItem: (key) => data.delete(key),
  };
}

describe('createSelectionStore', () => {
  it('adds, dedupes, and removes paths', () => {
    const store = createSelectionStore(memoryStorage());
    store.add('/a');
    store.add('/a');
    store.addMany(['/b', '/a']);
    expect(store.getAll()).toEqual(['/a', '/b']);
    expect(store.has('/b')).toBe(true);
    store.remove('/a');
    store.removeMany(['/b']);
    expect(store.count()).toBe(0);
    store.add('/c');
    store.clear();
    expect(store.getAll()).toEqual([]);
  });

  it('starts empty when storage is unreadable', () => {
    const store = createSelectionStore({
      getItem() { throw new Error('denied'); },
      setItem() {},
      removeItem() {},
    });
    expect(store.getAll()).toEqual([]);
    store.add('/a');
    expect(store.count()).toBe(1);
  });
});
