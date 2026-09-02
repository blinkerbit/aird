'use strict';

function actionList() {
  if (!globalThis.__AIRD_ROW_MORE_ACTIONS) {
    globalThis.__AIRD_ROW_MORE_ACTIONS = [];
  }
  return globalThis.__AIRD_ROW_MORE_ACTIONS;
}

export function registerRowMoreAction(spec) {
  if (!spec?.id || !spec?.label || typeof spec.run !== 'function') return;
  const list = actionList();
  const existing = list.findIndex((a) => a.id === spec.id);
  const entry = {
    id: spec.id,
    label: spec.label,
    when: typeof spec.when === 'function' ? spec.when : () => true,
    run: spec.run,
  };
  if (existing >= 0) list[existing] = entry;
  else list.push(entry);
}

export function buildMoreMenuHtml() {
  return (
    '<span class="row-more-wrap">'
    + '<button type="button" class="action-link row-more-toggle" title="More options" aria-label="More options" aria-haspopup="menu" aria-expanded="false">'
    + '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="5" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="12" cy="19" r="1.5"/></svg>'
    + '</button>'
    + '<div class="row-more-menu" role="menu" hidden></div>'
    + '</span>'
  );
}

function menuForToggle(toggle) {
  const wrap = toggle.closest('.row-more-wrap');
  if (wrap) {
    return wrap.querySelector('.row-more-menu');
  }
  const next = toggle.nextElementSibling;
  return next?.classList.contains('row-more-menu') ? next : null;
}

function rowContext(btn) {
  const row = btn.closest('tr.file-row');
  return {
    path: row?.dataset.path || '',
    isDir: row?.querySelector('.row-checkbox')?.dataset.isDir === '1',
    name: row?.querySelector('.file-name')?.textContent?.trim() || '',
    row,
    cell: btn.closest('.actions-cell'),
  };
}

function closeAllMenus(except) {
  document.querySelectorAll('.row-more-menu').forEach((menu) => {
    if (except && menu === except) return;
    menu.hidden = true;
    const toggle = menu.closest('.row-more-wrap')?.querySelector('.row-more-toggle')
      || menu.previousElementSibling;
    if (toggle?.classList.contains('row-more-toggle')) {
      toggle.setAttribute('aria-expanded', 'false');
    }
  });
}

function populateMenu(menu, ctx) {
  menu.innerHTML = '';
  const visible = actionList().filter((a) => {
    try { return a.when(ctx); } catch { return false; }
  });
  if (!visible.length) {
    menu.hidden = true;
    return false;
  }
  visible.forEach((action) => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'row-more-item';
    item.role = 'menuitem';
    item.textContent = action.label;
    item.dataset.rowMoreAction = action.id;
    menu.append(item);
  });
  return true;
}

export function initRowMoreMenus() {
  if (globalThis.__AIRD_ROW_MORE_MENUS_INIT) return;
  globalThis.__AIRD_ROW_MORE_MENUS_INIT = true;

  document.addEventListener('click', (e) => {
    const toggle = e.target.closest('.row-more-toggle');
    if (toggle) {
      e.preventDefault();
      e.stopPropagation();
      const menu = menuForToggle(toggle);
      if (!menu) return;
      const ctx = rowContext(toggle);
      const hasItems = populateMenu(menu, ctx);
      if (!hasItems) return;
      const open = menu.hidden;
      closeAllMenus(open ? menu : null);
      menu.hidden = !open;
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
      return;
    }
    const item = e.target.closest('.row-more-item');
    if (item) {
      e.preventDefault();
      e.stopPropagation();
      const menu = item.closest('.row-more-menu');
      const toggle = menu?.closest('.row-more-wrap')?.querySelector('.row-more-toggle')
        || menu?.previousElementSibling;
      const ctx = toggle ? rowContext(toggle) : {};
      const action = actionList().find((a) => a.id === item.dataset.rowMoreAction);
      closeAllMenus();
      if (action) {
        Promise.resolve(action.run(ctx)).catch((err) => {
          console.warn('row-more action', err);
        });
      }
      return;
    }
    if (!e.target.closest('.row-more-menu')) closeAllMenus();
  });
}
