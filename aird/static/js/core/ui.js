/**
 * Nav chrome wiring (theme picker, mobile dropdowns) shared by every page
 * through the classic `AirdCore` facade bundle.
 */
import { placeNearAnchor } from './anchor.js';
import { wireDialogCancelOnce } from './dialog.js';

function wireAppThemePicker() {
  const btn = document.getElementById('airdAppThemeBtn');
  const menu = document.getElementById('airdAppThemeMenu');
  if (!btn || !menu) return;

  const menuHome = { parent: menu.parentElement, next: menu.nextSibling };
  let open = false;

  function placeMenu() {
    const rect = btn.getBoundingClientRect();
    const pos = placeNearAnchor({
      anchor: rect,
      panel: { width: menu.offsetWidth || 176, height: menu.offsetHeight || 280 },
      viewport: {
        width: document.documentElement.clientWidth,
        height: globalThis.innerHeight,
      },
    });
    menu.style.setProperty('top', `${pos.top}px`);
    menu.style.setProperty('left', `${pos.left}px`);
  }

  function showMenu() {
    if (open) return;
    open = true;
    if (menu.parentElement !== document.body) {
      document.body.appendChild(menu);
    }
    menu.hidden = false;
    menu.classList.add('aird-app-theme-menu--open');
    btn.setAttribute('aria-expanded', 'true');
    globalThis.requestAnimationFrame(() => {
      globalThis.requestAnimationFrame(placeMenu);
    });
  }

  function hideMenu() {
    if (!open) return;
    open = false;
    menu.hidden = true;
    menu.classList.remove('aird-app-theme-menu--open');
    btn.setAttribute('aria-expanded', 'false');
    menu.style.removeProperty('top');
    menu.style.removeProperty('left');
    if (menuHome.parent && menu.parentElement === document.body) {
      menuHome.parent.insertBefore(menu, menuHome.next);
    }
  }

  btn.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (open) hideMenu();
    else showMenu();
  });

  menu.querySelectorAll('[data-set-theme]').forEach((item) => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      hideMenu();
    });
  });

  document.addEventListener('click', (e) => {
    if (!btn.contains(e.target) && !menu.contains(e.target)) {
      hideMenu();
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideMenu();
  });

  globalThis.addEventListener('resize', () => {
    if (open) placeMenu();
  });
}

