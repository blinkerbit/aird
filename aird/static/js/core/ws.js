/**
 * Same-origin WebSocket session — one reconnect policy for live sockets.
 *
 * Call sites used to each invent a 3s timer, a 1008 (auth reject) halt, and
 * a slightly different "already open" guard. `createReconnectingSocket`
 * owns that. One-shot sockets (search, a single file transfer) keep using
 * `new WebSocket(sameOriginWsUrl(...))` directly.
 */

const DEFAULT_DELAY_MS = 3000;
const AUTH_CLOSE = 1008;

/** `wss:` or `ws:` URL on this host. `path` includes the leading slash and any query. */
export function sameOriginWsUrl(path) {
  const scheme = globalThis.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const suffix = String(path || '').startsWith('/') ? String(path) : `/${path || ''}`;
  return `${scheme}//${globalThis.location.host}${suffix}`;
}

/**
 * @param {object} options
 * @param {string|(() => string)} options.url
 * @param {(socket: WebSocket) => void} [options.onSocket] Fired as soon as the socket exists.
 * @param {(socket: WebSocket) => void} [options.onOpen]
 * @param {(event: MessageEvent) => void} [options.onMessage]
 * @param {(data: *, event: MessageEvent) => void} [options.onJson] Parsed text frames.
 * @param {(err: Error, event: MessageEvent) => void} [options.onJsonError]
 * @param {(event: Event) => void} [options.onError]
 * @param {(event: CloseEvent) => void} [options.onClose]
 * @param {(err: Error) => void} [options.onConstructError]
 * @param {(info: { event: CloseEvent, opened: boolean, gotPayload: boolean }) => boolean} [options.stopWhen]
 *   Extra halt besides close code 1008. Runtime config and feature flags use
 *   this for "opened, then kicked before any payload".
 * @param {number} [options.delayMs]
 */
export function createReconnectingSocket(options = {}) {
  const delayMs = options.delayMs ?? DEFAULT_DELAY_MS;
  let socket = null;
  let timer = null;
  let stopped = false;
  let generation = 0;

  function clearTimer() {
    if (timer != null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function connect() {
    if (stopped) return;
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
      return;
    }
    clearTimer();
    const attempt = ++generation;
    let opened = false;
    let gotPayload = false;
    let ws;
    try {
      const url = typeof options.url === 'function' ? options.url() : options.url;
      ws = new WebSocket(url);
    } catch (err) {
      options.onConstructError?.(err);
      return;
    }
    socket = ws;
    options.onSocket?.(ws);

    ws.onopen = () => {
      if (attempt !== generation) return;
      opened = true;
      options.onOpen?.(ws);
    };
    ws.onmessage = (event) => {
      if (attempt !== generation) return;
      gotPayload = true;
      if (!options.onJson) {
        options.onMessage?.(event);
        return;
      }
      try {
        options.onJson(JSON.parse(event.data), event);
      } catch (err) {
        if (options.onJsonError) options.onJsonError(err, event);
        else console.debug('Invalid WebSocket payload', err);
      }
    };
    ws.onerror = (event) => {
      options.onError?.(event);
    };
    ws.onclose = (event) => {
      if (socket === ws) socket = null;
      options.onClose?.(event);
      if (stopped || attempt !== generation) return;
      const authReject = Boolean(event && event.code === AUTH_CLOSE);
      const halt = authReject || Boolean(options.stopWhen?.({ event, opened, gotPayload }));
      if (halt) return;
      clearTimer();
      timer = setTimeout(connect, delayMs);
    };
  }

  function close(code, reason) {
    stopped = true;
    generation += 1;
    clearTimer();
    if (!socket) return;
    const current = socket;
    socket = null;
    current.close(code, reason);
  }

  function send(data) {
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(typeof data === 'string' ? data : JSON.stringify(data));
    return true;
  }

  return {
    connect,
    close,
    send,
    isOpen() {
      return Boolean(socket && socket.readyState === WebSocket.OPEN);
    },
    get socket() {
      return socket;
    },
  };
}
