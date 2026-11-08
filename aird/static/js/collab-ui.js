import { apiJson, expiresLabel, visibilityChip } from './collab-api.js';

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function ensureDialog() {
  let dlg = document.getElementById('collabDialog');
  if (dlg) return dlg;
  dlg = document.createElement('dialog');
  dlg.id = 'collabDialog';
  dlg.className = 'collab-dialog';
  dlg.innerHTML = `
    <form class="collab-dialog-card" id="collabDialogForm">
      <header class="collab-dialog-head">
        <h2 id="collabDialogTitle"></h2>
        <button type="button" class="btn btn-ghost btn-sm btn-square" data-close aria-label="Close">✕</button>
      </header>
      <div class="collab-dialog-body" id="collabDialogBody"></div>
      <p class="collab-dialog-error" id="collabDialogError" hidden></p>
      <footer class="collab-dialog-foot">
        <button type="button" class="btn btn-ghost" data-close>Cancel</button>
        <button type="submit" class="btn btn-primary" id="collabDialogSubmit">Save</button>
      </footer>
    </form>`;
  document.body.appendChild(dlg);
  dlg.querySelectorAll('[data-close]').forEach((btn) => {
    btn.addEventListener('click', () => dlg.close());
  });
  return dlg;
}

function openForm({ title, submitLabel, html, onSubmit }) {
  const dlg = ensureDialog();
  dlg.querySelector('#collabDialogTitle').textContent = title;
  dlg.querySelector('#collabDialogSubmit').textContent = submitLabel || 'Save';
  const body = dlg.querySelector('#collabDialogBody');
  const err = dlg.querySelector('#collabDialogError');
  body.innerHTML = html;
  err.hidden = true;
  err.textContent = '';
  const form = dlg.querySelector('#collabDialogForm');
  const onClick = (event) => {
    const add = event.target.closest?.('[data-add-row]');
    const remove = event.target.closest?.('[data-remove-row]');
    if (add) {
      event.preventDefault();
      const list = body.querySelector(add.dataset.addRow);
      list?.insertAdjacentHTML('beforeend', rowHtml(add.dataset.rowKind || 'option'));
      list?.lastElementChild?.querySelector('input')?.focus();
    }
    if (remove) {
      event.preventDefault();
      const row = remove.closest('.collab-row');
      const list = row?.parentElement;
      if (list && list.querySelectorAll('.collab-row').length > Number(list.dataset.min || 0)) row.remove();
    }
  };
  body.addEventListener('click', onClick);
  const vis = body.querySelector('[data-visibility]');
  const people = body.querySelector('[data-people]');
  const syncPeople = () => {
    if (people) people.hidden = vis?.value !== 'private';
  };
  vis?.addEventListener('change', syncPeople);
  syncPeople();
  const slugInput = body.querySelector('[data-slug]');
  const source = body.querySelector('[data-question], [data-title]');
  if (slugInput && source) {
    const fill = () => {
      if (!slugInput.dataset.touched) slugInput.value = suggestSlug(source.value);
    };
    source.addEventListener('input', fill);
    slugInput.addEventListener('input', () => { slugInput.dataset.touched = '1'; });
    if (!slugInput.value) fill();
  }
  return new Promise((resolve) => {
    let settled = false;
    let result;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      body.removeEventListener('click', onClick);
      form.removeEventListener('submit', submit);
      dlg.removeEventListener('close', onClose);
      resolve(value);
    };
    const onClose = () => finish(result === undefined ? null : result);
    const submit = async (event) => {
      event.preventDefault();
      err.hidden = true;
      try {
        result = await onSubmit(body);
        dlg.close();
      } catch (e) {
        err.textContent = e.message || 'Could not save';
        err.hidden = false;
      }
    };
    form.addEventListener('submit', submit);
    dlg.addEventListener('close', onClose);
    dlg.showModal();
    body.querySelector('input, textarea')?.focus();
  });
}

