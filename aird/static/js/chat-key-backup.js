/**
 * Chat E2E key backup — browser only; private keys never reach the server.
 */
(function (global) {
  'use strict';

  const LS_BACKUP_AT = 'aird.chat.e2e.backupAt';
  const LS_OD_MAP = 'aird.chat.e2e.onedriveMap';
  const LS_OD_PATH = 'aird.chat.e2e.onedrivePath';
  const DEFAULT_OD_PARTS = ['Aird', 'chat-keys'];

  function me() {
    return global.__AIRD_CHAT_USER__ || '';
  }

  function e2e() {
    return global.AirdChatE2E;
  }

  function od() {
    return global.AirdOneDriveFe;
  }

  function odEnabled() {
    return !!global.__ONEDRIVE_BROWSER_CONFIG?.enabled || !!od()?.ensureAccessToken;
  }

  function pathPartsFromString(pathStr) {
    return String(pathStr || '').split('/').filter(Boolean);
  }

  function getOdPathParts() {
    try {
      const raw = localStorage.getItem(LS_OD_PATH);
      const parts = pathPartsFromString(raw);
      if (parts.length) return parts;
    } catch (_) { /* */ }
    return DEFAULT_OD_PARTS.slice();
  }

  function getOdPathString() {
    try {
      const raw = localStorage.getItem(LS_OD_PATH);
      if (raw) return raw.replace(/^\/+|\/+$/g, '');
    } catch (_) { /* */ }
    return DEFAULT_OD_PARTS.join('/');
  }

  function saveConfiguredPath(pathStr, folderId, username) {
    const norm = pathPartsFromString(pathStr).join('/');
    if (!norm) throw new Error('Enter a folder path');
    localStorage.setItem(LS_OD_PATH, norm);
    if (folderId) saveOdMap(folderId, username || me());
    return norm;
  }

  function loadOdMap() {
    try {
      return JSON.parse(localStorage.getItem(LS_OD_MAP) || 'null');
    } catch {
      return null;
    }
  }

  function saveOdMap(folderId, username) {
    try {
      localStorage.setItem(LS_OD_MAP, JSON.stringify({
        folderId,
        username,
        path: getOdPathString(),
        savedAt: Date.now(),
      }));
    } catch (_) { /* */ }
  }

  function backupStatusText() {
    const at = localStorage.getItem(LS_BACKUP_AT);
    const path = getOdPathString();
    if (at) {
      try {
        return `Last backup on this browser: ${new Date(at).toLocaleString()} · OneDrive path: ${path}`;
      } catch {
        return `A backup was recorded on this browser. OneDrive path: ${path}`;
      }
    }
    return `No backup recorded on this browser yet. OneDrive path: ${path}`;
  }

  function setMsg(el, text, isErr) {
    if (!el) return;
    el.textContent = text || '';
    el.classList.toggle('text-error', !!isErr);
    el.classList.toggle('text-success', !!text && !isErr);
    el.hidden = !text;
  }

  async function importBundle(bundle) {
    const E2E = e2e();
    if (!E2E?.importBackup) throw new Error('Encryption module not loaded');
    await E2E.importBackup(bundle, me());
    global.location.reload();
  }

  async function downloadKey(statusEl) {
    const E2E = e2e();
    if (!E2E?.downloadBackup) throw new Error('Encryption module not loaded');
    setMsg(statusEl, 'Preparing download…');
    await E2E.downloadBackup();
    setMsg(statusEl, 'Key downloaded. Store the file somewhere safe.');
  }

  async function backupToOneDrive(statusEl) {
    const E2E = e2e();
    const OD = od();
    if (!E2E?.exportBackup || !OD?.ensureAccessToken) {
      throw new Error('OneDrive browser backup is not available');
    }
    setMsg(statusEl, 'Connecting to OneDrive…');
    const bundle = await E2E.exportBackup();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const filename = E2E.backupFilename(bundle.username);
    const token = await OD.ensureAccessToken();
    if (!token) return;
    setMsg(statusEl, 'Choose a folder in OneDrive…');
    await OD.showFolderPicker({
      token,
      title: 'Backup chat key to OneDrive',
      onPick: async (folderId, names) => {
        await OD.uploadBlob(token, folderId, filename, blob);
        try {
          localStorage.setItem(LS_BACKUP_AT, bundle.exported_at);
        } catch (_) { /* */ }
        const path = Array.isArray(names) && names.length > 1
          ? names.slice(1).join('/')
          : getOdPathString();
        saveConfiguredPath(path, folderId, bundle.username);
        setMsg(statusEl, `Backed up to OneDrive as ${filename}.`);
      },
    });
  }

  async function backupToConfiguredOneDrive(statusEl) {
    const E2E = e2e();
    const OD = od();
    if (!E2E?.exportBackup || !OD?.ensureFolderPath) {
      throw new Error('OneDrive browser backup is not available');
    }
    const parts = getOdPathParts();
    setMsg(statusEl, 'Connecting to OneDrive…');
    const bundle = await E2E.exportBackup();
    const blob = new Blob([JSON.stringify(bundle, null, 2)], { type: 'application/json' });
    const filename = E2E.backupFilename(bundle.username);
    const token = await OD.ensureAccessToken();
    if (!token) return;
    setMsg(statusEl, `Saving to OneDrive/${parts.join('/')}…`);
    const folderId = await OD.ensureFolderPath(token, parts);
    await OD.uploadBlob(token, folderId, filename, blob);
    try {
      localStorage.setItem(LS_BACKUP_AT, bundle.exported_at);
    } catch (_) { /* */ }
    saveOdMap(folderId, bundle.username);
    setMsg(statusEl, `Backed up to OneDrive (${parts.join('/')}/${filename}).`);
  }

  async function restoreKeyFromFolder(token, folderId, username, statusEl) {
    const E2E = e2e();
    const OD = od();
    const name = username || me();
    const filename = E2E.backupFilename(name);
    const kids = await OD.listChildren(token, folderId);
    const hit = kids.find((item) => item.file && item.name === filename);
    if (!hit) throw new Error(`Could not find ${filename} in ${getOdPathString()}`);
    if (statusEl) setMsg(statusEl, 'Downloading key…');
    const text = await OD.downloadItemText(token, hit.id);
    await importBundle(JSON.parse(text));
  }

  async function restoreFromMappedOneDrive(statusEl) {
    const mapped = loadOdMap();
    const user = me();
    const OD = od();
    if (!mapped?.folderId || mapped.username !== user) {
      return restoreFromConfiguredPath(statusEl, user);
    }
    if (!OD?.ensureAccessToken) throw new Error('OneDrive browser is not available');
    setMsg(statusEl, 'Connecting to OneDrive…');
    const token = await OD.ensureAccessToken();
    if (!token) return;
    await restoreKeyFromFolder(token, mapped.folderId, user, statusEl);
  }

  async function restoreFromConfiguredPath(statusEl, username) {
    const OD = od();
    const parts = getOdPathParts();
    if (!OD?.ensureAccessToken || !OD.ensureFolderPath) {
      throw new Error('OneDrive browser is not available');
    }
    setMsg(statusEl, 'Connecting to OneDrive…');
    const token = await OD.ensureAccessToken();
    if (!token) return;
    setMsg(statusEl, `Looking in OneDrive/${parts.join('/')}…`);
    const folderId = await OD.ensureFolderPath(token, parts);
    saveOdMap(folderId, username || me());
    await restoreKeyFromFolder(token, folderId, username || me(), statusEl);
  }

  async function tryRestoreFromConfiguredPath(username) {
    const E2E = e2e();
    const user = username || me();
    if (!user || !E2E?.hasStoredIdentity) return false;
    if (await E2E.hasStoredIdentity(user)) return false;
    if (!odEnabled()) return false;
    try {
      await restoreFromConfiguredPath(null, user);
      return true;
    } catch (_) {
      return false;
    }
  }

  async function restoreFromOneDrive(statusEl) {
    const OD = od();
    if (!OD?.ensureAccessToken || !OD.showFilePicker) {
      throw new Error('OneDrive browser is not available');
    }
    setMsg(statusEl, 'Connecting to OneDrive…');
    const token = await OD.ensureAccessToken();
    if (!token) return;
    setMsg(statusEl, 'Pick your key backup file…');
    await new Promise((resolve, reject) => {
      OD.showFilePicker({
        token,
        title: 'Restore encryption key',
        filter: (file) => /\.json$/i.test(file.name) && /aird-chat-e2e-key/i.test(file.name),
        onPick: async (file) => {
          try {
            setMsg(statusEl, 'Downloading key…');
            const text = await OD.downloadItemText(token, file.id);
            await importBundle(JSON.parse(text));
            resolve();
          } catch (err) {
            reject(err);
          }
        },
      });
    });
  }

  async function restoreFromFileInput(file, statusEl) {
    if (!file) return;
    setMsg(statusEl, 'Reading backup file…');
    const text = await file.text();
    await importBundle(JSON.parse(text));
  }

  function wireAction(btn, handler, statusEl) {
    if (!btn) return;
    btn.addEventListener('click', () => {
      handler(statusEl).catch((err) => setMsg(statusEl, err.message || 'Action failed', true));
    });
  }

  function refreshDialog(dialog, statusEl) {
    const statusLine = dialog.querySelector('[data-chat-e2e-backup-status]');
    if (statusLine) statusLine.textContent = backupStatusText();
    const mapped = loadOdMap();
    const quickBtn = dialog.querySelector('#chatE2eRestoreMappedBtn');
    if (quickBtn) {
      const show = !!(mapped?.folderId && mapped.username === me()) || !!getOdPathString();
      quickBtn.hidden = !show || !odEnabled();
      quickBtn.textContent = mapped?.folderId
        ? 'Restore from mapped OneDrive folder'
        : `Restore from OneDrive/${getOdPathString()}`;
    }
    dialog.querySelectorAll('[data-chat-e2e-od-section]').forEach((el) => {
      el.hidden = !odEnabled();
    });
    const signed = !!od()?.isSignedInSync?.() || !!od()?.isSignedIn?.();
    if (!odEnabled()) {
      setMsg(statusEl, 'OneDrive browser plugin is off. Ask an admin to enable it, then sign in from Settings.', true);
    } else if (!signed) {
      setMsg(statusEl, 'Not signed in to OneDrive. Open your username → Settings → OneDrive browser to sign in, or click Back up to sign in now.');
    } else {
      setMsg(statusEl, '');
    }
  }

  function init() {
    const helpBtn = document.getElementById('chatE2eHelpBtn');
    const dialog = document.getElementById('chatE2eHelpDialog');
    if (!helpBtn || !dialog) return;

    const statusEl = dialog.querySelector('#chatE2eKeyStatus');
    const fileInput = dialog.querySelector('#chatE2eKeyFileInput');

    helpBtn.addEventListener('click', () => {
      refreshDialog(dialog, statusEl);
      if (typeof dialog.showModal === 'function') dialog.showModal();
    });

    dialog.querySelectorAll('[data-chat-e2e-close]').forEach((btn) => {
      btn.addEventListener('click', () => dialog.close());
    });

    wireAction(dialog.querySelector('#chatE2eDownloadBtn'), downloadKey, statusEl);
    wireAction(dialog.querySelector('#chatE2eBackupOdBtn'), backupToConfiguredOneDrive, statusEl);
    wireAction(dialog.querySelector('#chatE2eBackupOdPickBtn'), backupToOneDrive, statusEl);
    wireAction(dialog.querySelector('#chatE2eRestoreOdBtn'), restoreFromOneDrive, statusEl);
    wireAction(dialog.querySelector('#chatE2eRestoreMappedBtn'), restoreFromMappedOneDrive, statusEl);

    if (fileInput) {
      fileInput.addEventListener('change', () => {
        const file = fileInput.files?.[0];
        fileInput.value = '';
        restoreFromFileInput(file, statusEl).catch((err) => setMsg(statusEl, err.message || 'Restore failed', true));
      });
    }
  }

  function initProfile(opts) {
    const username = opts?.username || '';
    const pathInput = document.getElementById('chatKeyOdPath');
    const msgEl = document.getElementById('chatKeyOdMsg');
    const saveBtn = document.getElementById('chatKeySavePathBtn');
    const pickBtn = document.getElementById('chatKeyPickOdBtn');
    const restoreBtn = document.getElementById('chatKeyRestoreOdBtn');
    if (!pathInput) return;

    pathInput.value = getOdPathString();

    if (saveBtn) {
      saveBtn.addEventListener('click', () => {
        try {
          const saved = saveConfiguredPath(pathInput.value, null, username);
          pathInput.value = saved;
          setMsg(msgEl, `Saved OneDrive path: ${saved}`);
        } catch (err) {
          setMsg(msgEl, err.message || 'Could not save path', true);
        }
      });
    }

    if (pickBtn && odEnabled()) {
      pickBtn.addEventListener('click', async () => {
        const OD = od();
        try {
          setMsg(msgEl, 'Connecting to OneDrive…');
          const token = await OD.ensureAccessToken();
          if (!token) return;
          setMsg(msgEl, 'Choose the folder that contains your chat key backup…');
          await OD.showFolderPicker({
            token,
            title: 'Chat key backup folder',
            onPick: async (folderId, names) => {
              const path = Array.isArray(names) && names.length > 1
                ? names.slice(1).join('/')
                : pathInput.value.trim();
              const saved = saveConfiguredPath(path, folderId, username);
              pathInput.value = saved;
              setMsg(msgEl, `Mapped OneDrive folder: ${saved}`);
            },
          });
        } catch (err) {
          setMsg(msgEl, err.message || 'Could not map folder', true);
        }
      });
    }

    if (restoreBtn && odEnabled()) {
      restoreBtn.addEventListener('click', () => {
        restoreFromConfiguredPath(msgEl, username).catch((err) => {
          setMsg(msgEl, err.message || 'Restore failed', true);
        });
      });
    }
  }

  global.AirdChatKeyBackup = {
    init,
    initProfile,
    backupStatusText,
    loadOdMap,
    getOdPathString,
    getOdPathParts,
    saveConfiguredPath,
    tryRestoreFromConfiguredPath,
    restoreFromConfiguredPath,
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
