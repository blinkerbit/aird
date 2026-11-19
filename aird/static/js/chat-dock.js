/**
 * Desktop Messenger-style chat dock — floating conversation windows bottom-right.
 * Not used on narrow viewports (<1024px).
 */
import { apiFetch } from './core/http.js';
import { escapeHtml } from './core/dom-safe.js';

const DESKTOP_MQ = '(min-width: 1024px)';
const MAX_WINDOWS = 3;
const STATE_KEY = 'aird.chat.dock';
const STYLE_VER = '1';

/** @typedef {{ id: string, title: string, collapsed: boolean }} DockWin */

/** @type {DockWin[]} */
let windows = [];
let listOpen = false;
let root = null;
let mq = null;
let bootOnce = false;
/** @type {Map<string, object[]>} */
const msgCache = new Map();
/** @type {Map<string, object>} */
const convCache = new Map();
/** @type {Set<string>} */
const loading = new Set();

export function canUseDock() {
  if (typeof window === 'undefined' || !window.matchMedia) return false;
  return window.matchMedia(DESKTOP_MQ).matches;
}

export function getOpenIds() {
  return windows.map((w) => w.id);
}

export function isOpen(convId) {
  return windows.some((w) => w.id === String(convId));
}

export function viewing(convId) {
  if (!convId || !canUseDock() || document.hidden) return false;
  const win = windows.find((w) => w.id === String(convId));
  return Boolean(win && !win.collapsed);
}

function meName() {
  return globalThis.__AIRD_CHAT_USER__ || '';
}

async function api(url, opts = {}) {
  const res = await apiFetch(url, {
    ...opts,
    headers: {
      Accept: 'application/json',
      ...(opts.body ? { 'Content-Type': 'application/json' } : {}),
      ...(opts.headers || {}),
    },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || 'Request failed');
  return data;
}

function readState() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(STATE_KEY) || 'null');
    if (!parsed || !Array.isArray(parsed.windows)) return;
    windows = parsed.windows
      .filter((w) => w && w.id)
      .slice(0, MAX_WINDOWS)
      .map((w) => ({
        id: String(w.id),
        title: String(w.title || 'Chat'),
        collapsed: Boolean(w.collapsed),
      }));
    listOpen = Boolean(parsed.listOpen);
  } catch { /* private mode */ }
}

function writeState() {
  try {
    sessionStorage.setItem(STATE_KEY, JSON.stringify({
      windows: windows.map((w) => ({ id: w.id, title: w.title, collapsed: w.collapsed })),
      listOpen,
    }));
  } catch { /* private mode */ }
}

function syncOpenChatFlag() {
  const onChat = location.pathname === '/chat' || location.pathname.startsWith('/chat/');
  if (!onChat && canUseDock()) {
    const expanded = windows.find((w) => !w.collapsed);
    if (expanded) globalThis.__AIRD_OPEN_CHAT__ = expanded.id;
  }
  document.dispatchEvent(new CustomEvent('aird-chat-view'));
}

