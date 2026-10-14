/**
 * Observable store — single source of truth with change notification.
 *
 * Pattern: Observer over an immutable-by-convention state object.
 * `setState` shallow-merges a patch (or applies an updater function) and
 * notifies subscribers only when a value actually changed; `select`
 * subscribes to one derived slice. State snapshots handed out are copies,
 * so callers cannot mutate the store behind its back.
 */
export function createStore(initialState = {}) {
  let state = { ...initialState };
  const listeners = new Set();

  function getState() {
    return { ...state };
  }

  function notify(prev) {
    const snapshot = getState();
    for (const listener of [...listeners]) {
      try {
        listener(snapshot, prev);
      } catch (err) {
        console.error('Store subscriber failed:', err);
      }
    }
  }

  function setState(patch) {
    const prev = state;
    const next = typeof patch === 'function' ? patch(getState()) : patch;
    if (!next || typeof next !== 'object') return getState();
    state = { ...state, ...next };
    const changed = Object.keys(next).some((key) => !Object.is(prev[key], state[key]));
    if (changed) notify(prev);
    return getState();
  }

  function subscribe(listener) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  }

  function select(selector, listener) {
    let current = selector(getState());
    return subscribe((snapshot) => {
      const next = selector(snapshot);
      if (Object.is(next, current)) return;
      const old = current;
      current = next;
      listener(next, old);
    });
  }

  return { getState, setState, subscribe, select };
}
