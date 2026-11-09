"use strict";

import { fetchMyUserTags } from './user-tag-api.js';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

export function tagIdsFieldHtml() {
  return `<div class="collab-field" data-tag-picker><span>Tags</span><p class="collab-note">Loading tags…</p></div>`;
}

export async function hydrateTagPicker(root, selectedIds = []) {
  const host = root.querySelector('[data-tag-picker]');
  if (!host) return;
  const { tags } = await fetchMyUserTags();
  if (!tags?.length) {
    host.innerHTML = '<span>Tags</span><p class="collab-note">No tags yet. Create some on <a href="/tags">My tags</a>.</p>';
    return;
  }
  const set = new Set((selectedIds || []).map(Number));
  host.innerHTML = [
    '<span>Tags</span>',
    '<div class="collab-tag-grid">',
    tags.map((t) => (
      `<label class="collab-check"><input type="checkbox" data-tag-id value="${esc(String(t.id))}"`
      + `${set.has(Number(t.id)) ? ' checked' : ''}> ${esc(t.name)}`
      + ` <span class="opacity-50">(${esc(t.access || 'none')})</span></label>`
    )).join(''),
    '</div>',
  ].join('');
}

export function readTagIds(root) {
  return [...root.querySelectorAll('[data-tag-picker] [data-tag-id]:checked')]
    .map((el) => Number(el.value))
    .filter(Boolean);
}
