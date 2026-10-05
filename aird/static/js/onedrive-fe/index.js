'use strict';

import { clearTokens, isSignedIn, loadTokens } from './tokens.js';
import { ensureSignedIn, ensureAccessToken, finishCallback, startRedirectLogin, startDeviceCode, pollDeviceCode } from './auth.js';
import { getDefaultSave, setDefaultSave, getConflictPolicy, setConflictPolicy, getAuthMode, setAuthMode, getLocalAppConfig, setLocalAppConfig, clearLocalAppConfig, DEFAULT_PATH } from './settings.js';
import {
  ensureFolderPath,
  resolveDefaultFolderId,
  uploadTree,
  showFolderPicker,
  listChildren,
  downloadItemText,
  listFolders,
  uploadBlob,
  showFilePicker,
} from './graph.js';

const PENDING = 'aird.onedriveFe.pending';

export function savePendingAction(action) {
  sessionStorage.setItem(PENDING, JSON.stringify(action));
}

export function loadPendingAction() {
  try {
    return JSON.parse(sessionStorage.getItem(PENDING) || 'null');
  } catch {
    return null;
  }
}

export function clearPendingAction() {
  sessionStorage.removeItem(PENDING);
}

function reportProgress(onProgress, info) {
  if (onProgress) onProgress(info);
  const TT = globalThis.AirdTransferTracker;
  if (TT?.updateOneDriveCopy) TT.updateOneDriveCopy(info);
}

export async function copyItems(items, { folderId, onProgress } = {}) {
  const token = await ensureSignedIn({ returnTo: globalThis.location.href });
  if (!token) return null;
  let uploaded = 0;
  for (const item of items) {
    uploaded += await uploadTree(token, folderId, item.path, !!item.isDir, (info) => {
      reportProgress(onProgress, { ...info, item: item.path });
    });
  }
  return { uploaded, folderId };
}

export async function copyToDefault(items, opts = {}) {
  const token = await ensureSignedIn({ returnTo: globalThis.location.href });
  if (!token) return null;
  let folderId = await resolveDefaultFolderId(token);
  if (!folderId) {
    const parts = getDefaultSave().path.split('/').filter(Boolean);
    folderId = await ensureFolderPath(token, parts.length ? parts : DEFAULT_PATH.split('/'));
    setDefaultSave({ path: parts.join('/') || DEFAULT_PATH, folderId });
  }
  return copyItems(items, { folderId, onProgress: opts.onProgress });
}

export async function copyToFolder(items, opts = {}) {
  const token = await ensureSignedIn({ returnTo: globalThis.location.href });
  if (!token) return null;
  return showFolderPicker({
    token,
    title: 'Copy to OneDrive folder',
    onPick: async (pickedId, names) => {
      const path = Array.isArray(names) && names.length > 1 ? names.slice(1).join('/') : '';
      if (path) setDefaultSave({ path, folderId: pickedId });
      return copyItems(items, { folderId: pickedId, onProgress: opts.onProgress });
    },
  });
}

export async function resumePendingAction() {
  const pending = loadPendingAction();
  if (!pending?.items?.length) return null;
  clearPendingAction();
  const token = await ensureAccessToken();
  if (!token) return null;
  if (pending.action === 'copyFolder') {
    return copyToFolder(pending.items, pending.opts);
  }
  return copyToDefault(pending.items, pending.opts);
}

function appReturnPath(value) {
  const fallback = '/files/';
  try {
    const url = new URL(value || fallback, globalThis.location.origin);
    if (url.origin !== globalThis.location.origin || !url.pathname.startsWith('/')) return fallback;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return fallback;
  }
}

export async function runWithAuth(action, items, opts = {}) {
  savePendingAction({ action, items, opts });
  try {
    const token = await ensureSignedIn({ returnTo: globalThis.location.href });
    if (!token) return null;
    clearPendingAction();
    if (action === 'copyFolder') return copyToFolder(items, opts);
    return copyToDefault(items, opts);
  } catch (err) {
    clearPendingAction();
    throw err;
  }
}

export async function initOneDriveFeUi() {
  if (globalThis.location.pathname === '/onedrive-browser/callback') {
    try {
      const returnTo = await finishCallback();
      const next = appReturnPath(returnTo);
      if (next.startsWith('/')) globalThis.location.replace(next);
    } catch (err) {
      const msg = document.getElementById('odCbMsg');
      if (msg) msg.textContent = err.message || 'OneDrive sign-in failed';
    }
    return;
  }
  if (!globalThis.__ONEDRIVE_BROWSER_CONFIG?.enabled) return;
  const pending = loadPendingAction();
  if (pending?.items?.length && await isSignedIn()) {
    try {
      const result = await resumePendingAction();
      if (result?.uploaded != null) {
        const { showDialog } = await import('../browse/util.js');
        showDialog(`Copied ${result.uploaded} file(s) to OneDrive.`, 'OneDrive');
      }
    } catch (err) {
      console.warn('onedrive-fe resume', err);
    }
  }
}

export async function signOut() {
  await clearTokens();
}

function isSignedInSync() {
  try {
    const t = JSON.parse(localStorage.getItem('aird.onedriveFe.tokens') || 'null');
    return !!(t?.access_token || t?.refresh_token);
  } catch {
    return false;
  }
}

export {
  isSignedIn,
  loadTokens,
  ensureAccessToken,
  ensureSignedIn,
  startRedirectLogin,
  startDeviceCode,
  pollDeviceCode,
  getDefaultSave,
  setDefaultSave,
  getConflictPolicy,
  setConflictPolicy,
  getAuthMode,
  setAuthMode,
  getLocalAppConfig,
  setLocalAppConfig,
  clearLocalAppConfig,
  showFolderPicker,
  ensureFolderPath,
  listChildren,
  downloadItemText,
  listFolders,
  DEFAULT_PATH,
};

globalThis.AirdOneDriveFe = {
  copyToDefault,
  copyToFolder,
  runWithAuth,
  resumePendingAction,
  initOneDriveFeUi,
  signOut,
  isSignedIn,
  isSignedInSync,
  ensureSignedIn,
  ensureAccessToken,
  getDefaultSave,
  setDefaultSave,
  getConflictPolicy,
  setConflictPolicy,
  getLocalAppConfig,
  setLocalAppConfig,
  showFolderPicker,
  listChildren,
  downloadItemText,
  startDeviceCode,
  pollDeviceCode,
  startRedirectLogin,
  uploadBlob,
  showFilePicker,
};
