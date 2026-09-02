(function () {
  'use strict';

  const REACTIONS = ['👍', '❤️', '😂', '😮', '😢', '🎉', '🔥', '👀'];
  const IMAGE_EXTS = ['jpg', 'jpeg', 'jpe', 'jif', 'jfif', 'png', 'gif', 'bmp', 'webp', 'tiff', 'tif', 'svg', 'ico', 'avif', 'heic', 'heif', 'dng', 'cr2', 'nef', 'arw', 'orf', 'rw2', 'raf', 'raw'];

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
  const browseBtn = qs('#chatBrowseBtn');
  const replyBar = qs('#chatReplyBar');
  const replyPreview = qs('#chatReplyPreview');
  const searchInput = qs('#chatSearch');
  const searchResults = qs('#chatSearchResults');
  const mentionSuggest = qs('#chatMentionSuggest');

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
  let conversations = [];
  let pins = [];
  let receipts = [];
  let replyToId = null;
  let browsePath = '';
  let browseHits = [];
  let browseActiveName = '';
  let dirCache = new Map();
  let tabCycle = { key: '', i: -1 };
  let browseInputTimer = null;
  let pathSuggestHits = [];
  let pathSuggestIdx = 0;
  let pending = [];
  let userSearchTimer = null;
  let searchTimer = null;
  let pollTimer = null;
  let loadGen = 0;
  let typingTimer = null;
  let forwardMsgId = null;
  const presence = {};

  function lastMessageId() {
    const nodes = messagesEl?.querySelectorAll('[data-msg-id]');
    if (!nodes?.length) return '';
    return nodes[nodes.length - 1].dataset.msgId || '';
  }

  async function syncNewMessages() {
    if (!activeConvId || !messagesEl) return;
    const afterId = lastMessageId();
    const data = await api(
      `/api/chat/conversations/${activeConvId}/messages${afterId ? `?after_id=${encodeURIComponent(afterId)}` : ''}`,
    );
    let added = false;
    (data.messages || []).forEach((m) => {
      if (!messagesEl.querySelector(`[data-msg-id="${cssEscape(m.id)}"]`)) {
        appendMessage(m);
        added = true;
      }
    });
    if (data.pins) {
      pins = data.pins;
      renderPins();
    }
    if (data.receipts) receipts = data.receipts;
    if (added) {
      const last = lastMessageId();
      if (last) markRead({ id: last });
    }
  }

  function cssEscape(s) {
    return String(s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  }

  function setPollEnabled(on) {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
    if (!on || !activeConvId) return;
    pollTimer = setInterval(() => {
      if (window.AirdChatWS?.isOpen()) return;
      syncNewMessages().catch(() => { /* */ });
    }, 4000);
  }

  function xsrf() {
    const m = document.cookie.match(/(?:^|;\s*)_xsrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: {
        Accept: 'application/json',
        ...(opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(opts.method && opts.method !== 'GET' ? { 'X-XSRFToken': xsrf() } : {}),
      },
      ...opts,
    });
    const text = await res.text();
    let json = {};
    try { json = JSON.parse(text); } catch { json = { error: text }; }
    if (!res.ok) throw new Error(json.error || text || `HTTP ${res.status}`);
    return json;
  }

  const E2E = window.AirdChatE2E;
  const e2ePlain = new Map();

  function memberNames(conv) {
    return (conv?.members || []).map((m) => m.username).filter(Boolean);
  }

  async function e2eReady(convId) {
    if (!E2E?.ready()) return false;
    const conv = conversations.find((c) => c.id === convId);
    await E2E.ensureConversation(api, convId, memberNames(conv));
    return true;
  }

  async function decryptPayload(convId, payload) {
    if (!payload || !E2E) return '';
    try {
      const html = await E2E.decrypt(convId, payload);
      return E2E.sanitizeHtml(html);
    } catch (_) {
      return '<em class="chat-e2e-fail">Unable to decrypt on this device</em>';
    }
  }

  async function prepareMessage(msg) {
    if (!msg) return msg;
    const blob = msg.metadata && msg.metadata.e2e;
    if (msg.e2e || blob) {
      const html = await decryptPayload(msg.conversation_id, blob);
      msg.body = html;
      msg._e2e = true;
      e2ePlain.set(msg.id, html);
    }
    return msg;
  }

  function extractMentions(html, conv) {
    const names = memberNames(conv).filter((n) => n !== me);
    if (E2E?.extractMentions) return E2E.extractMentions(html, names);
    return [];
  }

  function esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function avatarHue(name) {
    let h = 0;
    const s = String(name || '');
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return [250, 280, 200, 160, 30, 340, 220][h % 7];
  }

  function initials(name) {
    const s = String(name || '?').trim();
    if (!s) return '?';
    return s.slice(0, 2).toUpperCase();
  }

  function avatarHtml(name, extraClass) {
    const hue = avatarHue(name);
    return `<span class="chat-avatar${extraClass ? ` ${extraClass}` : ''}" style="--chat-hue:${hue}" aria-hidden="true">${esc(initials(name))}</span>`;
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
    return !!(conv && (conv.members || []).some((m) => m.username === username && m.online));
  }

  function setThreadHeader(conv) {
    if (!header) return;
    if (!conv) {
      header.innerHTML = '<div class="chat-empty chat-empty--head"><p>Select a conversation</p></div>';
      return;
    }
    const title = convTitle(conv);
    const muted = conv.muted ? 'Muted' : '';
    let sub;
    if (conv.kind === 'group') {
      const n = (conv.members || []).length;
      const on = (conv.members || []).filter((m) => m.online || isOnlineUser(m.username)).length;
      sub = `${n} members · ${on} online${muted ? ' · muted' : ''}`;
    } else {
      const peer = conv.peer_username;
      const on = isOnlineUser(peer);
      sub = `${on ? 'Online' : 'Direct message'}${muted ? ' · muted' : ''}`;
    }
    const dot = conv.kind === 'dm' && isOnlineUser(conv.peer_username)
      ? '<span class="chat-online-dot" title="Online"></span>'
      : '';
      const lock = '<span class="chat-e2e-lock" title="End-to-end encrypted">🔒</span>';
    header.innerHTML = `<span class="chat-avatar-wrap">${avatarHtml(title)}${dot}</span><div class="chat-thread-head-main"><div class="chat-thread-title">${esc(title)} ${lock}</div><div class="chat-thread-sub">${esc(sub)}</div></div>
      <div class="chat-thread-actions">
        <button type="button" class="chat-icon-btn" id="chatMuteBtn" title="${conv.muted ? 'Unmute' : 'Mute'}">${conv.muted ? 'Unmute' : 'Mute'}</button>
        ${conv.kind === 'group' ? '<button type="button" class="chat-icon-btn" id="chatAddMemberBtn">Add</button>' : ''}
      </div>`;
    qs('#chatMuteBtn')?.addEventListener('click', () => toggleMute(conv).catch((e) => alert(e.message)));
    qs('#chatAddMemberBtn')?.addEventListener('click', () => addMember(conv).catch((e) => alert(e.message)));
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

  async function addMember(conv) {
    const username = prompt('Username to add');
    if (!username) return;
    await api(`/api/chat/conversations/${conv.id}/members`, {
      method: 'POST',
      body: JSON.stringify({ username: username.trim() }),
    });
    await loadConversations();
    const updated = conversations.find((c) => c.id === conv.id);
    setThreadHeader(updated || conv);
    try { await e2eReady(conv.id); } catch (_) { /* */ }
  }

  function showEmptyThread() {
    if (!messagesEl) return;
    messagesEl.innerHTML = '<div class="chat-empty"><div class="chat-empty-icon" aria-hidden="true">💬</div><p>No messages yet. Say hello!</p></div>';
  }

  function clearMessagesView() {
    if (!messagesEl) return;
    messagesEl.innerHTML = '';
  }

  function renderConvList() {
    if (!convList) return;
    if (!conversations.length) {
      convList.innerHTML = '<li class="p-3 text-sm opacity-50 text-center">No conversations yet</li>';
      return;
    }
    convList.innerHTML = conversations.map((c) => {
      const active = c.id === activeConvId ? ' active' : '';
      const badge = c.unread ? `<span class="badge badge-primary badge-xs">${c.unread}</span>` : '';
      const preview = esc(c.last_preview || 'No messages yet');
      const title = convTitle(c);
      const on = c.kind === 'dm' && isOnlineUser(c.peer_username);
      const dot = on ? '<span class="chat-online-dot chat-online-dot--list"></span>' : '';
      const mute = c.muted ? ' <span class="opacity-40">🔇</span>' : '';
      return `<li><a href="#" class="chat-conv-item${active}" data-id="${esc(c.id)}">${avatarHtml(title)}${dot}<span class="chat-conv-main"><span class="chat-conv-top"><span class="chat-conv-name">${esc(title)}${mute}</span>${badge}</span><span class="chat-conv-preview">${preview}</span></span></a></li>`;
    }).join('');
    convList.querySelectorAll('.chat-conv-item').forEach((el) => {
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
      if (!c.last_e2e || !E2E?.ready()) return;
      try {
        await e2eReady(c.id);
        const html = await decryptPayload(c.id, c.last_e2e);
        const tmp = document.createElement('div');
        tmp.innerHTML = html;
        const text = (tmp.innerText || '').trim();
        if (text) c.last_preview = text.slice(0, 80);
      } catch (_) { /* keep placeholder */ }
    }));
    renderConvList();
    updateNavBadge();
  }

  function attOpenUrl(att, msg, index) {
    if (att && att.share_url) return att.share_url;
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
    return (att && att.media_kind) === 'folder';
  }

  function isImageAtt(att, msg) {
    if (isFolderAtt(att)) return false;
    const kind = (att && att.media_kind) || (msg && msg.msg_type === 'gif' ? 'gif' : '');
    if (kind === 'image' || kind === 'gif') return true;
    const name = String((att && att.original_name) || '').toLowerCase();
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
    const emojis = REACTIONS.map((e) =>
      `<button type="button" class="chat-react-emoji" data-emoji="${e}" data-id="${esc(msg.id)}" title="${e}">${e}</button>`
    ).join('');
    return `<div class="chat-hover-bar" role="toolbar" aria-label="Message actions">
      ${emojis}
      <details class="chat-msg-more">
        <summary aria-label="More">⋯</summary>
        <div class="chat-msg-more-list">
          <button type="button" data-act="reply" data-id="${esc(msg.id)}">Reply</button>
          <button type="button" data-act="forward" data-id="${esc(msg.id)}">Forward</button>
          <button type="button" data-act="pin" data-id="${esc(msg.id)}">Pin</button>
          ${msg._mine ? `<button type="button" data-act="edit" data-id="${esc(msg.id)}">Edit</button><button type="button" data-act="delete" data-id="${esc(msg.id)}">Delete</button>` : ''}
        </div>
      </details>
    </div>`;
  }

  function seenHtml(msg) {
    if (!msg._mine || !receipts.length) return '';
    const conv = activeConv();
    const seen = receipts.filter((r) => r.last_read_id === msg.id || r.last_read_at >= msg.created_at);
    if (!seen.length) return '';
    if (conv?.kind === 'group') {
      return `<div class="chat-seen">Seen by ${esc(seen.map((s) => s.username).join(', '))}</div>`;
    }
    return '<div class="chat-seen">Seen</div>';
  }

  function messageHtml(msg) {
    const rowClass = msg._mine ? 'chat-msg-row--mine' : 'chat-msg-row--theirs';
    const bubbleClass = msg._mine ? 'aird-chat-bubble--mine' : 'aird-chat-bubble--theirs';
    let body = '';
    if (msg.forwarded_from || (msg.metadata && msg.metadata.forwarded_from)) {
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
    return `<div class="chat-msg-row ${rowClass}" data-msg-id="${esc(msg.id)}">${avatarHtml(msg.sender_username)}<div class="chat-msg-stack">${nameLine}<div class="chat-msg-main">${hoverBarHtml(msg)}<div class="aird-chat-bubble ${bubbleClass}"><div class="aird-chat-bubble-body">${body}</div>${reactionsHtml(msg)}</div></div><div class="chat-msg-time">${esc(formatTime(msg.created_at))}</div>${seenHtml(msg)}</div></div>`;
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
    const url = (imgEl && (imgEl.dataset.full || imgEl.src)) || '';
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
        if (!E2E?.ready()) throw new Error('Encrypted chat is not ready on this device');
        const e2e = await (async () => {
          await e2eReady(activeConvId);
          return E2E.encrypt(activeConvId, E2E.sanitizeHtml(textEl.innerHTML || next));
        })();
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
        alert(err.message);
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
      if (!E2E?.ready()) throw new Error('Encrypted chat is not ready on this device');
      if (plain.includes('chat-e2e-fail')) throw new Error('Cannot forward a message that did not decrypt on this device');
      const html = E2E.sanitizeHtml(plain);
      for (const id of ids) {
        await e2eReady(id);
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

  async function openConversation(convId) {
    if (activeConvId && convId !== activeConvId) {
      pending = [];
      renderPending();
    }
    const gen = ++loadGen;
    activeConvId = convId;
    const conv = conversations.find((c) => c.id === convId);
    setThreadHeader(conv || null);
    composer.classList.remove('hidden');
    messagesEl.innerHTML = '<div class="chat-empty"><p>Loading…</p></div>';
    renderConvList();
    setPollEnabled(true);
    try { await e2eReady(convId); } catch (_) { /* */ }
    const data = await api(`/api/chat/conversations/${convId}/messages`);
    if (gen !== loadGen) return;
    clearMessagesView();
    pins = data.pins || [];
    receipts = data.receipts || [];
    renderPins();
    const msgs = data.messages || [];
    for (const m of msgs) await prepareMessage(m);
    if (!msgs.length) showEmptyThread();
    else msgs.forEach((m) => {
      tagMine(m);
      messagesEl.insertAdjacentHTML('beforeend', messageHtml(m));
      bindMessageUi(messagesEl.lastElementChild);
    });
    if (msgs.length) messagesEl.scrollTop = messagesEl.scrollHeight;
    const last = msgs.slice(-1)[0];
    if (last) markRead(last);
    const params = new URLSearchParams(location.search);
    params.set('c', convId);
    history.replaceState(null, '', `${location.pathname}?${params}`);
  }

  function markRead(msg) {
    const WS = window.AirdChatWS;
    if (!WS?.isOpen() || !activeConvId) return;
    WS.send({
      type: 'chat_read',
      conversation_id: activeConvId,
      message_id: msg.id,
    });
    loadConversations();
  }

  function handleWsEvent(data) {
    if (!data?.type) return;
    if (data.type === 'chat_message' && data.message) {
      const msg = tagMine({ ...data.message });
      if (msg.conversation_id === activeConvId) {
        if (!messagesEl?.querySelector(`[data-msg-id="${cssEscape(msg.id)}"]`)) {
          appendMessage(msg).then(() => markRead(msg)).catch(() => {});
        }
      }
      loadConversations();
      updateNavBadge();
    } else if (data.type === 'chat_message_deleted') {
      if (data.conversation_id === activeConvId) removeMessageEl(data.message_id);
      loadConversations();
      updateNavBadge();
    } else if (data.type === 'chat_message_edited' && data.message) {
      if (data.message.conversation_id === activeConvId) {
        replaceMessage(data.message).catch(() => {});
      }
    } else if (data.type === 'chat_reaction') {
      if (data.conversation_id === activeConvId) applyReactions(data.message_id, data.reactions || []);
    } else if (data.type === 'chat_receipt') {
      if (data.conversation_id === activeConvId) {
        receipts = receipts.filter((r) => r.username !== data.username);
        receipts.push({ username: data.username, last_read_id: data.last_read_id });
      }
    } else if (data.type === 'chat_pin' || data.type === 'chat_unpin') {
      if (data.conversation_id === activeConvId) {
        pins = data.pins || [];
        renderPins();
      }
    } else if (data.type === 'chat_presence') {
      presence[data.username] = !!data.online;
      renderConvList();
      if (activeConvId) setThreadHeader(activeConv());
    } else if (data.type === 'chat_notify') {
      updateNavBadge(data.unread_total);
      if (data.conversation_id === activeConvId) syncNewMessages().catch(() => { /* */ });
      loadConversations();
    } else if (data.type === 'chat_ws_close') {
      setPollEnabled(true);
    } else if (data.type === 'chat_ws_open') {
      setPollEnabled(!!activeConvId);
    } else if (data.type === 'chat_typing' && data.conversation_id === activeConvId) {
      const sub = header?.querySelector('.chat-thread-sub');
      if (sub && data.username !== me) {
        const prev = sub.dataset.base || sub.textContent;
        sub.dataset.base = prev;
        sub.textContent = `${data.username} is typing…`;
        clearTimeout(typingTimer);
        typingTimer = setTimeout(() => { sub.textContent = sub.dataset.base || prev; }, 1500);
      }
    }
  }

  async function startConversation(username) {
    const name = (username || newUserInput?.value || '').trim();
    if (!name) return;
    hideUserSuggest();
    const data = await api('/api/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ username: name }),
    });
    if (newUserInput) newUserInput.value = name;
    await loadConversations();
    const id = data.conversation?.id;
    if (id) openConversation(id);
  }

  async function createGroup() {
    const title = qs('#chatGroupTitle')?.value.trim() || 'Group';
    const members = (qs('#chatGroupMembers')?.value || '').split(',').map((s) => s.trim()).filter(Boolean);
    const data = await api('/api/chat/conversations', {
      method: 'POST',
      body: JSON.stringify({ title, members }),
    });
    qs('#chatGroupModal')?.close();
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

  async function searchUsers(q) {
    if (!userSuggest) return;
    const query = (q || '').trim();
    if (query.length < 2) {
      hideUserSuggest();
      return;
    }
    try {
      const data = await api(`/api/users/search?q=${encodeURIComponent(query)}`);
      const users = (data.users || []).filter((u) => u.username !== me);
      if (!users.length) {
        hideUserSuggest();
        return;
      }
      userSuggest.innerHTML = users.map((u) =>
        `<button type="button" class="w-full text-left px-3 py-2 hover:bg-base-200" data-user="${esc(u.username)}">${esc(u.username)}</button>`
      ).join('');
      userSuggest.classList.remove('hidden');
      userSuggest.querySelectorAll('[data-user]').forEach((btn) => {
        btn.addEventListener('click', () => {
          if (newUserInput) newUserInput.value = btn.dataset.user || '';
          hideUserSuggest();
        });
      });
    } catch (_) {
      hideUserSuggest();
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
    searchTimer = setTimeout(() => runSearch(searchInput.value).catch(() => { /* */ }), 280);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.chat-new-user-wrap')) hideUserSuggest();
  });

  const ENTER_SEND_KEY = 'aird-chat-enter-to-send';
  const enterToSendEl = qs('#chatEnterToSend');

  function enterToSendEnabled() {
    if (enterToSendEl) return enterToSendEl.checked;
    return true;
  }

  if (enterToSendEl) {
    const stored = localStorage.getItem(ENTER_SEND_KEY);
    enterToSendEl.checked = stored === null ? true : stored === '1';
    enterToSendEl.addEventListener('change', () => {
      localStorage.setItem(ENTER_SEND_KEY, enterToSendEl.checked ? '1' : '0');
    });
  }

  async function sendEditorMessage() {
    if (!activeConvId || !editor) return;
    const hasText = (editor.innerText || '').replace(/\u00a0/g, ' ').trim().length > 0;
    if (!hasText && !pending.length) return;
    const body = editor.innerHTML.trim();
    const reply = replyToId;
    let e2e = null;
    let mentions = [];
    if (hasText) {
      if (!E2E?.ready()) {
        alert('Encrypted chat is not ready on this device.');
        return;
      }
      try {
        await e2eReady(activeConvId);
        const html = E2E.sanitizeHtml(body);
        e2e = await E2E.encrypt(activeConvId, html);
        mentions = extractMentions(html, activeConv());
      } catch (err) {
        alert(err.message);
        return;
      }
    }
    const staged = pending.slice();
    pending = [];
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
        updateComposerPathSuggest(tokenInfo).catch(() => {});
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
              editor.innerHTML = `${editor.innerHTML.replace(/@[\w.\-@]*$/, '')}@${btn.dataset.user}&nbsp;`;
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

  document.querySelectorAll('.chat-toolbar [data-cmd], [data-cmd]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      if (cmd === 'link') {
        const url = prompt('URL');
        if (url) document.execCommand('createLink', false, url);
      } else if (cmd) {
        document.execCommand(cmd, false, null);
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
    document.execCommand('insertText', false, ch);
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
    el.innerHTML = pending.map((p) =>
      `<span class="chat-pending-chip${p.isDir ? ' is-folder' : ''}"><span class="truncate" title="${esc(p.path || p.name)}">${esc(p.name)}</span><button type="button" data-rm="${esc(p.id)}" aria-label="Remove ${esc(p.name)}">×</button></span>`
    ).join('');
    el.querySelectorAll('[data-rm]').forEach((btn) => {
      btn.addEventListener('click', () => {
        pending = pending.filter((x) => x.id !== btn.dataset.rm);
        renderPending();
      });
    });
  }

  function stagePath(relativePath, opts = {}) {
    const path = normalizeRel(relativePath);
    if (!path) return;
    if (pending.some((p) => p.path === path)) return;
    if (pending.length >= 10) {
      alert('Up to 10 files per message');
      return;
    }
    const isDir = !!opts.isDir;
    const base = path.split('/').pop();
    pending.push({ id: `p:${path}`, name: isDir ? `${base}/` : base, path, isDir });
    renderPending();
  }

  function stageBlob(file) {
    if (!file) return;
    if (pending.length >= 10) {
      alert('Up to 10 files per message');
      return;
    }
    pending.push({ id: `b:${Date.now()}:${file.name}`, name: file.name, file });
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

  function trimBrowsePath(path) {
    let s = String(path || '').trim().replace(/\\/g, '/');
    while (s.startsWith('/')) s = s.slice(1);
    while (s.endsWith('/')) s = s.slice(0, -1);
    return s;
  }

  function normalizeRel(path) {
    const parts = [];
    for (const seg of trimBrowsePath(path).split('/')) {
      if (!seg || seg === '.') continue;
      if (seg === '..') {
        parts.pop();
        continue;
      }
      parts.push(seg);
    }
    return parts.join('/');
  }

  function joinRel(dir, name) {
    const d = normalizeRel(dir);
    const n = String(name || '').replace(/^\/+|\/+$/g, '');
    if (!d) return n;
    if (!n) return d;
    return `${d}/${n}`;
  }

  function parsePathInput(raw) {
    let s = String(raw || '').replace(/\\/g, '/').trim();
    if (s.startsWith('~/')) s = s.slice(2);
    while (s.startsWith('/')) s = s.slice(1);
    const dirEnds = !s || s.endsWith('/');
    const parts = s.split('/').filter(Boolean);
    if (dirEnds) return { dir: parts.join('/'), prefix: '' };
    return { dir: parts.slice(0, -1).join('/'), prefix: parts[parts.length - 1] || '' };
  }

  function formatRelPath(dir, name, isDir) {
    const body = joinRel(dir, name);
    if (!body) return '/';
    return `/${body}${isDir ? '/' : ''}`;
  }

  function sameBrowseValue(a, b) {
    const norm = (v) => {
      const s = String(v || '').replace(/\\/g, '/').trim();
      const slash = s.endsWith('/') && s !== '/';
      const rel = normalizeRel(s);
      return `${rel}${slash ? '/' : ''}`;
    };
    return norm(a) === norm(b);
  }

  function restyleCompleted(token, value) {
    const body = String(value || '').replace(/^\/+/, '');
    if (token.startsWith('./')) return `./${body}`;
    if (token.startsWith('/')) return `/${body}`;
    return body;
  }

  function commonPrefix(names) {
    if (!names.length) return '';
    let p = names[0];
    for (const n of names.slice(1)) {
      let i = 0;
      const max = Math.min(p.length, n.length);
      while (i < max && p[i].toLowerCase() === n[i].toLowerCase()) i += 1;
      p = p.slice(0, i);
    }
    return p;
  }

  function matchFiles(files, prefix) {
    const q = String(prefix || '').toLowerCase();
    return files
      .filter((f) => !q || f.name.toLowerCase().startsWith(q))
      .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
  }

  function looksLikePath(token) {
    const t = String(token || '').trim();
    if (!t || t.startsWith('@') || /^[a-z]+:\/\//i.test(t) || t.startsWith('mailto:')) return false;
    if (t.includes('/') || t.includes('\\')) return true;
    if (t.startsWith('./') || t.startsWith('../')) return true;
    if (/^\.[A-Za-z0-9._-]+/.test(t)) return true;
    return false;
  }

  async function listDir(relPath) {
    const key = normalizeRel(relPath);
    const hit = dirCache.get(key);
    if (hit && Date.now() - hit.ts < 8000) return hit.files;
    const url = key ? `/api/files/${key.split('/').map(encodeURIComponent).join('/')}` : '/api/files/';
    try {
      const data = await api(url);
      const files = data.files || [];
      dirCache.set(key, { files, ts: Date.now() });
      return files;
    } catch (_) {
      return [];
    }
  }

  async function bashComplete(raw) {
    const parsed = parsePathInput(raw);
    const dir = normalizeRel(parsed.dir);
    const files = await listDir(dir);
    const hits = matchFiles(files, parsed.prefix);
    if (!hits.length) return { dir, prefix: parsed.prefix, hits, value: raw, unique: false };
    const labels = hits.map((f) => `${f.name}${f.is_dir ? '/' : ''}`);
    let filled = parsed.prefix;
    if (hits.length === 1) filled = labels[0];
    else {
      const common = commonPrefix(labels);
      if (common.length > parsed.prefix.length) filled = common;
    }
    const dirSlash = filled.endsWith('/');
    const body = joinRel(dir, filled.replace(/\/+$/, ''));
    const value = body ? `/${body}${dirSlash ? '/' : ''}` : '/';
    return { dir, prefix: parsed.prefix, hits, value, unique: hits.length === 1 };
  }

  function setBrowsePathValue(path, isDir) {
    if (!browsePathEl) return;
    const rel = normalizeRel(path);
    browsePathEl.value = rel ? `/${rel}${isDir ? '/' : ''}` : '/';
  }

  function renderBrowseList(files, dir, activeName) {
    if (!browseList) return;
    browseHits = files.slice();
    if (!files.length) {
      browseActiveName = '';
      browseList.innerHTML = '<p class="p-4 text-sm opacity-50 text-center">No matches</p>';
      return;
    }
    if (!activeName) activeName = files[0].name;
    browseActiveName = activeName;
    browseList.innerHTML = files.map((f) => {
      const full = joinRel(dir, f.name);
      const icon = f.is_dir ? '📁' : '📄';
      const active = activeName && f.name === activeName ? ' is-active' : '';
      const share = f.is_dir
        ? `<button type="button" class="chat-browse-share" data-path="${esc(full)}" data-dir="1">Share</button>`
        : '';
      return `<div class="chat-browse-row flex items-center gap-2 px-3 py-2${active}" data-action="${f.is_dir ? 'dir' : 'file'}" data-path="${esc(full)}"><span>${icon}</span><span class="truncate">${esc(f.name)}${f.is_dir ? '/' : ''}</span>${share}</div>`;
    }).join('');
    browseList.querySelectorAll('.chat-browse-row').forEach((row) => {
      row.addEventListener('click', (e) => {
        if (e.target.closest('.chat-browse-share')) return;
        const p = row.dataset.path || '';
        const name = p.split('/').filter(Boolean).pop() || '';
        if (row.dataset.action === 'dir') {
          browseActiveName = name;
          browseList.querySelectorAll('.chat-browse-row').forEach((r) => {
            r.classList.toggle('is-active', r === row);
          });
          if (browsePathEl) browsePathEl.value = formatRelPath(browsePath, name, false);
          return;
        }
        attachFromBrowse(p, false);
      });
      row.addEventListener('dblclick', (e) => {
        if (e.target.closest('.chat-browse-share')) return;
        if (row.dataset.action === 'dir') {
          loadBrowseDir(row.dataset.path || '').catch((err) => alert(err.message));
        }
      });
    });
    browseList.querySelectorAll('.chat-browse-share').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        attachFromBrowse(btn.dataset.path || '', true);
      });
    });
    browseList.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
  }

  async function loadBrowseDir(path, opts = {}) {
    browsePath = normalizeRel(path);
    if (!opts.keepInput) setBrowsePathValue(browsePath, true);
    const files = await listDir(browsePath);
    const hits = matchFiles(files, opts.filter || '');
    renderBrowseList(hits, browsePath, opts.activeName || (hits[0] && hits[0].name) || '');
  }

  function bellPathInput() {
    if (!browsePathEl) return;
    browsePathEl.classList.remove('chat-browse-path--miss');
    void browsePathEl.offsetWidth;
    browsePathEl.classList.add('chat-browse-path--miss');
  }

  async function onBrowseTab(shift) {
    if (!browsePathEl) return;
    const raw = browsePathEl.value;
    const result = await bashComplete(raw);
    if (!result.hits.length) {
      bellPathInput();
      return;
    }
    if (result.unique) {
      tabCycle = { key: '', i: -1 };
      const hit = result.hits[0];
      const full = joinRel(result.dir, hit.name);
      if (hit.is_dir) {
        await loadBrowseDir(full);
        setBrowsePathValue(full, true);
      } else {
        browsePathEl.value = result.value;
        await loadBrowseDir(result.dir, { keepInput: true, filter: hit.name });
      }
      return;
    }
    if (sameBrowseValue(raw, result.value)) {
      const delta = shift ? -1 : 1;
      let steps = 0;
      let hit = result.hits[0];
      do {
        tabCycle.i = (tabCycle.i + delta + result.hits.length) % result.hits.length;
        hit = result.hits[tabCycle.i];
        steps += 1;
      } while (steps < result.hits.length && sameBrowseValue(formatRelPath(result.dir, hit.name, false), raw));
      browsePathEl.value = formatRelPath(result.dir, hit.name, false);
      renderBrowseList(result.hits, result.dir, hit.name);
      return;
    }
    tabCycle = { key: '', i: -1 };
    browsePathEl.value = result.value;
    await loadBrowseDir(result.dir, { keepInput: true, filter: parsePathInput(result.value).prefix });
  }

  function moveBrowseSelection(delta) {
    if (!browseHits.length) return;
    let i = browseHits.findIndex((f) => f.name === browseActiveName);
    if (i < 0) i = 0;
    i = (i + delta + browseHits.length) % browseHits.length;
    const hit = browseHits[i];
    renderBrowseList(browseHits, browsePath, hit.name);
    if (browsePathEl) browsePathEl.value = formatRelPath(browsePath, hit.name, false);
  }

  async function onBrowseEnter() {
    const active = browseList?.querySelector('.chat-browse-row.is-active');
    if (active?.dataset.path) {
      attachFromBrowse(active.dataset.path, active.dataset.action === 'dir');
      return;
    }
    if (!browsePathEl) return;
    const parsed = parsePathInput(browsePathEl.value);
    const dir = normalizeRel(parsed.dir);
    const files = await listDir(dir);
    const exact = parsed.prefix
      ? files.find((f) => f.name.toLowerCase() === parsed.prefix.toLowerCase())
      : null;
    const hits = matchFiles(files, parsed.prefix);
    const pick = exact || hits[0];
    if (!pick) {
      bellPathInput();
      return;
    }
    attachFromBrowse(joinRel(dir, pick.name), !!pick.is_dir);
  }

  async function onBrowseInput() {
    tabCycle = { key: '', i: -1 };
    const parsed = parsePathInput(browsePathEl.value);
    const dir = normalizeRel(parsed.dir);
    await loadBrowseDir(dir, { keepInput: true, filter: parsed.prefix });
  }

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
    left = left.replace(/\u00a0/g, ' ').replace(/\r/g, '');
    const line = left.split('\n').pop() || '';
    const m = line.match(/([^\s]+)$/);
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
      const m = left.match(/([^\s]+)$/);
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
    const raw = (editor.innerText || '').replace(/\u00a0/g, ' ');
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
    if (!activeConvId) return;
    dirCache.clear();
    browseModal?.showModal();
    loadBrowseDir('').then(() => {
      browsePathEl?.focus();
      browsePathEl?.select();
    }).catch((err) => alert(err.message));
  });
  browseUp?.addEventListener('click', () => {
    if (!browsePath) return;
    const parts = browsePath.split('/').filter(Boolean);
    parts.pop();
    loadBrowseDir(parts.join('/')).catch((err) => alert(err.message));
  });
  browseClose?.addEventListener('click', () => browseModal?.close());
  browsePathEl?.addEventListener('keydown', (e) => {
    if (e.key === 'Tab') {
      e.preventDefault();
      onBrowseTab(e.shiftKey).catch((err) => alert(err.message));
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      moveBrowseSelection(1);
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      moveBrowseSelection(-1);
      return;
    }
    if (e.key === 'Enter' || e.key === 'NumpadEnter') {
      e.preventDefault();
      onBrowseEnter().catch((err) => alert(err.message));
    }
  });
  browsePathEl?.addEventListener('input', () => {
    clearTimeout(browseInputTimer);
    browseInputTimer = setTimeout(() => {
      onBrowseInput().catch(() => {});
    }, 80);
  });
  startBtn?.addEventListener('click', () => startConversation().catch((err) => alert(err.message)));
  qs('#chatGroupBtn')?.addEventListener('click', () => qs('#chatGroupModal')?.showModal());
  qs('#chatGroupCancel')?.addEventListener('click', () => qs('#chatGroupModal')?.close());
  qs('#chatGroupCreate')?.addEventListener('click', () => createGroup().catch((e) => alert(e.message)));
  qs('#chatReplyCancel')?.addEventListener('click', clearReply);
  qs('#chatForwardCancel')?.addEventListener('click', () => qs('#chatForwardModal')?.close());
  qs('#chatForwardSend')?.addEventListener('click', () => sendForward().catch((e) => alert(e.message)));
  qs('#chatLightboxClose')?.addEventListener('click', () => qs('#chatLightbox')?.close());

  const dropTarget = qs('.chat-thread-panel');
  dropTarget?.addEventListener('dragover', (e) => { e.preventDefault(); dropTarget.classList.add('chat-drop'); });
  dropTarget?.addEventListener('dragleave', () => dropTarget.classList.remove('chat-drop'));
  dropTarget?.addEventListener('drop', (e) => {
    e.preventDefault();
    dropTarget.classList.remove('chat-drop');
    const files = [...(e.dataTransfer?.files || [])];
    files.forEach((file) => attachFileBlob(file));
  });
  editor?.addEventListener('paste', (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith('image/')) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) attachFileBlob(file);
        return;
      }
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
    } catch (_) { /* */ }
  }

  function boot() {
    globalThis.AirdFolderPicker?.init?.();
    const WS = window.AirdChatWS;
    if (WS) {
      WS.connect();
      WS.subscribe(handleWsEvent);
    }
    const start = async () => {
      if (E2E && me) {
        const KB = globalThis.AirdChatKeyBackup;
        if (KB?.tryRestoreFromConfiguredPath) {
          const restored = await KB.tryRestoreFromConfiguredPath(me);
          if (restored) return;
        }
        try { await E2E.init(me, api); } catch (err) { console.warn('chat e2e', err); }
      }
      await loadConversations();
      const initialConv = new URLSearchParams(location.search).get('c');
      if (initialConv) await openConversation(initialConv);
    };
    start().catch((err) => {
      if (messagesEl) messagesEl.innerHTML = `<p class="text-error">${esc(err.message)}</p>`;
    });
  }

  boot();
})();
