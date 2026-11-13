export function blockType(value, fallback = 'checkbox') {
  const raw = String(value || '').trim().toLowerCase();
  if (raw === 'checkbox' || raw === 'check' || raw === 'todo') return 'checkbox';
  if (raw === 'bullet') return 'bullet';
  if (raw === 'paragraph' || raw === 'note' || raw === 'text') return 'paragraph';
  return fallback;
}

export function normalizeBlock(raw, listId = '') {
  const type = blockType(raw?.type || raw?.kind);
  return {
    id: String(raw?.id || ''),
    listId: String(raw?.listId || raw?.list_id || listId || ''),
    type,
    content: String(raw?.content ?? raw?.text ?? ''),
    order: String(raw?.order || raw?.order_key || ''),
    depth: Math.max(0, Math.min(5, Number(raw?.depth) || 0)),
    isChecked: Boolean(raw?.isChecked ?? raw?.done),
    isCollapsed: Boolean(raw?.isCollapsed ?? raw?.collapsed),
    tags: Array.isArray(raw?.tags) ? raw.tags : (Array.isArray(raw?.labels) ? raw.labels : []),
    createdAt: Number(raw?.createdAt) || Date.now(),
    updatedAt: Number(raw?.updatedAt) || Date.now(),
    version: Number(raw?.version) || 1,
  };
}

export function fromApiList(list) {
  return (list?.items || []).map((item) => normalizeBlock(item, list.id));
}
