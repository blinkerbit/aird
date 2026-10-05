"use strict";

function browseConfig() {
  return globalThis.__BROWSE_CONFIG || {};
}

export function getMaxFileSize() {
  return browseConfig().maxFileSize || 10737418240;
}

export function getCanTag() {
  return !!browseConfig().canTag;
}

export function getTagColors() {
  return browseConfig().tagColors || {};
}

export function setTagColorLocal(tag, hex, { broadcast = true } = {}) {
  if (!tag || !hex) return;
  const cfg = globalThis.__BROWSE_CONFIG || (globalThis.__BROWSE_CONFIG = {});
  cfg.tagColors = { ...(cfg.tagColors || {}), [tag]: hex };
  if (!broadcast || typeof BroadcastChannel === 'undefined') return;
  try {
    const bc = new BroadcastChannel(TAG_COLOR_BC);
    bc.postMessage({ tag: tag, hex: hex });
    bc.close();
  } catch { /* ignore */ }
}

export const RECENT_TAGS_KEY = 'aird.tags.recent';
export const TAG_COLOR_BC = 'aird.tag-color.v1';

export function getRecentTags() {
  try {
    const arr = JSON.parse(localStorage.getItem(RECENT_TAGS_KEY) || '[]');
    return Array.isArray(arr) ? arr.filter(Boolean) : [];
  } catch {
    return [];
  }
}

export function noteRecentTag(tag) {
  const t = String(tag || '').trim().toLowerCase().replaceAll(/\s+/g, '-');
  if (!t) return;
  const recent = getRecentTags().filter(function (n) { return n !== t; });
  recent.unshift(t);
  try {
    localStorage.setItem(RECENT_TAGS_KEY, JSON.stringify(recent.slice(0, 12)));
  } catch { /* ignore */ }
}

let _tagColorBcBound = false;
export function listenTagColorSync(onColor) {
  if (_tagColorBcBound || typeof BroadcastChannel === 'undefined' || !onColor) return;
  _tagColorBcBound = true;
  const bc = new BroadcastChannel(TAG_COLOR_BC);
  bc.onmessage = function (ev) {
    const tag = ev.data?.tag;
    const hex = ev.data?.hex;
    if (tag && hex) onColor(tag, hex, { fromRemote: true });
  };
}

export const AUTO_TAG_COLOR_KEY = 'aird.tags.autoColor';

export function isAutoColorEnabled() {
  try {
    return localStorage.getItem(AUTO_TAG_COLOR_KEY) !== '0';
  } catch {
    return true;
  }
}

export function setAutoColorEnabled(on) {
  try {
    localStorage.setItem(AUTO_TAG_COLOR_KEY, on ? '1' : '0');
  } catch { /* ignore */ }
}

export function getChatEnabled() {
  return !!browseConfig().chat;
}

/** @deprecated Prefer getMaxFileSize() — kept for call sites that need a snapshot. */
export const MAX_FILE_SIZE = getMaxFileSize();
export const CAN_TAG = getCanTag();
export const TAG_COLORS = getTagColors();

export function pathBasename(p) {
  const segs = String(p).split('/').filter(Boolean);
  return segs.length ? segs.at(-1) : p;
}

export function escapeHtml(text) {
  if (globalThis.AirdCore?.escapeHtml) return globalThis.AirdCore.escapeHtml(text);
  return String(text ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;');
}

export function escapeAttr(text) {
  if (globalThis.AirdCore?.escapeAttr) return globalThis.AirdCore.escapeAttr(text);
  return escapeHtml(text).replaceAll("'", '&#39;');
}

export function showDialog(...args) {
  if (globalThis.AirdCore?.showDialog) {
    return globalThis.AirdCore.showDialog(...args);
  }
  const msg = args[0] ?? '';
  const opts = args[2];
  if (opts?.showCancel) {
    return Promise.resolve(globalThis.confirm(msg));
  }
  globalThis.alert(msg);
  return Promise.resolve(true);
}

/** True if keyboard events should be ignored (typing in a field). */
export function isInputKeyTarget(target) {
  if (!target) return false;
  const tag = (target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

export function getXSRFToken() {
  if (globalThis.AirdCore?.getXSRFToken) return globalThis.AirdCore.getXSRFToken();
  const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
  return m ? decodeURIComponent(m[1]) : '';
}

export function createEl(tag, props, text) {
  const el = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      if (key === 'className') el.className = props[key];
      else if (key === 'dataset') {
        for (const [dk, dv] of Object.entries(props[key])) {
          el.dataset[dk] = dv;
        }
      }
      else el.setAttribute(key, props[key]);
    }
  }
  if (text != null) el.textContent = text;
  return el;
}

export function wireBrowseButton(id, handler) {
  const el = document.getElementById(id);
  if (el) el.addEventListener('click', handler);
  return el;
}

export function safeReload() {
  try {
    globalThis.location.reload();
  } catch (e) {
    console.warn('Reload failed:', e);
  }
}
