/**
 * One Escape stack for overlays.
 *
 * Pages used to add a document `keydown` listener per modal, so one Escape
 * closed every open dialog at once. Layers register here; the single
 * listener closes only the highest-priority layer that is currently open.
 */

const layers = [];
let wired = false;

function orderedLayers() {
  return layers
    .map((layer, index) => ({ layer, index }))
    .sort((a, b) => b.layer.priority - a.layer.priority || b.index - a.index)
    .map((entry) => entry.layer);
}

/** Close the top open overlay. Returns true when something closed. */
export function dismissTopOverlay() {
  for (const layer of orderedLayers()) {
    if (!layer.isOpen()) continue;
    if (layer.close() !== false) return true;
  }
  return false;
}

function onKey(event) {
  if (event.key !== 'Escape' || event.defaultPrevented) return;
  if (!dismissTopOverlay()) return;
  event.preventDefault();
  event.stopPropagation();
}

function ensureWired() {
  if (wired || typeof document === 'undefined') return;
  wired = true;
  document.addEventListener('keydown', onKey, true);
}

/**
 * @param {{ priority?: number, isOpen: () => boolean, close: () => boolean|void }} layer
 * @returns {() => void} unregister
 */
export function registerOverlay(layer) {
  ensureWired();
  const entry = {
    priority: layer.priority ?? 0,
    isOpen: layer.isOpen,
    close: layer.close,
  };
  layers.push(entry);
  return () => {
    const index = layers.indexOf(entry);
    if (index >= 0) layers.splice(index, 1);
  };
}