function ensureStyles() {
  const existing = document.getElementById('airdChatDockStyles');
  if (existing?.dataset.version === STYLE_VER) return;
  existing?.remove();
  const style = document.createElement('style');
  style.id = 'airdChatDockStyles';
  style.dataset.version = STYLE_VER;
  style.textContent = `
    #airdChatDock{position:fixed;right:max(.75rem,env(safe-area-inset-right,0));bottom:max(.75rem,env(safe-area-inset-bottom,0));z-index:90;display:none;flex-direction:row-reverse;align-items:flex-end;gap:.55rem;pointer-events:none;max-width:calc(100vw - 1.5rem)}
    #airdChatDock.is-on{display:flex}
    #airdChatDock > *{pointer-events:auto}
    .chat-dock-launcher{display:flex;flex-direction:column;align-items:flex-end;gap:.4rem}
    .chat-dock-fab{appearance:none;border:0;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:.4rem;min-width:3rem;height:3rem;padding:0 .95rem;border-radius:999px;background:var(--color-primary,#3b82f6);color:var(--color-primary-content,#fff);font:inherit;font-weight:700;font-size:.85rem;box-shadow:0 10px 28px rgba(0,0,0,.22)}
    .chat-dock-fab svg{width:1.15rem;height:1.15rem}
    .chat-dock-list{width:min(20rem,calc(100vw - 2rem));max-height:min(24rem,60vh);overflow:auto;border-radius:1rem;background:var(--color-base-100,#fff);color:var(--color-base-content,#111);border:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 85%,var(--color-base-content,#111));box-shadow:0 16px 40px rgba(0,0,0,.18)}
    .chat-dock-list.hidden{display:none !important}
    .chat-dock-list-head{display:flex;align-items:center;justify-content:space-between;padding:.7rem .85rem;font-weight:750;border-bottom:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 80%,transparent)}
    .chat-dock-list-head a{font-size:.78rem;font-weight:650;opacity:.75;text-decoration:none;color:inherit}
    .chat-dock-list-body{padding:.35rem}
    .chat-dock-list-row{display:flex;align-items:center;gap:.55rem;width:100%;text-align:left;padding:.55rem .6rem;border:0;border-radius:.7rem;background:transparent;color:inherit;font:inherit;cursor:pointer}
    .chat-dock-list-row:hover{background:color-mix(in oklch,var(--color-base-200,#f4f4f5) 90%,transparent)}
    .chat-dock-list-row strong{display:block;font-size:.88rem;font-weight:700}
    .chat-dock-list-row span{display:block;font-size:.75rem;opacity:.65;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:14rem}
    .chat-dock-empty{padding:1rem;font-size:.85rem;opacity:.65;text-align:center}
    .chat-dock-window{width:min(20.5rem,calc(100vw - 2rem));height:min(28rem,70vh);display:flex;flex-direction:column;border-radius:1rem 1rem .35rem .35rem;overflow:hidden;background:var(--color-base-100,#fff);color:var(--color-base-content,#111);border:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 85%,var(--color-base-content,#111));box-shadow:0 18px 48px rgba(0,0,0,.22)}
    .chat-dock-window.is-collapsed{width:auto;height:auto;border-radius:999px;flex-direction:row;align-items:center}
    .chat-dock-window.is-collapsed .chat-dock-body,
    .chat-dock-window.is-collapsed .chat-dock-composer,
    .chat-dock-window.is-collapsed .chat-dock-head-actions .chat-dock-collapse{display:none !important}
    .chat-dock-window.is-collapsed .chat-dock-head{padding:.45rem .7rem;gap:.45rem;cursor:pointer;border:0}
    .chat-dock-window.is-collapsed .chat-dock-peer-title{max-width:9rem}
    .chat-dock-head{display:flex;align-items:center;gap:.5rem;padding:.55rem .65rem;background:color-mix(in oklch,var(--color-base-200,#f4f4f5) 88%,var(--color-primary,#3b82f6));border-bottom:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 70%,transparent);flex-shrink:0}
    .chat-dock-peer{min-width:0;flex:1}
    .chat-dock-peer-title{font-weight:750;font-size:.9rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    .chat-dock-head-actions{display:flex;gap:.2rem;flex-shrink:0}
    .chat-dock-icon{appearance:none;border:0;background:transparent;color:inherit;width:1.85rem;height:1.85rem;border-radius:.45rem;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;opacity:.8}
    .chat-dock-icon:hover{background:color-mix(in oklch,var(--color-base-content,#111) 8%,transparent);opacity:1}
    .chat-dock-icon svg{width:1rem;height:1rem}
    .chat-dock-body{flex:1;min-height:0;overflow:auto;padding:.65rem .7rem;display:flex;flex-direction:column;gap:.45rem;background:color-mix(in oklch,var(--color-base-200,#f4f4f5) 55%,var(--color-base-100,#fff))}
    .chat-dock-msg{max-width:88%;padding:.4rem .6rem;border-radius:.85rem;font-size:.86rem;line-height:1.35;word-break:break-word}
    .chat-dock-msg--mine{align-self:flex-end;background:var(--color-primary,#3b82f6);color:var(--color-primary-content,#fff);border-bottom-right-radius:.25rem}
    .chat-dock-msg--theirs{align-self:flex-start;background:var(--color-base-100,#fff);border:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 80%,transparent);border-bottom-left-radius:.25rem}
    .chat-dock-msg--meta{align-self:center;font-size:.72rem;opacity:.6;background:transparent;border:0;padding:.15rem}
    .chat-dock-msg a{color:inherit;text-decoration:underline}
    .chat-dock-composer{display:flex;gap:.4rem;align-items:flex-end;padding:.5rem .55rem;border-top:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 75%,transparent);background:var(--color-base-100,#fff);flex-shrink:0}
    .chat-dock-composer textarea{flex:1;min-height:2.35rem;max-height:6.5rem;resize:none;border:1px solid color-mix(in oklch,var(--color-base-300,#d4d4d8) 85%,transparent);border-radius:.75rem;padding:.45rem .6rem;font:inherit;font-size:.88rem;background:var(--color-base-100,#fff);color:inherit}
    .chat-dock-send{appearance:none;border:0;cursor:pointer;width:2.35rem;height:2.35rem;border-radius:999px;background:var(--color-primary,#3b82f6);color:var(--color-primary-content,#fff);display:inline-flex;align-items:center;justify-content:center;flex-shrink:0}
    .chat-dock-send svg{width:1.05rem;height:1.05rem}
    .chat-dock-send:disabled{opacity:.45;cursor:default}
    @media (max-width:1023px){#airdChatDock{display:none !important}}
  `;
  document.head.appendChild(style);
}

