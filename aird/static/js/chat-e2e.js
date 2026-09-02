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
    const bin = atob(str);
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

  async function loadOrCreateIdentity(user) {
    username = user;
    const stored = await idbGet('identity', user);
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

  const convCache = new Map();

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
    convCache.set(convId, copy);
    await idbSet('convKeys', convId, copy);
  }

  async function ensureConversation(api, convId, memberNames) {
    if (!identity) throw new Error('E2E identity missing');
    const state = await api(`/api/chat/conversations/${encodeURIComponent(convId)}/e2e`);
    const wraps = state.wraps || {};
    const keys = state.keys || {};
    let raw = await loadConvRaw(convId);
    const mine = wraps[username];
    if (!raw && mine) {
      try {
        raw = await unwrapForMe(mine, convId);
        await saveConvRaw(convId, raw);
      } catch (_) {
        raw = null;
      }
    }
    if (!raw) {
      if (Object.keys(wraps).length) {
        throw new Error('Waiting for a current member to share the encryption key with this device.');
      }
      raw = crypto.getRandomValues(new Uint8Array(32));
      await saveConvRaw(convId, raw);
    }
    const next = {};
    let changed = false;
    const names = memberNames && memberNames.length ? memberNames : Object.keys(keys);
    for (const name of names) {
      const pub = keys[name];
      if (!pub) continue;
      const kid = await fingerprint(pub);
      const existing = wraps[name];
      if (existing && existing.kid === kid) {
        next[name] = existing;
        continue;
      }
      next[name] = await wrapFor(raw, pub, convId);
      changed = true;
    }
    if (changed) {
      await api(`/api/chat/conversations/${encodeURIComponent(convId)}/e2e`, {
        method: 'PUT',
        body: JSON.stringify({ wraps: next }),
      });
    }
    return importAes(raw);
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
    const key = await aesFor(convId);
    const pt = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: unb64(payload.iv), additionalData: new TextEncoder().encode(convId) },
      key,
      unb64(payload.ct),
    );
    return new TextDecoder().decode(pt);
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
      await loadOrCreateIdentity(user);
      await publishIdentity(api);
      return identity.publicJwk;
    },
    ensureConversation,
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
    publishIdentity: (api) => publishIdentity(api),
  };
})(window);