function rowHtml(kind, value = '') {
  if (kind === 'item') {
    const text = typeof value === 'string' ? value : (value.text || '');
    const labels = typeof value === 'string' ? '' : (value.labels || []).join(', ');
    return `<div class="collab-row">
      <input class="input input-bordered input-sm" data-text value="${esc(text)}" placeholder="Item" required>
      <input class="input input-bordered input-sm" data-labels value="${esc(labels)}" placeholder="Labels">
      <button type="button" class="btn btn-ghost btn-sm btn-square" data-remove-row aria-label="Remove">×</button>
    </div>`;
  }
  const label = typeof value === 'string' ? value : (value.label || '');
  return `<div class="collab-row">
    <input class="input input-bordered input-sm" data-text value="${esc(label)}" placeholder="Option" required>
    <button type="button" class="btn btn-ghost btn-sm btn-square" data-remove-row aria-label="Remove">×</button>
  </div>`;
}

export function suggestSlug(title) {
  const months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
  const now = new Date();
  const base = String(title || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 28) || 'item';
  return `${base}-${months[now.getMonth()]}${now.getDate()}`;
}

function slugField(existing, hint) {
  return `<label class="collab-field"><span>Short name</span>
    <input class="input input-bordered input-sm" data-slug value="${esc(existing?.slug || '')}" placeholder="${esc(suggestSlug('name'))}" autocomplete="off">
    <span class="collab-hint">${hint}</span>
  </label>`;
}

function peopleField(names) {
  return `<label class="collab-field" data-people>
    <span>People who can open this</span>
    <input class="input input-bordered input-sm" data-participants value="${esc((names || []).join(', '))}" placeholder="usernames, comma separated">
  </label>`;
}

function visibilityField(value) {
  const v = value === 'private' ? 'private' : 'public';
  return `<label class="collab-field">
    <span>Who can open the link</span>
    <select class="select select-bordered select-sm" data-visibility>
      <option value="public" ${v === 'public' ? 'selected' : ''}>Anyone signed in</option>
      <option value="private" ${v === 'private' ? 'selected' : ''}>Only people you invite</option>
    </select>
  </label>`;
}

