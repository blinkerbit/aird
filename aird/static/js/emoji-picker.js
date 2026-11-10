import { EMOJI_GROUPS } from './emoji-catalog.js';

const ICONS = {
  Recent: '🕘',
  'Smileys & Emotion': '😀',
  'People & Body': '👋',
  'Animals & Nature': '🐻',
  'Food & Drink': '🍔',
  'Travel & Places': '✈️',
  Activities: '⚽',
  Objects: '💡',
  Symbols: '❤️',
  Flags: '🏳️',
};

const RECENT_KEY = 'aird.emoji.recent';

function loadRecent() {
  try {
    const saved = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(saved) ? saved.filter((item) => typeof item === 'string').slice(0, 32) : [];
  } catch {
    return [];
  }
}

function remember(emoji) {
  const next = [emoji, ...loadRecent().filter((item) => item !== emoji)].slice(0, 32);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(next)); } catch { /* ignore */ }
}

function byChar() {
  const map = new Map();
  EMOJI_GROUPS.forEach((group) => {
    group[1].forEach((row) => {
      map.set(row[0], { e: row[0], n: row[1], t: row[2] || [] });
      (row[2] || []).forEach((tone) => map.set(tone, { e: tone, n: row[1], t: [] }));
    });
  });
  return map;
}

const LOOKUP = byChar();

export function mountEmojiPicker(root, onPick) {
  if (!root || root.dataset.mounted === '1') return;
  root.dataset.mounted = '1';
  root.innerHTML = `
    <input type="search" class="chat-emoji-search" placeholder="Search emoji" aria-label="Search emoji" autocomplete="off">
    <div class="chat-emoji-tones" hidden></div>
    <div class="chat-emoji-grid"></div>
    <div class="chat-emoji-tabs" role="tablist"></div>`;
  const search = root.querySelector('.chat-emoji-search');
  const tones = root.querySelector('.chat-emoji-tones');
  const grid = root.querySelector('.chat-emoji-grid');
  const tabs = root.querySelector('.chat-emoji-tabs');
  let shown = [];
  let toneList = [];
  let active = 'Recent';
  let holdTimer = 0;
  let suppressClick = false;
  let press = null;

  function attr(value) {
    return String(value || '')
      .replaceAll('&', '&amp;')
      .replaceAll('"', '&quot;')
      .replaceAll('<', '&lt;');
  }

  function groups() {
    const recent = loadRecent()
      .map((emoji) => LOOKUP.get(emoji))
      .filter(Boolean);
    return [{ name: 'Recent', items: recent }, ...EMOJI_GROUPS.map((group) => ({
      name: group[0],
      items: group[1].map((row) => ({ e: row[0], n: row[1], t: row[2] || [] })),
    }))];
  }

  function paintTabs() {
    const names = ['Recent', ...EMOJI_GROUPS.map((group) => group[0])];
    tabs.innerHTML = names.map((name) => (
      `<button type="button" class="chat-emoji-tab${name === active ? ' is-on' : ''}" data-emoji-group="${name}" title="${name}" aria-label="${name}">${ICONS[name] || '•'}</button>`
    )).join('');
  }

  function paint(list) {
    shown = list;
    grid.innerHTML = list.length
      ? list.map((item, i) => `<button type="button" class="chat-emoji-item" data-i="${i}" title="${attr(item.n)}">${item.e}</button>`).join('')
      : '<p class="chat-emoji-empty">No matches</p>';
  }

  function showGroup(name) {
    active = name;
    search.value = '';
    tones.hidden = true;
    const group = groups().find((item) => item.name === name);
    paint(group ? group.items : []);
    paintTabs();
  }

  function choose(emoji) {
    if (!emoji) return;
    remember(emoji);
    onPick(emoji);
  }

  search.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') event.preventDefault();
  });

  search.addEventListener('input', () => {
    const q = search.value.trim().toLowerCase();
    tones.hidden = true;
    if (!q) {
      showGroup(active || 'Smileys & Emotion');
      return;
    }
    active = '';
    paintTabs();
    const hits = [];
    EMOJI_GROUPS.forEach((group) => {
      group[1].forEach((row) => {
        if (row[1].includes(q) || row[0] === search.value.trim()) {
          hits.push({ e: row[0], n: row[1], t: row[2] || [] });
        }
      });
    });
    paint(hits.slice(0, 240));
  });

  tabs.addEventListener('click', (event) => {
    const btn = event.target.closest('[data-emoji-group]');
    if (!btn) return;
    showGroup(btn.dataset.emojiGroup);
  });

  grid.addEventListener('pointerdown', (event) => {
    const btn = event.target.closest('[data-i]');
    if (!btn) return;
    event.preventDefault();
    const item = shown[Number(btn.dataset.i)];
    suppressClick = false;
    press = { i: Number(btn.dataset.i), x: event.clientX, y: event.clientY };
    clearTimeout(holdTimer);
    if (!item?.t?.length) return;
    holdTimer = setTimeout(() => {
      suppressClick = true;
      toneList = item.t;
      tones.hidden = false;
      tones.innerHTML = item.t.map((emoji, i) => `<button type="button" class="chat-emoji-item" data-tone-i="${i}">${emoji}</button>`).join('');
    }, 450);
  });

  function endPress(event) {
    clearTimeout(holdTimer);
    const start = press;
    press = null;
    if (!start || suppressClick) {
      suppressClick = false;
      return;
    }
    if (Math.abs(event.clientX - start.x) + Math.abs(event.clientY - start.y) > 10) return;
    choose(shown[start.i]?.e);
  }

  grid.addEventListener('pointermove', (event) => {
    if (!press) return;
    if (Math.abs(event.clientX - press.x) + Math.abs(event.clientY - press.y) <= 10) return;
    clearTimeout(holdTimer);
    press = null;
  });

  grid.addEventListener('pointerup', endPress);
  grid.addEventListener('pointercancel', () => {
    clearTimeout(holdTimer);
    press = null;
  });

  tones.addEventListener('pointerdown', (event) => {
    if (event.target.closest('[data-tone-i]')) event.preventDefault();
  });

  tones.addEventListener('pointerup', (event) => {
    const btn = event.target.closest('[data-tone-i]');
    if (!btn) return;
    choose(toneList[Number(btn.dataset.toneI)]);
  });

  showGroup(loadRecent().length ? 'Recent' : 'Smileys & Emotion');

  return {
    reset() {
      if (search.value) search.value = '';
      showGroup(loadRecent().length ? 'Recent' : active || 'Smileys & Emotion');
    },
  };
}
