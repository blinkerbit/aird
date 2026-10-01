"use strict";

import { getXSRFToken, isAutoColorEnabled, setTagColorLocal } from './util.js';

export function normalizeRelPath(path) {
  return String(path || '').replaceAll('\\', '/').replace(/^\/+/, '');
}

export function pathToGlob(path) {
  const rel = normalizeRelPath(path);
  return rel ? `/${rel}` : '/';
}

export async function fetchTagCatalog() {
  try {
    const res = await fetch('/api/tags', { headers: { Accept: 'application/json' } });
    if (!res.ok) return { names: [], colors: {} };
    const data = await res.json();
    return { names: Array.isArray(data.names) ? data.names : [], colors: data.colors || {} };
  } catch {
    return { names: [], colors: {} };
  }
}

export async function postTagRule(tag, globPattern, { color, autoColor } = {}) {
  const body = { tag, glob_pattern: globPattern };
  if (color) body.color = color;
  body.auto_color = autoColor ?? isAutoColorEnabled();
  const res = await fetch('/admin/api/abac/tags', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(function () { return {}; });
  const ok = res.ok || res.status === 409;
  if (ok && data.color) setTagColorLocal(tag, data.color);
  return { ok, created: res.status === 201, color: data.color || '' };
}

export async function putTagColor(tag, color) {
  const res = await fetch('/admin/api/abac/tag-colors', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ tag, color }),
  });
  const data = await res.json().catch(function () { return {}; });
  if (res.ok && data.color) setTagColorLocal(tag, data.color);
  return { ok: res.ok, color: data.color || color };
}

export async function deleteTagRuleIds(ids) {
  if (!ids.length) return { ok: true, deleted: [] };
  const res = await fetch('/admin/api/abac/tags', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ ids: ids }),
  });
  if (!res.ok) return { ok: false, deleted: [] };
  const data = await res.json().catch(function () { return {}; });
  return { ok: true, deleted: data.ids || [] };
}

export async function untagPath(tag, path) {
  const res = await fetch('/admin/api/abac/tags', {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ tag, glob_pattern: pathToGlob(path) }),
  });
  if (!res.ok) return { ok: false, count: 0 };
  const data = await res.json().catch(function () { return {}; });
  return { ok: true, count: data.count || 0 };
}

export async function fetchAllTagRules() {
  try {
    const res = await fetch('/admin/api/abac/tags', { headers: { 'X-XSRFToken': getXSRFToken() } });
    if (!res.ok) return [];
    const data = await res.json();
    return data.tags || [];
  } catch {
    return [];
  }
}

export async function renameTag(oldTag, newTag) {
  const res = await fetch('/admin/api/abac/tag-rename', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ old_tag: oldTag, new_tag: newTag }),
  });
  const data = await res.json().catch(function () { return {}; });
  return { ok: res.ok, data: data };
}

export async function importTagSnapshot(snapshot) {
  const res = await fetch('/admin/api/abac/tags', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ import_snapshot: true, ...snapshot }),
  });
  const data = await res.json().catch(function () { return {}; });
  return { ok: res.ok, data: data };
}

export async function fetchTagExport() {
  const res = await fetch('/admin/api/abac/tags', { headers: { 'X-XSRFToken': getXSRFToken() } });
  if (!res.ok) return { tags: [], colors: {} };
  return res.json();
}

function escapeGlobChar(ch) {
  return ch.replaceAll(/[.+^${}()|[\]\\]/g, String.raw`\$&`);
}

export function globPatternToRegex(pattern) {
  let p = String(pattern).replaceAll('\\', '/').replace(/^\/+/, '').replace(/\/$/, '');
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
  let created = 0;
  let failed = 0;
  for (const path of paths) {
    const glob = pathToGlob(path);
    for (const tag of tags) {
      try {
        const result = await postTagRule(tag, glob, { color: colors[tag], autoColor });
        if (result.ok) { created++; } else { failed++; }
      } catch { failed++; }
    }
  }
  return { created, failed };
}