function readPeople(root) {
  return (root.querySelector('[data-participants]')?.value || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function pollDialog(existing) {
  const options = existing?.options?.length ? existing.options : [{ label: '' }, { label: '' }];
  return openForm({
    title: existing ? 'Edit poll' : 'New poll',
    submitLabel: existing ? 'Save poll' : 'Create poll',
    html: `
      ${existing ? '<p class="collab-note">Changing the question or the options clears every vote.</p>' : ''}
      <label class="collab-field"><span>Question</span>
        <textarea class="textarea textarea-bordered" data-question rows="2" required>${esc(existing?.question || '')}</textarea>
      </label>
      ${slugField(existing, 'In chat, type $$name to mention this poll.')}
      <div class="collab-field"><span>Options</span>
        <div class="collab-rows" data-rows data-min="2">${options.map((o) => rowHtml('option', o)).join('')}</div>
        <button type="button" class="btn btn-ghost btn-xs" data-add-row="[data-rows]" data-row-kind="option">Add option</button>
      </div>
      <label class="collab-check"><input type="checkbox" data-multi ${existing?.allow_multi ? 'checked' : ''}> Allow more than one choice</label>
      ${visibilityField(existing?.visibility)}
      ${peopleField((existing?.participants || []).filter((n) => n !== existing?.organizer))}
    `,
    onSubmit(root) {
      const question = root.querySelector('[data-question]').value.trim();
      const optionsOut = [...root.querySelectorAll('[data-rows] [data-text]')].map((n) => n.value.trim()).filter(Boolean);
      if (!question) throw new Error('Add a question.');
      if (optionsOut.length < 2) throw new Error('Add at least two options.');
      return {
        question,
        slug: root.querySelector('[data-slug]')?.value.trim(),
        options: optionsOut,
        allow_multi: root.querySelector('[data-multi]').checked,
        visibility: root.querySelector('[data-visibility]').value,
        participants: readPeople(root),
      };
    },
  });
}

export function listDialog(existing) {
  const items = existing?.items?.length ? existing.items : [{ text: '', labels: [] }];
  const kind = existing?.kind === 'bullet' ? 'bullet' : 'todo';
  return openForm({
    title: existing ? 'Edit list' : 'New list',
    submitLabel: existing ? 'Save list' : 'Create list',
    html: `
      <label class="collab-field"><span>Title</span>
        <input class="input input-bordered input-sm" data-title value="${esc(existing?.title || '')}" required>
      </label>
      ${slugField(existing, 'In chat, type #name. Example: #grocery-nov1')}
      <label class="collab-field"><span>Type</span>
        <select class="select select-bordered select-sm" data-kind>
          <option value="todo" ${kind === 'todo' ? 'selected' : ''}>Checklist</option>
          <option value="bullet" ${kind === 'bullet' ? 'selected' : ''}>Bullet list</option>
        </select>
      </label>
      ${existing ? '' : `<div class="collab-field"><span>Items</span>
        <div class="collab-rows" data-rows data-min="0">${items.map((it) => rowHtml('item', it)).join('')}</div>
        <button type="button" class="btn btn-ghost btn-xs" data-add-row="[data-rows]" data-row-kind="item">Add item</button>
      </div>`}
      ${visibilityField(existing?.visibility)}
      ${peopleField((existing?.participants || []).filter((n) => n !== existing?.organizer))}
    `,
    onSubmit(root) {
      const title = root.querySelector('[data-title]').value.trim();
      if (!title) throw new Error('Add a title.');
      const payload = {
        title,
        slug: root.querySelector('[data-slug]')?.value.trim(),
        kind: root.querySelector('[data-kind]').value,
        visibility: root.querySelector('[data-visibility]').value,
        participants: readPeople(root),
      };
      if (!existing) {
        payload.items = [...root.querySelectorAll('[data-rows] .collab-row')].map((row) => ({
          text: row.querySelector('[data-text]').value.trim(),
          labels: row.querySelector('[data-labels]').value.split(',').map((s) => s.trim()).filter(Boolean),
        })).filter((it) => it.text);
      }
      return payload;
    },
  });
}

export function meetDialog(existing) {
  return openForm({
    title: existing ? 'Edit meeting' : 'New meeting',
    submitLabel: existing ? 'Save meeting' : 'Create meeting',
    html: `
      <label class="collab-field"><span>Title</span>
        <input class="input input-bordered input-sm" data-title value="${esc(existing?.title || '')}" required placeholder="Team sync">
      </label>
      ${slugField(existing, 'In chat, type //name to mention this meeting.')}
      <p class="collab-note">The join link stays live for 48 hours.</p>
      ${visibilityField(existing?.visibility)}
      ${peopleField((existing?.participants || []).filter((n) => n !== existing?.organizer))}
    `,
    onSubmit(root) {
      const title = root.querySelector('[data-title]').value.trim();
      if (!title) throw new Error('Add a title.');
      return {
        title,
        slug: root.querySelector('[data-slug]')?.value.trim(),
        visibility: root.querySelector('[data-visibility]').value,
        participants: readPeople(root),
      };
    },
  });
}

export async function sharePicker(kind, id) {
  let conversations = [];
  try {
    const data = await apiJson('/api/chat/conversations');
    conversations = data.conversations || [];
  } catch {
    throw new Error('Chat is not available.');
  }
  if (!conversations.length) throw new Error('Start a conversation before sharing.');
  const options = conversations.map((c) => {
    const title = c.title || c.name || c.peer_username || 'Conversation';
    return `<option value="${esc(c.id)}">${esc(title)}</option>`;
  }).join('');
  return openForm({
    title: 'Add to a chat',
    submitLabel: 'Add',
    html: `<label class="collab-field"><span>Conversation</span><select class="select select-bordered select-sm" data-conv>${options}</select></label>`,
    onSubmit: async (root) => {
      const conversation_id = root.querySelector('[data-conv]').value;
      await apiJson(`/api/chat/conversations/${conversation_id}/refs`, {
        method: 'POST',
        body: { kind, id },
      });
      return true;
    },
  });
}

function bars(poll) {
  const max = Math.max(1, ...(poll.options || []).map((o) => o.votes || 0));
  return (poll.options || []).map((o) => {
    const pct = Math.round(((o.votes || 0) / max) * 100);
    return `<button type="button" class="collab-vote ${o.mine ? 'is-mine' : ''}" data-option="${esc(o.id)}" style="--pct:${pct}%">
      <span class="collab-vote-fill"></span>
      <span class="collab-vote-label">${esc(o.label)}</span>
      <span class="collab-vote-count">${o.votes || 0}</span>
    </button>`;
  }).join('');
}

export function mountPoll(root, poll, { me, onChange }) {
  const mine = poll.organizer === me;
  root.innerHTML = `
    <h2 class="text-lg font-bold">${esc(poll.question)}</h2>
    <div class="collab-chips">
      <span class="collab-chip">${visibilityChip(poll.visibility)}</span>
      <span class="collab-chip">${poll.total_votes || 0} votes</span>
      ${poll.allow_multi ? '<span class="collab-chip">Multiple choice</span>' : ''}
    </div>
    <div class="collab-ribbon">
      ${mine ? '<button type="button" class="btn btn-ghost btn-sm" data-act="edit">Edit</button>' : ''}
      <button type="button" class="btn btn-ghost btn-sm" data-act="share">Add to chat</button>
    </div>
    <div class="collab-votes">${bars(poll)}</div>
    <p class="collab-hint">${poll.allow_multi ? 'Select any options, then vote.' : 'Choose one option.'}</p>
    ${poll.allow_multi ? '<button type="button" class="btn btn-primary btn-sm" data-act="vote">Save vote</button>' : ''}`;
  const selected = new Set((poll.options || []).filter((o) => o.mine).map((o) => o.id));
  root.querySelectorAll('[data-option]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.option;
      if (poll.allow_multi) {
        btn.classList.toggle('is-mine');
        if (selected.has(id)) selected.delete(id);
        else selected.add(id);
        return;
      }
      const detail = await apiJson(`/api/polls/${poll.id}/vote`, { method: 'POST', body: { option_ids: [id] } });
      onChange(detail);
    });
  });
  root.querySelector('[data-act="vote"]')?.addEventListener('click', async () => {
    const detail = await apiJson(`/api/polls/${poll.id}/vote`, { method: 'POST', body: { option_ids: [...selected] } });
    onChange(detail);
  });
  root.querySelector('[data-act="edit"]')?.addEventListener('click', async () => {
    const draft = await pollDialog(poll);
    if (!draft) return;
    const detail = await apiJson(`/api/polls/${poll.id}`, { method: 'PATCH', body: draft });
    onChange(detail);
  });
  root.querySelector('[data-act="share"]')?.addEventListener('click', () => sharePicker('poll', poll.id).catch((e) => alert(e.message)));
}

