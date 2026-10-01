/**
 * Enhanced Secure Chat. Keys and plaintext live in sessionStorage for this tab only.
 * The server relays ciphertext and keeps no copy of either.
 */
(function (global) {
  'use strict';

  const STORE = 'aird-enhanced-secure-v1';
  const ECDH = { name: 'ECDH', namedCurve: 'P-256' };
  const INFO = new TextEncoder().encode('aird-enhanced-secure-chat-v1');
  const rooms = new Map();
  let viewing = null;
  let draftPeer = null;

  function b64(buf) {
    const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : buf;
    let s = '';
    for (let i = 0; i < bytes.length; i += 1) s += String.fromCharCode(bytes[i]);
    return btoa(s);
  }

  function unb64(str) {
    const n = String(str || '').replace(/-/g, '+').replace(/_/g, '/');
    const pad = n.length % 4 === 0 ? '' : '='.repeat(4 - (n.length % 4));
    const bin = atob(n + pad);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
    return out;
  }

  function esc(s) {
    return String(s ?? '').replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
    }[c]));
  }

  function loadStore() {
    try {
      const raw = global.sessionStorage.getItem(STORE);
      const rows = raw ? JSON.parse(raw) : [];
      return Array.isArray(rows) ? rows : [];
    } catch (_) {
      return [];
    }
  }

  function saveStore() {
    const rows = [...rooms.values()].map((room) => ({
      roomId: room.roomId,
      peer: room.peer,
      publicJwk: room.publicJwk,
      privateJwk: room.privateJwk,
      messages: (room.messages || []).slice(-400),
      open: !!room.open,
      requested: !!room.requested,
      inviterEpk: room.inviterEpk || null,
      unread: Number(room.unread || 0),
    }));
    try {
      global.sessionStorage.setItem(STORE, JSON.stringify(rows));
    } catch (_) { /* tab storage full: keep the in-memory copy */ }
  }

  function wipe(roomId) {
    rooms.delete(roomId);
    saveStore();
    if (viewing === roomId) {
      viewing = null;
      paintEnded();
    }
    global.AirdChatPage?.refreshList?.();
  }

  async function importPrivate(jwk) {
    return crypto.subtle.importKey('jwk', jwk, ECDH, true, ['deriveBits']);
  }

  async function importPublic(jwk) {
    return crypto.subtle.importKey(
      'jwk',
      { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y },
      ECDH,
      true,
      [],
    );
  }

  async function freshKey() {
    const pair = await crypto.subtle.generateKey(ECDH, true, ['deriveBits']);
    const publicJwk = await crypto.subtle.exportKey('jwk', pair.publicKey);
    const privateJwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
    delete publicJwk.d;
    return {
      publicJwk: { kty: 'EC', crv: 'P-256', x: publicJwk.x, y: publicJwk.y },
      privateJwk,
      privateKey: pair.privateKey,
    };
  }

  async function derive(room, theirPub) {
    if (!room?.privateKey || !theirPub) return;
    const pub = await importPublic(theirPub);
    const bits = await crypto.subtle.deriveBits(
      { name: 'ECDH', public: pub },
      room.privateKey,
      256,
    );
    const hkdf = await crypto.subtle.importKey('raw', bits, 'HKDF', false, ['deriveKey']);
    room.aes = await crypto.subtle.deriveKey(
      {
        name: 'HKDF',
        hash: 'SHA-256',
        salt: new TextEncoder().encode(room.roomId),
        info: INFO,
      },
      hkdf,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
    room.open = true;
    saveStore();
  }

  function status(text) {
    const el = document.getElementById('chatE2eStatus');
    if (!el) return;
    if (!text) {
      el.hidden = true;
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.textContent = text;
  }

  function paintEnded() {
    const header = document.getElementById('chatThreadHeader');
    const messages = document.getElementById('chatMessages');
    document.getElementById('chatComposer')?.classList.add('hidden');
    if (header) {
      header.innerHTML = '<div class="chat-empty chat-empty--head"><p>Enhanced Secure Chat ended</p></div>';
    }
    if (messages) {
      messages.innerHTML = '<div class="chat-empty"><p>This Enhanced Secure Chat is gone from this browser.</p></div>';
    }
    status('');
    global.AirdChatPage?.leaveEnhancedSecure?.();
  }

  function paint(room) {
    const header = document.getElementById('chatThreadHeader');
    const messages = document.getElementById('chatMessages');
    const composer = document.getElementById('chatComposer');
    if (!room || !header || !messages) return;
    composer?.classList.toggle('hidden', !!room.requested);
    const state = room.requested
      ? `${room.peer} requested an Enhanced Secure Chat`
      : (room.open ? 'Enhanced Secure Chat · live while you are both here' : `Waiting for ${room.peer} to accept`);
    header.innerHTML = `<button type="button" class="chat-back" id="chatEnhancedBack" aria-label="Back to conversations"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M15 18l-6-6 6-6"/></svg></button><div class="chat-thread-head-main"><div class="chat-thread-title">${esc(room.peer)} <span class="chat-enhanced-lock" title="Enhanced Secure Chat">⏱</span></div><div class="chat-thread-sub">${esc(state)}</div></div><div class="chat-thread-actions"><button type="button" id="chatEnhancedEnd" class="chat-enhanced-end">${room.requested ? 'Decline' : 'End'}</button></div>`;
    document.getElementById('chatEnhancedBack')?.addEventListener('click', () => {
      global.AirdChatPage?.showList?.();
    });
    document.getElementById('chatEnhancedEnd')?.addEventListener('click', () => (
      room.requested ? decline(room.roomId) : end(room.roomId)
    ));
    const banner = '<div class="chat-enhanced-banner">Enhanced Secure Chat exists only in this browser tab. Closing the tab deletes it. Nothing is saved on the server.</div>';
    const body = (room.messages || []).map((m) => (
      `<div class="chat-msg-row ${m.mine ? 'chat-msg-row--mine' : 'chat-msg-row--theirs'}"><div class="chat-msg-stack"><div class="aird-chat-bubble ${m.mine ? 'aird-chat-bubble--mine' : 'aird-chat-bubble--theirs'}"><div class="aird-chat-bubble-body">${esc(m.text)}</div></div>${m.mine && m.status ? `<div class="chat-seen">${m.status === 'seen' ? 'Decrypted &amp; seen' : 'Decrypted'}</div>` : ''}</div></div>`
    )).join('');
    const request = room.requested
      ? `<div class="chat-enhanced-request"><p>${esc(room.peer)} wants to start an Enhanced Secure Chat.</p><div><button type="button" id="chatEnhancedAccept" class="btn btn-primary btn-sm">Accept</button><button type="button" id="chatEnhancedDecline" class="btn btn-ghost btn-sm">Decline</button></div></div>`
      : '';
    messages.innerHTML = banner + request + (body || (room.requested ? '' : '<div class="chat-empty"><p>No messages yet</p></div>'));
    document.getElementById('chatEnhancedAccept')?.addEventListener('click', () => accept(room.roomId));
    document.getElementById('chatEnhancedDecline')?.addEventListener('click', () => decline(room.roomId));
    messages.scrollTop = messages.scrollHeight;
    const editor = document.getElementById('chatEditor');
    if (editor) editor.dataset.placeholder = room.open ? 'Enhanced secure message…' : `Waiting for ${room.peer}…`;
    status(room.requested
      ? `${room.peer} requested an Enhanced Secure Chat. Accept it to exchange temporary keys.`
      : (room.open ? '' : `Waiting for ${room.peer} to accept. The room closes if either of you leaves.`));
  }

  function acknowledgeSeen(room) {
    if (!room || document.visibilityState !== 'visible') return false;
    let changed = false;
    for (const message of room.messages || []) {
      if (message.mine || message.status !== 'decrypted' || !message.id) continue;
      global.AirdChatWS?.send({
        type: 'enhanced_receipt',
        room_id: room.roomId,
        message_id: message.id,
        status: 'seen',
      });
      message.status = 'seen';
      changed = true;
    }
    if (changed) room.unread = 0;
    return changed;
  }

  function show(roomId) {
    const room = rooms.get(roomId);
    if (!room) return;
    viewing = roomId;
    room.unread = 0;
    acknowledgeSeen(room);
    saveStore();
    global.AirdChatPage?.enterEnhancedSecure?.();
    paint(room);
    global.AirdChatPage?.refreshList?.();
  }

  function closeView() {
    viewing = null;
    const editor = document.getElementById('chatEditor');
    if (editor) editor.dataset.placeholder = 'Write a message…';
    status('');
    global.AirdChatPage?.leaveEnhancedSecure?.();
  }

  function listHtml() {
    return [...rooms.values()].map((room) => {
      const active = room.roomId === viewing ? ' active' : '';
      const preview = room.requested
        ? 'Enhanced Secure Chat request'
        : (room.open ? 'Enhanced Secure Chat' : 'Waiting for acceptance…');
      const actions = room.requested
        ? `<span class="chat-enhanced-list-actions"><button type="button" data-enhanced-accept="${esc(room.roomId)}">Accept</button><button type="button" data-enhanced-decline="${esc(room.roomId)}">Decline</button></span>`
        : '';
      const unread = room.unread
        ? `<span class="badge badge-primary badge-xs">${room.unread}</span>`
        : '';
      return `<li class="chat-enhanced-row"><a href="#" class="chat-conv-item chat-enhanced-item${active}" data-enhanced-room="${esc(room.roomId)}"><span class="chat-conv-main"><span class="chat-conv-top"><span class="chat-conv-name">${esc(room.peer)} <span class="chat-enhanced-lock">⏱</span></span>${unread}</span><span class="chat-conv-preview">${preview}</span></span></a>${actions}</li>`;
    }).join('');
  }

  async function remember(peer, keys) {
    draftPeer = { peer, ...keys };
  }

  function takeDraft(peer) {
    if (!draftPeer || draftPeer.peer !== peer) return null;
    const draft = draftPeer;
    draftPeer = null;
    return draft;
  }

  async function start(peer) {
    const name = String(peer || '').trim();
    if (!name) return;
    const existing = [...rooms.values()].find((room) => room.peer === name);
    if (existing) {
      if (existing.requested) {
        show(existing.roomId);
        return;
      }
      if (!global.AirdChatWS?.send({
        type: 'enhanced_join',
        room_id: existing.roomId,
        epk: existing.publicJwk,
      })) {
        status('Chat connection is not open');
        return;
      }
      show(existing.roomId);
      return;
    }
    const keys = await freshKey();
    await remember(name, keys);
    if (!global.AirdChatWS?.send({ type: 'enhanced_invite', peer: name, epk: keys.publicJwk })) {
      draftPeer = null;
      status('Chat connection is not open');
    }
  }

  function end(roomId) {
    const id = roomId || viewing;
    const room = id ? rooms.get(id) : null;
    if (!room) return;
    global.AirdChatWS?.send({ type: 'enhanced_close', room_id: room.roomId });
    wipe(room.roomId);
  }

  async function accept(roomId) {
    const room = rooms.get(roomId);
    if (!room?.requested) return;
    const keys = await freshKey();
    room.publicJwk = keys.publicJwk;
    room.privateJwk = keys.privateJwk;
    room.privateKey = keys.privateKey;
    room.requested = false;
    saveStore();
    if (!global.AirdChatWS?.send({
      type: 'enhanced_join',
      room_id: room.roomId,
      epk: room.publicJwk,
    })) {
      room.requested = true;
      saveStore();
      status('Chat connection is not open');
      return;
    }
    show(room.roomId);
  }

  function decline(roomId) {
    const room = rooms.get(roomId);
    if (!room) return;
    global.AirdChatWS?.send({ type: 'enhanced_close', room_id: room.roomId });
    wipe(room.roomId);
  }

  async function sendText(text) {
    const room = viewing ? rooms.get(viewing) : null;
    const body = String(text || '').trim();
    if (!room || !body) return;
    if (!room.open || !room.aes) throw new Error(`Waiting for ${room.peer}`);
    const messageId = b64(crypto.getRandomValues(new Uint8Array(18)))
      .replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv },
      room.aes,
      new TextEncoder().encode(body),
    );
    if (!global.AirdChatWS?.send({
      type: 'enhanced_msg',
      room_id: room.roomId,
      message_id: messageId,
      v: 1,
      iv: b64(iv),
      ct: b64(ct),
    })) {
      throw new Error('Chat connection is not open');
    }
    room.messages.push({ id: messageId, mine: true, text: body, at: Date.now(), status: '' });
    saveStore();
    paint(room);
  }

  async function ensureRoom(roomId, peer) {
    let room = rooms.get(roomId);
    if (room) return room;
    const draft = takeDraft(peer);
    const keys = draft || await freshKey();
    room = {
      roomId,
      peer,
      publicJwk: keys.publicJwk,
      privateJwk: keys.privateJwk,
      privateKey: keys.privateKey || await importPrivate(keys.privateJwk),
      messages: [],
      open: false,
      requested: false,
    };
    rooms.set(roomId, room);
    saveStore();
    return room;
  }

  async function onRequest(msg) {
    let room = rooms.get(msg.room_id);
    if (!room) {
      room = {
        roomId: msg.room_id,
        peer: msg.from,
        publicJwk: null,
        privateJwk: null,
        privateKey: null,
        inviterEpk: msg.epk,
        messages: [],
        open: false,
        requested: true,
        unread: 0,
      };
      rooms.set(room.roomId, room);
      saveStore();
    }
    global.AirdChatPage?.refreshList?.();
    status(`${msg.from} requested an Enhanced Secure Chat`);
  }

  async function onRoom(msg) {
    const room = await ensureRoom(msg.room_id, msg.peer);
    room.open = false;
    room.requested = false;
    saveStore();
    show(room.roomId);
  }

  async function onOpen(msg) {
    const room = await ensureRoom(msg.room_id, msg.peer);
    await derive(room, msg.epk);
    if (viewing === room.roomId) paint(room);
    global.AirdChatPage?.refreshList?.();
    if (viewing !== room.roomId && !viewing) {
      status(`Enhanced Secure Chat with ${room.peer} is ready`);
    }
  }

  async function onMsg(msg) {
    const room = rooms.get(msg.room_id);
    if (!room?.aes) return;
    try {
      const plain = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: unb64(msg.iv) },
        room.aes,
        unb64(msg.ct),
      );
      const text = new TextDecoder().decode(plain);
      room.messages.push({
        id: msg.message_id,
        mine: false,
        text,
        at: Date.now(),
        status: 'decrypted',
      });
      global.AirdChatWS?.send({
        type: 'enhanced_receipt',
        room_id: room.roomId,
        message_id: msg.message_id,
        status: 'decrypted',
      });
      if (viewing === room.roomId && document.visibilityState === 'visible') {
        global.AirdChatWS?.send({
          type: 'enhanced_receipt',
          room_id: room.roomId,
          message_id: msg.message_id,
          status: 'seen',
        });
        room.messages[room.messages.length - 1].status = 'seen';
      } else {
        room.unread = Number(room.unread || 0) + 1;
      }
      saveStore();
      if (viewing === room.roomId) paint(room);
      global.AirdChatPage?.refreshList?.();
    } catch (_) {
      status('Could not decrypt an Enhanced Secure Chat message');
    }
  }

  function onReceipt(msg) {
    const room = rooms.get(msg.room_id);
    const message = room?.messages?.find((item) => item.mine && item.id === msg.message_id);
    if (!message) return;
    if (msg.status === 'seen' || !message.status) message.status = msg.status;
    saveStore();
    if (viewing === room.roomId) paint(room);
  }

  function rejoin() {
    rooms.forEach((room) => {
      if (room.requested || !room.publicJwk) return;
      global.AirdChatWS?.send({
        type: 'enhanced_join',
        room_id: room.roomId,
        epk: room.publicJwk,
      });
    });
  }

  async function onEvent(msg) {
    if (!msg || !msg.type) return;
    try {
      if (msg.type === 'enhanced_request') await onRequest(msg);
      else if (msg.type === 'enhanced_room') await onRoom(msg);
      else if (msg.type === 'enhanced_open') await onOpen(msg);
      else if (msg.type === 'enhanced_msg') await onMsg(msg);
      else if (msg.type === 'enhanced_receipt') onReceipt(msg);
      else if (msg.type === 'enhanced_ended') wipe(msg.room_id);
      else if (msg.type === 'enhanced_error') status(msg.message || 'Enhanced Secure Chat failed');
      else if (msg.type === 'chat_ws_open') rejoin();
    } catch (err) {
      status(err.message || 'Enhanced Secure Chat failed');
    }
  }

  async function boot() {
    for (const row of loadStore()) {
      if (!row?.roomId) continue;
      if (row.requested) {
        rooms.set(row.roomId, {
          ...row,
          messages: Array.isArray(row.messages) ? row.messages : [],
          privateKey: null,
          open: false,
        });
        continue;
      }
      if (!row.privateJwk) continue;
      try {
        rooms.set(row.roomId, {
          ...row,
          messages: Array.isArray(row.messages) ? row.messages : [],
          privateKey: await importPrivate(row.privateJwk),
          open: false,
        });
      } catch (_) { /* drop a corrupt tab entry */ }
    }
    global.AirdChatWS?.subscribe(onEvent);
    global.AirdChatWS?.connect();
    if (global.AirdChatWS?.isOpen()) rejoin();
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState !== 'visible' || !viewing) return;
      const room = rooms.get(viewing);
      if (!acknowledgeSeen(room)) return;
      saveStore();
      paint(room);
      global.AirdChatPage?.refreshList?.();
    });
  }

  global.AirdEnhancedSecure = {
    start,
    end,
    accept,
    decline,
    show,
    closeView,
    viewing: () => viewing,
    sendText,
    listHtml,
  };

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', () => { boot().catch(() => {}); });
  } else {
    boot().catch(() => {});
  }
})(window);