function ensureRoot() {
  if (root && document.body.contains(root)) return root;
  root = document.getElementById('airdChatDock');
  if (!root) {
    root = document.createElement('div');
    root.id = 'airdChatDock';
    root.setAttribute('data-aird-feature', 'direct_messages');
    root.setAttribute('aria-label', 'Chat dock');
    document.body.appendChild(root);
  }
  return root;
}

function convTitle(conv, fallback = 'Chat') {
  if (!conv) return fallback;
  if (conv.kind === 'group') return conv.title || 'Group';
  return conv.peer_display_name || conv.peer_username || fallback;
}

function memberNames(conv) {
  return (conv?.members || []).map((m) => m.username).filter(Boolean);
}

async function ensureE2e(convId) {
  const E2E = globalThis.AirdChatE2E;
  const me = meName();
  if (!E2E || !me) return null;
  if (!E2E.ready()) await E2E.init(me, api);
  const conv = convCache.get(convId);
  try {
    await E2E.ensureConversation(api, convId, memberNames(conv));
  } catch (err) {
    console.debug('dock e2e ensure', err);
  }
  return E2E;
}

async function decryptMsg(convId, msg) {
  const blob = msg.metadata?.e2e || msg.e2e;
  if (!blob) return { ...msg, _body: msg.body || '', _e2e: false, _ok: true };
  const E2E = await ensureE2e(convId);
  if (!E2E) {
    return { ...msg, _body: '<em>Encrypted message</em>', _e2e: true, _ok: false };
  }
  try {
    const plain = await E2E.decrypt(convId, blob);
    const html = E2E.sanitizeHtml ? E2E.sanitizeHtml(plain) : escapeHtml(plain);
    return { ...msg, _body: html, _e2e: true, _ok: true };
  } catch {
    return { ...msg, _body: '<em>Unable to decrypt</em>', _e2e: true, _ok: false };
  }
}

function paintMessages(convId) {
  const body = root?.querySelector(`.chat-dock-window[data-conv="${CSS.escape(convId)}"] .chat-dock-body`);
  if (!body) return;
  const msgs = msgCache.get(convId) || [];
  const me = meName();
  if (!msgs.length) {
    body.innerHTML = '<div class="chat-dock-msg chat-dock-msg--meta">No messages yet</div>';
    return;
  }
  body.innerHTML = msgs.map((m) => {
    const mine = String(m.sender_username || '') === me;
    const cls = mine ? 'chat-dock-msg--mine' : 'chat-dock-msg--theirs';
    const att = Array.isArray(m.metadata?.attachments) && m.metadata.attachments.length
      ? `<div><em>${m.metadata.attachments.length} attachment(s)</em></div>`
      : '';
    return `<div class="chat-dock-msg ${cls}">${m._body || ''}${att}</div>`;
  }).join('');
  body.scrollTop = body.scrollHeight;
}

function markRead(convId, msg) {
  if (!msg?.id || document.visibilityState !== 'visible') return;
  if (msg._e2e && !msg._ok) return;
  globalThis.AirdChatWS?.send({
    type: 'chat_read',
    conversation_id: convId,
    message_id: msg.id,
    decrypted: !!(msg._e2e && msg._ok),
  });
}

