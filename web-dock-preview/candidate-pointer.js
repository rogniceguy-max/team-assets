'use strict';

/** Pointer gestures for long-press candidate and folder sorting. */
function createCandidatePointer(services) {
  const {document, isCandidateMode, findFolderById, onDrop, onError} = services;
  let activeDrag = null;

  function clearDropTarget() {
    document.querySelectorAll('.candidate-card.is-drop-target,.candidate-folder-card.is-drop-folder')
      .forEach(card => card.classList.remove('is-drop-target', 'is-drop-folder'));
  }

  function targetAtPoint(x, y, drag) {
    const element = document.elementFromPoint(x, y)?.closest?.('.candidate-card');
    if (!element || element === drag.card) return null;
    const source = drag.payload;
    if (element.dataset.materialId) {
      const parentPath = element.dataset.folderPath || '';
      const sourceParent = source.kind === 'candidate' ? (source.item.folderPath || '') : (source.folder.parent_path || '');
      if (parentPath !== sourceParent) return null;
      return {element, kind: 'candidate', id: element.dataset.materialId, parentPath};
    }
    if (element.dataset.folderId) {
      const folder = findFolderById(element.dataset.folderId);
      if (!folder) return null;
      if (source.kind === 'candidate') {
        if (folder.path === (source.item.folderPath || '')) return null;
        return {element, kind: 'folder', id: folder.id, folder};
      }
      if ((folder.parent_path || '') !== (source.folder.parent_path || '')) return null;
      return {element, kind: 'folder', id: folder.id, folder};
    }
    return null;
  }

  function update(event) {
    const drag = activeDrag;
    if (!drag) return;
    drag.ghost.style.left = `${event.clientX}px`;
    drag.ghost.style.top = `${event.clientY}px`;
    const next = targetAtPoint(event.clientX, event.clientY, drag);
    if (drag.target?.element !== next?.element) {
      clearDropTarget();
      if (next) next.element.classList.add(next.kind === 'folder' ? 'is-drop-folder' : 'is-drop-target');
    }
    drag.target = next;
  }

  function start(card, payload, event) {
    if (activeDrag) return;
    const ghost = card.cloneNode(true);
    ghost.classList.add('candidate-drag-ghost');
    ghost.style.width = `${card.getBoundingClientRect().width}px`;
    ghost.style.height = `${card.getBoundingClientRect().height}px`;
    document.body.append(ghost);
    activeDrag = {card, payload, pointerId: event.pointerId, ghost, target: null};
    card.classList.add('is-dragging');
    document.body.classList.add('candidate-pointer-dragging');
    update(event);
  }

  async function finish() {
    const drag = activeDrag;
    if (!drag) return;
    activeDrag = null;
    clearDropTarget();
    drag.card.classList.remove('is-dragging');
    drag.ghost.remove();
    document.body.classList.remove('candidate-pointer-dragging');
    if (!drag.target) return;
    try { await onDrop(drag.payload, drag.target); }
    catch (error) { onError(error); }
  }

  function bind(card, payload) {
    let press = null;
    const cancel = () => { if (press?.timer) clearTimeout(press.timer); press = null; };
    card.addEventListener('pointerdown', event => {
      if (event.button !== 0 || !isCandidateMode()) return;
      const current = {pointerId: event.pointerId, x: event.clientX, y: event.clientY, moved: false, started: false, timer: null};
      press = current;
      try { card.setPointerCapture(event.pointerId); } catch {}
      current.timer = setTimeout(() => {
        if (press !== current || current.moved) return;
        current.started = true;
        start(card, payload, event);
      }, 420);
    });
    card.addEventListener('pointermove', event => {
      if (!press || press.pointerId !== event.pointerId) return;
      const distance = Math.hypot(event.clientX - press.x, event.clientY - press.y);
      if (!press.started && distance > 8) { press.moved = true; clearTimeout(press.timer); }
      if (press.started) { event.preventDefault(); update(event); }
    });
    const finishPress = event => {
      if (!press || press.pointerId !== event.pointerId) return;
      const started = press.started;
      clearTimeout(press.timer);
      if (started) { event.preventDefault(); card._candidateSuppressClick = true; finish(); }
      press = null;
      try { card.releasePointerCapture(event.pointerId); } catch {}
    };
    card.addEventListener('pointerup', finishPress);
    card.addEventListener('pointercancel', finishPress);
    card.addEventListener('lostpointercapture', () => { if (press?.started) finish(); cancel(); });
  }

  return Object.freeze({bind});
}
