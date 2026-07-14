"use strict";

import { getXSRFToken } from '/static/js/browse/util.js';

export async function postTagRule(tag, globPattern) {
  const res = await fetch('/admin/api/abac/tags', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': getXSRFToken() },
    body: JSON.stringify({ tag, glob_pattern: globPattern }),
  });
  return res.ok || res.status === 409;
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

export function normalizeRelPath(p) {
  return String(p).replaceAll('\\', '/').replace(/^\/+/, '');
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

export async function applyTagRules(tags, paths) {
  let created = 0;
  let failed = 0;
  for (const path of paths) {
    const norm = path.startsWith('/') ? path : '/' + path.replace(/^\/+/, '');
    for (const tag of tags) {
      try {
        if (await postTagRule(tag, norm)) { created++; } else { failed++; }
      } catch { failed++; }
    }
  }
  return { created, failed };
}