/** Keep nav dropdowns (theme, etc.) inside the viewport on narrow screens. */
function wireMobileAppDropdowns() {
  const mq = globalThis.matchMedia('(max-width: 767px)');
  const compact = () => mq.matches || document.documentElement.classList.contains('aird-android');
  const panelSel = '.aird-app-dropdown-panel, .dropdown-content';

  function safeInset(edge) {
    const v = Number.parseFloat(
      getComputedStyle(document.documentElement).getPropertyValue(`env(safe-area-inset-${edge})`)
    );
    return Number.isFinite(v) ? v : 0;
  }

  function getPanel(root) {
    return root._airdDropdownPanel || root.querySelector(panelSel);
  }

  function panelContains(root, target) {
    const panel = getPanel(root);
    return Boolean(panel && target instanceof Node && panel.contains(target));
  }

  function positionPanel(root) {
    if (!compact()) return;
    const btn = root.querySelector(':scope > button');
    const panel = getPanel(root);
    if (!btn || !panel) return;

    if (!root._airdPanelHome) {
      root._airdPanelHome = { parent: panel.parentElement, next: panel.nextSibling };
      root._airdDropdownPanel = panel;
    }
    if (panel.parentElement !== document.body) {
      document.body.appendChild(panel);
    }

    const pad = 8;
    const gap = 6;
    const rect = btn.getBoundingClientRect();
    const vw = document.documentElement.clientWidth;
    const vh = globalThis.innerHeight;
    const safeL = safeInset('left');
    const safeR = safeInset('right');
    const safeT = safeInset('top');
    const safeB = safeInset('bottom');
    const menuW = Math.min(176, vw - pad * 2 - safeL - safeR);

    panel.classList.add('aird-dropdown-fixed', 'aird-dropdown-portal', 'aird-dropdown-portal-open');
    panel.style.setProperty('position', 'fixed', 'important');
    panel.style.setProperty('display', 'block', 'important');
    panel.style.setProperty('visibility', 'visible', 'important');
    panel.style.setProperty('opacity', '1', 'important');
    panel.style.setProperty('width', `${menuW}px`, 'important');
    panel.style.setProperty('max-width', `${menuW}px`, 'important');
    panel.style.setProperty('margin', '0', 'important');
    panel.style.setProperty('transform', 'none', 'important');
    panel.style.setProperty('translate', 'none', 'important');
    panel.style.setProperty('inset', 'unset', 'important');
    panel.style.setProperty('right', 'auto', 'important');
    panel.style.setProperty('bottom', 'auto', 'important');

    globalThis.requestAnimationFrame(() => {
      const ph = panel.getBoundingClientRect().height || panel.offsetHeight || 0;
      const pw = panel.getBoundingClientRect().width || menuW;
      const pos = placeNearAnchor({
        anchor: rect,
        panel: { width: pw, height: ph },
        viewport: { width: vw, height: vh },
        pad,
        gap,
        safe: { left: safeL, right: safeR, top: safeT, bottom: safeB },
      });
      panel.style.setProperty('top', `${pos.top}px`, 'important');
      panel.style.setProperty('left', `${pos.left}px`, 'important');
    });
  }

  function resetPanel(root) {
    const panel = getPanel(root);
    if (!panel) return;
    panel.classList.remove('aird-dropdown-fixed', 'aird-dropdown-portal', 'aird-dropdown-portal-open');
    [
      'position', 'top', 'left', 'right', 'bottom', 'width', 'max-width', 'margin',
      'transform', 'translate', 'inset', 'visibility', 'opacity', 'display',
    ].forEach((prop) => {
      panel.style.removeProperty(prop);
    });
    const home = root._airdPanelHome;
    if (home?.parent && panel.parentElement === document.body) {
      home.parent.insertBefore(panel, home.next);
    }
  }

  function closeAllDropdowns(except) {
    document.querySelectorAll('.aird-app-dropdown.dropdown-open').forEach((root) => {
      if (root === except) return;
      root.classList.remove('dropdown-open');
      resetPanel(root);
      const b = root.querySelector(':scope > button');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  }

  document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
    const btn = root.querySelector(':scope > button');

    if (btn) {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const opening = !root.classList.contains('dropdown-open');
        closeAllDropdowns(root);
        root.classList.toggle('dropdown-open', opening);
        btn.setAttribute('aria-expanded', opening ? 'true' : 'false');
        if (opening) {
          if (compact()) {
            positionPanel(root);
          }
        } else {
          resetPanel(root);
        }
      });
    }
  });

  document.addEventListener('click', (e) => {
    document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
      if (root.contains(e.target) || panelContains(root, e.target)) return;
      root.classList.remove('dropdown-open');
      resetPanel(root);
      const b = root.querySelector(':scope > button');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  });

  globalThis.addEventListener('resize', () => {
    document.querySelectorAll('.aird-app-dropdown.dropdown-open').forEach((root) => {
      if (compact()) positionPanel(root);
      else resetPanel(root);
    });
  });

  mq.addEventListener('change', () => {
    document.querySelectorAll('.aird-app-dropdown').forEach((root) => {
      root.classList.remove('dropdown-open');
      resetPanel(root);
      const b = root.querySelector(':scope > button');
      if (b) b.setAttribute('aria-expanded', 'false');
    });
  });
}

export function wireAirdCoreUi() {
  wireDialogCancelOnce();
  wireAppThemePicker();
  wireMobileAppDropdowns();
}
