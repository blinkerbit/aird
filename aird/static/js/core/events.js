/**
 * Event bus — Mediator/Observer primitive for decoupled modules.
 *
 * Modules publish facts (`bus.emit('selection:changed', paths)`) instead
 * of importing each other; subscribers register with `on`/`once` and get
 * an unsubscribe function back. A throwing listener never breaks the
 * publisher or the remaining listeners.
 */
/**
 * Call every listener with `call(listener)`; a throwing listener is logged
 * and the rest still run. Shared by the bus and the store so subscriber
 * isolation stays identical in both.
 */
export function safeNotify(listeners, call, label) {
  for (const listener of [...listeners]) {
    try {
      call(listener);
    } catch (err) {
      console.error(label, err);
    }
  }
}

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
    safeNotify(set, (listener) => listener(payload), `Event listener for "${event}" failed:`);
    return true;
  }

  function clear(event) {
    if (event === undefined) listeners.clear();
    else listeners.delete(event);
  }

  return { on, once, off, emit, clear };
}
