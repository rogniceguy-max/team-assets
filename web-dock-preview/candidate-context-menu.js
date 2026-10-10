'use strict';

/** Positioning, hover behavior, and event routing for candidate context menus. */
function createCandidateContextMenu(services) {
  const {document, getCandidateById, findFolderById, onAction, onMark} = services;
  const $ = selector => document.querySelector(selector);
  let contextId = null;
  let contextKind = 'candidate';
  let contextFolderId = null;
  let submenuHideTimer = null;

  function elements() {
    return {menu: $('#candidateContextMenu'), submenu: $('#candidateContextSubmenu') || document.querySelector('.candidate-context-submenu')};
  }

  function positionSubmenu(menu, submenu) {
    if (!menu || !submenu) return;
    const rect = menu.getBoundingClientRect(), width = 177, height = 226, gap = 6;
    const opensLeft = rect.right + gap + width > innerWidth - 8;
    const left = opensLeft ? rect.left - gap - width : rect.right + gap;
    const top = Math.max(8, Math.min(rect.top, innerHeight - height - 8));
    submenu.style.left = `${Math.max(8, Math.min(left, innerWidth - width - 8))}px`;
    submenu.style.top = `${top}px`;
    submenu.classList.toggle('opens-left', opensLeft);
  }

  function syncDisableAction(item) {
    const menu = $('#candidateContextMenu'), action = menu?.querySelector('[data-candidate-action="disable"], [data-candidate-action="restore"]');
    if (!action) return;
    const disabled = Boolean(item?.disabled);
    action.dataset.candidateAction = disabled ? 'restore' : 'disable';
    action.querySelector('.context-label').textContent = disabled ? '还原' : '禁用';
    action.querySelector('.context-icon')?.style.setProperty('--icon', `url('/icons/context-${disabled ? 'restore' : 'disable'}.svg')`);
  }

  function openCandidate(item, x, y) {
    const {menu, submenu} = elements(); if (!menu) return;
    clearTimeout(submenuHideTimer);
    contextKind = 'candidate'; contextId = item.id; contextFolderId = null;
    menu.dataset.kind = 'candidate'; syncDisableAction(item); menu.hidden = false;
    if (submenu) { submenu.hidden = true; positionSubmenu(menu, submenu); }
    const width = 193, height = 226;
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - height - 8))}px`;
    if (submenu) positionSubmenu(menu, submenu);
  }

  function openFolder(folder, x, y) {
    const {menu, submenu} = elements(); if (!menu) return;
    clearTimeout(submenuHideTimer);
    contextKind = 'folder'; contextId = null; contextFolderId = folder.id;
    menu.dataset.kind = 'folder'; syncDisableAction(folder); menu.hidden = false;
    if (submenu) submenu.hidden = true;
    const width = 193, height = 116;
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - height - 8))}px`;
  }

  function showSubmenu() {
    const {menu, submenu} = elements();
    if (!submenu) return;
    clearTimeout(submenuHideTimer);
    positionSubmenu(menu, submenu);
    submenu.hidden = false;
  }

  function scheduleHideSubmenu() {
    clearTimeout(submenuHideTimer);
    submenuHideTimer = setTimeout(() => {
      const {menu, submenu} = elements();
      if (menu && submenu && !menu.hidden) submenu.hidden = true;
    }, 180);
  }

  function close() {
    clearTimeout(submenuHideTimer);
    const {menu, submenu} = elements();
    if (menu) { menu.hidden = true; menu.dataset.kind = 'candidate'; }
    if (submenu) submenu.hidden = true;
    contextId = null; contextFolderId = null; contextKind = 'candidate';
  }

  function currentCandidate() { return contextKind === 'candidate' ? getCandidateById(contextId) : null; }
  function currentFolder() { return contextKind === 'folder' ? findFolderById(contextFolderId) : null; }

  function handleClick(event) {
    const action = event.target.closest('[data-candidate-action]')?.dataset.candidateAction;
    const category = event.target.closest('[data-candidate-category]')?.dataset.candidateCategory;
    if (category) {
      const item = currentCandidate();
      if (item) onMark(item, category);
      close();
      return;
    }
    if (action === 'mark') { showSubmenu(); return; }
    if (action) {
      const context = {kind: contextKind, item: currentCandidate(), folder: currentFolder()};
      close();
      onAction(action, context);
    }
  }

  function bind() {
    const {menu, submenu} = elements(); if (!menu || menu.dataset.contextMenuBound === 'true') return;
    menu.dataset.contextMenuBound = 'true';
    const mark = menu.querySelector('[data-candidate-action="mark"]');
    menu.addEventListener('click', handleClick);
    submenu?.addEventListener('click', handleClick);
    mark?.addEventListener('pointerenter', showSubmenu);
    mark?.addEventListener('pointerleave', scheduleHideSubmenu);
    submenu?.addEventListener('pointerenter', showSubmenu);
    submenu?.addEventListener('pointerleave', scheduleHideSubmenu);
    document.addEventListener('pointerdown', event => {
      if (!menu.hidden && !menu.contains(event.target) && !submenu?.contains(event.target)) close();
    });
    document.addEventListener('keydown', event => { if (event.key === 'Escape') close(); });
  }

  return Object.freeze({openCandidate, openFolder, close, currentCandidate, currentFolder, bind});
}
