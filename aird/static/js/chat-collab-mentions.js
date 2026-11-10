import { apiJson } from './collab-api.js';
import { listDialog, meetDialog, openArtifact, pollDialog } from './collab-ui.js';

const KINDS = {
  '#': { kind: 'list', label: 'list', create: 'Create a list' },
  '$$': { kind: 'poll', label: 'poll', create: 'Create a poll' },
  '//': { kind: 'meet', label: 'meeting', create: 'Create a meeting' },
};

let box = null;
let rows = [];
let idx = 0;
let suppressed = '';
let busy = false;

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

export function triggerFromToken(token, opts = {}) {
  const raw = String(token || '');
  let sigil = '';
  if (raw.startsWith('$$')) sigil = '$$';
  else if (raw.startsWith('//')) sigil = '//';
  else if (raw.startsWith('#')) sigil = '#';
  else return null;
  let query = raw.slice(sigil.length);
  query = query.replace(/^(list|poll|meet):/i, '');
  if (!/^[a-z0-9-]*$/i.test(query)) return null;
  if (sigil === '#' && /^\d+$/.test(query)) return null;
  if (!opts.ignoreSuppress && suppressed && suppressed === raw) return null;
  return { sigil, kind: KINDS[sigil].kind, query: query.toLowerCase(), token: raw };
}

export function mentionTokens(text) {
  const found = [];
  for (const part of String(text || '').split(/\s+/)) {
    const clean = part.replace(/[.,!?;:]+$/g, '');
    const trig = triggerFromToken(clean, { ignoreSuppress: true });
    if (!trig?.query) continue;
    const explicit = /^(#list:|\$\$poll:|\/\/meet:)/i.test(clean);
    if (!explicit && !trig.query.includes('-')) continue;
    found.push(trig);
  }
  return found;
}

function ensureBox(anchor) {
  if (box) return box;
  box = document.createElement('div');
  box.id = 'chatCollabSuggest';
  box.className = 'chat-collab-suggest';
  box.hidden = true;
  anchor?.appendChild(box);
  return box;
}

function hide() {
  rows = [];
  idx = 0;
  if (box) {
    box.hidden = true;
    box.innerHTML = '';
  }
}

export function hideCollabMentions() {
  hide();
}

function paint(ctx, info) {
  if (!box) return;
  box.hidden = false;
  box.innerHTML = rows.map((row, i) => {
    const on = i === idx ? ' is-active' : '';
    const sub = row.item ? esc(row.item.token) : '';
    return `<button type="button" data-i="${i}" class="${on}"><span>${esc(row.label)}</span><span class="collab-hint">${sub}</span></button>`;
  }).join('');
  box.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('mousedown', (event) => event.preventDefault());
    btn.addEventListener('click', () => {
      idx = Number(btn.dataset.i);
      choose(ctx, info).catch((err) => alert(err.message));
    });
  });
  box.querySelector('.is-active')?.scrollIntoView({ block: 'nearest' });
}

async function choose(ctx, info) {
  const row = rows[idx];
  if (!row || busy) return;
  if (row.keep) {
    suppressed = info.token;
    hide();
    return;
  }
  busy = true;
  try {
    let item = row.item;
    if (row.create) {
      const draft = row.kind === 'poll' ? await pollDialog() : row.kind === 'meet' ? await meetDialog() : await listDialog();
      if (!draft) return;
      const path = row.kind === 'poll' ? '/api/polls' : row.kind === 'meet' ? '/api/meet' : '/api/lists';
      const created = await apiJson(path, { method: 'POST', body: draft });
      const linked = await apiJson(`/api/chat/conversations/${ctx.convId()}/refs`, {
        method: 'POST',
        body: { kind: row.kind, id: created.id },
      });
      item = linked.ref;
    } else if (ctx.convId()) {
      const linked = await apiJson(`/api/chat/conversations/${ctx.convId()}/refs`, {
        method: 'POST',
        body: { kind: item.kind, id: item.id },
      });
      item = linked.ref || item;
    }
    if (item?.token) ctx.replace(info, `${item.token} `);
    suppressed = '';
    hide();
    ctx.onLinked?.();
  } finally {
    busy = false;
  }
}

