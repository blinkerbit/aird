"use strict";

// Repository pattern: all tag endpoints sit behind `tagsRequest`, which
// goes through the shared HTTP gateway (XSRF + parsing in one place).
import { apiFetch } from '../core/http.js';
import { isAutoColorEnabled, setTagColorLocal } from './util.js';

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

export function normalizeRelPath(path) {
  return String(path || '').replaceAll('\\', '/').replaceAll(/^\/+/g, '');
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

function escapeGlobChar(ch) {
  return ch.replaceAll(/[.+^${}()|[\]\\]/g, String.raw`\$&`);
}

function trimTrailingSlashes(value) {
  let end = value.length;
  while (end > 0 && value[end - 1] === '/') end -= 1;
  return end === value.length ? value : value.slice(0, end);
}

export function globPatternToRegex(pattern) {
  let p = trimTrailingSlashes(String(pattern).replaceAll('\\', '/').replaceAll(/^\/+/g, ''));
  if (!p) return null;
  let out = '';
  for (let i = 0; i < p.length; i += 1) {
    const ch = p[i];
    if (ch === '*' && p[i + 1] === '*') {
      if (p[i + 2] === '/') { out += '(?:.*/)?'; i += 2; }
      else { out += '.*'; i += 1; }
    } else if (ch === '*') {
      out += '[^/]*';
    } else if (ch === '?') {
      out += '[^/]';
    } else {
      out += escapeGlobChar(ch);
    }
  }
  return new RegExp('^' + out + '$');
}

export function ruleMatchesPath(rule, relPath) {
  const rel = normalizeRelPath(relPath);
  const pat = String(rule.glob_pattern || '');
  if (!pat) return false;
  const normPat = normalizeRelPath(pat);
  if (!pat.includes('*') && !pat.includes('?') && !pat.includes('**')) {
    return normPat === rel;
  }
  try {
    const re = globPatternToRegex(pat);
    return re ? re.test(rel) : false;
  } catch {
    return false;
  }
}

export function tagsOnPath(rules, path) {
  const byTag = new Map();
  for (const rule of rules) {
    const tag = rule.tag || '';
    if (!tag || !ruleMatchesPath(rule, path)) continue;
    if (!byTag.has(tag)) byTag.set(tag, []);
    byTag.get(tag).push(rule.id);
  }
  return byTag;
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
