(function () {
  "use strict";

  class P2PMediator {
    constructor() {
      this.subscribers = new Map();
    }

    on(event, fn) {
      if (!this.subscribers.has(event)) {
        this.subscribers.set(event, new Set());
      }
      this.subscribers.get(event).add(fn);
      return () => this.subscribers.get(event)?.delete(fn);
    }

    emit(event, payload) {
      const listeners = this.subscribers.get(event);
      if (!listeners) return;
      for (const listener of listeners) {
        listener(payload);
      }
    }
  }

  globalThis.P2PPatterns = globalThis.P2PPatterns || {};
  globalThis.P2PPatterns.P2PMediator = P2PMediator;
})();