export async function updateCollabMention(info, ctx) {
  const trig = info ? triggerFromToken(info.token) : null;
  if (!trig || !ctx.convId()) {
    hide();
    return;
  }
  ensureBox(ctx.anchor);
  const meta = KINDS[trig.sigil];
  let items = [];
  try {
    const data = await apiJson(`/api/chat/collab-suggest?kind=${trig.kind}&q=${encodeURIComponent(trig.query)}`);
    items = (data.items || []).filter((item) => item.may_share !== false);
  } catch {
    items = [];
  }
  const latest = ctx.token();
  if (!latest || latest.token !== info.token) return;
  rows = [
    { create: true, kind: trig.kind, label: meta.create },
    ...items.map((item) => ({ item, kind: trig.kind, label: item.title })),
    { keep: true, label: `Keep ${trig.sigil} in the message` },
  ];
  idx = 0;
  paint(ctx, latest);
}

export function collabMentionKey(event, info, ctx) {
  const trig = info ? triggerFromToken(info.token) : null;
  const open = box && !box.hidden && rows.length;
  if (!open) return false;
  if (!trig) {
    hide();
    return false;
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    const delta = event.key === 'ArrowDown' ? 1 : -1;
    idx = (idx + delta + rows.length) % rows.length;
    paint(ctx, info);
    return true;
  }
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    event.stopPropagation();
    choose(ctx, info).catch((err) => alert(err.message));
    return true;
  }
  if (event.key === 'Escape') {
    event.preventDefault();
    suppressed = info.token;
    hide();
    return true;
  }
  return false;
}

export async function linkMentionTokens(convId, text) {
  const tokens = mentionTokens(text);
  for (const trig of tokens) {
    try {
      await apiJson(`/api/chat/conversations/${convId}/refs`, {
        method: 'POST',
        body: { kind: trig.kind, slug: trig.query },
      });
    } catch {
      /* unknown or private name stays as text */
    }
  }
}

const FOCUS_KIND = { poll: 'Poll', list: 'List', meet: 'Meet' };

function composerShowsFocus() {
  const composer = document.getElementById('chatComposer');
  if (!composer || composer.classList.contains('hidden')) return false;
  if (composer.classList.contains('chat-composer--enhanced')) return false;
  return true;
}

export async function renderShelf(convId, me) {
  const shelf = document.getElementById('chatCollabShelf');
  if (!shelf) return;
  const settle = (stuck) => {
    if (stuck) requestAnimationFrame(() => globalThis.AirdChatThread?.end?.());
  };
  if (!convId) {
    const stuck = globalThis.AirdChatThread?.near?.();
    shelf.hidden = true;
    shelf.innerHTML = '';
    settle(stuck);
    return;
  }
  let refs = [];
  try {
    const data = await apiJson(`/api/chat/conversations/${convId}/refs`);
    refs = (data.refs || []).filter((ref) => ref.active !== false && (ref.kind === 'list' || ref.kind === 'poll'));
  } catch {
    refs = [];
  }
  const stuck = globalThis.AirdChatThread?.near?.();
  if (!composerShowsFocus() || !refs.length) {
    shelf.hidden = true;
    shelf.innerHTML = '';
    settle(stuck);
    return;
  }
  shelf.hidden = false;
  shelf.innerHTML = refs.map((ref, i) => (
    `<button type="button" class="chat-focus-bubble" data-kind="${esc(ref.kind)}" data-i="${i}" title="${esc(ref.title)}"><span class="chat-focus-kind">${esc(FOCUS_KIND[ref.kind] || ref.kind)}</span><span class="chat-focus-title">${esc(ref.title)}</span></button>`
  )).join('');
  shelf.querySelectorAll('button').forEach((btn) => {
    btn.addEventListener('click', () => {
      const ref = refs[Number(btn.dataset.i)];
      if (!ref) return;
      openArtifact(ref, me).catch((err) => alert(err.message));
    });
  });
  settle(stuck);
}

export async function openMentionToken(token, me) {
  const trig = triggerFromToken(String(token || '').trim(), { ignoreSuppress: true });
  if (!trig?.query) return;
  const prev = suppressed;
  suppressed = '';
  const data = await apiJson(`/api/chat/collab-suggest?kind=${trig.kind}&q=${encodeURIComponent(trig.query)}`);
  suppressed = prev;
  const needle = String(trig.query || '').toLowerCase();
  const item = (data.items || []).find((row) => String(row.slug || '').toLowerCase() === needle);
  if (!item) return;
  await openArtifact(item, me);
}