export function mountList(root, list, { me, onChange }) {
  const mine = list.organizer === me;
  const prog = list.progress || {};
  const items = (list.items || []).map((it) => `
    <div class="collab-item ${it.done ? 'is-done' : ''}">
      ${list.kind === 'todo' ? `<input type="checkbox" data-done="${esc(it.id)}" ${it.done ? 'checked' : ''}>` : '<span class="collab-bullet"></span>'}
      <div class="collab-item-main">
        <span>${esc(it.text)}</span>
        ${(it.labels || []).map((lb) => `<span class="collab-chip">${esc(lb)}</span>`).join('')}
      </div>
      <button type="button" class="btn btn-ghost btn-xs" data-edit-item="${esc(it.id)}">Edit</button>
      <button type="button" class="btn btn-ghost btn-xs" data-del-item="${esc(it.id)}" aria-label="Remove">×</button>
    </div>`).join('');
  root.innerHTML = `
    <h2 class="text-lg font-bold">${esc(list.title)}</h2>
    <div class="collab-chips">
      <span class="collab-chip">${visibilityChip(list.visibility)}</span>
      <span class="collab-chip">${list.kind === 'bullet' ? 'Bullets' : 'Checklist'}</span>
      <span class="collab-chip">${prog.done || 0}/${prog.total || 0}</span>
    </div>
    <div class="collab-ribbon">
      ${mine ? '<button type="button" class="btn btn-ghost btn-sm" data-act="edit">Edit</button>' : ''}
      <button type="button" class="btn btn-ghost btn-sm" data-act="share">Add to chat</button>
    </div>
    <div class="collab-items">${items || '<p class="collab-empty">No items yet.</p>'}</div>
    <form class="collab-add" data-add>
      <input class="input input-bordered input-sm" name="text" placeholder="Add an item" required>
      <input class="input input-bordered input-sm" name="labels" placeholder="Labels">
      <button class="btn btn-primary btn-sm" type="submit">Add</button>
    </form>`;
  root.querySelectorAll('[data-done]').forEach((box) => {
    box.addEventListener('change', async () => {
      const detail = await apiJson(`/api/lists/${list.id}/items/${box.dataset.done}`, {
        method: 'PATCH',
        body: { done: box.checked },
      });
      onChange(detail);
    });
  });
  root.querySelectorAll('[data-del-item]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const detail = await apiJson(`/api/lists/${list.id}/items/${btn.dataset.delItem}`, { method: 'DELETE' });
      onChange(detail);
    });
  });
  root.querySelectorAll('[data-edit-item]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const item = (list.items || []).find((it) => it.id === btn.dataset.editItem);
      if (!item) return;
      const draft = await openForm({
        title: 'Edit item',
        submitLabel: 'Save item',
        html: `<label class="collab-field"><span>Text</span><input class="input input-bordered input-sm" data-text value="${esc(item.text)}" required></label>
          <label class="collab-field"><span>Labels</span><input class="input input-bordered input-sm" data-labels value="${esc((item.labels || []).join(', '))}"></label>`,
        onSubmit(node) {
          return {
            text: node.querySelector('[data-text]').value.trim(),
            labels: node.querySelector('[data-labels]').value.split(',').map((s) => s.trim()).filter(Boolean),
          };
        },
      });
      if (!draft?.text) return;
      const detail = await apiJson(`/api/lists/${list.id}/items/${item.id}`, { method: 'PATCH', body: draft });
      onChange(detail);
    });
  });
  root.querySelector('[data-add]')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const fd = new FormData(event.currentTarget);
    const detail = await apiJson(`/api/lists/${list.id}/items`, {
      method: 'POST',
      body: {
        text: String(fd.get('text') || ''),
        labels: String(fd.get('labels') || '').split(',').map((s) => s.trim()).filter(Boolean),
      },
    });
    onChange(detail);
  });
  root.querySelector('[data-act="edit"]')?.addEventListener('click', async () => {
    const draft = await listDialog(list);
    if (!draft) return;
    const detail = await apiJson(`/api/lists/${list.id}`, { method: 'PATCH', body: draft });
    onChange(detail);
  });
  root.querySelector('[data-act="share"]')?.addEventListener('click', () => sharePicker('list', list.id).catch((e) => alert(e.message)));
}

