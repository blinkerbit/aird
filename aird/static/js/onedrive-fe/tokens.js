'use strict';

const DB_NAME = 'aird-onedrive-fe';
const DB_VER = 1;
const STORE = 'tokens';
const LS_MIRROR = 'aird.onedriveFe.tokens';

let dbPromise = null;

function openDb() {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VER);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE);
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return dbPromise;
}

export async function loadTokens() {
  try {
    const db = await openDb();
    const fromIdb = await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get('session');
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
    if (fromIdb) return fromIdb;
  } catch { /* */ }
  try {
    return JSON.parse(localStorage.getItem(LS_MIRROR) || 'null');
  } catch {
    return null;
  }
}

export async function saveTokens(tokens) {
  const db = await openDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite');
    tx.objectStore(STORE).put(tokens, 'session');
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  try {
    localStorage.setItem(LS_MIRROR, JSON.stringify(tokens));
  } catch { /* */ }
}

export async function clearTokens() {
  try {
    const db = await openDb();
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete('session');
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  } catch { /* */ }
  try { localStorage.removeItem(LS_MIRROR); } catch { /* */ }
}

export async function isSignedIn() {
  const t = await loadTokens();
  return !!(t?.access_token || t?.refresh_token);
}
