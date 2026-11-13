"use strict";

import { fetchMyUserTags } from './user-tag-api.js';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

export function tagIdsFieldHtml() {
  return `<div class="collab-field collab-tag-field" data-tag-picker>
    <span>Tags</span>
    <div class="collab-tag-chips" data-tag-chips></div>
    <input class="input input-bordered input-sm" data-tag-query placeholder="Type to add a tag" autocomplete="off">
    <div class="collab-tag-suggest" data-tag-suggest hidden></div>
  </div>`;
}

export function readTagIds(root) {
  return [...root.querySelectorAll('[data-tag-picker] [data-tag-id]')]
    .filter((el) => el.type !== 'checkbox' || el.checked)
    .map((el) => Number(el.value))
    .filter(Boolean);
}

export async function hydrateTagPicker(root, selectedIds = [], options = {}) {
  const host = root.querySelector?.('[data-tag-picker]') || null;
  if (!host) return;
  const query = host.querySelector('[data-tag-query]');
  const chips = host.querySelector('[data-tag-chips]');
  const suggest = host.querySelector('[data-tag-suggest]');
  if (!query || !chips || !suggest) return;

  let tags = [];
  try {
    const data = await fetchMyUserTags();
    tags = data.tags || [];
  } catch {
    tags = [];
  }
  if (!host.isConnected) return;

  const known = new Map(tags.map((tag) => [Number(tag.id), tag]));
  const selected = new Set((selectedIds || []).map(Number).filter((id) => known.has(id)));
  let active = 0;
  let open = false;

  const matches = () => {
    const q = query.value.trim().toLowerCase();
    if (!q) return [];
    return tags.filter((tag) => !selected.has(Number(tag.id)) && String(tag.name || '').toLowerCase().includes(q));
  };

  const emit = () => {
    host.querySelectorAll('[data-tag-id]').forEach((node) => node.remove());
    selected.forEach((id) => {
      const input = document.createElement('input');
      input.type = 'hidden';
      input.dataset.tagId = String(id);
      input.value = String(id);
      host.appendChild(input);
    });
    options.onChange?.([...selected]);
  };

  const paintChips = () => {
    const rows = [...selected].map((id) => known.get(id)).filter(Boolean);
    chips.innerHTML = rows.map((tag) => (
      `<button type="button" class="collab-tag-chip" data-remove-tag="${esc(String(tag.id))}">${esc(tag.name)}<span aria-hidden="true">×</span></button>`
    )).join('');
  };

  const paintSuggest = () => {
    const rows = matches().slice(0, 8);
    if (!open || !query.value.trim()) {
      suggest.hidden = true;
      suggest.innerHTML = '';
      return;
    }
    if (active >= rows.length) active = 0;
    suggest.hidden = false;
    if (!rows.length) {
      suggest.innerHTML = '<p class="collab-hint">No matching tags</p>';
      return;
    }
    suggest.innerHTML = rows.map((tag, index) => (
      `<button type="button" class="${index === active ? 'is-active' : ''}" data-pick-tag="${esc(String(tag.id))}">${esc(tag.name)}</button>`
    )).join('');
  };

  const addTag = (id) => {
    const num = Number(id);
    if (!known.has(num) || selected.has(num)) return;
    selected.add(num);
    query.value = '';
    open = false;
    active = 0;
    paintChips();
    paintSuggest();
    emit();
    query.focus();
  };

  const removeTag = (id) => {
    selected.delete(Number(id));
    paintChips();
    paintSuggest();
    emit();
  };

  if (!tags.length) {
    query.disabled = true;
    query.placeholder = 'No tags yet';
    chips.innerHTML = '<p class="collab-hint">Create tags on <a href="/tags">My tags</a>.</p>';
    return;
  }

  paintChips();
  host.querySelectorAll('[data-tag-id]').forEach((node) => node.remove());
  selected.forEach((id) => {
    const input = document.createElement('input');
    input.type = 'hidden';
    input.dataset.tagId = String(id);
    input.value = String(id);
    host.appendChild(input);
  });

  query.addEventListener('input', () => {
    open = true;
    active = 0;
    paintSuggest();
  });
  query.addEventListener('focus', () => {
    if (query.value.trim()) {
      open = true;
      paintSuggest();
    }
  });
  query.addEventListener('keydown', (event) => {
    const rows = matches().slice(0, 8);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      if (!rows.length) return;
      event.preventDefault();
      open = true;
      const step = event.key === 'ArrowDown' ? 1 : rows.length - 1;
      active = (active + step) % rows.length;
      paintSuggest();
      return;
    }
    if (event.key === 'Enter') {
      event.preventDefault();
      const exact = rows.find((tag) => tag.name.toLowerCase() === query.value.trim().toLowerCase());
      const pick = exact || rows[active] || rows[0];
      if (pick) addTag(pick.id);
      return;
    }
    if (event.key === 'Escape') {
      if (!open) return;
      event.preventDefault();
      event.stopPropagation();
      open = false;
      paintSuggest();
      return;
    }
    if (event.key === 'Backspace' && !query.value && selected.size) {
      const last = [...selected].pop();
      removeTag(last);
    }
  });
  suggest.addEventListener('mousedown', (event) => event.preventDefault());
  suggest.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-pick-tag]');
    if (!button) return;
    addTag(button.dataset.pickTag);
  });
  chips.addEventListener('click', (event) => {
    const button = event.target.closest?.('[data-remove-tag]');
    if (!button) return;
    removeTag(button.dataset.removeTag);
  });
  host.addEventListener('focusout', () => {
    setTimeout(() => {
      if (host.contains(document.activeElement)) return;
      open = false;
      paintSuggest();
    }, 0);
  });
}