async function loadThread(convId) {
  if (loading.has(convId)) return;
  loading.add(convId);
  try {
    let conv = convCache.get(convId);
    if (!conv) {
      const all = await api('/api/chat/conversations');
      (all.conversations || []).forEach((c) => convCache.set(c.id, c));
      conv = convCache.get(convId);
      const win = windows.find((w) => w.id === convId);
      if (win && conv) {
        win.title = convTitle(conv, win.title);
        writeState();
      }
    }
    await ensureE2e(convId);
    const data = await api(`/api/chat/conversations/${encodeURIComponent(convId)}/messages?limit=40`);
    const prepared = [];
    for (const m of (data.messages || [])) {
      prepared.push(await decryptMsg(convId, m));
    }
    msgCache.set(convId, prepared);
    paintMessages(convId);
    const last = [...prepared].reverse().find((m) => !m._e2e || m._ok);
    if (last) markRead(convId, last);
    render();
  } catch (err) {
    const body = root?.querySelector(`.chat-dock-window[data-conv="${CSS.escape(convId)}"] .chat-dock-body`);
    if (body) body.innerHTML = `<div class="chat-dock-msg chat-dock-msg--meta">${escapeHtml(err.message || 'Could not load')}</div>`;
  } finally {
    loading.delete(convId);
  }
}

async function sendText(convId, text) {
  const body = String(text || '').trim();
  if (!body) return;
  const E2E = await ensureE2e(convId);
  if (!E2E?.ready()) throw new Error('Waiting for the chat key…');
  let e2e;
  try {
    e2e = await E2E.encrypt(convId, body);
  } catch {
    await ensureE2e(convId);
    e2e = await E2E.encrypt(convId, body);
  }
  const conv = convCache.get(convId);
  const mentions = E2E.extractMentions
    ? E2E.extractMentions(body, memberNames(conv).filter((n) => n !== meName()))
    : [];
  const payload = {
    type: 'chat_send',
    conversation_id: convId,
    e2e,
    mentions,
  };
  const WS = globalThis.AirdChatWS;
  if (!(WS?.isOpen() && WS.send(payload))) {
    await api(`/api/chat/conversations/${encodeURIComponent(convId)}/messages`, {
      method: 'POST',
      body: JSON.stringify({ type: 'text', e2e, mentions }),
    });
    await loadThread(convId);
  }
}

async function loadList() {
  const data = await api('/api/chat/conversations');
  const list = data.conversations || [];
  list.forEach((c) => convCache.set(c.id, c));
  return list;
}

function windowMarkup(win) {
  const collapsed = win.collapsed ? ' is-collapsed' : '';
  const title = escapeHtml(win.title || 'Chat');
  return `<section class="chat-dock-window${collapsed}" data-conv="${escapeHtml(win.id)}" data-collapsed="${win.collapsed ? '1' : '0'}">
    <header class="chat-dock-head">
      <div class="chat-dock-peer"><div class="chat-dock-peer-title">${title}</div></div>
      <div class="chat-dock-head-actions">
        <a class="chat-dock-icon" href="/chat?c=${encodeURIComponent(win.id)}" title="Open full chat" aria-label="Open full chat">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
        </a>
        <button type="button" class="chat-dock-icon chat-dock-collapse" data-act="collapse" title="Minimize" aria-label="Minimize">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M5 12h14"/></svg>
        </button>
        <button type="button" class="chat-dock-icon" data-act="close" title="Close" aria-label="Close">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>
        </button>
      </div>
    </header>
    <div class="chat-dock-body" role="log" aria-live="polite"></div>
    <form class="chat-dock-composer">
      <textarea rows="1" placeholder="Message…" aria-label="Message"></textarea>
      <button type="submit" class="chat-dock-send" aria-label="Send">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4z"/><path d="M22 2 11 13"/></svg>
      </button>
    </form>
  </section>`;
}

