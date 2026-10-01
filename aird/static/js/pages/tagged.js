"use strict";

function xsrfToken() {
  const m = /(?:^|; )_xsrf=([^;]*)/.exec(document.cookie);
  return m ? decodeURIComponent(m[1]) : '';
}

function tagChipColors(hex) {
  if (!hex || typeof hex !== 'string') return null;
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const r = Number.parseInt(m[1].slice(0, 2), 16);
  const g = Number.parseInt(m[1].slice(2, 4), 16);
  const b = Number.parseInt(m[1].slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const norm = '#' + m[1].toLowerCase();
  return {
    bg: norm,
    fg: lum > 0.55 ? '#111827' : '#f9fafb',
    border: 'color-mix(in oklch, ' + norm + ' 65%, transparent)',
  };
}

function paintTagColor(tag, hex) {
  const vars = tagChipColors(hex);
  if (!vars) return;
  const sel = '.file-tag-chip[data-tag="' + CSS.escape(tag) + '"], a[data-tag="' + CSS.escape(tag) + '"]';
  document.querySelectorAll(sel).forEach(function (el) {
    el.style.background = vars.bg;
    el.style.color = vars.fg;
    el.style.borderColor = vars.border;
  });
  const wrap = document.getElementById('taggedPageColor')?.closest('.tag-color-btn');
  if (wrap) wrap.style.setProperty('--tag-swatch', vars.bg);
}

function setError(msg) {
  const errEl = document.getElementById('taggedPageColorError');
  if (!errEl) return;
  errEl.textContent = msg || '';
  errEl.classList.toggle('hidden', !msg);
}

async function saveColor(tag, hex) {
  const res = await fetch('/admin/api/abac/tag-colors', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'X-XSRFToken': xsrfToken() },
    body: JSON.stringify({ tag: tag, color: hex }),
  });
  const data = await res.json().catch(function () { return {}; });
  if (!res.ok) throw new Error(data.error || 'Could not save color.');
  return data.color || hex;
}

function boot() {
  const input = document.getElementById('taggedPageColor');
  if (!input?.dataset.tag) return;
  const tag = input.dataset.tag;
  input.addEventListener('input', function () {
    paintTagColor(tag, input.value);
  });
  input.addEventListener('change', function () {
    paintTagColor(tag, input.value);
    saveColor(tag, input.value).then(function (saved) {
      if (saved && saved !== input.value) input.value = saved;
      paintTagColor(tag, saved || input.value);
      setError('');
      if (typeof BroadcastChannel !== 'undefined') {
        try {
          const bc = new BroadcastChannel('aird.tag-color.v1');
          bc.postMessage({ tag: tag, hex: saved || input.value });
          bc.close();
        } catch { /* ignore */ }
      }
    }).catch(function (err) {
      setError(err.message || 'Could not save color.');
    });
  });
  if (typeof BroadcastChannel !== 'undefined') {
    const bc = new BroadcastChannel('aird.tag-color.v1');
    bc.onmessage = function (ev) {
      const t = ev.data?.tag;
      const hex = ev.data?.hex;
      if (t && hex && t === tag) {
        input.value = hex;
        paintTagColor(tag, hex);
      }
    };
  }
}

boot();
