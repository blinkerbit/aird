import { afterEach, describe, expect, it, vi } from 'vitest';
import { createReconnectingSocket, sameOriginWsUrl } from '/static/js/core/ws.js';
import { saveBlob } from '/static/js/core/download.js';

class FakeSocket {
  static OPEN = 1;
  static CONNECTING = 0;
  static CLOSED = 3;

  constructor(url) {
    this.url = url;
    this.readyState = FakeSocket.CONNECTING;
    FakeSocket.instances.push(this);
  }

  close() {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code: 1000 });
  }

  send(data) {
    this.sent = data;
  }
}

describe('sameOriginWsUrl', () => {
  it('uses the page scheme and host', () => {
    const expected = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/features`;
    expect(sameOriginWsUrl('/features')).toBe(expected);
    expect(sameOriginWsUrl('features')).toBe(expected);
  });
});

describe('createReconnectingSocket', () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('reconnects after an abnormal close and stops on auth reject', () => {
    vi.useFakeTimers();
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    const live = createReconnectingSocket({
      url: '/features',
      delayMs: 3000,
    });
    live.connect();
    expect(FakeSocket.instances).toHaveLength(1);

    FakeSocket.instances[0].onclose({ code: 1006 });
    vi.advanceTimersByTime(2999);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);

    FakeSocket.instances[1].onclose({ code: 1008 });
    vi.advanceTimersByTime(3000);
    expect(FakeSocket.instances).toHaveLength(2);
  });

  it('parses JSON frames and sends objects', () => {
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    const frames = [];
    const live = createReconnectingSocket({
      url: () => '/ws/chat',
      onJson(data) { frames.push(data); },
    });
    live.connect();
    const socket = FakeSocket.instances[0];
    socket.readyState = FakeSocket.OPEN;
    socket.onmessage({ data: '{"type":"ping"}' });
    expect(frames).toEqual([{ type: 'ping' }]);
    expect(live.send({ type: 'pong' })).toBe(true);
    expect(socket.sent).toBe('{"type":"pong"}');
  });

  it('does not reconnect when stopWhen matches', () => {
    vi.useFakeTimers();
    FakeSocket.instances = [];
    vi.stubGlobal('WebSocket', FakeSocket);
    const live = createReconnectingSocket({
      url: '/runtime-config',
      stopWhen({ opened, gotPayload }) { return opened && !gotPayload; },
    });
    live.connect();
    const socket = FakeSocket.instances[0];
    socket.onopen();
    socket.onclose({ code: 1006 });
    vi.advanceTimersByTime(3000);
    expect(FakeSocket.instances).toHaveLength(1);
  });
});

describe('saveBlob', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('clicks a hidden download link when the file picker is absent', async () => {
    vi.useFakeTimers();
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:1');
    vi.stubGlobal('showSaveFilePicker', undefined);
    const realCreate = document.createElement.bind(document);
    let link = null;
    vi.spyOn(document, 'createElement').mockImplementation((tag) => {
      const el = realCreate(tag);
      if (tag === 'a') {
        link = el;
        el.click = () => {};
      }
      return el;
    });
    await saveBlob(new Blob(['hi']), 'note.txt');
    expect(link.download).toBe('note.txt');
    expect(link.rel).toBe('noopener');
    vi.advanceTimersByTime(60000);
    expect(revoke).toHaveBeenCalledWith('blob:1');
    vi.useRealTimers();
  });
});
