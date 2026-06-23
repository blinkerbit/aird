/**
 * IndexedDB persistence for in-progress ranged uploads (survives page reload).
 */
(function (global) {
  'use strict';

  const DB_NAME = 'aird-transfer';
  const DB_VERSION = 1;
  const STORE = 'uploads';

  function openDb() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onerror = () => reject(req.error);
      req.onsuccess = () => resolve(req.result);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(STORE)) {
          db.createObjectStore(STORE, { keyPath: 'jobId' });
        }
      };
    });
  }

  async function saveJob(job) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).put(job);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function getJob(jobId) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).get(jobId);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
  }

  async function listJobs() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readonly');
      const req = tx.objectStore(STORE).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = () => reject(req.error);
    });
  }

  async function deleteJob(jobId) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite');
      tx.objectStore(STORE).delete(jobId);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  }

  async function clearCompleted() {
    const jobs = await listJobs();
    await Promise.all(
      jobs.filter((j) => j.status === 'complete').map((j) => deleteJob(j.jobId))
    );
  }

  global.AirdResumeStore = {
    saveJob,
    getJob,
    listJobs,
    deleteJob,
    clearCompleted,
  };
})(typeof globalThis !== 'undefined' ? globalThis : window);
