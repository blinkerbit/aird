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

export function isCompactUi() {
  if (document.documentElement.classList.contains('aird-android')) return true;
  return globalThis.matchMedia('(max-width: 767px)').matches;
}

/**
 * Move a menu to document.body and pin it to the anchor so an ancestor
 * with overflow, blur, or transform cannot clip it.
 */
export function placePortaledMenu(menu, anchorEl, opts = {}) {
  if (!menu || !anchorEl) return;
  if (!menu._airdHome) {
    menu._airdHome = { parent: menu.parentElement, next: menu.nextSibling };
  }
  if (menu.parentElement !== document.body) document.body.appendChild(menu);
  menu.classList.add('aird-portaled-menu');
  if (opts.display) menu.style.setProperty('display', opts.display, 'important');
  const rect = anchorEl.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = globalThis.innerHeight || document.documentElement.clientHeight;
  const width = Math.min(opts.width || menu.offsetWidth || 200, Math.max(160, vw - 16));
  menu.style.setProperty('width', `${width}px`, 'important');
  const pos = placeNearAnchor({
    anchor: rect,
    panel: { width, height: menu.offsetHeight || opts.height || 180 },
    viewport: { width: vw, height: vh },
  });
  menu.style.setProperty('position', 'fixed', 'important');
  menu.style.setProperty('top', `${pos.top}px`, 'important');
  menu.style.setProperty('left', `${pos.left}px`, 'important');
  menu.style.setProperty('right', 'auto', 'important');
  menu.style.setProperty('margin', '0', 'important');
  menu.style.setProperty('z-index', '50000', 'important');
  menu.style.setProperty('transform', 'none', 'important');
}

export function restorePortaledMenu(menu) {
  if (!menu) return;
  menu.classList.remove('aird-portaled-menu');
  ['position', 'top', 'left', 'right', 'bottom', 'width', 'margin', 'z-index', 'transform', 'display']
    .forEach((prop) => menu.style.removeProperty(prop));
  const home = menu._airdHome;
  if (home?.parent && menu.parentElement === document.body) {
    home.parent.insertBefore(menu, home.next);
  }
}
