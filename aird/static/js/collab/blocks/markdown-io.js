import { newId } from './hlc.js';
import { midOrder } from './order-key.js';

export function parseMarkdown(source, listId = '') {
  const lines = String(source || '').replace(/\r\n/g, '\n').split('\n');
  const items = [];
  let prev = null;
  lines.forEach((line) => {
    if (!line.trim() && !items.length) return;
    let depth = 0;
    const indent = line.match(/^(\t| {2})*/)?.[0] || '';
    depth = Math.min(5, indent.includes('\t') ? indent.length : Math.floor(indent.length / 2));
    const body = line.slice(indent.length);
    let type = 'paragraph';
    let content = body;
    let isChecked = false;
    const check = body.match(/^[-*]\s+\[([ xX])\]\s+(.*)$/);
    const bullet = body.match(/^[-*•]\s+(.*)$/);
    if (check) {
      type = 'checkbox';
      isChecked = check[1].toLowerCase() === 'x';
      content = check[2];
    } else if (bullet) {
      type = 'bullet';
      content = bullet[1];
    }
    const order = midOrder(prev?.order, null);
    const item = {
      id: newId(),
      listId,
      type,
      content,
      order,
      depth,
      isChecked,
      isCollapsed: false,
      tags: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      version: 1,
    };
    items.push(item);
    prev = item;
  });
  return items;
}

export function toMarkdown(title, items) {
  const lines = [];
  if (title) lines.push(`# ${title}`, '');
  for (const item of items) {
    const pad = '  '.repeat(item.depth || 0);
    if (item.type === 'checkbox') {
      lines.push(`${pad}- [${item.isChecked ? 'x' : ' '}] ${item.content}`);
    } else if (item.type === 'bullet') {
      lines.push(`${pad}* ${item.content}`);
    } else {
      lines.push(`${pad}${item.content}`);
    }
  }
  return lines.join('\n');
}
