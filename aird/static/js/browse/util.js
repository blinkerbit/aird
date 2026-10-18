"use strict";

// Canonical shared primitives (single source of truth in ../core/).
import { onById } from '../core/dom.js';
export { getXSRFToken } from '../core/xsrf.js';
export { escapeHtml, escapeAttr } from '../core/dom-safe.js';
export { showDialog } from '../core/dialog.js';

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

export function setTagColorLocal(tag, hex, options) {
  if (!tag || !hex) return;
  let cfg = globalThis.__BROWSE_CONFIG;
  if (!cfg) cfg = globalThis.__BROWSE_CONFIG = Object.create(null);
  const prev = cfg.tagColors;
  cfg.tagColors = prev ? { ...prev, [tag]: hex } : { [tag]: hex };
  if (options?.broadcast === false || typeof BroadcastChannel === 'undefined') return;
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

export function pathBasename(p) {
  const segs = String(p).split('/').filter(Boolean);
  return segs.length ? segs.at(-1) : p;
}

/** True if keyboard events should be ignored (typing in a field). */
export function isInputKeyTarget(target) {
  if (!target) return false;
  const tag = (target.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || target.isContentEditable;
}

export { createEl } from '../core/dom.js';

export function wireBrowseButton(id, handler) {
  return onById(id, 'click', handler);
}
