/**
 * Event bus — Mediator/Observer primitive for decoupled modules.
 *
 * Modules publish facts (`bus.emit('selection:changed', paths)`) instead
 * of importing each other; subscribers register with `on`/`once` and get
 * an unsubscribe function back. A throwing listener never breaks the
 * publisher or the remaining listeners.
 */
export function createEventBus() {
  const listeners = new Map();

  function bucket(event) {
    let set = listeners.get(event);
    if (!set) {
      set = new Set();
      listeners.set(event, set);
    }
    return set;
  }

  function on(event, listener) {
    bucket(event).add(listener);
    return () => off(event, listener);
  }

  function once(event, listener) {
    const wrapper = (payload) => {
      off(event, wrapper);
      listener(payload);
    };
    return on(event, wrapper);
  }

  function off(event, listener) {
    const set = listeners.get(event);
    if (!set) return;
    set.delete(listener);
    if (set.size === 0) listeners.delete(event);
  }

  function emit(event, payload) {
    const set = listeners.get(event);
    if (!set) return false;
    for (const listener of [...set]) {
      try {
        listener(payload);
      } catch (err) {
        console.error(`Event listener for "${event}" failed:`, err);
      }
    }
    return true;
  }

  function clear(event) {
    if (event === undefined) listeners.clear();
    else listeners.delete(event);
  }

  return { on, once, off, emit, clear };
}
