'use strict';

const LS_DEFAULT = 'aird.onedriveFe.defaultSave';
const LS_CONFLICT = 'aird.onedriveFe.conflict';
const LS_AUTH_MODE = 'aird.onedriveFe.authMode';
const LS_APP = 'aird.onedriveFe.app';
export const DEFAULT_PATH = 'aird/datacopy';

export function getDefaultSave() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_DEFAULT) || 'null');
    if (raw?.path) return raw;
  } catch { /* */ }
  return { path: DEFAULT_PATH, folderId: '' };
}

export function setDefaultSave({ path, folderId } = {}) {
  const parts = String(path || DEFAULT_PATH).split('/').filter(Boolean);
  const norm = parts.join('/') || DEFAULT_PATH;
  const next = { path: norm, folderId: folderId || '', savedAt: Date.now() };
  localStorage.setItem(LS_DEFAULT, JSON.stringify(next));
  return next;
}

export function clearDefaultSave() {
  localStorage.removeItem(LS_DEFAULT);
}

export function getConflictPolicy() {
  const v = localStorage.getItem(LS_CONFLICT) || 'replace';
  return ['replace', 'rename', 'fail'].includes(v) ? v : 'replace';
}

export function setConflictPolicy(value) {
  const v = ['replace', 'rename', 'fail'].includes(value) ? value : 'replace';
  localStorage.setItem(LS_CONFLICT, v);
  return v;
}

export function graphConflictBehavior(policy) {
  if (policy === 'rename') return 'rename';
  if (policy === 'fail') return 'fail';
  return 'replace';
}

export function getAuthMode() {
  return localStorage.getItem(LS_AUTH_MODE) === 'device' ? 'device' : 'redirect';
}

export function setAuthMode(mode) {
  localStorage.setItem(LS_AUTH_MODE, mode === 'device' ? 'device' : 'redirect');
}

const CLIENT_ID_RE = /^[0-9a-fA-F-]{8,80}$/;

export function getLocalAppConfig() {
  try {
    const raw = JSON.parse(localStorage.getItem(LS_APP) || 'null');
    if (raw?.clientId && CLIENT_ID_RE.test(raw.clientId)) return raw;
  } catch { /* */ }
  return null;
}

export function setLocalAppConfig({ clientId, tenant } = {}) {
  const id = String(clientId || '').trim();
  if (!CLIENT_ID_RE.test(id)) throw new Error('Enter a valid Azure application (client) ID.');
  const next = {
    clientId: id,
    tenant: String(tenant || 'common').trim() || 'common',
    savedAt: Date.now(),
  };
  localStorage.setItem(LS_APP, JSON.stringify(next));
  globalThis.__ONEDRIVE_BROWSER_CONFIG = {
    enabled: true,
    clientId: next.clientId,
    tenant: next.tenant,
  };
  return next;
}

export function clearLocalAppConfig() {
  localStorage.removeItem(LS_APP);
}
