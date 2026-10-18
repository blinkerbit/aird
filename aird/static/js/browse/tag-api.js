"use strict";

// Repository pattern: all tag endpoints sit behind `tagsRequest`, which
// goes through the shared HTTP gateway (XSRF + parsing in one place).
import { apiFetch } from '../core/http.js';
import { isAutoColorEnabled, setTagColorLocal } from './util.js';
import { normalizeRelPath } from './rows.js';

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
  try {
    const { ok, data } = await tagsRequest('/api/tags', 'GET');
    if (!ok) return { names: [], colors: {} };
    return { names: Array.isArray(data.names) ? data.names : [], colors: data.colors || {} };
  } catch {
    return { names: [], colors: {} };
  }
}

export async function postTagRule(tag, globPattern, { color, autoColor } = {}) {
  const body = { tag, glob_pattern: globPattern };
  if (color) body.color = color;
  body.auto_color = autoColor ?? isAutoColorEnabled();
  const { ok: resOk, status, data } = await tagsRequest(TAGS_URL, 'POST', body);
  const ok = resOk || status === 409;
  if (ok && data.color) setTagColorLocal(tag, data.color);
  return { ok, created: status === 201, color: data.color || '' };
}

export async function putTagColor(tag, color) {
  const { ok, data } = await tagsRequest(TAG_COLORS_URL, 'PUT', { tag, color });
  if (ok && data.color) setTagColorLocal(tag, data.color);
  return { ok, color: data.color || color };
}

export async function deleteTagRuleIds(ids) {
  if (!ids.length) return { ok: true, deleted: [] };
  const { ok, data } = await tagsRequest(TAGS_URL, 'DELETE', { ids: ids });
  if (!ok) return { ok: false, deleted: [] };
  return { ok: true, deleted: data.ids || [] };
}

export async function untagPath(tag, path) {
  const { ok, data } = await tagsRequest(TAGS_URL, 'DELETE', { tag, glob_pattern: pathToGlob(path) });
  if (!ok) return { ok: false, count: 0 };
  return { ok: true, count: data.count || 0 };
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
  const autoColor = isAutoColorEnabled();
  const jobs = [];
  for (const path of paths) {
    const glob = pathToGlob(path);
    for (const tag of tags) {
      jobs.push({ tag, glob });
    }
  }
  const outcomes = await Promise.all(jobs.map(async ({ tag, glob }) => {
    try {
      return await postTagRule(tag, glob, { color: colors[tag], autoColor });
    } catch {
      return { ok: false };
    }
  }));
  let created = 0;
  let failed = 0;
  for (const result of outcomes) {
    if (result.ok) created++;
    else failed++;
  }
  return { created, failed };
}