function launcherMarkup() {
  return `<div class="chat-dock-launcher">
    <div class="chat-dock-list${listOpen ? '' : ' hidden'}" id="airdChatDockList">
      <div class="chat-dock-list-head"><span>Messages</span><a href="/chat">Open Chat</a></div>
      <div class="chat-dock-list-body" id="airdChatDockListBody"><div class="chat-dock-empty">Loading…</div></div>
    </div>
    <button type="button" class="chat-dock-fab" id="airdChatDockFab" title="Messages" aria-label="Messages" aria-expanded="${listOpen ? 'true' : 'false'}">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>
    </button>
  </div>`;
}

function wireWindow(el, win) {
  const head = el.querySelector('.chat-dock-head');
  head?.addEventListener('click', (event) => {
    if (event.target.closest('a, button')) return;
    if (win.collapsed) {
      win.collapsed = false;
      writeState();
      render();
      void loadThread(win.id);
    }
  });
  el.querySelector('[data-act="collapse"]')?.addEventListener('click', () => {
    win.collapsed = true;
    writeState();
    render();
    syncOpenChatFlag();
  });
  el.querySelector('[data-act="close"]')?.addEventListener('click', () => {
    closeDockWindow(win.id);
  });
  const form = el.querySelector('.chat-dock-composer');
  const ta = form?.querySelector('textarea');
  ta?.addEventListener('input', () => {
    ta.style.height = 'auto';
    ta.style.height = `${Math.min(ta.scrollHeight, 104)}px`;
  });
  ta?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      form?.requestSubmit();
    }
  });
  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    const text = ta?.value || '';
    if (!text.trim()) return;
    const sendBtn = form.querySelector('.chat-dock-send');
    if (sendBtn) sendBtn.disabled = true;
    sendText(win.id, text)
      .then(() => {
        if (ta) {
          ta.value = '';
          ta.style.height = 'auto';
        }
        const optimistic = {
          id: `local-${Date.now()}`,
          sender_username: meName(),
          _body: escapeHtml(text.trim()).replaceAll('\n', '<br>'),
          _e2e: true,
          _ok: true,
        };
        const cur = msgCache.get(win.id) || [];
        msgCache.set(win.id, [...cur, optimistic]);
        paintMessages(win.id);
      })
      .catch((err) => {
        const body = el.querySelector('.chat-dock-body');
        if (body) {
          const note = document.createElement('div');
          note.className = 'chat-dock-msg chat-dock-msg--meta';
          note.textContent = err.message || 'Send failed';
          body.appendChild(note);
        }
      })
      .finally(() => {
        if (sendBtn) sendBtn.disabled = false;
        ta?.focus();
      });
  });
  if (!win.collapsed) {
    paintMessages(win.id);
    if (!msgCache.has(win.id)) void loadThread(win.id);
  }
}

async function fillList() {
  const body = document.getElementById('airdChatDockListBody');
  if (!body) return;
  try {
    const list = await loadList();
    if (!list.length) {
      body.innerHTML = '<div class="chat-dock-empty">No conversations yet</div>';
      return;
    }
    body.innerHTML = list.slice(0, 40).map((c) => {
      const title = escapeHtml(convTitle(c));
      const preview = escapeHtml(String(c.preview || c.last_preview || '').slice(0, 72));
      return `<button type="button" class="chat-dock-list-row" data-open-conv="${escapeHtml(c.id)}" data-title="${title}">
        <div><strong>${title}</strong><span>${preview || 'Open conversation'}</span></div>
      </button>`;
    }).join('');
    body.querySelectorAll('[data-open-conv]').forEach((btn) => {
      btn.addEventListener('click', () => {
        listOpen = false;
        void open(btn.getAttribute('data-open-conv'), {
          title: btn.getAttribute('data-title') || 'Chat',
        });
      });
    });
  } catch (err) {
    body.innerHTML = `<div class="chat-dock-empty">${escapeHtml(err.message || 'Could not load')}</div>`;
  }
}

function render() {
  ensureStyles();
  const host = ensureRoot();
  const on = canUseDock();
  host.classList.toggle('is-on', on);
  if (!on) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = `${windows.map(windowMarkup).join('')}${launcherMarkup()}`;
  windows.forEach((win) => {
    const el = host.querySelector(`.chat-dock-window[data-conv="${CSS.escape(win.id)}"]`);
    if (el) wireWindow(el, win);
  });
  document.getElementById('airdChatDockFab')?.addEventListener('click', () => {
    listOpen = !listOpen;
    writeState();
    render();
    if (listOpen) void fillList();
  });
  if (listOpen) void fillList();
  syncOpenChatFlag();
}

