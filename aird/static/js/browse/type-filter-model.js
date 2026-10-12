/**
 * Pure browse type-filter state. The DOM adapter in type-filter.js applies it.
 */

export const TYPE_CATEGORIES = {
  documents: ['.pdf', '.doc', '.docx', '.txt', '.rtf', '.odt', '.md'],
  images: ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp'],
  videos: ['.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm'],
  archives: ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2'],
  code: ['.js', '.py', '.html', '.css', '.java', '.cpp', '.c'],
  audio: ['.mp3', '.wav', '.flac', '.aac', '.ogg'],
};

const EXT_RE = /^\.[a-z0-9]{1,16}$/;

export function emptyTypeFilter() {
  return { categories: [], extensions: [] };
}

export function parseExtensions(raw) {
  return String(raw || '')
    .split(/[\s,]+/)
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean)
    .map((part) => (part.startsWith('.') ? part : `.${part}`))
    .filter((part) => EXT_RE.test(part));
}

export function normalizeTypeFilter(raw) {
  const categories = (raw?.categories || []).filter((cat) => TYPE_CATEGORIES[cat]);
  const extensions = (raw?.extensions || []).filter((ext) => EXT_RE.test(ext));
  return { categories, extensions };
}

export function typeFilterFromSearch(type, ext) {
  if (!type && !ext) return null;
  return normalizeTypeFilter({
    categories: String(type || '').split(',').filter(Boolean),
    extensions: parseExtensions(ext || ''),
  });
}

export function toggleCategory(state, cat) {
  if (!TYPE_CATEGORIES[cat]) return state;
  const categories = state.categories.includes(cat)
    ? state.categories.filter((item) => item !== cat)
    : state.categories.concat(cat);
  return { categories, extensions: state.extensions };
}

export function isFiltering(state) {
  return state.categories.length > 0 || state.extensions.length > 0;
}

export function extensionFromName(name) {
  const text = String(name || '').trim();
  const dot = text.lastIndexOf('.');
  if (dot <= 0) return '';
  return text.slice(dot).toLowerCase();
}

export function rowMatchesFilter(state, { isDir, name }) {
  if (isDir || !isFiltering(state)) return true;
  const ext = extensionFromName(name);
  if (state.extensions.includes(ext)) return true;
  return state.categories.some((cat) => TYPE_CATEGORIES[cat].includes(ext));
}
