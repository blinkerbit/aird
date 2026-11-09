"use strict";

import { apiFetch } from '../core/http.js';
import {
  createUserTag,
  deleteUserTag,
  fetchMyUserTags,
  updateUserTag,
} from '../user-tag-api.js';

const ACCESS = [
  { id: 'none', title: 'Only you', detail: 'A private label. Nobody else can use it.' },
  { id: 'read', title: 'Can view', detail: 'People you add can open what is tagged.' },
  { id: 'write', title: 'Can edit', detail: 'People you add can change what is tagged.' },
];

const openTagIds = new Set();
const tagStatus = new Map();
let lastTags = [];

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function accessSummary(tag) {
  const members = tag.members || [];
  if (tag.access === 'read') {
    return members.length ? `Can view · ${members.join(', ')}` : 'Can view · no one added yet';
  }
  if (tag.access === 'write') {
    return members.length ? `Can edit · ${members.join(', ')}` : 'Can edit · no one added yet';
  }
  return 'Only you';
}

function choiceHtml(tag) {
  return ACCESS.map(function (opt) {
    const on = (tag.access || 'none') === opt.id;
    return '<button type="button" class="tag-access-choice' + (on ? ' is-on' : '') + '" data-access="' + opt.id + '" aria-pressed="' + (on ? 'true' : 'false') + '">'
      + '<strong>' + opt.title + '</strong><span>' + opt.detail + '</span></button>';
  }).join('');
}

function peopleHtml(tag) {
  const members = tag.members || [];
  const chips = members.map(function (name) {
    return '<span class="tag-person">' + escapeHtml(name)
      + '<button type="button" data-remove-member="' + escapeHtml(name) + '" aria-label="Remove ' + escapeHtml(name) + '">×</button></span>';
  }).join('');
  return '<div class="tag-people"' + (tag.access === 'read' || tag.access === 'write' ? '' : ' hidden') + '>'
    + '<p class="tag-people-label">People with this access</p>'
    + (chips ? '<div class="tag-people-chips">' + chips + '</div>' : '')
    + '<div class="tag-person-box">'
    + '<input type="text" class="input input-bordered input-sm w-full tag-person-input" placeholder="Add a person" autocomplete="off">'
    + '<div class="tag-person-suggest" hidden></div>'
    + '</div></div>';
}

function renderTags(tags) {
  const host = document.getElementById('userTagsList');
  if (!host) return;
  if (!tags.length) {
    host.innerHTML = '<p class="opacity-60 text-sm">No tags yet.</p>';
    return;
  }
  host.innerHTML = tags.map(function (tag) {
    const open = openTagIds.has(tag.id);
    const href = '/tagged/' + encodeURIComponent(tag.name);
    const status = tagStatus.get(tag.id);
    const statusHtml = status
      ? '<p class="tag-access-status' + (status.kind === 'error' ? ' is-error' : '') + '">' + escapeHtml(status.text) + '</p>'
      : '<p class="tag-access-status"></p>';
    return '<article class="tag-card card bg-base-200/40 border border-base-content/10" data-tag-id="' + tag.id + '">'
      + '<header class="tag-card-head">'
      + '<a class="tag-card-name" style="color:' + escapeHtml(tag.color) + '" href="' + href + '">' + escapeHtml(tag.name) + '</a>'
      + '<span class="tag-card-summary">' + escapeHtml(accessSummary(tag)) + '</span>'
      + '<button type="button" class="btn btn-xs btn-outline tag-access-toggle" aria-expanded="' + (open ? 'true' : 'false') + '">Access</button>'
      + '</header>'
      + '<div class="tag-access-panel"' + (open ? '' : ' hidden') + '>'
      + '<div class="tag-access-choices" role="group" aria-label="Access for ' + escapeHtml(tag.name) + '">' + choiceHtml(tag) + '</div>'
      + peopleHtml(tag)
      + statusHtml
      + '<div class="tag-card-foot"><button type="button" class="btn btn-xs btn-ghost tag-delete">Delete tag</button></div>'
      + '</div></article>';
  }).join('');
}

function tagGrantBrowseHref(browsePath) {
  const parts = String(browsePath || '').split('/').filter(Boolean);
  if (!parts.length) return '/files/';
  return '/files/' + parts.map(encodeURIComponent).join('/');
}

