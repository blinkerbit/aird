from pathlib import Path

root = Path("/home/vis/aird/aird")

handlers = root / "plugins/calls/handlers.py"
text = handlers.read_text()
old = """            room=room,
            identity=username,
            name=username,"""
new = """            room=room,
            identity=f"{username}:{uuid.uuid4().hex[:8]}",
            name=username,"""
if old not in text:
    raise SystemExit("token identity missing")
handlers.write_text(text.replace(old, new, 1))

notify = root / "plugins/calls/notify.py"
src = notify.read_text()
old_fn = '''def dispatch_call_signal(
    *,
    username: str,
    conversation_id: str,
    actor_id: int,
    signal_type: str,
    extra: dict | None = None,
) -> None:
    ids = _member_ids(username, conversation_id)
    payload = {
        "type": signal_type,
        "conversation_id": conversation_id,
        "from": username,
    }
    if extra:
        payload.update(extra)
    get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=actor_id)
    clear_call_rings(username, conversation_id)
'''
new_fn = '''def peek_call_ring(username: str) -> dict | None:
    conn = _ring_conn()
    if conn is None:
        return None
    try:
        return read_call_ring(conn, username)
    finally:
        conn.close()


def dispatch_call_signal(
    *,
    username: str,
    conversation_id: str,
    actor_id: int,
    signal_type: str,
    extra: dict | None = None,
) -> None:
    # A decline is only for the device that tapped it. Other devices keep ringing.
    if signal_type == "call_decline":
        return
    ids = _member_ids(username, conversation_id)
    payload = {
        "type": signal_type,
        "conversation_id": conversation_id,
        "from": username,
    }
    if extra:
        payload.update(extra)
    if signal_type == "call_accept":
        # The answering device is already in the call. Tell the caller.
        # Leave the ring in place so every other signed-in device keeps ringing.
        get_chat_hub().broadcast_user_ids(ids, payload, exclude_user_id=actor_id)
        return
    get_chat_hub().broadcast_user_ids(ids, payload)
    clear_call_rings(username, conversation_id)
'''
if old_fn not in src:
    raise SystemExit("dispatch_call_signal missing")
notify.write_text(src.replace(old_fn, new_fn, 1))

ws = root / "plugins/chat/ws.py"
wst = ws.read_text()
old_open = '''        self.write_message(json.dumps({"type": "chat_ready"}))
        for note in get_enhanced_secure_hub().pending_for(username):
            self.write_message(json.dumps(note))
        dispatch_presence(username, uid, True)
'''
new_open = '''        self.write_message(json.dumps({"type": "chat_ready"}))
        for note in get_enhanced_secure_hub().pending_for(username):
            self.write_message(json.dumps(note))
        try:
            from aird.plugins.calls.notify import peek_call_ring
            ring = peek_call_ring(username)
            if ring:
                self.write_message(json.dumps(ring))
        except Exception:
            pass
        dispatch_presence(username, uid, True)
'''
if old_open not in wst:
    raise SystemExit("ws open missing")
ws.write_text(wst.replace(old_open, new_open, 1))

js = root / "static/js/chat-notify.js"
body = js.read_text()
old_show = '''  function showCallPopup(payload) {
    if (!payload?.conversation_id) return;
    if (payload.type && payload.type !== 'call_invite') return;
'''
new_show = '''  function silencedCalls() {
    try { return JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]'); }
    catch { return []; }
  }

  function silenceCall(id) {
    if (!id) return;
    const cur = silencedCalls();
    if (!cur.includes(id)) cur.push(id);
    sessionStorage.setItem('aird-silenced-calls', JSON.stringify(cur.slice(-30)));
  }

  function showCallPopup(payload) {
    if (!payload?.conversation_id) return;
    if (payload.type && payload.type !== 'call_invite') return;
    if (payload.call_id && silencedCalls().includes(payload.call_id)) return;
'''
if old_show not in body:
    raise SystemExit("showCallPopup missing")
body = body.replace(old_show, new_show, 1)
old_decline = '''        hideCallPopup();
        if (ring && typeof globalThis.__AIRD_REJECT_CALL__ === 'function') globalThis.__AIRD_REJECT_CALL__(ring);
'''
new_decline = '''        silenceCall(ring?.call_id);
        hideCallPopup();
        if (ring && typeof globalThis.__AIRD_REJECT_CALL__ === 'function') globalThis.__AIRD_REJECT_CALL__(ring);
'''
if old_decline not in body:
    raise SystemExit("decline click missing")
