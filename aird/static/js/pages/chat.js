import {
  attachAllowed,
  bindDropZone,
  clipboardFiles,
  completePath,
  createBrowsePicker,
  createPendingStore,
  filesApiUrl,
  formatRelPath,
  joinRel,
  looksLikePath,
  normalizeRel,
  parsePathInput,
  restyleCompleted,
  sameBrowseValue,
  takePendingBrowseAttachAll,
} from '../chat-attach.js?v=20260930h';

(function () {
  'use strict';

  const REACTIONS = ['👍', '❤️', '😂', '🎉'];
  const IMAGE_EXTS = ['jpg', 'jpeg', 'jpe', 'jif', 'jfif', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif', 'svg', 'ico', 'avif', 'heic', 'heif', 'dng', 'cr2', 'nef', 'arw', 'orf', 'rw2', 'raf', 'raw'];
  let pendingBrowseAttachQueue = takePendingBrowseAttachAll();

  const qs = (sel) => document.querySelector(sel);
  const convList = qs('#chatConvList');
  const messagesEl = qs('#chatMessages');
  const composer = qs('#chatComposer');
  const editor = qs('#chatEditor');
  const header = qs('#chatThreadHeader');
  const pinBar = qs('#chatPinBar');
  const startBtn = qs('#chatStartBtn');
  const newUserInput = qs('#chatNewUser');
  const userSuggest = qs('#chatUserSuggest');
  const fileInput = qs('#chatFileInput');
  const gifInput = qs('#chatGifInput');
  const browseModal = qs('#chatBrowseModal');
  const browseList = qs('#chatBrowseList');
  const browsePathEl = qs('#chatBrowsePath');
  const browseUp = qs('#chatBrowseUp');
  const browseClose = qs('#chatBrowseClose');
  const browseAttach = qs('#chatBrowseAttach');
  const browseBtn = qs('#chatBrowseBtn');
  const replyBar = qs('#chatReplyBar');
  const replyPreview = qs('#chatReplyPreview');
  const searchInput = qs('#chatSearch');
  const searchResults = qs('#chatSearchResults');
  const mentionSuggest = qs('#chatMentionSuggest');
  const layoutEl = qs('#chatLayout');
  const newBtn = qs('#chatNewBtn');
  const newPanel = qs('#chatNewPanel');
  const groupFields = qs('#chatNewGroupFields');
  const groupChipsEl = qs('#chatGroupChips');
  const groupCreateBtn = qs('#chatGroupCreate');
  const memberModal = qs('#chatMemberModal');
  const memberUserInput = qs('#chatMemberUser');
  const memberSuggest = qs('#chatMemberSuggest');

  function getPathSuggest() {
    let el = document.getElementById('chatPathSuggest');
    if (el) return el;
    if (!editor) return null;
    let wrap = editor.closest('.chat-editor-wrap');
    if (!wrap) {
      wrap = document.createElement('div');
      wrap.className = 'chat-editor-wrap';
      editor.parentNode.insertBefore(wrap, editor);
      wrap.appendChild(editor);
    }
    el = document.createElement('div');
    el.id = 'chatPathSuggest';
    el.className = 'chat-path-suggest';
    el.hidden = true;
    wrap.insertBefore(el, editor);
    return el;
  }

  const me = window.__AIRD_CHAT_USER__ || '';
  let activeConvId = null;
  let enhancedSecureView = false;
  let conversations = [];
  let pins = [];
  let receipts = [];
  let replyToId = null;
  let tabCycle = { key: '', i: -1 };
  let pathSuggestHits = [];
  let pathSuggestIdx = 0;
  const pendingStore = createPendingStore();
  const pending = pendingStore.items;
  let userSearchTimer = null;
  let searchTimer = null;
  let pollTimer = null;
  let loadGen = 0;
  let typingTimer = null;
  let forwardMsgId = null;
  let loadingOlder = false;
  let hasOlder = false;
  let newMode = 'dm';
  let groupPicks = [];
  let keyWaitTimer = null;
  let keyWaitConv = '';
  const presence = {};

  function lastMessageId() {
    const nodes = messagesEl?.querySelectorAll('[data-msg-id]');
    if (!nodes?.length) return '';
    return nodes[nodes.length - 1].dataset.msgId || '';
  }

  function firstMessageId() {
    return messagesEl?.querySelector('[data-msg-id]')?.dataset.msgId || '';
  }

  async function syncNewMessages() {
    if (enhancedSecureView || !activeConvId || !messagesEl) return;
    const afterId = lastMessageId();
    const query = afterId ? `?after_id=${encodeURIComponent(afterId)}` : '';
    const data = await api(`/api/chat/conversations/${activeConvId}/messages${query}`);
    let added = false;
    let lastReadable = null;
    for (const m of (data.messages || [])) {
      if (!messagesEl.querySelector(`[data-msg-id="${cssEscape(m.id)}"]`)) {
        await appendMessage(m);
        added = true;
        if (!m._e2e || m._decryptOk) lastReadable = m;
      }
    }
    if (data.pins) {
      pins = data.pins;
      renderPins();
    }
    if (data.receipts) receipts = data.receipts;
    if (added) {
      if (lastReadable) markRead(lastReadable);
    }
  }

  function cssEscape(s) {
    return String(s).replaceAll('\\', '\\\\').replaceAll('"', '\\"');
  }

  function setPollEnabled(on) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (!on || !activeConvId) return;
    pollTimer = setInterval(() => {
      if (window.AirdChatWS?.isOpen()) return;
      syncNewMessages().catch((err) => { console.debug('chat poll sync failed', err); });
    }, 4000);
  }

  const XSRF_RE = /(?:^|;\s*)_xsrf=([^;]+)/;

  function xsrf() {
    const m = XSRF_RE.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  function apiUrl(path, opts = {}) {
    const method = String(opts.method || 'GET').toUpperCase();
    if (method !== 'GET' || /[?&]_=/.test(path)) return path;
    return `${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`;
  }

  async function api(path, opts = {}) {
    const { _retry304, ...fetchOpts } = opts;
    const res = await fetch(apiUrl(path, fetchOpts), {
      cache: 'no-store',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
        Pragma: 'no-cache',
        ...(fetchOpts.body && !(fetchOpts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(fetchOpts.method && fetchOpts.method !== 'GET' ? { 'X-XSRFToken': xsrf() } : {}),
      },
      ...fetchOpts,
    });
    if (res.status === 304) {
      if (_retry304) throw new Error('HTTP 304');
      return api(path, { ...opts, _retry304: true });
    }
    const text = await res.text();
    let json = {};
    try { json = JSON.parse(text); } catch { json = { error: text }; }
    if (!res.ok) throw new Error(json.error || text || `HTTP ${res.status}`);
    return json;
  }

  const E2E = window.AirdChatE2E;
  const e2ePlain = new Map();

  function memberAccepted(member) {
    return member?.accepted !== 0 && member?.accepted !== false;
  }

  function memberNames(conv) {
    return (conv?.members || []).filter(memberAccepted).map((m) => m.username).filter(Boolean);
  }

  function hideRequest() {
    qs('#chatRequest')?.classList.add('hidden');
  }

  function showRequest(conv) {
    const box = qs('#chatRequest');
    const text = qs('#chatRequestText');
    const actions = qs('#chatRequestActions');
    if (!box || !conv?.request) {
      hideRequest();
      return false;
    }
    box.classList.remove('hidden');
    composer?.classList.add('hidden');
    setE2eStatus('');
    stopKeyWait();
    if (conv.request === 'incoming') {
      text.textContent = conv.kind === 'dm'
        ? `${conv.peer_username || 'Someone'} wants to chat.`
        : `You were added to ${convTitle(conv)}.`;
      actions?.classList.remove('hidden');
    } else {
      text.textContent = `Waiting for ${conv.peer_username || 'them'} to accept.`;
      actions?.classList.add('hidden');
    }
    return true;
  }

  async function e2eReady(convId) {
    if (!E2E?.ready()) return false;
    try {
      const conv = conversations.find((c) => c.id === convId);
      const key = await E2E.ensureConversation(api, convId, memberNames(conv));
      if (key) {
        stopKeyWait();
        setE2eStatus('');
        return true;
      }
    } catch (err) {
      console.debug('e2e ready failed', err);
    }
    E2E.requestKeyShare?.(convId);
    startKeyWait(convId);
    setE2eStatus('Waiting for the chat key…');
    return false;
  }

  function setE2eStatus(text) {
    const el = qs('#chatE2eStatus');
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = text;
  }

  function stopKeyWait() {
    if (keyWaitTimer) {
      clearInterval(keyWaitTimer);
      keyWaitTimer = null;
    }
    keyWaitConv = '';
  }

  function startKeyWait(convId) {
    if (keyWaitConv === convId && keyWaitTimer) return;
    stopKeyWait();
    keyWaitConv = convId;
    keyWaitTimer = setInterval(() => {
      if (convId !== activeConvId) {
        stopKeyWait();
        return;
      }
      E2E.requestKeyShare?.(convId);
      e2eReady(convId).then((ok) => {
        if (ok) reloadThreadAfterKey(convId);
      }).catch((err) => console.debug('e2e wait retry failed', err));
    }, 2500);
  }

  async function reloadThreadAfterKey(convId) {
    if (convId !== activeConvId) return;
    setE2eStatus('');
    await loadConversations();
    await openConversation(convId);
  }

  async function decryptPayload(convId, payload) {
    if (!payload || !E2E) return { html: '', decrypted: false };
    const tryDecrypt = async () => E2E.sanitizeHtml(await E2E.decrypt(convId, payload));
    try {
      return { html: await tryDecrypt(), decrypted: true };
    } catch (_) {
      try {
        const conv = conversations.find((c) => c.id === convId);
        await E2E.ensureConversation(api, convId, memberNames(conv));
        return { html: await tryDecrypt(), decrypted: true };
      } catch {
        return {
          html: '<em class="chat-e2e-fail">Unable to decrypt on this device</em>',
          decrypted: false,
        };
      }
    }
  }

  async function prepareMessage(msg) {
    if (!msg) return msg;
    const blob = msg.metadata?.e2e;
    if (msg.e2e || blob) {
      const result = await decryptPayload(msg.conversation_id, blob);
      msg.body = result.html;
      msg._e2e = true;
      msg._decryptOk = result.decrypted;
      if (result.decrypted) e2ePlain.set(msg.id, result.html);
    }
    return msg;
  }

  function extractMentions(html, conv) {
    const names = memberNames(conv).filter((n) => n !== me);
    if (E2E?.extractMentions) return E2E.extractMentions(html, names);
    return [];
  }

  function esc(s) {
    return String(s ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
  }

  function avatarHue(name) {
    let h = 0;
    const s = String(name || '');
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.codePointAt(i)) >>> 0;
    return [250, 280, 200, 160, 30, 340, 220][h % 7];
  }

  function initials(name) {
    const s = String(name || '?').trim();
    if (!s) return '?';
    return s.slice(0, 2).toUpperCase();
  }

  function avatarHtml(name, extraClass) {
    const hue = avatarHue(name);
    const cls = extraClass ? ` ${extraClass}` : '';
    return `<span class="chat-avatar${cls}" style="--chat-hue:${hue}" aria-hidden="true">${esc(initials(name))}</span>`;
  }

  function formatTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const time = d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    if (sameDay) return time;
    return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} · ${time}`;
  }

  function formatConvTime(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const now = new Date();
    if (d.toDateString() === now.toDateString()) {
      return d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    }
    if ((now - d) / 86400000 < 7) return d.toLocaleDateString([], { weekday: 'short' });
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
  }

  function dayKey(iso) {
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : d.toDateString();
  }

  function dateLabel(iso) {
    const key = dayKey(iso);
    if (!key) return '';
    const now = new Date();
    if (key === now.toDateString()) return 'Today';
    const y = new Date(now);
    y.setDate(now.getDate() - 1);
    if (key === y.toDateString()) return 'Yesterday';
    return new Date(iso).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  }

  function dateChipHtml(iso) {
    const key = dayKey(iso);
    if (!key) return '';
    return `<div class="chat-day" data-day="${esc(key)}">${esc(dateLabel(iso))}</div>`;
  }

  function lastDayKey() {
    const days = messagesEl?.querySelectorAll('.chat-day');
    if (!days?.length) return '';
    return days[days.length - 1].dataset.day || '';
  }

  function setMobileView(view) {
    if (!layoutEl) return;
    layoutEl.classList.toggle('chat-layout--thread', view === 'thread');
    layoutEl.classList.toggle('chat-layout--list', view !== 'thread');
  }

  function tagMine(msg) {
    msg._mine = msg.sender_username === me;
    return msg;
  }

  function convTitle(c) {
    if (!c) return '';
    if (c.kind === 'group') return c.title || 'Group';
    return c.peer_username || 'Chat';
  }

  function isOnlineUser(username) {
    if (Object.prototype.hasOwnProperty.call(presence, username)) return presence[username];
    const conv = conversations.find((c) => (c.members || []).some((m) => m.username === username && m.online));
    return !!conv?.members?.some((m) => m.username === username && m.online);
  }

  function threadSubtitle(conv) {
    const mutedSuffix = conv.muted ? ' · muted' : '';
    if (conv.kind === 'group') {
      const n = (conv.members || []).length;
      const on = (conv.members || []).filter((m) => m.online || isOnlineUser(m.username)).length;
      return `${n} members · ${on} online${mutedSuffix}`;
    }
    const on = isOnlineUser(conv.peer_username);
    return `${on ? 'Online' : 'Direct message'}${mutedSuffix}`;
  }

  function setThreadHeader(conv) {
    if (!header) return;
    if (!conv) {
      header.innerHTML = '<div class="chat-empty chat-empty--head"><p>Select a conversation</p></div>';
      return;
    }
    const title = convTitle(conv);
    const sub = threadSubtitle(conv);
    const dot = conv.kind === 'dm' && isOnlineUser(conv.peer_username)
      ? '<span class="chat-online-dot" title="Online"></span>'
      : '';
    const lock = '<span class="chat-e2e-lock" title="End-to-end encrypted">🔒</span>';
    const groupActs = conv.kind === 'group'
      ? '<button type="button" id="chatAddMemberBtn">Add member</button><button type="button" id="chatLeaveBtn" class="is-danger">Leave group</button>'
      : '';
    header.innerHTML = `<button type="button" class="chat-back" id="chatBackBtn" aria-label="Back to conversations"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg></button><span class="chat-avatar-wrap">${avatarHtml(title)}${dot}</span><div class="chat-thread-head-main"><div class="chat-thread-title">${esc(title)} ${lock}</div><div class="chat-thread-sub">${esc(sub)}</div></div>
      <div class="chat-thread-actions">
        <details class="chat-head-more">
          <summary aria-label="Conversation actions">⋯</summary>
          <div class="chat-head-menu">
            <button type="button" id="chatMuteBtn">${conv.muted ? 'Unmute' : 'Mute'}</button>
            ${conv.kind === 'dm' && conv.peer_username ? '<button type="button" id="chatEnhancedSecureBtn">Enhanced Secure Chat</button>' : ''}
            ${groupActs}
          </div>
        </details>
      </div>`;
    qs('#chatBackBtn')?.addEventListener('click', () => setMobileView('list'));
    qs('#chatMuteBtn')?.addEventListener('click', () => toggleMute(conv).catch((e) => alert(e.message)));
    qs('#chatEnhancedSecureBtn')?.addEventListener('click', () => {
      qs('.chat-head-more')?.removeAttribute('open');
      window.AirdEnhancedSecure?.start(conv.peer_username);
    });
    qs('#chatAddMemberBtn')?.addEventListener('click', () => openAddMember());
    qs('#chatLeaveBtn')?.addEventListener('click', () => leaveGroup(conv).catch((e) => alert(e.message)));
  }

  async function toggleMute(conv) {
    const next = !conv.muted;
    await api(`/api/chat/conversations/${conv.id}/mute`, {
      method: 'POST',
      body: JSON.stringify({ muted: next }),
    });
    conv.muted = next;
    setThreadHeader(conv);
    renderConvList();
  }

  function openAddMember() {
    qs('.chat-head-more')?.removeAttribute('open');
    if (memberUserInput) memberUserInput.value = '';
    memberSuggest?.classList.add('hidden');
    memberModal?.showModal();
    memberUserInput?.focus();
  }

  async function addMember(conv, username) {
    conv = conv || activeConv();
    const name = (username || memberUserInput?.value || '').trim();
    if (!conv || !name) return;
    await api(`/api/chat/conversations/${conv.id}/members`, {
      method: 'POST',
      body: JSON.stringify({ username: name }),
    });
    memberModal?.close();
    await loadConversations();
    const updated = conversations.find((c) => c.id === conv.id);
    setThreadHeader(updated || conv);
    try { await e2eReady(conv.id); } catch (err) { console.debug('e2e ready after addMember failed', err); }
  }

  async function leaveGroup(conv) {
    if (!confirm('Leave this group?')) return;
    await api(`/api/chat/conversations/${conv.id}/members?username=${encodeURIComponent(me)}`, { method: 'DELETE' });
    activeConvId = null;
    composer?.classList.add('hidden');
    setThreadHeader(null);
    clearMessagesView();
    showEmptyThread();
    setMobileView('list');
    history.replaceState(null, '', location.pathname);
    await loadConversations();
  }

  function showEmptyThread() {
    if (!messagesEl) return;
    messagesEl.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon" aria-hidden="true"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div><p>No messages yet</p><p class="chat-empty-hint">Say hello.</p></div>';
  }

  function clearMessagesView() {
    if (!messagesEl) return;
    messagesEl.innerHTML = '';
  }

  function renderConvList() {
    if (!convList) return;
    const enhancedHtml = window.AirdEnhancedSecure?.listHtml?.() || '';
    if (!conversations.length && !enhancedHtml) {
      convList.innerHTML = '<li class="p-3 text-sm opacity-50 text-center">No conversations yet</li>';
      return;
    }
    convList.innerHTML = enhancedHtml + conversations.map((c) => {
      const active = !enhancedSecureView && c.id === activeConvId ? ' active' : '';
      const badge = c.unread ? `<span class="badge badge-primary badge-xs">${c.unread}</span>` : '';
      const preview = c.request === 'incoming'
        ? 'Chat request'
        : c.request === 'outgoing'
          ? 'Waiting for acceptance'
          : esc(c.last_preview || 'No messages yet');
      const title = convTitle(c);
      const on = c.kind === 'dm' && isOnlineUser(c.peer_username);
      const dot = on ? '<span class="chat-online-dot chat-online-dot--list"></span>' : '';
      const mute = c.muted ? ' <span class="opacity-40">🔇</span>' : '';
      const when = formatConvTime(c.updated_at);
      const more = c.kind === 'dm' && c.peer_username
        ? `<details class="chat-conv-more"><summary aria-label="More">⋯</summary><div class="chat-head-menu"><button type="button" data-enhanced-peer="${esc(c.peer_username)}">Enhanced Secure Chat</button></div></details>`
        : '';
      return `<li class="chat-conv-row"><a href="#" class="chat-conv-item${active}" data-id="${esc(c.id)}">${avatarHtml(title)}${dot}<span class="chat-conv-main"><span class="chat-conv-top"><span class="chat-conv-name">${esc(title)}${mute}</span><span class="chat-conv-meta">${when}${badge}</span></span><span class="chat-conv-preview">${preview}</span></span></a>${more}</li>`;
    }).join('');
    convList.querySelectorAll('.chat-conv-item[data-id]').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        openConversation(el.dataset.id);
      });
    });
  }

  async function loadConversations() {
    const data = await api('/api/chat/conversations');
    conversations = data.conversations || [];
    conversations.forEach((c) => {
      (c.members || []).forEach((m) => {
        if (typeof m.online === 'boolean') presence[m.username] = m.online;
      });
    });
    await Promise.all(conversations.map(async (c) => {
      if (!c.last_e2e || c.request || !E2E?.ready()) return;
      try {
        await e2eReady(c.id);
        const result = await decryptPayload(c.id, c.last_e2e);
        const html = result.html;
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const text = (tmp.innerText || '').trim();
        if (text) c.last_preview = text.slice(0, 80);
      } catch (err) { console.debug('conv preview decrypt failed', err); }
    }));
    renderConvList();
    updateNavBadge();
  }

  function attOpenUrl(att, msg, index) {
    if (att?.share_url) return att.share_url;
    return fileUrl(msg, index);
  }

  function fileUrl(msg, index) {
    const base = msg.file_url || `/api/chat/conversations/${msg.conversation_id}/messages/${msg.id}/file`;
    const i = index || 0;
    return i ? `${base}?i=${i}` : base;
  }

  function msgAttachments(msg) {
    const meta = msg.metadata || {};
    if (Array.isArray(meta.attachments) && meta.attachments.length) return meta.attachments;
    if (meta.owner_rel) return [meta];
    return [];
  }

  function isFolderAtt(att) {
    return att?.media_kind === 'folder';
  }

  function isImageAtt(att, msg) {
    if (isFolderAtt(att)) return false;
    const kind = att?.media_kind || (msg?.msg_type === 'gif' ? 'gif' : '');
    if (kind === 'image' || kind === 'gif') return true;
    const name = String(att?.original_name || '').toLowerCase();
    const ext = name.split('.').pop();
    return IMAGE_EXTS.includes(ext);
  }

  function isImageMsg(msg) {
    const atts = msgAttachments(msg);
    if (atts.length) return atts.every((a) => isImageAtt(a, msg));
    return isImageAtt(msg.metadata || {}, msg);
  }

  function highlightMentions(html) {
    return String(html || '').replace(/@([A-Za-z0-9_.\-@]+)/g, (all, name) => {
      const members = (activeConv()?.members || []).map((m) => m.username);
      if (members.includes(name)) return `<span class="chat-mention">@${esc(name)}</span>`;
      return all;
    });
  }

  function activeConv() {
    return conversations.find((c) => c.id === activeConvId) || null;
  }

  function attachmentHtml(msg) {
    const atts = msgAttachments(msg);
    if (!atts.length) return '';
    const mine = msg.sender_username === me;
    const cards = atts.map((att, i) => {
      const name = att.original_name || 'file';
      const previewUrl = fileUrl(msg, i);
      const openUrl = attOpenUrl(att, msg, i);
      const blank = att.share_url ? '' : ' target="_blank" rel="noopener"';
      const menu = mine ? '' : `<button type="button" class="chat-link-btn" data-att="save" data-id="${esc(msg.id)}" data-i="${i}">Save a copy</button>`;
      if (isImageAtt(att, msg)) {
        return `<div class="chat-att-wrap"><img src="${esc(previewUrl)}" alt="${esc(name)}" class="chat-msg-image" data-full="${esc(previewUrl)}" onerror="this.replaceWith(Object.assign(document.createElement('div'),{className:'chat-file-unavailable',textContent:'File unavailable'}))">${menu}</div>`;
      }
      if (isFolderAtt(att)) {
        return `<div class="chat-att-wrap"><a class="chat-file-card chat-file-card--folder" href="${esc(openUrl)}">${esc(name)}/</a>${menu}</div>`;
      }
      return `<div class="chat-att-wrap"><a class="chat-file-card" href="${esc(openUrl)}"${blank}>${esc(name)}</a>${menu}</div>`;
    }).join('');
    return `<div class="chat-att-stack">${cards}</div>`;
  }

  function replyQuoteHtml(msg) {
    if (!msg.reply_to_id) return '';
    const src = messagesEl?.querySelector(`[data-msg-id="${cssEscape(msg.reply_to_id)}"]`);
    const preview = src ? (src.querySelector('.aird-chat-bubble-body')?.innerText || '').slice(0, 80) : 'Message';
    return `<button type="button" class="chat-quote" data-jump="${esc(msg.reply_to_id)}">${esc(preview || 'Original message')}</button>`;
  }

  function reactionsHtml(msg) {
    const groups = {};
    (msg.reactions || []).forEach((r) => {
      groups[r.emoji] = groups[r.emoji] || [];
      groups[r.emoji].push(r.username);
    });
    const chips = Object.entries(groups).map(([emoji, users]) => {
      const mineChip = users.includes(me) ? ' is-mine' : '';
      return `<button type="button" class="chat-react-chip${mineChip}" data-emoji="${esc(emoji)}" data-id="${esc(msg.id)}" title="${esc(users.join(', '))}">${emoji} ${users.length}</button>`;
    }).join('');
    return chips ? `<div class="chat-react-row">${chips}</div>` : '';
  }

  function hoverBarHtml(msg) {
    const id = esc(msg.id);
    const emojis = REACTIONS.map((e) =>
      `<button type="button" class="chat-react-emoji" data-emoji="${e}" data-id="${id}" title="${e}">${e}</button>`
    ).join('');
    const mineActs = msg._mine
      ? `<button type="button" data-act="edit" data-id="${id}">Edit</button><button type="button" data-act="delete" data-id="${id}">Delete</button>`
      : '';
    return `<div class="chat-hover-bar" role="toolbar" aria-label="Message actions">
      ${emojis}
      <details class="chat-msg-more">
        <summary aria-label="More">⋯</summary>
        <div class="chat-msg-more-list">
          <button type="button" data-act="reply" data-id="${id}">Reply</button>
          <button type="button" data-act="forward" data-id="${id}">Forward</button>
          <button type="button" data-act="pin" data-id="${id}">Pin</button>
          ${mineActs}
        </div>
      </details>
    </div>`;
  }

  function seenHtml(msg) {
    if (!msg._mine || !receipts.length) return '';
    const conv = activeConv();
    const seen = receipts.filter((r) => (
      r.last_read_id === msg.id
      || (!msg._e2e && r.last_read_at >= msg.created_at)
    ));
    if (!seen.length) return '';
    if (conv?.kind === 'group') {
      const names = seen.map((s) => (
        `${s.username}${msg._e2e && s.decrypted ? ' (decrypted)' : ''}`
      ));
      return `<div class="chat-seen">Seen by ${esc(names.join(', '))}</div>`;
    }
    if (msg._e2e) {
      return seen.some((receipt) => receipt.decrypted)
        ? '<div class="chat-seen">Decrypted &amp; seen</div>'
        : '';
    }
    return '<div class="chat-seen">Seen</div>';
  }

  function messageHtml(msg) {
    const rowClass = msg._mine ? 'chat-msg-row--mine' : 'chat-msg-row--theirs';
    const bubbleClass = msg._mine ? 'aird-chat-bubble--mine' : 'aird-chat-bubble--theirs';
    let body = '';
    if (msg.forwarded_from || msg.metadata?.forwarded_from) {
      body += '<div class="chat-forwarded">Forwarded</div>';
    }
    body += replyQuoteHtml(msg);
    const hasAtt = msgAttachments(msg).length > 0 || msg.msg_type === 'file' || msg.msg_type === 'gif';
    const text = String(msg.body || '').replace(/<br\s*\/?>/gi, '').trim();
    if (text) {
      body += `<div class="chat-msg-text">${highlightMentions(msg.body || '')}</div>`;
    }
    if (hasAtt) {
      body += attachmentHtml(msg);
    }
    if (msg.edited_at) body += '<span class="chat-edited">edited</span>';
    const nameLine = msg._mine
      ? ''
      : `<div class="chat-msg-name">${esc(msg.sender_username)}</div>`;
    return `<div class="chat-msg-row ${rowClass}" data-msg-id="${esc(msg.id)}" data-e2e="${msg._e2e ? '1' : '0'}" data-decrypt-ok="${msg._decryptOk ? '1' : '0'}" data-created-at="${esc(msg.created_at || '')}">${avatarHtml(msg.sender_username)}<div class="chat-msg-stack">${nameLine}<div class="chat-msg-main">${hoverBarHtml(msg)}<div class="aird-chat-bubble ${bubbleClass}"><div class="aird-chat-bubble-body">${body}</div>${reactionsHtml(msg)}</div></div><div class="chat-msg-time">${esc(formatTime(msg.created_at))}</div>${seenHtml(msg)}</div></div>`;
  }

  function refreshSeenIndicator(messageId) {
    const row = messagesEl?.querySelector(`[data-msg-id="${cssEscape(messageId)}"]`);
    const stack = row?.querySelector('.chat-msg-stack');
    if (!row || !stack || !row.classList.contains('chat-msg-row--mine')) return;
    stack.querySelectorAll('.chat-seen').forEach((node) => node.remove());
    const html = seenHtml({
      id: messageId,
      created_at: row.dataset.createdAt || '',
      _mine: true,
      _e2e: row.dataset.e2e === '1',
    });
    if (html) stack.insertAdjacentHTML('beforeend', html);
  }

  function bindMessageUi(root) {
    const scope = root || messagesEl;
    if (!scope) return;
    scope.querySelectorAll('.chat-msg-image').forEach((img) => {
      img.addEventListener('click', () => openLightbox(img));
    });
    scope.querySelectorAll('[data-att="open"]').forEach((btn) => {
      btn.addEventListener('click', () => window.open(btn.dataset.url, '_blank', 'noopener'));
    });
    scope.querySelectorAll('[data-att="save"]').forEach((btn) => {
      btn.addEventListener('click', () => saveCopy(btn.dataset.id, btn.dataset.i).catch((e) => alert(e.message)));
    });
    scope.querySelectorAll('[data-act]').forEach((btn) => {
      btn.addEventListener('click', () => onMsgAction(btn.dataset.act, btn.dataset.id));
    });
    scope.querySelectorAll('.chat-react-chip, .chat-react-emoji').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        react(btn.dataset.id, btn.dataset.emoji).catch((err) => alert(err.message));
      });
    });
    scope.querySelectorAll('.chat-quote').forEach((btn) => {
      btn.addEventListener('click', () => jumpTo(btn.dataset.jump));
    });
  }

  function openLightbox(imgEl) {
    const dlg = qs('#chatLightbox');
    const img = qs('#chatLightboxImg');
    const url = (imgEl?.dataset.full || imgEl?.src) || '';
    if (img && url) img.src = url;
    dlg?.showModal();
  }

  function jumpTo(id) {
    const el = messagesEl?.querySelector(`[data-msg-id="${cssEscape(id)}"]`);
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    el.classList.add('chat-msg-flash');
    setTimeout(() => el.classList.remove('chat-msg-flash'), 1200);
  }

  async function saveCopy(messageId, index) {
    if (!activeConvId) return;
    const picker = globalThis.AirdFolderPicker;
    if (!picker?.open) throw new Error('Folder picker unavailable');
    picker.init?.();
    const dest = await picker.open('copy', { title: 'Save a copy to…', confirmLabel: 'Save here' });
    if (dest === null || dest === undefined) return;
    const q = index != null && index !== '' ? `?i=${encodeURIComponent(index)}` : '';
    const data = await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}/save-copy${q}`, {
      method: 'POST',
      body: JSON.stringify({ dest_dir: dest }),
    });
    alert(data.already_owner ? 'You already own this file.' : `Saved to ${data.saved_rel}`);
  }

  async function react(messageId, emoji) {
    if (!activeConvId) return;
    const data = await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}/reactions`, {
      method: 'POST',
      body: JSON.stringify({ emoji }),
    });
    applyReactions(messageId, data.reactions || []);
  }

  function applyReactions(messageId, reactions) {
    const row = messagesEl?.querySelector(`[data-msg-id="${cssEscape(messageId)}"]`);
    if (!row) return;
    const fake = { id: messageId, reactions };
    const next = reactionsHtml(fake);
    const old = row.querySelector('.chat-react-row');
    if (old) old.outerHTML = next;
    bindMessageUi(row);
  }

  function onMsgAction(act, id) {
    if (act === 'reply') setReply(id);
    else if (act === 'delete') deleteMessage(id).catch((e) => alert(e.message));
    else if (act === 'edit') editMessage(id).catch((e) => alert(e.message));
    else if (act === 'pin') pinMessage(id).catch((e) => alert(e.message));
    else if (act === 'forward') openForward(id);
  }

  function setReply(id) {
    replyToId = id;
    const row = messagesEl?.querySelector(`[data-msg-id="${cssEscape(id)}"]`);
    const text = (row?.querySelector('.aird-chat-bubble-body')?.innerText || 'Message').slice(0, 100);
    if (replyPreview) replyPreview.textContent = `Replying: ${text}`;
    replyBar?.classList.remove('hidden');
    editor?.focus();
  }

  function clearReply() {
    replyToId = null;
    replyBar?.classList.add('hidden');
  }

  async function editMessage(messageId) {
    const row = messagesEl?.querySelector(`[data-msg-id="${cssEscape(messageId)}"]`);
    const textEl = row?.querySelector('.chat-msg-text');
    if (!textEl || textEl.isContentEditable) return;
    row.querySelectorAll('details[open]').forEach((d) => { d.open = false; });
    const originalHtml = textEl.innerHTML;
    const originalText = (textEl.innerText || '').trim();
    textEl.contentEditable = 'true';
    textEl.classList.add('chat-msg-text--editing');
    textEl.focus();
    const sel = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(textEl);
    sel?.removeAllRanges();
    sel?.addRange(range);
    let done = false;
    const finish = async (save) => {
      if (done) return;
      done = true;
      textEl.contentEditable = 'false';
      textEl.classList.remove('chat-msg-text--editing');
      textEl.removeEventListener('keydown', onKey);
      textEl.removeEventListener('blur', onBlur);
      const next = (textEl.innerText || '').trim();
      if (!save || !next || next === originalText) {
        textEl.innerHTML = originalHtml;
        return;
      }
      try {
        if (!E2E?.ready()) throw new Error('Waiting for the chat key…');
        const ok = await e2eReady(activeConvId);
        if (!ok) throw new Error('Waiting for the chat key…');
        const e2e = await E2E.encrypt(activeConvId, E2E.sanitizeHtml(textEl.innerHTML || next));
        const data = await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}`, {
          method: 'PATCH',
          body: JSON.stringify({
            e2e,
            mentions: extractMentions(textEl.innerHTML || next, activeConv()),
          }),
        });
        if (data.message) replaceMessage(await prepareMessage(data.message));
      } catch (err) {
        textEl.innerHTML = originalHtml;
        setE2eStatus(err.message);
      }
    };
    const onKey = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        textEl.blur();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        finish(false);
      }
    };
    const onBlur = () => finish(true);
    textEl.addEventListener('keydown', onKey);
    textEl.addEventListener('blur', onBlur);
  }

  async function pinMessage(messageId) {
    const data = await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}/pin`, { method: 'POST' });
    pins = data.pins || [];
    renderPins();
  }

  async function unpinMessage(messageId) {
    const data = await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}/pin`, { method: 'DELETE' });
    pins = data.pins || [];
    renderPins();
  }

  function renderPins() {
    if (!pinBar) return;
    if (!pins.length) {
      pinBar.hidden = true;
      pinBar.innerHTML = '';
      return;
    }
    pinBar.hidden = false;
    pinBar.innerHTML = pins.map((p) =>
      `<button type="button" class="chat-pin-item" data-id="${esc(p.message_id)}">${esc(p.preview || 'Pinned')}</button><button type="button" class="chat-pin-x" data-unpin="${esc(p.message_id)}" aria-label="Unpin">×</button>`
    ).join('');
    pinBar.querySelectorAll('.chat-pin-item').forEach((btn) => btn.addEventListener('click', () => jumpTo(btn.dataset.id)));
    pinBar.querySelectorAll('[data-unpin]').forEach((btn) => {
      btn.addEventListener('click', () => unpinMessage(btn.dataset.unpin).catch((e) => alert(e.message)));
    });
  }

  function openForward(messageId) {
    forwardMsgId = messageId;
    const list = qs('#chatForwardList');
    if (list) {
      list.innerHTML = conversations.filter((c) => c.id !== activeConvId).map((c) =>
        `<label class="flex items-center gap-2 py-2 px-1"><input type="checkbox" value="${esc(c.id)}"> ${esc(convTitle(c))}</label>`
      ).join('') || '<p class="text-sm opacity-60">No other conversations</p>';
    }
    qs('#chatForwardModal')?.showModal();
  }

  async function sendForward() {
    if (!activeConvId || !forwardMsgId) return;
    const ids = [...(qs('#chatForwardList')?.querySelectorAll('input:checked') || [])].map((i) => i.value);
    if (!ids.length) return;
    const isE2e = e2ePlain.has(forwardMsgId);
    const plain = e2ePlain.get(forwardMsgId)
      || messagesEl?.querySelector(`[data-msg-id="${cssEscape(forwardMsgId)}"] .chat-msg-text`)?.innerHTML
      || '';
    if (isE2e) {
      if (!E2E?.ready()) throw new Error('Waiting for the chat key…');
      if (plain.includes('chat-e2e-fail')) throw new Error('Cannot forward a message that did not decrypt on this device');
      const html = E2E.sanitizeHtml(plain);
      for (const id of ids) {
        const ok = await e2eReady(id);
        if (!ok) throw new Error('Waiting for the chat key…');
        const e2e = await E2E.encrypt(id, html);
        await api(`/api/chat/conversations/${id}/messages`, {
          method: 'POST',
          body: JSON.stringify({ type: 'text', e2e }),
        });
      }
    } else {
      await api(`/api/chat/conversations/${activeConvId}/messages/${forwardMsgId}/forward`, {
        method: 'POST',
        body: JSON.stringify({ conversation_ids: ids }),
      });
    }
    qs('#chatForwardModal')?.close();
    await loadConversations();
  }

  async function appendMessage(msg) {
    if (!messagesEl) return;
    await prepareMessage(msg);
    tagMine(msg);
    messagesEl.querySelector('.chat-empty')?.remove();
    const day = dayKey(msg.created_at);
    if (day && day !== lastDayKey()) {
      messagesEl.insertAdjacentHTML('beforeend', dateChipHtml(msg.created_at));
    }
    messagesEl.insertAdjacentHTML('beforeend', messageHtml(msg));
    bindMessageUi(messagesEl.lastElementChild);
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  async function replaceMessage(msg) {
    await prepareMessage(msg);
    tagMine(msg);
    const el = messagesEl?.querySelector(`[data-msg-id="${cssEscape(msg.id)}"]`);
    if (!el) {
      appendMessage(msg);
      return;
    }
    el.outerHTML = messageHtml(msg);
    const next = messagesEl.querySelector(`[data-msg-id="${cssEscape(msg.id)}"]`);
    bindMessageUi(next);
  }

  function removeMessageEl(messageId) {
    const el = messagesEl?.querySelector(`[data-msg-id="${cssEscape(messageId)}"]`);
    el?.remove();
    if (messagesEl && !messagesEl.querySelector('[data-msg-id]')) showEmptyThread();
  }

  function paintMessage(msg) {
    tagMine(msg);
    const day = dayKey(msg.created_at);
    if (day && day !== lastDayKey()) {
      messagesEl.insertAdjacentHTML('beforeend', dateChipHtml(msg.created_at));
    }
    messagesEl.insertAdjacentHTML('beforeend', messageHtml(msg));
    bindMessageUi(messagesEl.lastElementChild);
  }

  async function loadOlderMessages() {
    if (!activeConvId || !messagesEl || loadingOlder || !hasOlder) return;
    const beforeId = firstMessageId();
    if (!beforeId) return;
    loadingOlder = true;
    const prevH = messagesEl.scrollHeight;
    const prevTop = messagesEl.scrollTop;
    try {
      const data = await api(`/api/chat/conversations/${activeConvId}/messages?before_id=${encodeURIComponent(beforeId)}&limit=50`);
      const msgs = data.messages || [];
      hasOlder = msgs.length >= 50;
      if (!msgs.length) return;
      for (const m of msgs) await prepareMessage(m);
      const firstDay = messagesEl.querySelector('.chat-day');
      const frag = document.createElement('div');
      let prevDay = '';
      msgs.forEach((m) => {
        tagMine(m);
        const day = dayKey(m.created_at);
        if (day && day !== prevDay) {
          frag.insertAdjacentHTML('beforeend', dateChipHtml(m.created_at));
          prevDay = day;
        }
        frag.insertAdjacentHTML('beforeend', messageHtml(m));
      });
      if (firstDay && firstDay.dataset.day === prevDay) firstDay.remove();
      const nodes = [...frag.childNodes];
      const first = messagesEl.firstChild;
      nodes.forEach((n) => messagesEl.insertBefore(n, first));
      nodes.forEach((n) => {
        if (n.nodeType === 1 && n.matches?.('[data-msg-id]')) bindMessageUi(n);
      });
      messagesEl.scrollTop = messagesEl.scrollHeight - prevH + prevTop;
    } catch (err) {
      console.debug('load older failed', err);
    } finally {
      loadingOlder = false;
    }
  }

  async function openConversation(convId) {
    window.AirdEnhancedSecure?.closeView?.();
    enhancedSecureView = false;
    if (activeConvId && convId !== activeConvId) {
      pending.forEach(function (p) { revokePendingPreview(p); });
      pendingStore.clear();
      renderPending();
    }
    const gen = ++loadGen;
    activeConvId = convId;
    loadingOlder = false;
    const conv = conversations.find((c) => c.id === convId);
    setThreadHeader(conv || null);
    hideRequest();
    composer.classList.add('hidden');
    messagesEl.innerHTML = '<div class="chat-empty"><p>Loading…</p></div>';
    renderConvList();
    setPollEnabled(true);
    setMobileView('thread');
    if (showRequest(conv)) {
      if (conv.request === 'outgoing') {
        try { await e2eReady(convId); } catch (err) { console.debug('e2e ready on request failed', err); }
      }
      clearMessagesView();
      messagesEl.innerHTML = '';
      const params = new URLSearchParams(location.search);
      params.set('c', convId);
      history.replaceState(null, '', `${location.pathname}?${params}`);
      return;
    }
    composer.classList.remove('hidden');
    try { await e2eReady(convId); } catch (err) { console.debug('e2e ready on openConversation failed', err); }
    const data = await api(`/api/chat/conversations/${convId}/messages?limit=50`);
    if (gen !== loadGen) return;
    clearMessagesView();
    pins = data.pins || [];
    receipts = data.receipts || [];
    renderPins();
    const msgs = data.messages || [];
    hasOlder = msgs.length >= 50;
    for (const m of msgs) await prepareMessage(m);
    if (!msgs.length) showEmptyThread();
    else msgs.forEach((m) => paintMessage(m));
    if (msgs.length) messagesEl.scrollTop = messagesEl.scrollHeight;
    const last = [...msgs].reverse().find((msg) => !msg._e2e || msg._decryptOk);
    if (last) markRead(last);
    const params = new URLSearchParams(location.search);
    params.set('c', convId);
    params.delete('attach');
    params.delete('attachDir');
    params.delete('attachBatch');
    history.replaceState(null, '', `${location.pathname}?${params}`);
    applyPendingBrowseAttach();
  }

  function applyPendingBrowseAttach() {
    if (!pendingBrowseAttachQueue.length || !activeConvId) return;
    const gate = canAttachFiles();
    if (!gate.ok) {
      setE2eStatus(gate.reason);
      return;
    }
    for (const item of pendingBrowseAttachQueue) {
      attachFromBrowse(item.path, item.isDir);
    }
    pendingBrowseAttachQueue = [];
    setE2eStatus('');
  }

  function markRead(msg) {
    const WS = window.AirdChatWS;
    if (document.visibilityState !== 'visible' || !WS?.isOpen() || !activeConvId) return;
    if (msg._e2e && !msg._decryptOk) return;
    WS.send({
      type: 'chat_read',
      conversation_id: activeConvId,
      message_id: msg.id,
      decrypted: !!(msg._e2e && msg._decryptOk),
    });
    loadConversations();
  }

  function markLatestVisibleRead() {
    if (enhancedSecureView || document.visibilityState !== 'visible') return;
    const rows = [...(messagesEl?.querySelectorAll('[data-msg-id]') || [])];
    const row = rows.reverse().find((item) => (
      item.dataset.e2e !== '1' || item.dataset.decryptOk === '1'
    ));
    if (!row) return;
    markRead({
      id: row.dataset.msgId,
      _e2e: row.dataset.e2e === '1',
      _decryptOk: row.dataset.decryptOk === '1',
    });
  }

  function onWsChatMessage(data) {
    if (enhancedSecureView) {
      loadConversations();
      updateNavBadge();
      return;
    }
    if (!data.message) return;
    const msg = tagMine({ ...data.message });
    if (msg.conversation_id === activeConvId) {
      if (!messagesEl?.querySelector(`[data-msg-id="${cssEscape(msg.id)}"]`)) {
        appendMessage(msg).then(() => markRead(msg)).catch((err) => {
          console.debug('appendMessage failed', err);
        });
      }
    }
    loadConversations();
    updateNavBadge();
  }

  function onWsTyping(data) {
    if (enhancedSecureView || data.conversation_id !== activeConvId) return;
    const sub = header?.querySelector('.chat-thread-sub');
    if (!sub || data.username === me) return;
    const prev = sub.dataset.base || sub.textContent;
    sub.dataset.base = prev;
    sub.textContent = `${data.username} is typing…`;
    clearTimeout(typingTimer);
    typingTimer = setTimeout(() => { sub.textContent = sub.dataset.base || prev; }, 1500);
  }

  const WS_HANDLERS = {
    chat_message: onWsChatMessage,
    chat_message_deleted: (data) => {
      if (!enhancedSecureView && data.conversation_id === activeConvId) removeMessageEl(data.message_id);
      loadConversations();
      updateNavBadge();
    },
    chat_message_edited: (data) => {
      if (enhancedSecureView) return;
      if (data.message?.conversation_id === activeConvId) {
        replaceMessage(data.message).catch((err) => {
          console.debug('replaceMessage failed', err);
        });
      }
    },
    chat_reaction: (data) => {
      if (enhancedSecureView) return;
      if (data.conversation_id === activeConvId) applyReactions(data.message_id, data.reactions || []);
    },
    chat_receipt: (data) => {
      if (enhancedSecureView || data.conversation_id !== activeConvId) return;
      receipts = receipts.filter((r) => r.username !== data.username);
      receipts.push({
        username: data.username,
        last_read_id: data.last_read_id,
        decrypted: data.decrypted === true,
      });
      refreshSeenIndicator(data.last_read_id);
    },
    chat_pin: (data) => {
      if (enhancedSecureView || data.conversation_id !== activeConvId) return;
      pins = data.pins || [];
      renderPins();
    },
    chat_unpin: (data) => {
      if (enhancedSecureView || data.conversation_id !== activeConvId) return;
      pins = data.pins || [];
      renderPins();
    },
    chat_presence: (data) => {
      presence[data.username] = !!data.online;
      renderConvList();
      if (!enhancedSecureView && activeConvId) setThreadHeader(activeConv());
    },
    chat_notify: (data) => {
      updateNavBadge(data.unread_total);
      if (!enhancedSecureView && data.conversation_id === activeConvId) {
        syncNewMessages().catch((err) => { console.debug('chat_notify sync failed', err); });
      }
      loadConversations();
    },
    chat_ws_close: () => setPollEnabled(true),
    chat_ws_open: () => setPollEnabled(!!activeConvId),
    chat_typing: onWsTyping,
    chat_e2e_key_ready: (data) => {
      const convId = data.conversation_id;
      if (!convId) return;
      const had = E2E?.hasConvKey?.(convId);
      e2eReady(convId).then((ok) => {
        if (!ok) return;
        loadConversations();
        if (convId === activeConvId && !had && !enhancedSecureView) reloadThreadAfterKey(convId);
      }).catch((err) => console.debug('e2e key ready failed', err));
    },
    chat_request: (data) => {
      const convId = data.conversation_id;
      loadConversations().then(() => {
        const conv = conversations.find((c) => c.id === convId);
        if (conv && !conv.request) {
          e2eReady(convId).catch((err) => console.debug('e2e ready after accept failed', err));
        }
        if (convId && convId === activeConvId && !enhancedSecureView) openConversation(convId);
      }).catch((err) => console.debug('chat request refresh failed', err));
    },
  };

  function handleWsEvent(data) {
    const handler = data?.type && WS_HANDLERS[data.type];
    if (handler) handler(data);
  }

  async function startConversation(username) {
    const name = (username || newUserInput?.value || '').trim();
    if (!name) return;
    hideUserSuggest();
    const data = await api('/api/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ username: name }),
    });
    if (newUserInput) newUserInput.value = '';
    setNewPanel(false);
    await loadConversations();
    const id = data.conversation?.id;
    if (id) openConversation(id);
  }

  function renderGroupChips() {
    if (!groupChipsEl) return;
    if (!groupPicks.length) {
      groupChipsEl.hidden = true;
      groupChipsEl.innerHTML = '';
      return;
    }
    groupChipsEl.hidden = false;
    groupChipsEl.innerHTML = groupPicks.map((u) =>
      `<span class="chat-chip">${esc(u)}<button type="button" data-rm="${esc(u)}" aria-label="Remove ${esc(u)}">×</button></span>`
    ).join('');
    groupChipsEl.querySelectorAll('[data-rm]').forEach((btn) => {
      btn.addEventListener('click', () => {
        groupPicks = groupPicks.filter((x) => x !== btn.dataset.rm);
        renderGroupChips();
      });
    });
  }

  function addGroupPick(username) {
    const name = (username || '').trim();
    if (!name || name === me || groupPicks.includes(name)) return;
    groupPicks.push(name);
    renderGroupChips();
    if (newUserInput) newUserInput.value = '';
    hideUserSuggest();
  }

  function setNewMode(mode) {
    newMode = mode === 'group' ? 'group' : 'dm';
    document.querySelectorAll('.chat-new-tab').forEach((btn) => {
      const on = btn.dataset.new === newMode;
      btn.classList.toggle('is-on', on);
      btn.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    if (groupFields) groupFields.hidden = newMode !== 'group';
    if (startBtn) startBtn.hidden = newMode !== 'dm';
    if (groupCreateBtn) groupCreateBtn.hidden = newMode !== 'group';
    if (newUserInput) newUserInput.placeholder = newMode === 'group' ? 'Add people…' : 'Find people…';
  }

  function setNewPanel(open) {
    if (!newPanel) return;
    newPanel.hidden = !open;
    if (open) {
      setNewMode(newMode);
      newUserInput?.focus();
    } else {
      hideUserSuggest();
    }
  }

  async function createGroup() {
    const title = qs('#chatGroupTitle')?.value.trim() || 'Group';
    const members = groupPicks.slice();
    if (!members.length) {
      alert('Add at least one person.');
      return;
    }
    const data = await api('/api/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ title, members }),
    });
    groupPicks = [];
    renderGroupChips();
    if (qs('#chatGroupTitle')) qs('#chatGroupTitle').value = '';
    setNewPanel(false);
    await loadConversations();
    if (data.conversation?.id) openConversation(data.conversation.id);
  }

  async function deleteMessage(messageId) {
    if (!activeConvId) return;
    if (!confirm('Delete this message?')) return;
    await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}`, { method: 'DELETE' });
    removeMessageEl(messageId);
    await loadConversations();
  }

  async function searchUsers(q, suggestEl, onPick) {
    const box = suggestEl || userSuggest;
    if (!box) return;
    const query = (q || '').trim();
    if (query.length < 2) {
      box.classList.add('hidden');
      box.innerHTML = '';
      return;
    }
    try {
      const data = await api(`/api/users/search?q=${encodeURIComponent(query)}`);
      const users = (data.users || []).filter((u) => u.username !== me);
      if (!users.length) {
        box.classList.add('hidden');
        box.innerHTML = '';
        return;
      }
      box.innerHTML = users.map((u) =>
        `<button type="button" class="w-full text-left px-3 py-2 hover:bg-base-200" data-user="${esc(u.username)}">${esc(u.username)}</button>`
      ).join('');
      box.classList.remove('hidden');
      box.querySelectorAll('[data-user]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const name = btn.dataset.user || '';
          if (onPick) onPick(name);
          else if (newMode === 'group') addGroupPick(name);
          else startConversation(name).catch((err) => alert(err.message));
        });
      });
    } catch (err) {
      console.debug('user search failed', err);
      box.classList.add('hidden');
    }
  }

  function hideUserSuggest() {
    if (!userSuggest) return;
    userSuggest.classList.add('hidden');
    userSuggest.innerHTML = '';
  }

  async function runSearch(q) {
    if (!searchResults) return;
    const query = (q || '').trim();
    if (query.length < 2) {
      searchResults.classList.add('hidden');
      searchResults.innerHTML = '';
      return;
    }
    const data = await api(`/api/chat/search?q=${encodeURIComponent(query)}`);
    const rows = data.results || [];
    if (!rows.length) {
      searchResults.innerHTML = '<p class="text-xs opacity-50 p-2">No matches</p>';
      searchResults.classList.remove('hidden');
      return;
    }
    searchResults.innerHTML = rows.map((r) =>
      `<button type="button" class="chat-search-hit" data-cid="${esc(r.conversation_id)}" data-mid="${esc(r.id)}"><strong>${esc(r.sender_username)}</strong> ${esc(r.snippet || '')}</button>`
    ).join('');
    searchResults.classList.remove('hidden');
    searchResults.querySelectorAll('.chat-search-hit').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await openConversation(btn.dataset.cid);
        jumpTo(btn.dataset.mid);
      });
    });
  }

  newUserInput?.addEventListener('input', () => {
    clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(() => searchUsers(newUserInput.value), 250);
  });
  searchInput?.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(searchInput.value).catch((err) => {
      console.debug('chat search failed', err);
    }), 280);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.chat-new-user-wrap')) hideUserSuggest();
    if (!e.target.closest('#chatMemberModal')) memberSuggest?.classList.add('hidden');
  });

  const ENTER_SEND_KEY = 'aird-chat-enter-to-send';

  function enterToSendEnabled() {
    const stored = localStorage.getItem(ENTER_SEND_KEY);
    return stored === null ? true : stored === '1';
  }

  async function sendEditorMessage() {
    if (window.AirdEnhancedSecure?.viewing()) {
      const text = (editor?.innerText || '').replaceAll('\u00a0', ' ').trim();
      if (pending.length) {
        setE2eStatus('Enhanced Secure Chat is text only.');
        return;
      }
      if (!text) return;
      try {
        await window.AirdEnhancedSecure.sendText(text);
        editor.innerHTML = '';
        setE2eStatus('');
      } catch (err) {
        setE2eStatus(err.message || 'Could not send.');
      }
      return;
    }
    if (!activeConvId || !editor) return;
    const hasText = (editor.innerText || '').replaceAll('\u00a0', ' ').trim().length > 0;
    if (!hasText && !pending.length) return;
    const body = editor.innerHTML.trim();
    const reply = replyToId;
    let e2e = null;
    let mentions = [];
    if (hasText) {
      if (!E2E?.ready()) {
        setE2eStatus('Waiting for the chat key…');
        return;
      }
      try {
        const ok = await e2eReady(activeConvId);
        if (!ok) return;
        const html = E2E.sanitizeHtml(body);
        e2e = await E2E.encrypt(activeConvId, html);
        mentions = extractMentions(html, activeConv());
      } catch (err) {
        setE2eStatus(err.message || 'Could not encrypt this message.');
        return;
      }
    }
    pending.forEach(function (p) { revokePendingPreview(p); });
    const staged = pendingStore.take();
    renderPending();
    editor.innerHTML = '';
    clearReply();
    hidePathSuggest();
    if (staged.length) {
      const fd = new FormData();
      if (e2e) {
        fd.append('e2e', JSON.stringify(e2e));
        fd.append('mentions', JSON.stringify(mentions));
      } else if (hasText) {
        fd.append('caption', body);
      }
      if (reply) fd.append('reply_to_id', reply);
      for (const item of staged) {
        if (item.path) fd.append('source_path', item.path);
        else fd.append('file', item.file, item.file.name);
      }
      await api(`/api/chat/conversations/${activeConvId}/attach`, { method: 'POST', body: fd });
      await openConversation(activeConvId);
      await loadConversations();
      return;
    }
    let sentHttp = false;
    if (hasText) {
      const payload = e2e
        ? { type: 'chat_send', conversation_id: activeConvId, e2e, mentions, reply_to_id: reply }
        : { type: 'chat_send', conversation_id: activeConvId, body, reply_to_id: reply };
      const WS = window.AirdChatWS;
      if (!(WS?.isOpen() && WS.send(payload))) {
        await api(`/api/chat/conversations/${activeConvId}/messages`, {
          method: 'POST',
          body: JSON.stringify(e2e
            ? { type: 'text', e2e, mentions, reply_to_id: reply }
            : { type: 'text', body, reply_to_id: reply }),
        });
        sentHttp = true;
      }
    }
    if (sentHttp) {
      await openConversation(activeConvId);
      await loadConversations();
    }
  }

  composer?.addEventListener('submit', async (e) => {
    e.preventDefault();
    await sendEditorMessage();
  });

  function pathPopupVisible() {
    const box = getPathSuggest();
    return !!(box && !box.hidden);
  }

  async function attachComposerSelection(info) {
    if (!info) info = caretPathToken();
    if (!info) return false;
    let hit = pathSuggestHits[pathSuggestIdx];
    if (!hit) {
      const result = await bashComplete(info.token);
      const parsed = parsePathInput(info.token);
      const prefix = (parsed.prefix || '').toLowerCase();
      hit = result.hits.find((f) => f.name.toLowerCase() === prefix)
        || (result.unique ? result.hits[0] : null)
        || result.hits[0];
    }
    if (!hit) return false;
    await stagePathHit(info, hit);
    return true;
  }

  editor?.addEventListener('keydown', (e) => {
    const tokenInfo = caretPathToken();
    const pathMode = (tokenInfo && looksLikePath(tokenInfo.token)) || pathPopupVisible();
    if (pathMode) {
      if (e.key === 'Tab') {
        e.preventDefault();
        e.stopPropagation();
        const info = tokenInfo || caretPathToken();
        const hit = pathSuggestHits[pathSuggestIdx];
        if (hit?.is_dir && !e.shiftKey && info) {
          applyPathHit(info, hit).catch((err) => alert(err.message));
          return;
        }
        completeComposerPath(info, e.shiftKey).catch((err) => alert(err.message));
        return;
      }
      if (e.key === 'ArrowDown' && pathSuggestHits.length) {
        e.preventDefault();
        pathSuggestIdx = (pathSuggestIdx + 1) % pathSuggestHits.length;
        renderPathSuggest(tokenInfo);
        return;
      }
      if (e.key === 'ArrowUp' && pathSuggestHits.length) {
        e.preventDefault();
        pathSuggestIdx = (pathSuggestIdx - 1 + pathSuggestHits.length) % pathSuggestHits.length;
        renderPathSuggest(tokenInfo);
        return;
      }
      if ((e.key === 'Enter' || e.key === 'NumpadEnter') && !e.shiftKey) {
        e.preventDefault();
        e.stopPropagation();
        attachComposerSelection(tokenInfo).catch((err) => alert(err.message));
        return;
      }
      if (e.key === 'Escape' && (pathSuggestHits.length || pathPopupVisible())) {
        e.preventDefault();
        hidePathSuggest();
        return;
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && enterToSendEnabled()) {
      e.preventDefault();
      sendEditorMessage().catch((err) => alert(err.message));
      return;
    }
    window.AirdChatWS?.send({ type: 'chat_typing', conversation_id: activeConvId });
  }, true);

  editor?.addEventListener('input', () => {
    window.requestAnimationFrame(() => {
      const text = editor.innerText || '';
      const at = text.split(/\s/).pop() || '';
      const tokenInfo = caretPathToken();
      if (tokenInfo && looksLikePath(tokenInfo.token)) {
        updateComposerPathSuggest(tokenInfo).catch((err) => {
          console.debug('path suggest update failed', err);
        });
        return;
      }
      hidePathSuggest();
      if (at.startsWith('@') && at.length > 1 && activeConv()?.kind === 'group') {
        const q = at.slice(1).toLowerCase();
        const hits = (activeConv().members || []).filter((m) => m.username !== me && m.username.toLowerCase().includes(q));
        if (hits.length && mentionSuggest) {
          mentionSuggest.innerHTML = hits.map((m) => `<button type="button" data-user="${esc(m.username)}">@${esc(m.username)}</button>`).join('');
          mentionSuggest.classList.remove('hidden');
          mentionSuggest.querySelectorAll('[data-user]').forEach((btn) => {
            btn.addEventListener('click', () => {
              const prefix = editor.innerHTML.replace(/@[\w.\-@]*$/, '');
              editor.innerHTML = prefix + '@' + btn.dataset.user + '&nbsp;';
              mentionSuggest.classList.add('hidden');
              editor.focus();
            });
          });
          return;
        }
      }
      mentionSuggest?.classList.add('hidden');
    });
  });

  // document.execCommand is deprecated but remains the most reliable option for
  // contenteditable toolbar formatting across browsers.
  function execEditorCommand(cmd, value) {
    document.execCommand(cmd, false, value ?? null);
  }

  function insertTextAtCaret(text) {
    const sel = window.getSelection();
    if (!sel?.rangeCount) return;
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(text));
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  document.querySelectorAll('.chat-toolbar [data-cmd], [data-cmd]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      if (cmd === 'link') {
        const url = prompt('URL');
        if (url) execEditorCommand('createLink', url);
      } else if (cmd) {
        execEditorCommand(cmd);
      }
      editor?.focus();
    });
  });

  const EMOJIS = ['😀','😁','😂','🤣','😊','😇','🙂','😉','😍','😘','😜','🤔','😎','🤩','🥳','😢','😭','😤','😡','🤯','😳','🤗','🙏','👍','👎','👏','🔥','❤️','💯','🎉','✨','⭐','👀','✅','❌'];
  const emojiBtn = qs('#chatEmojiBtn');
  const emojiPicker = qs('#chatEmojiPicker');

  function insertEmoji(ch) {
    if (!editor) return;
    editor.focus();
    insertTextAtCaret(ch);
  }

  function hideEmojiPicker() {
    if (!emojiPicker) return;
    emojiPicker.hidden = true;
    emojiPicker.classList.add('hidden');
  }

  function toggleEmojiPicker() {
    if (!emojiPicker) return;
    if (!emojiPicker.dataset.ready) {
      emojiPicker.innerHTML = EMOJIS.map((e) =>
        `<button type="button" class="chat-emoji-item">${e}</button>`
      ).join('');
      emojiPicker.dataset.ready = '1';
      emojiPicker.querySelectorAll('.chat-emoji-item').forEach((btn) => {
        btn.addEventListener('click', () => {
          insertEmoji(btn.textContent);
          hideEmojiPicker();
        });
      });
    }
    const show = emojiPicker.hidden;
    emojiPicker.hidden = !show;
    emojiPicker.classList.toggle('hidden', !show);
  }

  emojiBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleEmojiPicker();
  });
  document.addEventListener('click', (e) => {
    if (!e.target.closest('#chatEmojiPicker, #chatEmojiBtn')) hideEmojiPicker();
  });

  function revokePendingPreview(item) {
    if (item?.previewUrl) {
      try { URL.revokeObjectURL(item.previewUrl); } catch { /* ignore */ }
      delete item.previewUrl;
    }
  }

  function pendingChipHtml(p) {
    const label = esc(p.path || p.name);
    const rm = esc(p.id);
    if (p.file && String(p.file.type || '').startsWith('image/')) {
      if (!p.previewUrl) p.previewUrl = URL.createObjectURL(p.file);
      return '<span class="chat-pending-chip chat-pending-chip--image">'
        + '<img class="chat-pending-thumb" src="' + esc(p.previewUrl) + '" alt="">'
        + '<span class="truncate" title="' + label + '">' + esc(p.name) + '</span>'
        + '<button type="button" data-rm="' + rm + '" aria-label="Remove ' + esc(p.name) + '">×</button></span>';
    }
    return '<span class="chat-pending-chip' + (p.isDir ? ' is-folder' : '') + '">'
      + '<span class="truncate" title="' + label + '">' + esc(p.name) + '</span>'
      + '<button type="button" data-rm="' + rm + '" aria-label="Remove ' + esc(p.name) + '">×</button></span>';
  }

  function renderPending() {
    let el = document.getElementById('chatPending');
    if (!el && editor) {
      const wrap = editor.closest('.chat-editor-wrap') || editor.parentNode;
      el = document.createElement('div');
      el.id = 'chatPending';
      el.className = 'chat-pending';
      wrap.insertBefore(el, editor);
    }
    if (!el) return;
    if (!pending.length) {
      el.hidden = true;
      el.innerHTML = '';
      return;
    }
    el.hidden = false;
    el.innerHTML = pending.map((p) => pendingChipHtml(p)).join('');
    el.querySelectorAll('[data-rm]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const item = pending.find((p) => p.id === btn.dataset.rm);
        revokePendingPreview(item);
        pendingStore.remove(btn.dataset.rm);
        renderPending();
      });
    });
  }

  function stagePath(relativePath, opts = {}) {
    const result = pendingStore.stagePath(relativePath, opts);
    if (!result.ok) {
      setE2eStatus(result.reason);
      return;
    }
    renderPending();
  }

  function stageBlob(file) {
    const result = pendingStore.stageBlob(file);
    if (!result.ok) {
      setE2eStatus(result.reason);
      return;
    }
    renderPending();
  }

  function attachFileBlob(file) {
    stageBlob(file);
  }

  function attachUpload(input) {
    if (!input.files?.length) return;
    [...input.files].forEach((f) => stageBlob(f));
    input.value = '';
  }

  function attachFromBrowse(relativePath, isDir) {
    stagePath(relativePath, { isDir: !!isDir });
    editor?.focus();
  }

  function canAttachFiles() {
    return attachAllowed({
      enhanced: !!(enhancedSecureView || window.AirdEnhancedSecure?.viewing()),
      convId: activeConvId,
    });
  }

  async function listDir(relPath) {
    const data = await api(filesApiUrl(relPath));
    if (!Array.isArray(data?.files)) throw new Error('Could not list files');
    return data.files;
  }

  async function bashComplete(raw) {
    const parsed = parsePathInput(raw);
    const files = await listDir(parsed.dir);
    return completePath(raw, files);
  }

  const filePicker = createBrowsePicker({
    modal: browseModal,
    list: browseList,
    pathInput: browsePathEl,
    upBtn: browseUp,
    closeBtn: browseClose,
    attachBtn: browseAttach,
    listDir,
    onAttach: attachFromBrowse,
    onError: (msg) => setE2eStatus(msg),
  });

  function caretPathToken() {
    if (!editor) return null;
    const sel = window.getSelection();
    let left = '';
    if (sel?.rangeCount && (editor === sel.anchorNode || editor.contains(sel.anchorNode))) {
      const pre = document.createRange();
      pre.selectNodeContents(editor);
      try {
        pre.setEnd(sel.anchorNode, sel.anchorOffset);
        left = pre.toString();
      } catch (_) {
        left = '';
      }
    }
    if (!left) left = editor.innerText || editor.textContent || '';
    left = left.replaceAll('\u00a0', ' ').replaceAll('\r', '');
    const line = left.split('\n').pop() || '';
    const m = /([^\s]+)$/.exec(line);
    if (!m) return null;
    return { token: m[1], start: left.length - m[1].length, end: left.length };
  }

  function placeEditorCaret(offset) {
    if (!editor) return;
    const sel = window.getSelection();
    if (!sel) return;
    const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
    let pos = 0;
    let node = walker.nextNode();
    while (node) {
      const len = node.textContent.length;
      if (pos + len >= offset) {
        const range = document.createRange();
        range.setStart(node, Math.max(0, offset - pos));
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        return;
      }
      pos += len;
      node = walker.nextNode();
    }
    const range = document.createRange();
    range.selectNodeContents(editor);
    range.collapse(false);
    sel.removeAllRanges();
    sel.addRange(range);
  }

  function replaceCaretToken(info, next) {
    const sel = window.getSelection();
    const node = sel?.anchorNode;
    if (node && node.nodeType === Node.TEXT_NODE && editor.contains(node)) {
      const text = node.textContent || '';
      const offset = sel.anchorOffset;
      const left = text.slice(0, offset);
      const m = /([^\s]+)$/.exec(left);
      if (m) {
        const start = offset - m[1].length;
        node.textContent = `${text.slice(0, start)}${next}${text.slice(offset)}`;
        const range = document.createRange();
        range.setStart(node, start + next.length);
        range.collapse(true);
        sel.removeAllRanges();
        sel.addRange(range);
        return;
      }
    }
    const raw = (editor.innerText || '').replaceAll('\u00a0', ' ');
    const start = info?.start ?? Math.max(0, raw.length - (info?.token || '').length);
    const end = info?.end ?? raw.length;
    editor.textContent = `${raw.slice(0, start)}${next}${raw.slice(end)}`;
    placeEditorCaret(start + next.length);
  }

  function hidePathSuggest() {
    pathSuggestHits = [];
    pathSuggestIdx = 0;
    const box = getPathSuggest();
    if (!box) return;
    box.hidden = true;
    box.innerHTML = '';
  }

  function renderPathSuggest(info) {
    const box = getPathSuggest();
    if (!box) return;
    if (!pathSuggestHits.length) {
      box.innerHTML = '<div class="chat-path-empty">No matching files</div>';
      box.hidden = false;
      return;
    }
    box.innerHTML = pathSuggestHits.map((f, i) => {
      const label = `${f.name}${f.is_dir ? '/' : ''}`;
      const icon = f.is_dir ? '📁' : '📄';
      const on = i === pathSuggestIdx ? ' is-active' : '';
      return `<button type="button" class="chat-path-hit${on}" data-i="${i}"><span class="chat-path-hit-icon">${icon}</span><span class="truncate">${esc(label)}</span></button>`;
    }).join('');
    box.hidden = false;
    box.querySelectorAll('[data-i]').forEach((btn) => {
      btn.addEventListener('mousedown', (e) => e.preventDefault());
      btn.addEventListener('click', () => {
        const i = Number(btn.dataset.i);
        const hit = pathSuggestHits[i];
        if (!hit) return;
        if (hit.is_dir) {
          pathSuggestIdx = i;
          box.querySelectorAll('.chat-path-hit').forEach((b) => {
            b.classList.toggle('is-active', Number(b.dataset.i) === i);
          });
          return;
        }
        applyPathHit(info, hit).catch((err) => alert(err.message));
      });
      btn.addEventListener('dblclick', () => {
        const hit = pathSuggestHits[Number(btn.dataset.i)];
        if (hit?.is_dir) applyPathHit(info, hit).catch((err) => alert(err.message));
      });
    });
    box.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
  }

  async function applyPathHit(info, hit) {
    const parsed = parsePathInput(info.token);
    const dir = normalizeRel(parsed.dir);
    if (hit.is_dir) {
      replaceCaretToken(info, restyleCompleted(info.token, formatRelPath(dir, hit.name, true)));
      const next = caretPathToken();
      if (next) await updateComposerPathSuggest(next);
      return;
    }
    await stagePathHit(info, hit);
  }

  async function stagePathHit(info, hit) {
    if (!hit) return;
    const parsed = parsePathInput(info.token);
    const dir = normalizeRel(parsed.dir);
    const full = joinRel(dir, hit.name);
    stagePath(full, { isDir: !!hit.is_dir });
    replaceCaretToken(info, restyleCompleted(info.token, formatRelPath(dir, '', true)));
    const next = caretPathToken();
    if (next && looksLikePath(next.token)) await updateComposerPathSuggest(next);
    else hidePathSuggest();
    editor?.focus();
  }

  let pathSuggestTimer = null;
  async function updateComposerPathSuggest(info) {
    clearTimeout(pathSuggestTimer);
    await new Promise((resolve) => {
      pathSuggestTimer = setTimeout(resolve, 40);
    });
    const latest = caretPathToken();
    if (!latest || !looksLikePath(latest.token)) {
      hidePathSuggest();
      return;
    }
    const result = await bashComplete(latest.token);
    pathSuggestHits = result.hits.slice(0, 24);
    pathSuggestIdx = 0;
    renderPathSuggest(latest);
  }

  async function completeComposerPath(info, shift) {
    const result = await bashComplete(info.token);
    if (!result.hits.length) {
      pathSuggestHits = [];
      pathSuggestIdx = 0;
      renderPathSuggest(info);
      return;
    }
    if (result.unique) {
      await applyPathHit(info, result.hits[0]);
      return;
    }
    const shown = restyleCompleted(info.token, result.value);
    if (sameBrowseValue(info.token, result.value)) {
      if (!pathSuggestHits.length) pathSuggestHits = result.hits.slice(0, 12);
      const delta = shift ? -1 : 1;
      pathSuggestIdx = (pathSuggestIdx + delta + pathSuggestHits.length) % pathSuggestHits.length;
      const hit = pathSuggestHits[pathSuggestIdx];
      replaceCaretToken(info, restyleCompleted(info.token, formatRelPath(result.dir, hit.name, false)));
      renderPathSuggest(caretPathToken() || info);
      return;
    }
    tabCycle = { key: '', i: -1 };
    replaceCaretToken(info, shown);
    const next = caretPathToken();
    pathSuggestHits = result.hits.slice(0, 12);
    pathSuggestIdx = 0;
    if (next) renderPathSuggest(next);
  }

  fileInput?.addEventListener('change', () => attachUpload(fileInput));
  gifInput?.addEventListener('change', () => attachUpload(gifInput));
  browseBtn?.addEventListener('click', () => {
    const gate = canAttachFiles();
    if (!gate.ok) {
      setE2eStatus(gate.reason);
      return;
    }
    filePicker.open();
  });
  startBtn?.addEventListener('click', () => startConversation().catch((err) => alert(err.message)));
  newBtn?.addEventListener('click', () => setNewPanel(newPanel?.hidden !== false));
  document.querySelectorAll('.chat-new-tab').forEach((btn) => {
    btn.addEventListener('click', () => setNewMode(btn.dataset.new));
  });
  groupCreateBtn?.addEventListener('click', () => createGroup().catch((e) => alert(e.message)));
  newUserInput?.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    if (newMode === 'group') addGroupPick(newUserInput.value);
    else startConversation().catch((err) => alert(err.message));
  });
  memberUserInput?.addEventListener('input', () => {
    clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(() => {
      searchUsers(memberUserInput.value, memberSuggest, (name) => {
        addMember(activeConv(), name).catch((err) => alert(err.message));
      });
    }, 250);
  });
  qs('#chatMemberCancel')?.addEventListener('click', () => memberModal?.close());
  qs('#chatMemberAdd')?.addEventListener('click', () => {
    const conv = activeConv();
    if (conv) addMember(conv).catch((e) => alert(e.message));
  });
  memberUserInput?.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      const conv = activeConv();
      if (conv) addMember(conv).catch((err) => alert(err.message));
    }
  });
  messagesEl?.addEventListener('scroll', () => {
    if (messagesEl.scrollTop < 64) loadOlderMessages();
  });
  qs('#chatReplyCancel')?.addEventListener('click', clearReply);
  qs('#chatForwardCancel')?.addEventListener('click', () => qs('#chatForwardModal')?.close());
  qs('#chatForwardSend')?.addEventListener('click', () => sendForward().catch((e) => alert(e.message)));
  qs('#chatLightboxClose')?.addEventListener('click', () => qs('#chatLightbox')?.close());

  bindDropZone(qs('.chat-thread-panel'), {
    canAttach: canAttachFiles,
    onFiles(files) {
      const before = pending.length;
      files.forEach((file) => attachFileBlob(file));
      if (pending.length > before) {
        setE2eStatus('');
        editor?.focus();
      }
    },
    onBlocked: setE2eStatus,
  });
  editor?.addEventListener('paste', (e) => {
    const files = clipboardFiles(e.clipboardData);
    if (!files.length) return;
    e.preventDefault();
    const gate = canAttachFiles();
    if (!gate.ok) {
      setE2eStatus(gate.reason);
      return;
    }
    files.forEach((file) => attachFileBlob(file));
    if (files.length) {
      setE2eStatus('');
      editor?.focus();
    }
  });

  async function updateNavBadge(fallbackTotal) {
    const badge = document.getElementById('chatUnreadBadge');
    if (!badge) return;
    if (typeof fallbackTotal === 'number') {
      if (fallbackTotal > 0) {
        badge.textContent = fallbackTotal > 99 ? '99+' : String(fallbackTotal);
        badge.classList.remove('hidden');
      } else badge.classList.add('hidden');
      return;
    }
    try {
      const data = await api('/api/chat/unread');
      updateNavBadge(data.total || 0);
    } catch (err) { console.debug('nav badge unread fetch failed', err); }
  }

  globalThis.AirdChatPage = {
    refreshList: () => renderConvList(),
    showList: () => setMobileView('list'),
    enterEnhancedSecure() {
      enhancedSecureView = true;
      composer?.classList.remove('hidden');
      composer?.classList.add('chat-composer--enhanced');
      qs('#chatPinBar')?.setAttribute('hidden', '');
      qs('#chatReplyBar')?.classList.add('hidden');
      setMobileView('thread');
      setPollEnabled(false);
    },
    leaveEnhancedSecure() {
      enhancedSecureView = false;
      composer?.classList.remove('chat-composer--enhanced');
    },
  };

  convList?.addEventListener('click', (e) => {
    const peerBtn = e.target.closest('[data-enhanced-peer]');
    if (peerBtn) {
      e.preventDefault();
      e.stopPropagation();
      peerBtn.closest('details')?.removeAttribute('open');
      window.AirdEnhancedSecure?.start(peerBtn.dataset.enhancedPeer);
      return;
    }
    const acceptBtn = e.target.closest('[data-enhanced-accept]');
    if (acceptBtn) {
      e.preventDefault();
      e.stopPropagation();
      window.AirdEnhancedSecure?.accept(acceptBtn.dataset.enhancedAccept);
      return;
    }
    const declineBtn = e.target.closest('[data-enhanced-decline]');
    if (declineBtn) {
      e.preventDefault();
      e.stopPropagation();
      window.AirdEnhancedSecure?.decline(declineBtn.dataset.enhancedDecline);
      return;
    }
    const roomEl = e.target.closest('[data-enhanced-room]');
    if (roomEl) {
      e.preventDefault();
      window.AirdEnhancedSecure?.show(roomEl.dataset.enhancedRoom);
    }
  });

  async function respondRequest(action) {
    const id = activeConvId;
    if (!id) return;
    await api(`/api/chat/conversations/${id}/request`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    });
    if (action === 'decline') {
      activeConvId = '';
      hideRequest();
      composer?.classList.add('hidden');
      setThreadHeader(null);
      if (messagesEl) messagesEl.innerHTML = '';
      history.replaceState(null, '', location.pathname);
      await loadConversations();
      return;
    }
    await loadConversations();
    await openConversation(id);
  }

  document.addEventListener('visibilitychange', markLatestVisibleRead);

  function boot() {
    qs('#chatRequestAccept')?.addEventListener('click', () => {
      respondRequest('accept').catch((err) => setE2eStatus(err.message || 'Could not accept'));
    });
    qs('#chatRequestDecline')?.addEventListener('click', () => {
      respondRequest('decline').catch((err) => setE2eStatus(err.message || 'Could not decline'));
    });
    globalThis.AirdFolderPicker?.init?.();
    const WS = window.AirdChatWS;
    if (WS) {
      WS.connect();
      WS.subscribe(handleWsEvent);
    }
    const start = async () => {
      if (E2E && me) {
        try { await E2E.init(me, api); } catch (err) { console.warn('chat e2e', err); }
      }
      await loadConversations();
      const initialConv = new URLSearchParams(location.search).get('c');
      if (pendingBrowseAttachQueue.length && !initialConv) {
        const first = pendingBrowseAttachQueue[0];
        const label = first.path.split('/').pop() || first.path;
        const extra = pendingBrowseAttachQueue.length > 1
          ? ` (+${pendingBrowseAttachQueue.length - 1} more)` : '';
        setE2eStatus('Pick a conversation to attach ' + label + extra);
      }
      if (initialConv) await openConversation(initialConv);
    };
    start().catch((err) => {
      if (messagesEl) messagesEl.innerHTML = `<p class="text-error">${esc(err.message)}</p>`;
    });
  }

  boot();
})();
