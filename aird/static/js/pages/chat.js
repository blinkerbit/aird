(function () {
  'use strict';

  const qs = (sel) => document.querySelector(sel);
  const convList = qs('#chatConvList');
  const messagesEl = qs('#chatMessages');
  const composer = qs('#chatComposer');
  const editor = qs('#chatEditor');
  const header = qs('#chatThreadHeader');
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

  const me = window.__AIRD_CHAT_USER__ || '';
  let activeConvId = null;
  let conversations = [];
  let browsePath = '';
  let userSearchTimer = null;
  let wsUnsub = null;
  let pollTimer = null;
  let loadGen = 0;

  function lastMessageId() {
    const nodes = messagesEl?.querySelectorAll('[data-msg-id]');
    if (!nodes?.length) return 0;
    let max = 0;
    nodes.forEach((n) => {
      const id = Number(n.dataset.msgId);
      if (id > max) max = id;
    });
    return max;
  }

  async function syncNewMessages() {
    if (!activeConvId || !messagesEl) return;
    const afterId = lastMessageId();
    const data = await api(
      `/api/chat/conversations/${activeConvId}/messages${afterId ? `?after_id=${afterId}` : ''}`,
    );
    let added = false;
    (data.messages || []).forEach((m) => {
      if (!messagesEl.querySelector(`[data-msg-id="${m.id}"]`)) {
        appendMessage(m);
        added = true;
      }
    });
    if (added) {
      const nodes = messagesEl.querySelectorAll('[data-msg-id]');
      const last = nodes[nodes.length - 1];
      if (last) markRead({ id: Number(last.dataset.msgId) });
    }
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

  function setThreadHeader(peerName) {
    if (!header) return;
    if (!peerName) {
      header.innerHTML = '<div class="chat-empty chat-empty--head"><p>Select a conversation</p></div>';
      return;
    }
    header.innerHTML = `${avatarHtml(peerName)}<div><div class="chat-thread-title">${esc(peerName)}</div><div class="chat-thread-sub">Direct message</div></div>`;
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
      const active = Number(c.id) === Number(activeConvId) ? ' active' : '';
      const badge = c.unread ? `<span class="badge badge-primary badge-xs">${c.unread}</span>` : '';
      const preview = esc(c.last_preview || 'No messages yet');
      return `<li><a href="#" class="chat-conv-item${active}" data-id="${c.id}">${avatarHtml(c.peer_username)}<span class="chat-conv-main"><span class="chat-conv-top"><span class="chat-conv-name">${esc(c.peer_username)}</span>${badge}</span><span class="chat-conv-preview">${preview}</span></span></a></li>`;
    }).join('');
    convList.querySelectorAll('.chat-conv-item').forEach((el) => {
      el.addEventListener('click', (e) => {
        e.preventDefault();
        openConversation(Number(el.dataset.id));
      });
    });
  }

  async function loadConversations() {
    const data = await api('/api/chat/conversations');
    conversations = data.conversations || [];
    renderConvList();
    updateNavBadge();
  }

  function messageHtml(msg) {
    const rowClass = msg._mine ? 'chat-msg-row--mine' : 'chat-msg-row--theirs';
    const bubbleClass = msg._mine ? 'chat-bubble--mine' : 'chat-bubble--theirs';
    let body = '';
    if (msg.msg_type === 'text') {
      body = msg.body || '';
    } else if (msg.msg_type === 'gif' && msg.attachment_path) {
      const url = `/files/${encodeURI(msg.attachment_path)}`;
      body = `<img src="${esc(url)}" alt="GIF" class="max-w-[14rem] rounded-lg">` + (msg.body ? `<div class="mt-1">${msg.body}</div>` : '');
    } else if (msg.attachment_path) {
      const url = `/files/${encodeURI(msg.attachment_path)}`;
      const name = (msg.metadata && msg.metadata.original_name) || msg.attachment_path.split('/').pop();
      body = `<a class="chat-file-card" href="${esc(url)}" target="_blank" rel="noopener">${esc(name)}</a>` + (msg.body ? `<div class="mt-1">${msg.body}</div>` : '');
    }
    const delBtn = msg._mine
      ? `<button type="button" class="btn btn-ghost btn-xs text-error chat-msg-delete" data-id="${msg.id}" title="Delete message" aria-label="Delete message">×</button>`
      : '';
    const senderLine = msg._mine
      ? `<span class="chat-bubble-time">${esc(formatTime(msg.created_at))}</span>`
      : `<span class="chat-bubble-sender">${esc(msg.sender_username)}</span><span class="chat-bubble-time">${esc(formatTime(msg.created_at))}</span>`;
    return `<div class="chat-msg-row ${rowClass}" data-msg-id="${msg.id}">${avatarHtml(msg.sender_username)}<div class="chat-bubble ${bubbleClass}"><div class="chat-bubble-meta">${senderLine}${delBtn}</div><div class="chat-bubble-body">${body}</div></div></div>`;
  }

  function bindDeleteButtons() {
    messagesEl?.querySelectorAll('.chat-msg-delete').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        deleteMessage(Number(btn.dataset.id)).catch((err) => alert(err.message));
      });
    });
  }

  function appendMessage(msg) {
    if (!messagesEl) return;
    tagMine(msg);
    messagesEl.querySelector('.chat-empty')?.remove();
    messagesEl.insertAdjacentHTML('beforeend', messageHtml(msg));
    bindDeleteButtons();
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function removeMessageEl(messageId) {
    const el = messagesEl?.querySelector(`[data-msg-id="${messageId}"]`);
    el?.remove();
    if (messagesEl && !messagesEl.querySelector('[data-msg-id]')) showEmptyThread();
  }

  async function openConversation(convId) {
    const gen = ++loadGen;
    activeConvId = convId;
    const conv = conversations.find((c) => Number(c.id) === Number(convId));
    setThreadHeader(conv?.peer_username || null);
    composer.classList.remove('hidden');
    messagesEl.innerHTML = '<div class="chat-empty"><p>Loading…</p></div>';
    renderConvList();
    setPollEnabled(true);
    const data = await api(`/api/chat/conversations/${convId}/messages`);
    if (gen !== loadGen) return;
    clearMessagesView();
    const msgs = data.messages || [];
    if (!msgs.length) showEmptyThread();
    else msgs.forEach((m) => appendMessage(m));
    const last = msgs.slice(-1)[0];
    if (last) markRead(last);
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
      if (Number(msg.conversation_id) === Number(activeConvId)) {
        if (!messagesEl?.querySelector(`[data-msg-id="${msg.id}"]`)) {
          appendMessage(msg);
          markRead(msg);
        }
      }
      loadConversations();
      updateNavBadge();
    } else if (data.type === 'chat_message_deleted') {
      if (Number(data.conversation_id) === Number(activeConvId)) {
        removeMessageEl(data.message_id);
      }
      loadConversations();
      updateNavBadge();
    } else if (data.type === 'chat_notify') {
      updateNavBadge(data.unread_total);
      if (Number(data.conversation_id) === Number(activeConvId)) {
        syncNewMessages().catch(() => { /* */ });
      }
      loadConversations();
    } else if (data.type === 'chat_ws_close') {
      setPollEnabled(true);
    } else if (data.type === 'chat_ws_open') {
      setPollEnabled(!!activeConvId);
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

  async function deleteMessage(messageId) {
    if (!activeConvId) return;
    if (!confirm('Delete this message?')) return;
    await api(`/api/chat/conversations/${activeConvId}/messages/${messageId}`, {
      method: 'DELETE',
    });
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
        `<li><button type="button" class="w-full text-left px-3 py-2 hover:bg-base-200" data-user="${esc(u.username)}">${esc(u.username)}</button></li>`
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

  newUserInput?.addEventListener('input', () => {
    clearTimeout(userSearchTimer);
    userSearchTimer = setTimeout(() => searchUsers(newUserInput.value), 250);
  });

  document.addEventListener('click', (e) => {
    if (!e.target.closest('.chat-new-user-wrap')) hideUserSuggest();
  });

  composer?.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!activeConvId || !editor) return;
    const body = editor.innerHTML.trim();
    if (!body) return;
    const WS = window.AirdChatWS;
    if (WS?.isOpen() && WS.send({ type: 'chat_send', conversation_id: activeConvId, body })) {
      editor.innerHTML = '';
      return;
    }
    await api(`/api/chat/conversations/${activeConvId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ type: 'text', body }),
    });
    editor.innerHTML = '';
    await openConversation(activeConvId);
  });

  document.querySelectorAll('.chat-toolbar [data-cmd], [data-cmd]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cmd = btn.dataset.cmd;
      if (cmd === 'link') {
        const url = prompt('URL');
        if (url) document.execCommand('createLink', false, url);
      } else {
        document.execCommand(cmd, false, null);
      }
      editor?.focus();
    });
  });

  async function attachUpload(input) {
    if (!activeConvId || !input.files?.length) return;
    const fd = new FormData();
    fd.append('file', input.files[0]);
    await api(`/api/chat/conversations/${activeConvId}/attach`, { method: 'POST', body: fd });
    input.value = '';
    await openConversation(activeConvId);
    await loadConversations();
  }

  async function attachFromBrowse(relativePath) {
    if (!activeConvId) return;
    const fd = new FormData();
    fd.append('source_path', relativePath);
    await api(`/api/chat/conversations/${activeConvId}/attach`, { method: 'POST', body: fd });
    browseModal?.close();
    await openConversation(activeConvId);
    await loadConversations();
  }

  async function loadBrowseDir(path) {
    browsePath = (path || '').replace(/^\/+|\/+$/g, '');
    if (browsePathEl) browsePathEl.textContent = browsePath ? `/${browsePath}` : '/';
    const url = browsePath ? `/api/files/${encodeURI(browsePath)}` : '/api/files/';
    const data = await api(url);
    if (!browseList) return;
    const files = data.files || [];
    if (!files.length) {
      browseList.innerHTML = '<p class="p-4 text-sm opacity-50 text-center">Empty folder</p>';
      return;
    }
    browseList.innerHTML = files.map((f) => {
      const full = browsePath ? `${browsePath}/${f.name}` : f.name;
      const icon = f.is_dir ? '📁' : '📄';
      return `<div class="chat-browse-row flex items-center gap-2 px-3 py-2" data-action="${f.is_dir ? 'dir' : 'file'}" data-path="${esc(full)}"><span>${icon}</span><span class="truncate">${esc(f.name)}</span></div>`;
    }).join('');
    browseList.querySelectorAll('.chat-browse-row').forEach((row) => {
      row.addEventListener('click', () => {
        const p = row.dataset.path || '';
        if (row.dataset.action === 'dir') loadBrowseDir(p).catch((err) => alert(err.message));
        else attachFromBrowse(p).catch((err) => alert(err.message));
      });
    });
  }

  fileInput?.addEventListener('change', () => attachUpload(fileInput).catch((e) => alert(e.message)));
  gifInput?.addEventListener('change', () => attachUpload(gifInput).catch((e) => alert(e.message)));
  browseBtn?.addEventListener('click', () => {
    if (!activeConvId) return;
    browseModal?.showModal();
    loadBrowseDir('').catch((err) => alert(err.message));
  });
  browseUp?.addEventListener('click', () => {
    if (!browsePath) return;
    const parts = browsePath.split('/').filter(Boolean);
    parts.pop();
    loadBrowseDir(parts.join('/')).catch((err) => alert(err.message));
  });
  browseClose?.addEventListener('click', () => browseModal?.close());
  startBtn?.addEventListener('click', () => startConversation().catch((err) => alert(err.message)));

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
    const WS = window.AirdChatWS;
    if (WS) {
      WS.connect();
      wsUnsub = WS.subscribe(handleWsEvent);
    }
    loadConversations()
      .then(() => {
        const initialConv = new URLSearchParams(location.search).get('c');
        if (initialConv) openConversation(Number(initialConv));
      })
      .catch((err) => {
        if (messagesEl) messagesEl.innerHTML = `<p class="text-error">${esc(err.message)}</p>`;
      });
  }

  boot();
})();
