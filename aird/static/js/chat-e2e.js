/**
 * Client-side E2E for chat. Identity private keys never leave this browser.
 */
(function (global) {
  'use strict';

  const DB_NAME = 'aird-chat-e2e';
  const DB_VER = 1;
  const ECDH = { name: 'ECDH', namedCurve: 'P-256' };
  const INFO = new TextEncoder().encode('aird-chat-e2e-v1');

  let dbPromise = null;
  let identity = null;
  let username = '';

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

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VER);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('identity')) db.createObjectStore('identity');
        if (!db.objectStoreNames.contains('convKeys')) db.createObjectStore('convKeys');
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  function idbGet(store, key) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readonly');
      const req = tx.objectStore(store).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    }));
  }

  function idbSet(store, key, value) {
    return openDb().then((db) => new Promise((resolve, reject) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).put(value, key);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    }));
  }

  async function exportJwk(key) {
    return crypto.subtle.exportKey('jwk', key);
  }

  async function importPublic(jwk) {
    const pub = { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y };
    return crypto.subtle.importKey('jwk', pub, ECDH, true, []);
  }

  async function importPrivate(jwk) {
    return crypto.subtle.importKey('jwk', jwk, ECDH, true, ['deriveBits']);
  }

  async function fingerprint(jwk) {
    const raw = new TextEncoder().encode(`${jwk.x}.${jwk.y}`);
    const hash = await crypto.subtle.digest('SHA-256', raw);
    return b64(hash).replace(/=+$/, '');
  }

  async function deriveWrapKey(priv, pub, convId) {
    const bits = await crypto.subtle.deriveBits({ name: 'ECDH', public: pub }, priv, 256);
    const hkdf = await crypto.subtle.importKey('raw', bits, 'HKDF', false, ['deriveKey']);
    return crypto.subtle.deriveKey(
      { name: 'HKDF', hash: 'SHA-256', salt: new TextEncoder().encode(convId), info: INFO },
      hkdf,
      { name: 'AES-GCM', length: 256 },
      false,
      ['encrypt', 'decrypt'],
    );
  }

  async function loadOrCreateIdentity(user, api) {
    username = user;
    const stored = await idbGet('identity', user);
    if (!stored?.privateJwk && api) {
      try {
        const remote = await api('/api/chat/e2e/identity');
        const backup = remote?.backup;
        if (backup?.privateJwk && backup?.publicJwk) {
          await importBackup(backup, user);
          return identity;
        }
      } catch (err) {
        console.debug('aird identity restore failed', err);
      }
    }
    if (!(await idbGet('identity', user))?.privateJwk && global.AirdChatKeyBackup?.tryRestoreFromConfiguredPath) {
      try {
        const restored = await global.AirdChatKeyBackup.tryRestoreFromConfiguredPath(user);
        if (restored) return identity;
      } catch (err) {
        console.debug('onedrive identity restore failed', err);
      }
    }
    if (stored && stored.privateJwk && stored.publicJwk) {
      identity = {
        publicJwk: stored.publicJwk,
        privateKey: await importPrivate(stored.privateJwk),
        kid: stored.kid || await fingerprint(stored.publicJwk),
      };
      return identity;
    }
    const pair = await crypto.subtle.generateKey(ECDH, true, ['deriveBits']);
    const publicJwk = await exportJwk(pair.publicKey);
    const privateJwk = await exportJwk(pair.privateKey);
    delete publicJwk.d;
    const kid = await fingerprint(publicJwk);
    await idbSet('identity', user, { publicJwk, privateJwk, kid });
    identity = { publicJwk, privateKey: pair.privateKey, kid };
    return identity;
  }

  async function persistIdentity(api) {
    if (!api || !identity) return;
    const bundle = await exportBackup();
    let seenRemote = false;
    let remoteKid = '';
    try {
      remoteKid = (await api('/api/chat/e2e/identity'))?.backup?.kid || '';
      seenRemote = true;
    } catch (_) { /* keep any existing server copy */ }
    if (seenRemote && !remoteKid) {
      await api('/api/chat/e2e/identity', {
        method: 'PUT',
        body: JSON.stringify({ backup: bundle }),
      });
      return;
    }
    await publishIdentity(api);
  }

  async function publishIdentity(api) {
    if (!identity) return;
    await api('/api/chat/e2e/keys', {
      method: 'PUT',
      body: JSON.stringify({ public_jwk: identity.publicJwk }),
    });
  }

  async function wrapFor(rawKey, recipientPubJwk, convId) {
    const eph = await crypto.subtle.generateKey(ECDH, true, ['deriveBits']);
    const theirPub = await importPublic(recipientPubJwk);
    const wrapKey = await deriveWrapKey(eph.privateKey, theirPub, convId);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, wrapKey, rawKey);
    const epk = await exportJwk(eph.publicKey);
    delete epk.d;
    return {
      v: 1,
      kid: await fingerprint(recipientPubJwk),
      epk: { kty: 'EC', crv: 'P-256', x: epk.x, y: epk.y },
      iv: b64(iv),
      ct: b64(ct),
    };
  }

  async function unwrapForMe(wrap, convId) {
    const ephPub = await importPublic(wrap.epk);
    const wrapKey = await deriveWrapKey(identity.privateKey, ephPub, convId);
    const raw = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: unb64(wrap.iv) },
      wrapKey,
      unb64(wrap.ct),
    );
    return new Uint8Array(raw);
  }

  async function importAes(raw) {
    return crypto.subtle.importKey('raw', raw, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
  }

  function bytesEq(a, b) {
    if (!a || !b || a.length !== b.length) return false;
    let x = 0;
    for (let i = 0; i < a.length; i += 1) x |= a[i] ^ b[i];
    return x === 0;
  }

  function asBytes(raw) {
    if (!raw) return null;
    if (raw instanceof Uint8Array) return raw;
    if (raw instanceof ArrayBuffer) return new Uint8Array(raw);
    return new Uint8Array(raw);
  }

  const convCache = new Map();
  const convFallback = new Map();
  const inflight = new Map();

  async function loadConvRaw(convId) {
    const hit = convCache.get(convId);
    if (hit) return hit;
    const stored = await idbGet('convKeys', convId);
    if (stored) {
      convCache.set(convId, stored);
      return stored;
    }
    return null;
  }

  async function saveConvRaw(convId, raw) {
    const copy = raw instanceof Uint8Array ? raw : new Uint8Array(raw);
    const prev = asBytes(convCache.get(convId));
    if (prev && !bytesEq(prev, copy)) {
      const list = convFallback.get(convId) || [];
      list.push(prev);
      convFallback.set(convId, list.slice(-4));
    }
    convCache.set(convId, copy);
    await idbSet('convKeys', convId, copy);
  }

  async function ensureConversation(api, convId, memberNames) {
    const pending = inflight.get(convId);
    if (pending) return pending;
    const run = ensureConversationInner(api, convId, memberNames).finally(() => {
      if (inflight.get(convId) === run) inflight.delete(convId);
    });
    inflight.set(convId, run);
    return run;
  }

  async function ensureConversationInner(api, convId, memberNames) {
    api = api || defaultApi;
    if (!identity) throw new Error('E2E identity missing');
    const state = await api(`/api/chat/conversations/${encodeURIComponent(convId)}/e2e`);
    const wraps = state.wraps || {};
    const keys = state.keys || {};
    const local = asBytes(await loadConvRaw(convId));
    let raw = null;
    if (state.conv_key?.key) {
      try { raw = unb64(state.conv_key.key); } catch (_) { raw = null; }
    }
    const mine = wraps[username];
    if (!raw && mine) {
      try {
        raw = await unwrapForMe(mine, convId);
      } catch (_) {
        raw = null;
      }
      if (!raw) {
        try {
          const backup = (await api('/api/chat/e2e/identity'))?.backup;
          if (backup?.privateJwk) {
            await importBackup(backup, username);
            raw = await unwrapForMe(mine, convId);
          }
        } catch (_) {
          raw = null;
        }
      }
    }
    if (!raw) raw = local;
    const hasExisting = !!(Object.keys(wraps).length || state.conv_key);
    if (!raw) {
      if (hasExisting) return null;
      raw = crypto.getRandomValues(new Uint8Array(32));
    }
    if (!local || !bytesEq(local, raw)) await saveConvRaw(convId, raw);
    let wrapMatchesKey = !mine;
    if (mine && raw) {
      try {
        wrapMatchesKey = bytesEq(asBytes(await unwrapForMe(mine, convId)), raw);
      } catch (_) {
        wrapMatchesKey = false;
      }
    }
    const next = {};
    let changed = false;
    const names = memberNames && memberNames.length ? memberNames : Object.keys(keys);
    for (const name of names) {
      const pub = keys[name];
      if (!pub) continue;
      const kid = await fingerprint(pub);
      const existing = wraps[name];
      if (existing && existing.kid === kid && wrapMatchesKey) {
        next[name] = existing;
        continue;
      }
      next[name] = await wrapFor(raw, pub, convId);
      changed = true;
    }
    if (!changed && state.conv_key?.key) {
      return importAes(raw);
    }
    try {
      const saved = await api(`/api/chat/conversations/${encodeURIComponent(convId)}/e2e`, {
        method: 'PUT',
        body: JSON.stringify({
          wraps: next,
          conv_key: { v: 1, conversation_id: convId, key: b64(raw) },
        }),
      });
      if (saved?.conv_key?.key) {
        const canonical = unb64(saved.conv_key.key);
        if (!bytesEq(raw, canonical)) {
          raw = canonical;
          await saveConvRaw(convId, raw);
          const repaired = {};
          for (const name of names) {
            const pub = keys[name];
            if (!pub) continue;
            repaired[name] = await wrapFor(raw, pub, convId);
          }
          await api(`/api/chat/conversations/${encodeURIComponent(convId)}/e2e`, {
            method: 'PUT',
            body: JSON.stringify({
              wraps: repaired,
              conv_key: { v: 1, conversation_id: convId, key: b64(raw) },
            }),
          });
        }
      }
    } catch (err) {
      if (changed) throw err;
      console.debug('e2e share persist failed', err);
    }
    return importAes(raw);
  }

  const XSRF_RE = /(?:^|;\s*)_xsrf=([^;]+)/;

  function defaultApi(path, opts = {}) {
    const m = XSRF_RE.exec(document.cookie);
    const token = m ? decodeURIComponent(m[1]) : '';
    const { _retry304, ...fetchOpts } = opts;
    const method = String(fetchOpts.method || 'GET').toUpperCase();
    let url = path;
    if (method === 'GET' && !/[?&]_=/.test(path)) {
      url = `${path}${path.includes('?') ? '&' : '?'}_=${Date.now()}`;
    }
    return fetch(url, {
      cache: 'no-store',
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        'Cache-Control': 'no-cache',
        Pragma: 'no-cache',
        ...(fetchOpts.body && !(fetchOpts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(fetchOpts.method && fetchOpts.method !== 'GET' ? { 'X-XSRFToken': token } : {}),
      },
      ...fetchOpts,
    }).then(async (res) => {
      if (res.status === 304) {
        if (_retry304) throw new Error('HTTP 304');
        return defaultApi(path, { ...opts, _retry304: true });
      }
      const text = await res.text();
      let json = {};
      try { json = JSON.parse(text); } catch { json = { error: text }; }
      if (!res.ok) throw new Error(json.error || text || `HTTP ${res.status}`);
      return json;
    });
  }

  function requestKeyShare(convId) {
    if (!convId) return;
    global.AirdChatWS?.send({ type: 'chat_e2e_need_key', conversation_id: convId });
  }

  async function shareIfHaveKey(convId) {
    if (!identity || !convId) return false;
    const raw = await loadConvRaw(convId);
    if (!raw) return false;
    try {
      await ensureConversation(defaultApi, convId, []);
      return true;
    } catch (err) {
      console.debug('auto share conv key failed', err);
      return false;
    }
  }

  function onHubEvent(data) {
    if (data?.type === 'chat_e2e_need_key' && data.conversation_id && data.username !== username) {
      shareIfHaveKey(data.conversation_id);
    }
  }

  function bootBackground() {
    const user = global.__AIRD_CHAT_USER__;
    if (!user || !global.crypto?.subtle) return;
    const start = async () => {
      try {
        if (!identity) await loadOrCreateIdentity(user, defaultApi);
        try { await persistIdentity(defaultApi); } catch (err) { console.debug('e2e persist', err); }
      } catch (err) {
        console.debug('e2e background init failed', err);
      }
      const WS = global.AirdChatWS;
      if (WS) {
        WS.subscribe(onHubEvent);
        WS.connect();
      }
    };
    start();
  }

  async function aesFor(convId) {
    const raw = await loadConvRaw(convId);
    if (!raw) throw new Error('No conversation key');
    return importAes(raw);
  }

  async function encrypt(convId, plaintext) {
    const key = await aesFor(convId);
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const data = new TextEncoder().encode(plaintext || '');
    const ct = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(convId) },
      key,
      data,
    );
    return { v: 1, iv: b64(iv), ct: b64(ct) };
  }

  async function decrypt(convId, payload) {
    if (!payload || payload.v !== 1) throw new Error('Bad payload');
    const iv = unb64(payload.iv);
    const ct = unb64(payload.ct);
    const ad = { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(convId) };
    const primary = asBytes(await loadConvRaw(convId));
    const extras = (convFallback.get(convId) || []).map(asBytes).filter(Boolean);
    const seen = [];
    const candidates = [primary, ...extras].filter((raw) => {
      if (!raw) return false;
      if (seen.some((s) => bytesEq(s, raw))) return false;
      seen.push(raw);
      return true;
    });
    let lastErr = new Error('No conversation key');
    for (const raw of candidates) {
      try {
        const key = await importAes(raw);
        const pt = await crypto.subtle.decrypt(ad, key, ct);
        return new TextDecoder().decode(pt);
      } catch (err) {
        lastErr = err;
      }
    }
    throw lastErr;
  }

  const ALLOWED = new Set(['P', 'BR', 'STRONG', 'EM', 'A', 'CODE']);

  function sanitizeHtml(html) {
    const tpl = document.createElement('template');
    tpl.innerHTML = String(html || '');
    const walk = (node) => {
      [...node.childNodes].forEach((child) => {
        if (child.nodeType === 1) {
          if (!ALLOWED.has(child.tagName)) {
            const parent = child.parentNode;
            while (child.firstChild) parent.insertBefore(child.firstChild, child);
            parent.removeChild(child);
            return;
          }
          [...child.attributes].forEach((attr) => {
            const n = attr.name.toLowerCase();
            if (child.tagName === 'A' && (n === 'href' || n === 'title' || n === 'rel')) {
              if (n === 'href' && !/^(https?:|mailto:)/i.test(attr.value)) child.removeAttribute(attr.name);
              return;
            }
            child.removeAttribute(attr.name);
          });
          if (child.tagName === 'A') child.setAttribute('rel', 'noopener noreferrer');
          walk(child);
        } else if (child.nodeType === 8) {
          child.remove();
        }
      });
    };
    walk(tpl.content);
    return tpl.innerHTML;
  }

  function extractMentions(text, members) {
    const allowed = new Set(members || []);
    const found = [];
    const re = /@([A-Za-z0-9_.\-@]+)/g;
    let m;
    while ((m = re.exec(text || ''))) {
      if (allowed.has(m[1]) && !found.includes(m[1])) found.push(m[1]);
    }
    return found;
  }

  function backupFilename(user) {
    const safe = String(user || username || 'user').replace(/[^\w.-]+/g, '_');
    return `aird-chat-e2e-key-${safe}.json`;
  }

  async function exportBackup() {
    const user = username;
    if (!user) throw new Error('Not signed in');
    const stored = await idbGet('identity', user);
    if (!stored?.privateJwk) throw new Error('No encryption key on this device');
    return {
      v: 1,
      kind: 'aird-chat-e2e-identity',
      username: user,
      exported_at: new Date().toISOString(),
      publicJwk: stored.publicJwk,
      privateJwk: stored.privateJwk,
      kid: stored.kid || await fingerprint(stored.publicJwk),
    };
  }

  async function importBackup(bundle, user) {
    if (!bundle || bundle.kind !== 'aird-chat-e2e-identity' || bundle.v !== 1) {
      throw new Error('Invalid key backup file');
    }
    if (!bundle.publicJwk || !bundle.privateJwk || !bundle.kid) {
      throw new Error('Backup file is missing key material');
    }
    const owner = String(bundle.username || user || '').trim();
    if (!owner) throw new Error('Backup file has no username');
    if (user && owner !== user) {
      throw new Error(`This backup belongs to ${owner}, not ${user}`);
    }
    await idbSet('identity', owner, {
      publicJwk: bundle.publicJwk,
      privateJwk: bundle.privateJwk,
      kid: bundle.kid,
    });
    username = owner;
    identity = {
      publicJwk: bundle.publicJwk,
      privateKey: await importPrivate(bundle.privateJwk),
      kid: bundle.kid,
    };
    return identity;
  }

  async function hasStoredIdentity(user) {
    const stored = await idbGet('identity', user);
    return !!(stored?.privateJwk && stored?.publicJwk);
  }

  async function downloadBackup() {
    const bundle = await exportBackup();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = backupFilename(bundle.username);
    a.click();
    URL.revokeObjectURL(url);
    try {
      localStorage.setItem('aird.chat.e2e.backupAt', bundle.exported_at);
    } catch (_) { /* */ }
    return bundle;
  }

  global.AirdChatE2E = {
    async init(user, api) {
      if (!user || !global.crypto?.subtle) throw new Error('Web Crypto required for encrypted chat');
      await loadOrCreateIdentity(user, api || defaultApi);
      try {
        await persistIdentity(api || defaultApi);
      } catch (err) {
        console.warn('chat e2e persist', err);
        try { await publishIdentity(api || defaultApi); } catch (_) { /* */ }
      }
      return identity.publicJwk;
    },
    ensureConversation,
    requestKeyShare,
    hasConvKey: (convId) => convCache.has(convId),
    encrypt,
    decrypt,
    sanitizeHtml,
    extractMentions,
    ready: () => !!identity,
    exportBackup,
    importBackup,
    hasStoredIdentity,
    downloadBackup,
    backupFilename,
    publishIdentity: (api) => publishIdentity(api || defaultApi),
  };

  if (global.document.readyState === 'loading') {
    global.document.addEventListener('DOMContentLoaded', bootBackground);
  } else {
    bootBackground();
  }
})(window);
