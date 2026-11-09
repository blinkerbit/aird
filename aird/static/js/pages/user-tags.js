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

function renderTags(tags) {
  const host = document.getElementById('userTagsList');
  if (!host) return;
  if (!tags.length) {
    host.innerHTML = '<p class="opacity-60 text-sm">No tags yet.</p>';
    return;
  }
  host.innerHTML = tags.map(function (tag) {
    const members = (tag.members || []).join(', ');
    return (
      '<article class="card bg-base-200/40 border border-base-content/10 p-4" data-tag-id="' + tag.id + '">'
      + '<header class="flex flex-wrap items-center gap-2 mb-2">'
      + '<span class="font-semibold" style="color:' + escapeHtml(tag.color) + '">' + escapeHtml(tag.name) + '</span>'
      + '<span class="badge badge-sm">' + escapeHtml(tag.access) + '</span>'
      + '</header>'
      + '<label class="text-xs block mb-1">Members (comma-separated)</label>'
      + '<input type="text" class="input input-bordered input-sm w-full mb-2 tag-members" value="' + escapeHtml(members) + '" />'
      + '<div class="flex flex-wrap gap-2">'
      + '<select class="select select-bordered select-xs tag-access">'
      + ['none', 'read', 'write'].map(function (a) {
        return '<option value="' + a + '"' + (tag.access === a ? ' selected' : '') + '>' + a + '</option>';
      }).join('')
      + '</select>'
      + '<button type="button" class="btn btn-xs btn-primary tag-save">Save</button>'
      + '<button type="button" class="btn btn-xs btn-ghost tag-delete">Delete</button>'
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
    return '<li><a href="' + href + '">' + escapeHtml(g.tag_name) + '</a> — '
      + escapeHtml(g.owner_username) + '/' + escapeHtml(g.path) + ' (' + escapeHtml(g.access) + ')</li>';
  }).join('');
}

async function reload() {
  const { tags, granted_files: grants } = await fetchMyUserTags();
  renderTags(tags);
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
