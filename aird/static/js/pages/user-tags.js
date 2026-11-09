"use strict";

import {
  createUserTag,
  deleteUserTag,
  fetchMyUserTags,
  updateUserTag,
} from '../user-tag-api.js';

function escapeHtml(s) {
  return String(s || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const openTagIds = new Set();

function renderTags(tags) {
  const host = document.getElementById('userTagsList');
  if (!host) return;
  if (!tags.length) {
    host.innerHTML = '<p class="opacity-60 text-sm">No tags yet.</p>';
    return;
  }
  host.innerHTML = tags.map(function (tag) {
    const members = tag.members || [];
    const memberText = members.join(', ');
    const open = openTagIds.has(tag.id);
    const href = '/tagged/' + encodeURIComponent(tag.name);
    const access = tag.access && tag.access !== 'none'
      ? '<span class="badge badge-sm">' + escapeHtml(tag.access) + '</span>'
      : '';
    const memberLine = members.length
      ? '<p class="text-sm mt-1">' + escapeHtml(memberText) + '</p>'
      : '';
    return (
      '<article class="card bg-base-200/40 border border-base-content/10 p-4" data-tag-id="' + tag.id + '">'
      + '<header class="flex flex-wrap items-center gap-2">'
      + '<a class="font-semibold hover:underline" style="color:' + escapeHtml(tag.color) + '" href="' + href + '">' + escapeHtml(tag.name) + '</a>'
      + access
      + '<button type="button" class="btn btn-xs btn-outline ml-auto tag-expand" aria-expanded="' + (open ? 'true' : 'false') + '">' + (open ? 'Hide' : 'Edit') + '</button>'
      + '</header>'
      + (open ? '' : memberLine)
      + '<div class="tag-edit mt-3' + (open ? '' : ' hidden') + '">'
      + '<label class="text-xs block mb-1">Members (comma-separated)</label>'
      + '<input type="text" class="input input-bordered input-sm w-full mb-2 tag-members" value="' + escapeHtml(memberText) + '" />'
      + '<div class="flex flex-wrap gap-2">'
      + '<select class="select select-bordered select-xs tag-access">'
      + ['none', 'read', 'write'].map(function (a) {
        return '<option value="' + a + '"' + (tag.access === a ? ' selected' : '') + '>' + a + '</option>';
      }).join('')
      + '</select>'
      + '<button type="button" class="btn btn-xs btn-primary tag-save">Save</button>'
      + '<button type="button" class="btn btn-xs btn-ghost tag-delete">Delete</button>'
      + '</div>'
      + '</div>'
      + '</article>'
    );
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

let lastTags = [];

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
  const access = String(fd.get('access') || 'none');
  if (!name) return;
  const { ok } = await createUserTag(name, { access });
  if (ok) {
    e.target.reset();
    await reload();
  }
});

document.getElementById('userTagsList')?.addEventListener('click', async function (e) {
  const card = e.target.closest('[data-tag-id]');
  if (!card) return;
  const id = Number(card.dataset.tagId);
  if (e.target.classList.contains('tag-expand')) {
    if (openTagIds.has(id)) openTagIds.delete(id);
    else openTagIds.add(id);
    renderTags(lastTags);
    return;
  }
  if (e.target.classList.contains('tag-delete')) {
    if (!confirm('Delete this tag?')) return;
    await deleteUserTag(id);
    await reload();
    return;
  }
  if (e.target.classList.contains('tag-save')) {
    const members = card.querySelector('.tag-members')?.value
      .split(',')
      .map(function (s) { return s.trim(); })
      .filter(Boolean);
    const access = card.querySelector('.tag-access')?.value || 'none';
    await updateUserTag(id, { members, access });
    await reload();
  }
});

reload();
