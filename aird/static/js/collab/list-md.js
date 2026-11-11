function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function inline(text, notes) {
  return esc(text)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[\[([A-Za-z0-9_-]+)\]\]/g, (_, id) => {
      const hit = (notes || []).find((row) => row.id === id);
      const title = hit ? hit.title : 'Missing note';
      return hit
        ? `<button type="button" class="list-mention" data-note="${esc(id)}">${esc(title)}</button>`
        : `<span class="list-mention is-missing">${esc(title)}</span>`;
    })
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^\w*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
}

export function noteTitle(text) {
  const line = String(text || '').split(/\r?\n/).map((row) => row.trim()).find(Boolean) || '';
  return line.replace(/^#{1,3}\s+/, '').replace(/[*_`[\]]/g, '').trim() || 'Untitled';
}

export function notePreview(text) {
  const lines = String(text || '').split(/\r?\n/).map((row) => row.trim()).filter(Boolean);
  const body = lines.slice(1, 3).map((row) => row
    .replace(/^#{1,3}\s+/, '')
    .replace(/^[-*]\s+/, '')
    .replace(/^\d+\.\s+/, '')
    .replace(/^>\s?/, '')
    .replace(/[*_`]/g, '')).join(' ');
  return body;
}

export function renderMarkdown(source, notes) {
  const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
  const out = [];
  let list = '';
  let quote = [];
  let code = null;
  const flushList = () => {
    if (!list) return;
    out.push(`<${list} class="list-md-list">${buf.join('')}</${list}>`);
    list = '';
    buf = [];
  };
  let buf = [];
  const flushQuote = () => {
    if (!quote.length) return;
    out.push(`<blockquote>${quote.map((row) => inline(row, notes)).join('<br>')}</blockquote>`);
    quote = [];
  };
  lines.forEach((line) => {
    if (code) {
      if (line.trim().startsWith('```')) {
        out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
        code = null;
      } else code.push(line);
      return;
    }
    if (line.trim().startsWith('```')) {
      flushList();
      flushQuote();
      code = [];
      return;
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) {
      flushList();
      flushQuote();
      out.push('<hr>');
      return;
    }
    const task = line.match(/^(\s*)[-*]\s+\[([ xX])\]\s+(.*)$/);
    if (task) {
      flushQuote();
      if (list && list !== 'ul') flushList();
      list = 'ul';
      const done = task[2].toLowerCase() === 'x';
      buf.push(`<li class="list-md-task"><input type="checkbox" disabled ${done ? 'checked' : ''}> ${inline(task[3], notes)}</li>`);
      return;
    }
    const item = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
    if (item) {
      flushQuote();
      const next = item[2] === '-' || item[2] === '*' ? 'ul' : 'ol';
      if (list && list !== next) flushList();
      list = next;
      buf.push(`<li>${inline(item[3], notes)}</li>`);
      return;
    }
    flushList();
    if (line.startsWith('>')) {
      quote.push(line.replace(/^>\s?/, ''));
      return;
    }
    flushQuote();
    const head = line.match(/^(#{1,3})\s+(.*)$/);
    if (head) {
      const level = head[1].length;
      out.push(`<h${level}>${inline(head[2], notes)}</h${level}>`);
      return;
    }
    if (!line.trim()) {
      out.push('');
      return;
    }
    out.push(`<p>${inline(line, notes)}</p>`);
  });
  if (code) out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
  flushList();
  flushQuote();
  return out.filter((row) => row !== '').join('');
}

export function wrapSelection(field, before, after = before) {
  const start = field.selectionStart || 0;
  const end = field.selectionEnd || 0;
  const value = field.value;
  const selected = value.slice(start, end) || 'text';
  field.value = `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`;
  const cursor = start + before.length + selected.length;
  field.setSelectionRange(cursor, cursor);
  field.focus();
}