function renderGrants(grants) {
  const ul = document.getElementById('userTagGrants');
  if (!ul) return;
  if (!grants.length) {
    ul.innerHTML = '<li class="opacity-60">Nothing shared via tags yet.</li>';
    return;
  }
  ul.innerHTML = grants.map(function (g) {
    const href = tagGrantBrowseHref(g.browse_path);
    const tagHref = '/tagged/' + encodeURIComponent(g.tag_name || '');
    return '<li><a href="' + tagHref + '">' + escapeHtml(g.tag_name) + '</a> — '
      + '<a href="' + href + '">' + escapeHtml(g.owner_username) + '/' + escapeHtml(g.path) + '</a> (' + escapeHtml(g.access) + ')</li>';
  }).join('');
}

function replaceTag(next) {
  lastTags = lastTags.map(function (tag) {
    return Number(tag.id) === Number(next.id) ? next : tag;
  });
  renderTags(lastTags);
}

async function saveTag(id, patch) {
  const { ok, data } = await updateUserTag(id, patch);
  if (!ok) {
    tagStatus.set(id, { kind: 'error', text: (data && data.error) || 'Could not save access' });
    renderTags(lastTags);
    return;
  }
  tagStatus.set(id, { kind: 'ok', text: 'Saved' });
  replaceTag(data);
}

async function searchPeople(card, query) {
  const box = card.querySelector('.tag-person-suggest');
  if (!box) return;
  const q = query.trim();
  if (q.length < 2) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  const tag = lastTags.find(function (item) { return String(item.id) === card.dataset.tagId; });
  const have = new Set(tag?.members || []);
  let users = [];
  try {
    const res = await apiFetch('/api/users/search?q=' + encodeURIComponent(q));
    const data = await res.json();
    users = (data.users || []).filter(function (user) { return !have.has(user.username); });
  } catch {
    users = [];
  }
  if (!users.length) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  box.innerHTML = users.map(function (user) {
    const label = user.display_name && user.display_name !== user.username
      ? user.display_name + ' · ' + user.username
      : user.username;
    return '<button type="button" data-add-member="' + escapeHtml(user.username) + '">' + escapeHtml(label) + '</button>';
  }).join('');
}

async function reload() {
  const { tags, granted_files: grants } = await fetchMyUserTags();
  lastTags = tags || [];
  renderTags(lastTags);
  renderGrants(grants);
}

document.getElementById('userTagCreate')?.addEventListener('submit', async function (e) {
  e.preventDefault();
  const fd = new FormData(e.target);
  const name = String(fd.get('name') || '').trim();
  if (!name) return;
  const { ok } = await createUserTag(name, { access: 'none' });
  if (ok) {
    e.target.reset();
    await reload();
  }
});

let searchTimer = 0;
document.getElementById('userTagsList')?.addEventListener('input', function (e) {
  const input = e.target.closest('.tag-person-input');
  if (!input) return;
  const card = input.closest('[data-tag-id]');
  if (!card) return;
  clearTimeout(searchTimer);
  searchTimer = setTimeout(function () {
    searchPeople(card, input.value).catch(function () {});
  }, 200);
});

document.getElementById('userTagsList')?.addEventListener('click', async function (e) {
  const card = e.target.closest('[data-tag-id]');
  if (!card) return;
  const id = Number(card.dataset.tagId);
  const tag = lastTags.find(function (item) { return Number(item.id) === id; });
  if (!tag) return;
  if (e.target.closest('.tag-access-toggle')) {
    if (openTagIds.has(id)) openTagIds.delete(id);
    else openTagIds.add(id);
    renderTags(lastTags);
    return;
  }
  const choice = e.target.closest('[data-access]');
  if (choice) {
    if (choice.dataset.access === (tag.access || 'none')) return;
    await saveTag(id, { access: choice.dataset.access });
    return;
  }
  const add = e.target.closest('[data-add-member]');
  if (add) {
    const name = add.dataset.addMember;
    const members = (tag.members || []).concat(name);
    await saveTag(id, { members });
    return;
  }
  const remove = e.target.closest('[data-remove-member]');
  if (remove) {
    const name = remove.dataset.removeMember;
    const members = (tag.members || []).filter(function (member) { return member !== name; });
    await saveTag(id, { members });
    return;
  }
  if (e.target.closest('.tag-delete')) {
    if (!confirm('Delete this tag?')) return;
    await deleteUserTag(id);
    openTagIds.delete(id);
    tagStatus.delete(id);
    await reload();
  }
});

document.addEventListener('click', function (e) {
  if (e.target.closest('.tag-person-box')) return;
  document.querySelectorAll('.tag-person-suggest').forEach(function (box) {
    box.hidden = true;
  });
});

reload();