export function mountMeet(root, meet, { me, onChange }) {
  const mine = meet.organizer === me;
  const link = `${location.origin}${meet.join_path || `/meet/${meet.slug}`}`;
  root.innerHTML = `
    <h2 class="text-lg font-bold">${esc(meet.title)}</h2>
    <div class="collab-chips">
      <span class="collab-chip">${visibilityChip(meet.visibility)}</span>
      <span class="collab-chip">${meet.live ? expiresLabel(meet.expires_at) : 'Ended'}</span>
    </div>
    <div class="collab-ribbon">
      ${meet.live ? `<a class="btn btn-primary btn-sm" href="${esc(meet.join_path)}">Join</a>` : ''}
      <button type="button" class="btn btn-ghost btn-sm" data-act="copy">Copy link</button>
      ${mine ? '<button type="button" class="btn btn-ghost btn-sm" data-act="edit">Edit</button>' : ''}
      <button type="button" class="btn btn-ghost btn-sm" data-act="share">Add to chat</button>
      ${mine && meet.live ? '<button type="button" class="btn btn-error btn-sm" data-act="end">End</button>' : ''}
    </div>
    <p class="collab-hint break-all">${esc(link)}</p>`;
  root.querySelector('[data-act="copy"]')?.addEventListener('click', () => navigator.clipboard?.writeText(link));
  root.querySelector('[data-act="edit"]')?.addEventListener('click', async () => {
    const draft = await meetDialog(meet);
    if (!draft) return;
    const detail = await apiJson(`/api/meet/${meet.id}`, { method: 'PATCH', body: draft });
    onChange(detail);
  });
  root.querySelector('[data-act="end"]')?.addEventListener('click', async () => {
    await apiJson(`/api/meet/${meet.id}`, { method: 'DELETE' });
    const detail = await apiJson(`/api/meet/${meet.id}`);
    onChange(detail);
  });
  root.querySelector('[data-act="share"]')?.addEventListener('click', () => sharePicker('meet', meet.id).catch((e) => alert(e.message)));
}

