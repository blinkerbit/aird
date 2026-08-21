import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadClassicScript } from './helpers.js';

describe('AirdFileTransferHttp', () => {
  beforeEach(() => {
    delete globalThis.AirdFileTransferHttp;
    delete globalThis.AirdTransferBackground;
    delete globalThis.AirdRuntimeConfig;
    globalThis.__BROWSE_CONFIG = {};
    loadClassicScript('aird/static/js/transfer-background.js');
    loadClassicScript('aird/static/js/file-transfer-http.js');
  });

  it('createCancelScope abort cancels tracked XHRs', () => {
    const scope = globalThis.AirdFileTransferHttp.createCancelScope();
    const xhr = {
      abort: vi.fn(),
      addEventListener: vi.fn(),
    };
    scope.trackXhr(xhr);
    scope.abort();
    expect(scope.aborted).toBe(true);
    expect(xhr.abort).toHaveBeenCalled();
  });

  it('small upload syncs background state before sending', async () => {
    const BG = globalThis.AirdTransferBackground;
    const sync = vi.spyOn(BG, 'syncFromDocument');
    const open = vi.fn();
    const send = vi.fn();
    const setRequestHeader = vi.fn();

    class FakeXHR {
      constructor() {
        this.upload = { addEventListener: vi.fn() };
        this._listeners = {};
      }
      addEventListener(type, fn) {
        (this._listeners[type] ||= []).push(fn);
      }
      open(...args) { open(...args); }
      setRequestHeader(...args) { setRequestHeader(...args); }
      send(body) {
        send(body);
        queueMicrotask(() => {
          this.status = 200;
          this.responseText = 'Upload successful';
          for (const fn of this._listeners.load || []) fn();
        });
      }
    }
    vi.stubGlobal('XMLHttpRequest', FakeXHR);

    const file = new File(['hello'], 'hello.txt', { type: 'text/plain' });
    const result = await globalThis.AirdFileTransferHttp.uploadFile(file, {
      uploadDir: '',
      filename: 'hello.txt',
    });

    expect(sync).toHaveBeenCalled();
    expect(open).toHaveBeenCalledWith('POST', '/upload');
    expect(send).toHaveBeenCalled();
    expect(result.message).toMatch(/successful/i);
  });

  it('download returns a native streaming URL without fetching', async () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    const result = await globalThis.AirdFileTransferHttp.downloadFile('folder/a.bin');

    expect(result.native).toBe(true);
    expect(result.url).toBe('/files/folder/a.bin?download=1');
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
