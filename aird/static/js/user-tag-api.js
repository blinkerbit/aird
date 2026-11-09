"use strict";

import { apiFetch } from './core/http.js';
import { isAutoColorEnabled, setTagColorLocal } from './browse/util.js';
import { normalizeRelPath } from './browse/rows.js';

const USER_TAGS_URL = '/api/user-tags';
const BROWSE_CATALOG_URL = '/api/user-tags/browse-catalog';

async function tagsRequest(url, method, json) {
  const res = await apiFetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: json === undefined ? undefined : JSON.stringify(json),
  });
  const data = await res.json().catch(function () { return {}; });
  return { ok: res.ok, status: res.status, data };
}

export function pathToResourceKey(path) {
  return normalizeRelPath(path);
}

export async function fetchUserTagCatalog() {
  try {
    const { ok, data } = await tagsRequest(BROWSE_CATALOG_URL, 'GET');
    if (!ok) return { names: [], colors: {}, tags: [] };
    return {
      names: Array.isArray(data.names) ? data.names : [],
      colors: data.colors || {},
      tags: data.tags || [],
    };
  } catch {
    return { names: [], colors: {}, tags: [] };
  }
}

export async function fetchMyUserTags() {
  const { ok, data } = await tagsRequest(USER_TAGS_URL, 'GET');
  if (!ok) return { tags: [], granted_files: [] };
  return { tags: data.tags || [], granted_files: data.granted_files || [] };
}

export async function createUserTag(name, { access = 'none', color, members } = {}) {
  const body = { name, access };
  if (color) body.color = color;
  if (members) body.members = members;
  return tagsRequest(USER_TAGS_URL, 'POST', body);
}

export async function updateUserTag(id, patch) {
  return tagsRequest(`${USER_TAGS_URL}/${id}`, 'PATCH', patch);
}

export async function deleteUserTag(id) {
  return tagsRequest(`${USER_TAGS_URL}/${id}`, 'DELETE', {});
}

export async function linkUserTag(id, kind, resourceKey) {
  return tagsRequest(`${USER_TAGS_URL}/${id}/links`, 'POST', { kind, resource_key: resourceKey });
}

export async function unlinkUserTag(id, kind, resourceKey) {
  return tagsRequest(`${USER_TAGS_URL}/${id}/links`, 'DELETE', { kind, resource_key: resourceKey });
}

async function ensureUserTagByName(name, { color, autoColor } = {}) {
  const catalog = await fetchUserTagCatalog();
  let tag = (catalog.tags || []).find(function (t) { return t.name === name; });
  if (tag) return tag;
  const body = { name, access: 'none' };
  if (color) body.color = color;
  if (!color && (autoColor ?? isAutoColorEnabled())) body.auto_color = true;
  const { ok, status, data } = await createUserTag(name, body);
  if (ok || status === 201) return data;
  const again = await fetchUserTagCatalog();
  return (again.tags || []).find(function (t) { return t.name === name; });
}

export async function attachTagToFile(tagName, path, opts = {}) {
  const key = pathToResourceKey(path);
  if (!key && key !== '') return { ok: false };
  const tag = await ensureUserTagByName(tagName, opts);
  if (!tag || !tag.id) return { ok: false };
  const { ok } = await linkUserTag(tag.id, 'file', key);
  if (ok && tag.color) setTagColorLocal(tagName, tag.color);
  return { ok, color: tag.color || '' };
}

export async function detachTagFromFile(tagName, path) {
  const key = pathToResourceKey(path);
  const { tags } = await fetchMyUserTags();
  const tag = (tags || []).find(function (t) { return t.name === tagName; });
  if (!tag) return { ok: false, count: 0 };
  const { ok } = await unlinkUserTag(tag.id, 'file', key);
  return { ok, count: ok ? 1 : 0 };
}

export async function applyUserTagsToPaths(tagNames, paths, { colors = {} } = {}) {
  let created = 0;
  let failed = 0;
  for (const path of paths) {
    for (const tag of tagNames) {
      const result = await attachTagToFile(tag, path, { color: colors[tag] });
      if (result.ok) created++;
      else failed++;
    }
  }
  return { created, failed };
}
