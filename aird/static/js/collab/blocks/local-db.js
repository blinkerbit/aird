const DB_NAME = 'aird-list-blocks';
const DB_VERSION = 1;

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains('blocks')) {
        const blocks = db.createObjectStore('blocks', { keyPath: 'id' });
        blocks.createIndex('listId', 'listId', { unique: false });
      }
      if (!db.objectStoreNames.contains('ops')) {
        const ops = db.createObjectStore('ops', { keyPath: 'opId' });
        ops.createIndex('listId', 'listId', { unique: false });
        ops.createIndex('pending', 'pending', { unique: false });
      }
      if (!db.objectStoreNames.contains('meta')) {
        db.createObjectStore('meta', { keyPath: 'listId' });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function txDone(tx) {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
}

export async function saveBlocks(listId, items) {
  const db = await openDb();
  const tx = db.transaction(['blocks', 'meta'], 'readwrite');
  const store = tx.objectStore('blocks');
  const existing = await new Promise((resolve) => {
    const req = store.index('listId').getAll(listId);
    req.onsuccess = () => resolve(req.result || []);
  });
  existing.forEach((row) => store.delete(row.id));
  items.forEach((item) => store.put({ ...item, listId }));
  tx.objectStore('meta').put({ listId, tipHlc: '', updatedAt: Date.now() });
  await txDone(tx);
}

export async function loadBlocks(listId) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = db.transaction('blocks').objectStore('blocks').index('listId').getAll(listId);
    req.onsuccess = () => {
      const rows = (req.result || []).slice().sort((a, b) => String(a.order).localeCompare(String(b.order)));
      resolve(rows);
    };
    req.onerror = () => reject(req.error);
  });
}

export async function queueOps(ops) {
  if (!ops?.length) return;
  const db = await openDb();
  const tx = db.transaction('ops', 'readwrite');
  const store = tx.objectStore('ops');
  ops.forEach((op) => store.put({ ...op, pending: 1 }));
  await txDone(tx);
}

export async function pendingOps(listId) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = db.transaction('ops').objectStore('ops').index('listId').getAll(listId);
    req.onsuccess = () => resolve((req.result || []).filter((row) => row.pending));
    req.onerror = () => reject(req.error);
  });
}

export async function clearPending(opIds) {
  if (!opIds?.length) return;
  const db = await openDb();
  const tx = db.transaction('ops', 'readwrite');
  const store = tx.objectStore('ops');
  for (const id of opIds) {
    const row = await new Promise((resolve) => {
      const req = store.get(id);
      req.onsuccess = () => resolve(req.result);
    });
    if (row) store.put({ ...row, pending: 0 });
  }
  await txDone(tx);
}

export async function getTip(listId) {
  const db = await openDb();
  return new Promise((resolve) => {
    const req = db.transaction('meta').objectStore('meta').get(listId);
    req.onsuccess = () => resolve(req.result?.tipHlc || '');
  });
}

export async function setTip(listId, tipHlc) {
  const db = await openDb();
  const tx = db.transaction('meta', 'readwrite');
  tx.objectStore('meta').put({ listId, tipHlc, updatedAt: Date.now() });
  await txDone(tx);
}