/**
 * Open (or focus) a docked conversation window.
 * @param {string} convId
 * @param {{ title?: string, collapsed?: boolean }} [opts]
 */
export function open(convId, opts = {}) {
  const id = String(convId || '').trim();
  if (!id || !canUseDock()) return false;

  // On full chat page, prefer the SPA thread when available.
  if (
    (location.pathname === '/chat' || location.pathname.startsWith('/chat/'))
    && typeof globalThis.__AIRD_CHAT_GOTO__ === 'function'
  ) {
    globalThis.__AIRD_CHAT_GOTO__(id);
    return true;
  }

  let win = windows.find((w) => w.id === id);
  if (win) {
    win.collapsed = Boolean(opts.collapsed);
    if (opts.title) win.title = opts.title;
  } else {
    windows.push({
      id,
      title: opts.title || 'Chat',
      collapsed: Boolean(opts.collapsed),
    });
    while (windows.length > MAX_WINDOWS) {
      const dropped = windows.shift();
      if (dropped) msgCache.delete(dropped.id);
    }
  }
  // Only one expanded at a time keeps the desk usable (Messenger-like).
  if (!opts.collapsed) {
    windows.forEach((w) => {
      if (w.id !== id) w.collapsed = true;
    });
  }
  listOpen = false;
  writeState();
  render();
  if (!opts.collapsed) void loadThread(id);
  return true;
}

export function closeDockWindow(convId) {
  const id = String(convId || '');
  windows = windows.filter((w) => w.id !== id);
  msgCache.delete(id);
  writeState();
  render();
  syncOpenChatFlag();
}

/** @deprecated use closeDockWindow */
export function close(convId) {
  return closeDockWindow(convId);
}

export function collapse(convId) {
  const win = windows.find((w) => w.id === String(convId));
  if (!win) return;
  win.collapsed = true;
  writeState();
  render();
  syncOpenChatFlag();
}

export function getPeerTitle(convId) {
  const id = String(convId || '');
  const win = windows.find((w) => w.id === id);
  if (win?.title) return win.title;
  return convTitle(convCache.get(id), '');
}

function onWs(data) {
  if (!data?.type || !canUseDock()) return;
  if (data.type === 'chat_message' && data.message?.conversation_id) {
    const cid = data.message.conversation_id;
    if (!isOpen(cid)) return;
    void (async () => {
      const prepared = await decryptMsg(cid, data.message);
      const cur = msgCache.get(cid) || [];
      if (cur.some((m) => m.id === prepared.id)) return;
      // Drop optimistic local twin
      const next = cur.filter((m) => !String(m.id || '').startsWith('local-'));
      next.push(prepared);
      msgCache.set(cid, next);
      paintMessages(cid);
      const win = windows.find((w) => w.id === cid);
      if (win && !win.collapsed) markRead(cid, prepared);
    })();
  } else if (data.type === 'chat_message_deleted' && data.conversation_id && isOpen(data.conversation_id)) {
    void loadThread(data.conversation_id);
  }
}

function onMqChange() {
  if (!canUseDock()) {
    listOpen = false;
  }
  render();
  if (canUseDock() && windows.length) {
    windows.filter((w) => !w.collapsed).forEach((w) => void loadThread(w.id));
  }
}

export function boot() {
  if (bootOnce) return;
  bootOnce = true;
  ensureStyles();
  readState();
  mq = window.matchMedia(DESKTOP_MQ);
  mq.addEventListener?.('change', onMqChange);
  ensureRoot();
  render();
  globalThis.AirdChatWS?.subscribe?.(onWs);
  const me = meName();
  if (me && globalThis.AirdChatE2E && !globalThis.AirdChatE2E.ready()) {
    void globalThis.AirdChatE2E.init(me, api).catch((err) => console.debug('dock e2e init', err));
  }
  windows.filter((w) => !w.collapsed).forEach((w) => void loadThread(w.id));
}

const apiSurface = {
  canUseDock,
  open,
  close: closeDockWindow,
  closeDockWindow,
  collapse,
  viewing,
  isOpen,
  getOpenIds,
  getPeerTitle,
  boot,
};

globalThis.AirdChatDock = apiSurface;

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

export default apiSurface;
