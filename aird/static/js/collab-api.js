import { getXSRFToken } from './core/xsrf.js';

export async function apiJson(path, { method = 'GET', body } = {}) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') {
    const xsrf = getXSRFToken();
    if (xsrf) headers['X-XSRFToken'] = xsrf;
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText || 'Request failed');
  return data;
}

export function visibilityChip(v) {
  return v === 'private' ? 'Private' : 'Public';
}

export function expiresLabel(iso) {
  if (!iso) return '';
  const end = Date.parse(iso);
  if (Number.isNaN(end)) return '';
  const hrs = Math.max(0, Math.round((end - Date.now()) / 3600000));
  return hrs ? `Expires in ~${hrs}h` : 'Expired';
}
