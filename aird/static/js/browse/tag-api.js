"use strict";

// Repository pattern: all tag endpoints sit behind `tagsRequest`, which
// goes through the shared HTTP gateway (XSRF + parsing in one place).
import { apiFetch } from '../core/http.js';
import { setTagColorLocal } from './util.js';
import { normalizeRelPath } from './rows.js';
import {
  applyUserTagsToPaths,
  attachTagToFile,
  detachTagFromFile,
  fetchUserTagCatalog,
  updateUserTag,
} from '../user-tag-api.js';

const TAGS_URL = '/admin/api/abac/tags';
const TAG_COLORS_URL = '/admin/api/abac/tag-colors';

async function tagsRequest(url, method, json) {
  const res = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: json === undefined ? undefined : JSON.stringify(json),
  });
  const data = await res.json().catch(function () { return {}; });
  return { ok: res.ok, status: res.status, data };
}

export function pathToGlob(path) {
  const rel = normalizeRelPath(path);
  return rel ? `/${rel}` : '/';
}

export async function fetchTagCatalog() {
  return fetchUserTagCatalog();
}

export async function postTagRule(tag, globPattern, { color, autoColor } = {}) {
  const path = globPattern.replace(/^\//, '');
  const result = await attachTagToFile(tag, path, { color, autoColor });
  return { ok: !!result.ok, created: !!result.ok, color: result.color || '' };
}

export async function putTagColor(tag, color) {
  const catalog = await fetchUserTagCatalog();
  const row = (catalog.tags || []).find(function (t) { return t.name === tag; });
  if (!row) return { ok: false, color };
  const { ok, data } = await updateUserTag(row.id, { color });
  const hex = data?.color || color;
  if (ok && hex) setTagColorLocal(tag, hex);
  return { ok, color: hex };
}

export async function deleteTagRuleIds(ids) {
  if (!ids.length) return { ok: true, deleted: [] };
  const { ok, data } = await tagsRequest(TAGS_URL, 'DELETE', { ids: ids });
  if (!ok) return { ok: false, deleted: [] };
  return { ok: true, deleted: data.ids || [] };
}

export async function untagPath(tag, path) {
  return detachTagFromFile(tag, path);
}

export async function fetchAllTagRules() {
  try {
    const { ok, data } = await tagsRequest(TAGS_URL, 'GET');
    if (!ok) return [];
    return data.tags || [];
  } catch {
    return [];
  }
}

export async function renameTag(oldTag, newTag) {
  const { ok, data } = await tagsRequest('/admin/api/abac/tag-rename', 'POST', { old_tag: oldTag, new_tag: newTag });
  return { ok, data: data };
}

export async function importTagSnapshot(snapshot) {
  const { ok, data } = await tagsRequest(TAGS_URL, 'POST', { import_snapshot: true, ...snapshot });
  return { ok, data: data };
}

export async function fetchTagExport() {
  const { ok, data } = await tagsRequest(TAGS_URL, 'GET');
  if (!ok) return { tags: [], colors: {} };
  return data;
}

export async function applyTagRules(tags, paths, { colors = {} } = {}) {
  return applyUserTagsToPaths(tags, paths, { colors });
}
