"use strict";

export const RELOAD_DELAY_MS = 500;

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
      else if (key === 'dataset') Object.assign(el.dataset, props[key]);
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
