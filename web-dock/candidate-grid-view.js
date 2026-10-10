'use strict';

/** Candidate cards, justified grid layout, and layout-preserving transitions. */
function createCandidateGridView(services) {
  const {
    document,
    node,
    button,
    isVideo,
    folderPath,
    getZoomed,
    getFolderPath,
    setFolderPath,
    saveView,
    renderLibrary,
    openPreview,
    openCandidateContext,
    openFolderContext,
    bindLongPress,
    scheduleHighlightExpiry,
    getRevealIds,
    getRevealPlayedIds,
  } = services;

  function ratio(item) {
    const width = Number(item.width), height = Number(item.height);
    if (width > 0 && height > 0) return width / height;
    return isVideo(item) ? 16 / 9 : 1;
  }

  function rows(items, width, target = 200) {
    // Justified gallery: each complete row fills the available width while
    // staying close to the requested card height.
    const result = [], gap = 8;
    let row = [], sum = 0;
    const flush = justify => {
      if (!row.length) return;
      const available = width - gap * (row.length - 1);
      const height = Math.min(justify ? Infinity : target, Math.max(1, available / sum));
      result.push(row.map(entry => ({entry, width: entry.ratio * height, height})));
      row = []; sum = 0;
    };
    for (const raw of items) {
      const entry = raw.kind
        ? {...raw, ratio: raw.kind === 'folder' ? 1 : ratio(raw.item)}
        : {kind: 'candidate', item: raw, ratio: ratio(raw)};
      if (row.length && (sum + entry.ratio) * target + gap * row.length >= width) {
        const before = (width - gap * (row.length - 1)) / sum;
        const after = (width - gap * row.length) / (sum + entry.ratio);
        if (Math.abs(before - target) < Math.abs(after - target)) flush(true);
      }
      row.push(entry); sum += entry.ratio;
      if (sum * target + gap * (row.length - 1) >= width) flush(true);
    }
    flush(false);
    return result;
  }

  function releaseVideos(root) {
    if (!root?.querySelectorAll) return;
    root.querySelectorAll('video').forEach(video => {
      try {
        video.pause();
        video.removeAttribute('src');
        video.querySelectorAll('source').forEach(source => source.removeAttribute('src'));
        video.load();
      } catch { /* detached media nodes may already be disposed */ }
    });
  }

  function dispose(root) {
    if (!root?.querySelectorAll) return;
    releaseVideos(root);
    root.querySelectorAll('.candidate-library-grid').forEach(grid => {
      grid._candidateResizeObserver?.disconnect();
      grid._candidateResizeObserver = null;
    });
  }

  function media(item) {
    const url = item.thumbnail_url || item.url || '';
    if (isVideo(item) && !item.thumbnail_url) {
      const video = document.createElement('video');
      video.src = url;
      video.muted = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.setAttribute('aria-hidden', 'true');
      video.addEventListener('loadedmetadata', () => { try { video.currentTime = 0; } catch {} }, {once: true});
      return video;
    }
    const image = node('img'); image.src = url; image.alt = item.name || '候选素材'; image.loading = 'lazy';
    return image;
  }

  function candidateCard(item) {
    const card = button('', () => {
      if (card._candidateSuppressClick) { card._candidateSuppressClick = false; return; }
      openPreview(item);
    }, 'candidate-card');
    card.dataset.materialId = item.id;
    card.dataset.folderPath = item.folderPath || folderPath(item.candidatePath);
    card.title = item.name || '候选素材';
    card.setAttribute('aria-label', `预览 ${item.name || '候选素材'}`);
    card.classList.toggle('is-disabled', item.disabled);
    card.classList.toggle('is-highlighted', item.highlightUntil > Date.now());
    scheduleHighlightExpiry(item);
    card.append(media(item));
    if (isVideo(item)) card.append(node('span', 'candidate-video-badge', '▶'));
    if (item.disabled) card.append(node('span', 'candidate-disabled-badge', '已禁用'));
    card.append(node('span', 'candidate-name', item.name || '未命名素材'));
    card.addEventListener('contextmenu', event => { event.preventDefault(); openCandidateContext(item, event.clientX, event.clientY); });
    bindLongPress(card, {kind: 'candidate', item});
    return card;
  }

  function folderCard(folder) {
    const card = button('', () => {
      if (card._candidateSuppressClick) { card._candidateSuppressClick = false; return; }
      setFolderPath(folder.path); saveView(); renderLibrary();
    }, 'candidate-card candidate-folder-card');
    card.dataset.folderPath = folder.path;
    card.dataset.parentFolderPath = folder.parent_path || '';
    card.dataset.folderId = folder.id;
    card.title = `${folder.path}（文件夹）`;
    card.setAttribute('aria-label', `打开文件夹 ${folder.name}`);
    card.append(node('span', 'candidate-folder-icon', ''), node('span', 'candidate-folder-name', folder.name));
    if (folder.disabled) card.append(node('span', 'candidate-disabled-badge', '已禁用'));
    card.addEventListener('contextmenu', event => { event.preventDefault(); openFolderContext(folder, event.clientX, event.clientY); });
    bindLongPress(card, {kind: 'folder', folder});
    return card;
  }

  function viewportSnapshot(board) {
    const cards = [...(board?.querySelectorAll('.candidate-card[data-material-id]') || [])];
    if (!cards.length) return null;
    const viewport = window.innerHeight || document.documentElement.clientHeight || 800;
    const anchorCard = cards.find(card => {
      const rect = card.getBoundingClientRect();
      return rect.bottom > 0 && rect.top < viewport;
    });
    const anchorRect = anchorCard?.getBoundingClientRect();
    return {anchor: anchorCard && anchorRect ? {id: anchorCard.dataset.materialId, top: anchorRect.top} : null, scrollTop: window.scrollY};
  }

  function layoutSnapshot(board) {
    const cards = new Map();
    board?.querySelectorAll('.candidate-card[data-material-id]').forEach(card => {
      const rect = card.getBoundingClientRect();
      cards.set(card.dataset.materialId, {left: rect.left, top: rect.top, width: rect.width, height: rect.height});
    });
    const viewport = viewportSnapshot(board) || {anchor: null, scrollTop: window.scrollY};
    return {...viewport, cards};
  }

  function restoreViewport(board, snapshot) {
    if (!snapshot) return;
    const anchor = snapshot.anchor && [...(board?.querySelectorAll('.candidate-card[data-material-id]') || [])].find(card => card.dataset.materialId === snapshot.anchor.id);
    if (anchor) {
      const delta = anchor.getBoundingClientRect().top - snapshot.anchor.top;
      if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
      return;
    }
    const max = Math.max(0, document.documentElement.scrollHeight - (window.innerHeight || document.documentElement.clientHeight || 0));
    window.scrollTo(0, Math.min(snapshot.scrollTop || 0, max));
  }

  function animateLayout(board, snapshot) {
    if (!snapshot) return;
    restoreViewport(board, snapshot);
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || typeof Element.prototype.animate !== 'function') return;
    const duration = 280;
    board.querySelectorAll('.candidate-card[data-material-id]').forEach(card => {
      const before = snapshot.cards.get(card.dataset.materialId);
      if (!before) return;
      const after = card.getBoundingClientRect();
      const dx = before.left - after.left, dy = before.top - after.top;
      const sx = after.width ? before.width / after.width : 1, sy = after.height ? before.height / after.height : 1;
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5 && Math.abs(sx - 1) < 0.002 && Math.abs(sy - 1) < 0.002) return;
      card.style.transformOrigin = 'top left';
      card.style.willChange = 'transform';
      const animation = card.animate([
        {transform: `translate3d(${dx}px, ${dy}px, 0) scale(${sx}, ${sy})`},
        {transform: 'translate3d(0, 0, 0) scale(1, 1)'}
      ], {duration, easing: 'cubic-bezier(.22,.75,.25,1)', fill: 'both'});
      animation.onfinish = () => { animation.cancel(); card.style.willChange = ''; };
    });
  }

  function render(entries, host) {
    const grid = node('div', 'candidate-library-grid');
    let revealOrder = 0;
    const paint = () => {
      const width = grid.clientWidth;
      if (!width || grid.dataset.width === String(width)) return;
      grid.dataset.width = String(width);
      const currentRows = rows(entries, width, getZoomed() ? 300 : 200);
      releaseVideos(grid);
      grid.replaceChildren(...currentRows.map(rowItems => {
        const row = node('div', 'candidate-library-row');
        rowItems.forEach(({entry, width: cardWidth, height}) => {
          const card = entry.kind === 'folder' ? folderCard(entry.folder) : candidateCard(entry.item);
          const revealIds = getRevealIds(), playedIds = getRevealPlayedIds();
          if (entry.kind === 'candidate' && revealIds.has(entry.item.id) && !playedIds.has(entry.item.id)) {
            playedIds.add(entry.item.id);
            card.classList.add('is-entering');
            card.style.setProperty('--candidate-enter-delay', `${Math.min(revealOrder++, 20) * 90}ms`);
          }
          card.style.width = `${cardWidth}px`; card.style.height = `${height}px`; row.append(card);
        });
        return row;
      }));
    };
    grid._candidatePaint = paint;
    host.append(grid); paint();
    if (typeof ResizeObserver !== 'undefined') { const observer = new ResizeObserver(paint); observer.observe(grid); grid._candidateResizeObserver = observer; }
    return grid;
  }

  return Object.freeze({ratio, rows, dispose, render, viewportSnapshot, layoutSnapshot, restoreViewport, animateLayout});
}