export async function openArtifact(item, me) {
  const kind = item.kind;
  const id = item.id;
  const dlg = ensureDialog();
  const path = kind === 'list' ? `/api/lists/${id}` : kind === 'meet' ? `/api/meet/${id}` : `/api/polls/${id}`;
  const data = await apiJson(path);
  dlg.querySelector('#collabDialogTitle').textContent = data.title || data.question || 'Shared';
  dlg.querySelector('#collabDialogSubmit').textContent = 'Close';
  const body = dlg.querySelector('#collabDialogBody');
  const err = dlg.querySelector('#collabDialogError');
  err.hidden = true;
  const paint = (next) => {
    if (kind === 'poll') mountPoll(body, next, { me, onChange: paint });
    else if (kind === 'list') mountList(body, next, { me, onChange: paint });
    else mountMeet(body, next, { me, onChange: paint });
  };
  paint(data);
  const form = dlg.querySelector('#collabDialogForm');
  const submit = (event) => {
    event.preventDefault();
    dlg.close();
  };
  form.addEventListener('submit', submit);
  dlg.addEventListener('close', () => form.removeEventListener('submit', submit), { once: true });
  dlg.showModal();
}

export async function hydrateCard(node, { me }) {
  const kind = node.dataset.kind;
  let id = node.dataset.id;
  const live = node.querySelector('.chat-collab-live');
  if (!kind || !live) return;
  if (!id && node.dataset.slug) {
    const data = await apiJson(`/api/chat/collab-suggest?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(node.dataset.slug)}`);
    const item = (data.items || []).find((row) => row.slug === node.dataset.slug) || (data.items || [])[0];
    if (!item?.id) {
      live.innerHTML = `<p class="collab-hint">This ${esc(kind)} is not available.</p>`;
      return;
    }
    id = item.id;
    node.dataset.id = id;
  }
  if (!id) return;
  const path = kind === 'list' ? `/api/lists/${id}` : kind === 'meet' ? `/api/meet/${id}` : `/api/polls/${id}`;
  try {
    const data = await apiJson(path);
    const apply = (next) => {
      if (kind === 'poll') mountPoll(live, next, { me, onChange: apply });
      else if (kind === 'list') mountList(live, next, { me, onChange: apply });
      else mountMeet(live, next, { me, onChange: apply });
    };
    apply(data);
  } catch (err) {
    live.innerHTML = `<p class="collab-hint">${esc(err.message || 'Ask the organizer for access.')}</p>`;
  }
}
