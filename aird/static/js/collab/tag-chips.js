function esc(s) {
  const d = document.createElement('div');
  d.textContent = s ?? '';
  return d.innerHTML;
}

function chipColors(hex) {
  if (!hex || typeof hex !== 'string') return null;
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const r = Number.parseInt(m[1].slice(0, 2), 16);
  const g = Number.parseInt(m[1].slice(2, 4), 16);
  const b = Number.parseInt(m[1].slice(4, 6), 16);
  const lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  const fg = lum > 0.55 ? '#111827' : '#f9fafb';
  const norm = '#' + m[1].toLowerCase();
  return {
    bg: norm,
    fg,
    border: 'color-mix(in oklch, ' + norm + ' 65%, transparent)',
  };
}

export function tagChipHtml(tag, { className = 'lists-tag-chip', link = true } = {}) {
  const name = String(tag?.name || '');
  if (!name) return '';
  const label = esc(tag.display_name || name);
  const vars = chipColors(tag.color);
  const style = vars
    ? ` style="background:${vars.bg};color:${vars.fg};border-color:${vars.border}"`
    : '';
  const href = '/tagged/' + encodeURIComponent(name);
  if (link) {
    return `<a href="${esc(href)}" class="${className}" data-tag="${esc(name)}"${style}>${label}</a>`;
  }
  return `<span class="${className}" data-tag="${esc(name)}"${style}>${label}</span>`;
}

export function tagChipsRow(tags, opts = {}) {
  const list = Array.isArray(tags) ? tags : [];
  if (!list.length) return '';
  return `<div class="lists-tag-row">${list.map((t) => tagChipHtml(t, opts)).join('')}</div>`;
}
