/**
 * Chat cryptography primitives — canonical implementations.
 *
 * Both chat flavors (server-backed E2E in `chat-e2e.js` and tab-local
 * Enhanced Secure Chat in `chat-secret.js`) share base64url-tolerant
 * encoding plus P-256 ECDH key handling. The HKDF domain-separation
 * strings stay in the callers on purpose: each flavor must derive
 * different keys. Classic scripts use these via `globalThis.AirdCore`.
 */

/** Standard base64 encode of an ArrayBuffer or Uint8Array. */
export function b64encode(buf) {
  const bytes = buf instanceof ArrayBuffer ? new Uint8Array(buf) : buf;
  let s = '';
  for (let i = 0; i < bytes.length; i += 1) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

/** Base64 decode tolerant of unpadded and URL-safe (`-_/`) input. */
export function b64decode(str) {
  const n = String(str || '').replace(/-/g, '+').replace(/_/g, '/');
  const pad = n.length % 4 === 0 ? '' : '='.repeat(4 - (n.length % 4));
  const bin = atob(n + pad);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) out[i] = bin.charCodeAt(i);
  return out;
}

const ECDH_P256 = { name: 'ECDH', namedCurve: 'P-256' };

/** Import a P-256 public JWK (`{kty, crv, x, y}`) for ECDH. */
export function ecdhImportPublic(jwk) {
  return crypto.subtle.importKey(
    'jwk',
    { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y },
    ECDH_P256,
    true,
    [],
  );
}

/** Import a P-256 private JWK for ECDH key derivation. */
export function ecdhImportPrivate(jwk) {
  return crypto.subtle.importKey('jwk', jwk, ECDH_P256, true, ['deriveBits']);
}

/** Generate an ephemeral P-256 pair; returns public/private JWKs + key. */
export async function ecdhGenerateKey() {
  const pair = await crypto.subtle.generateKey(ECDH_P256, true, ['deriveBits']);
  const publicJwk = await crypto.subtle.exportKey('jwk', pair.publicKey);
  const privateJwk = await crypto.subtle.exportKey('jwk', pair.privateKey);
  delete publicJwk.d;
  return {
    publicJwk: { kty: 'EC', crv: 'P-256', x: publicJwk.x, y: publicJwk.y },
    privateJwk,
    privateKey: pair.privateKey,
  };
}

/**
 * ECDH → HKDF-SHA-256 → AES-GCM-256.
 *
 * @param {CryptoKey} privateKey our ECDH private key.
 * @param {CryptoKey} publicKey peer ECDH public key.
 * @param {string} saltText per-conversation salt (conv/room id).
 * @param {Uint8Array} info domain-separation bytes (caller-owned).
 */
export async function ecdhDeriveAesKey(privateKey, publicKey, saltText, info) {
  const bits = await crypto.subtle.deriveBits(
    { name: 'ECDH', public: publicKey },
    privateKey,
    256,
  );
  const hkdf = await crypto.subtle.importKey('raw', bits, 'HKDF', false, ['deriveKey']);
  return crypto.subtle.deriveKey(
    { name: 'HKDF', hash: 'SHA-256', salt: new TextEncoder().encode(saltText), info },
    hkdf,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  );
}
