import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { vi } from 'vitest';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/** Evaluate a browser classic script (IIFE) into the current happy-dom global. */
export function loadClassicScript(relFromRepo) {
  const code = fs.readFileSync(path.join(repoRoot, relFromRepo), 'utf8');
  // Scripts attach to globalThis / window; run in this realm.
  // eslint-disable-next-line no-new-func
  new Function(code)();
}

export function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => state,
  });
  document.dispatchEvent(new Event('visibilitychange'));
}

export function setOnline(online) {
  Object.defineProperty(navigator, 'onLine', {
    configurable: true,
    get: () => online,
  });
  window.dispatchEvent(new Event(online ? 'online' : 'offline'));
}

/** Minimal XHR stub that completes successfully on send(). */
export function stubXHRSuccess({ status = 200, responseText = 'ok' } = {}) {
  class FakeXHR {
    constructor() {
      this.upload = { addEventListener: vi.fn() };
      this._listeners = new Map();
      this.status = 0;
      this.responseText = '';
      this.withCredentials = false;
    }

    addEventListener(type, fn) {
      if (!this._listeners.has(type)) this._listeners.set(type, []);
      this._listeners.get(type).push(fn);
    }

    open() {}

    setRequestHeader() {}

    abort() {
      this._emit('abort');
      this._emit('loadend');
    }

    send() {
      queueMicrotask(() => {
        this.status = status;
        this.responseText = responseText;
        this._emit('load');
        this._emit('loadend');
      });
    }

    _emit(type) {
      for (const fn of this._listeners.get(type) || []) fn();
    }
  }
  vi.stubGlobal('XMLHttpRequest', FakeXHR);
  return FakeXHR;
}