body = body.replace(old_decline, new_decline, 1)
old_event = '''    } else if (data.type === 'call_invite' || data.type === 'call_accept' || data.type === 'call_end' || data.type === 'call_decline') {
      if (data.type === 'call_invite' && data.conversation_id) {
        globalThis.__AIRD_PENDING_CALL__ = data;
        showCallPopup(data);
      } else if (data.type !== 'call_invite' && globalThis.__AIRD_PENDING_CALL__?.conversation_id === data.conversation_id) {
        globalThis.__AIRD_PENDING_CALL__ = null;
        hideCallPopup();
      }
      globalThis.dispatchEvent(new CustomEvent('aird-call-invite', { detail: data }));
    }
'''
new_event = '''    } else if (data.type === 'call_invite' || data.type === 'call_accept' || data.type === 'call_end' || data.type === 'call_decline') {
      if (data.type === 'call_invite' && data.conversation_id) {
        globalThis.__AIRD_PENDING_CALL__ = data;
        showCallPopup(data);
      } else if (data.type === 'call_end' && globalThis.__AIRD_PENDING_CALL__?.conversation_id === data.conversation_id) {
        globalThis.__AIRD_PENDING_CALL__ = null;
        hideCallPopup();
      }
      globalThis.dispatchEvent(new CustomEvent('aird-call-invite', { detail: data }));
    }
'''
if old_event not in body:
    raise SystemExit("onChatEvent missing")
body = body.replace(old_event, new_event, 1)
old_poll = '''    const askRing = () => navigator.serviceWorker?.controller?.postMessage({ type: 'aird-call-peek' });
    askRing();
    navigator.serviceWorker?.ready.then(askRing).catch(() => {});
    const pollRing = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const res = await apiFetch('/api/chat/call-ring', { headers: { Accept: 'application/json' } });
        if (!res.ok) return;
        const data = await res.json();
        if (data.ring?.conversation_id) showCallPopup({ ...data.ring, type: 'call_invite' });
      } catch { /* offline */ }
    };
    void pollRing();
    setInterval(pollRing, 2000);
'''
new_poll = '''    const askRing = () => navigator.serviceWorker?.controller?.postMessage({ type: 'aird-call-peek' });
    askRing();
    navigator.serviceWorker?.ready.then(askRing).catch(() => {});
'''
if old_poll not in body:
    raise SystemExit("poll missing")
body = body.replace("chat-calls.js?v=20261022m", "chat-calls.js?v=20261023d")
js.write_text(body.replace(old_poll, new_poll, 1))

calls = root / "static/js/chat-calls.js"
ct = calls.read_text()
old_in = '''function showIncoming(payload) {
  if (!payload || payload.type !== 'call_invite') return;
  if (!payload.call_id || !payload.conversation_id || !payload.from || payload.from === meName()) return;
'''
new_in = '''function callSilenced(id) {
  try { return JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]').includes(id); }
  catch { return false; }
}

function silenceCall(id) {
  if (!id) return;
  let cur = [];
  try { cur = JSON.parse(sessionStorage.getItem('aird-silenced-calls') || '[]'); }
  catch { cur = []; }
  if (!cur.includes(id)) cur.push(id);
  sessionStorage.setItem('aird-silenced-calls', JSON.stringify(cur.slice(-30)));
}

function showIncoming(payload) {
  if (!payload || payload.type !== 'call_invite') return;
  if (!payload.call_id || !payload.conversation_id || !payload.from || payload.from === meName()) return;
  if (callSilenced(payload.call_id)) return;
'''
if old_in not in ct:
    raise SystemExit("showIncoming missing")
ct = ct.replace(old_in, new_in, 1)
old_acc = '''    phase = 'live';
    setStatus(mode === 'video' ? 'Video call' : 'Voice call');
    signalCall('call_accept', convId, callId);
'''
new_acc = '''    phase = 'live';
    silenceCall(callId);
    setStatus(mode === 'video' ? 'Video call' : 'Voice call');
    signalCall('call_accept', convId, callId);
'''
if old_acc not in ct:
    raise SystemExit("accept missing")
ct = ct.replace(old_acc, new_acc, 1)
old_dec = '''function declineIncoming() {
  const conv = incoming?.conversation_id;
  const id = incoming?.call_id || callId;
  if (conv) signalCall('call_decline', conv, id);
'''
new_dec = '''function declineIncoming() {
  const conv = incoming?.conversation_id;
  const id = incoming?.call_id || callId;
  silenceCall(id);
  if (conv) signalCall('call_decline', conv, id);
'''
if old_dec not in ct:
    raise SystemExit("declineIncoming missing")
ct = ct.replace(old_dec, new_dec, 1)
old_ws = '''  if (data.type === 'call_decline' && sameCall(data) && phase === 'outgoing') {
    void endLocal('Call declined', false);
  }
'''
new_ws = '''  if (data.type === 'call_decline' && sameCall(data) && phase === 'outgoing') {
    return;
  }
'''
if old_ws not in ct:
    raise SystemExit("decline ws missing")
calls.write_text(ct.replace(old_ws, new_ws, 1))

header = root / "templates/_app_nav_header.html"
ht = header.read_text()
ht2 = ht.replace("chat-notify.js?v=20261022m", "chat-notify.js?v=20261023d")
if ht2 == ht:
    raise SystemExit("header version missing")
header.write_text(ht2)
print("patched")
