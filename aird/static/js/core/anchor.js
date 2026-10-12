/**
 * Place a popover so it stays inside the viewport, flipping above the anchor
 * when there is not enough room below.
 *
 * @param {{
 *   anchor: { top: number, bottom: number, left: number },
 *   panel: { width: number, height: number },
 *   viewport: { width: number, height: number },
 *   pad?: number,
 *   gap?: number,
 *   safe?: { left?: number, right?: number, top?: number, bottom?: number },
 * }} spec
 * @returns {{ top: number, left: number }}
 */
export function placeNearAnchor(spec) {
  const pad = spec.pad ?? 8;
  const gap = spec.gap ?? 6;
  const safe = spec.safe || {};
  const safeL = safe.left || 0;
  const safeR = safe.right || 0;
  const safeT = safe.top || 0;
  const safeB = safe.bottom || 0;
  const { anchor, panel, viewport } = spec;

  let top = anchor.bottom + gap;
  if (top + panel.height > viewport.height - pad - safeB) {
    top = Math.max(pad + safeT, anchor.top - panel.height - gap);
  }

  let left = anchor.left;
  if (left + panel.width > viewport.width - pad - safeR) {
    left = viewport.width - pad - safeR - panel.width;
  }
  left = Math.max(pad + safeL, left);

  return { top, left };
}
