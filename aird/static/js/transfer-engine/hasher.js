/**
 * Chunk hashing via Web Crypto (runs in Worker; SIMD-free, native speed).
 */
(function (global) {
  'use strict';

  async function sha256Hex(buffer) {
    const digest = await global.crypto.subtle.digest('SHA-256', buffer);
    const bytes = new Uint8Array(digest);
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, '0');
    }
    return hex;
  }

  async function hashChunk(blob) {
    const buf = await blob.arrayBuffer();
    return sha256Hex(buf);
  }

  global.AirdHasher = { sha256Hex, hashChunk };
})(typeof self !== 'undefined' ? self : globalThis);
