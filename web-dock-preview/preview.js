'use strict';

/*
 * Full-screen preview mode.  It deliberately lives outside the asset and
 * candidate renderers: the underlying page is only blurred and locked while
 * the preview owns the active media sequence and its actions.
 */
(() => {
  const app = window.TeamAssetsApp;
  const overlay = document.querySelector('#previewOverlay');
  if (!overlay || !app) return;
  const filmstrip = document.querySelector('#previewFilmstrip');
  const stage = document.querySelector('#previewStage');
  const currentHost = document.querySelector('#previewCurrent');
  const caption = document.querySelector('#previewCaption');
  const toolbar = document.querySelector('#previewToolbar');
  const markMenu = document.querySelector('#previewMarkMenu');
  const neighborPrev = document.querySelector('#previewNeighborPrev');
  const neighborNext = document.querySelector('#previewNeighborNext');
  const api = (...args) => app.request(...args);
  const json = (...args) => app.json(...args);
  const toast = (...args) => app.toast(...args);
  const fail = (...args) => app.fail(...args);
  const refresh = (...args) => app.refresh(...args);
  const bridge = () => app.getCandidatePreview();
  const appState = () => app.getState() || {materials: [], variants: [], assets: []};
  const categoryIcons = {
    人物: '/icons/context-person.svg',
    场景: '/icons/context-scene.svg',
    道具: '/icons/context-prop.svg',
    其它: '/icons/context-other.svg',
  };
  let session = null;
  let savedScrollTop = 0;
  let previousFocus = null;
  let wheelReadyAt = 0;
  const PRELOAD_CACHE_LIMIT = 32;
  const preloadCache = new Map();

  function isVideo(item) {
    return String(item?.mime || item?.type || '').toLowerCase().startsWith('video/') || /\.(mp4|mov|m4v|webm|avi|mkv|wmv)$/i.test(item?.name || '');
  }
  function itemUrl(item, thumbnail = false) {
    if (!item) return '';
    return thumbnail ? (item.thumbnail_url || item.url || item.original_url || '') : (item.url || item.original_url || item.thumbnail_url || '');
  }
  function ratio(item) {
    const width = Number(item?.width), height = Number(item?.height);
    if (width > 0 && height > 0) return width / height;
    return isVideo(item) ? 16 / 9 : 1;
  }
  function uniqueItems(items) {
    const seen = new Set();
    return (Array.isArray(items) ? items : []).filter(item => {
      if (!item?.id || seen.has(item.id)) return false;
      seen.add(item.id); return true;
    });
  }
  function normalizedCategory(item) {
    const candidate = actionCandidate(item);
    const status = bridge()?.getStatus?.(candidate || item) || {};
    const value = status.category || candidate?.category || item?.category || '';
    return value === '其他' ? '其它' : value;
  }
  function actionCandidate(item) {
    const id = item?.candidate_id || item?.candidateId || item?.id;
    const candidate = bridge()?.getById?.(id);
    if (candidate && (candidate.file_id || candidate.relative_path || candidate.candidate_id)) return candidate;
    // Candidate-list items are themselves candidate records. Asset materials
    // created through a reference carry candidate_id and are resolved above.
    if (item?.relative_path || item?.folder_path || item?.project_id && !item?.variant_id) return item;
    return null;
  }
  function disabled(item) {
    const candidate = actionCandidate(item);
    const status = bridge()?.getStatus?.(candidate || item) || {};
    return Boolean(status.disabled || item?.disabled || item?.status === 'disabled');
  }
  function assetItems(assetId, current) {
    const currentState = appState();
    const materials = (Array.isArray(currentState.materials) ? currentState.materials : []).filter(item => {
      if (item.deleted_at || materialAssetId(item) !== assetId) return false;
      return !disabled(item) || item.id === current?.id;
    });
    return uniqueItems(materials);
  }
  function candidateItems(current) {
    const values = bridge()?.getItems?.() || [];
    // `getItems` already applies the active folder/category/search/status
    // filters. Do not append the current item back here: after a preview
    // action changes its category or disabled state, keeping it would make
    // the item remain in the old sequence instead of advancing to the next
    // matching candidate.
    return uniqueItems(values);
  }
  function setSource(item, options) {
    const source = options?.source || 'asset';
    if (source === 'candidate') return {source, items: candidateItems(item), targetVariantId: null};
    const assetId = item?.asset_id || materialAssetId(item);
    return {source, items: assetItems(assetId, item), assetId, targetVariantId: options?.targetVariantId || null};
  }
  function setIcon(button, icon) {
    const target = button?.querySelector?.('.preview-toolbar-icon') || button;
    if (target && icon) target.style.setProperty('--icon', `url(${icon})`);
  }
  function currentItem() { return session?.items?.[session.index] || null; }
  function actionState() {
    const item = currentItem();
    const candidate = actionCandidate(item);
    return {item, candidate, bridge: bridge()};
  }
  function updateMarker() {
    const {item} = actionState();
    const category = normalizedCategory(item);
    const icon = categoryIcons[category] || '/icons/context-mark.svg';
    document.querySelectorAll('[data-preview-marker]').forEach(el => el.style.setProperty('--icon', `url(${icon})`));
    document.querySelectorAll('[data-preview-marker-menu]').forEach(el => setIcon(el, icon));
    const button = toolbar?.querySelector('[data-preview-action="mark"]');
    if (button) { button.classList.toggle('is-active', Boolean(categoryIcons[category])); button.setAttribute('aria-label', categoryIcons[category] ? `标记：${category}` : '标记'); }
  }
  function mediaSource(item, mode) {
    return mode === 'thumbnail' ? itemUrl(item, true) : itemUrl(item);
  }
  function preloadItem(item) {
    if (!item || isVideo(item)) return;
    const url = mediaSource(item, 'current');
    if (!url) return;
    if (preloadCache.has(url)) {
      const value = preloadCache.get(url);
      preloadCache.delete(url);
      preloadCache.set(url, value);
      return;
    }
    const image = new Image();
    image.decoding = 'async';
    image.src = url;
    preloadCache.set(url, true);
    while (preloadCache.size > PRELOAD_CACHE_LIMIT) {
      preloadCache.delete(preloadCache.keys().next().value);
    }
  }
  function preloadAround() {
    if (!session) return;
    for (const offset of [-2, -1, 0, 1, 2]) preloadItem(session.items[session.index + offset]);
  }
  function configureMedia(media, item, {current = false, mode = 'neighbor'} = {}) {
    if (!media || !item) return media;
    const url = mediaSource(item, mode);
    media.dataset.previewItemId = item.id || '';
    media.dataset.previewSrc = url;
    media.setAttribute('aria-label', item.name || (isVideo(item) ? '视频预览' : '素材预览'));
    if (isVideo(item)) {
      if (media.getAttribute('src') !== url) media.src = url;
      media.muted = true;
      media.autoplay = Boolean(current);
      media.controls = Boolean(current);
      media.playsInline = true;
      media.preload = current ? 'auto' : 'metadata';
      if (current) media.addEventListener('canplay', () => { media.play().catch(() => {}); }, {once: true});
    } else {
      if (media.getAttribute('src') !== url) media.src = url;
      media.alt = item.name || '素材预览';
      media.loading = current ? 'eager' : 'lazy';
      media.decoding = 'async';
    }
    return media;
  }
  function renderMedia(container, item, {current = false, mode = current ? 'current' : 'neighbor'} = {}) {
    container.replaceChildren();
    if (!item) {
      delete container.dataset.previewItemId;
      delete container.dataset.previewSrc;
      return;
    }
    container.dataset.previewItemId = item.id || '';
    container.dataset.previewSrc = mediaSource(item, mode);
    if (isVideo(item)) {
      const video = document.createElement('video');
      configureMedia(video, item, {current, mode});
      video.addEventListener('loadedmetadata', () => { if (!current) { try { video.currentTime = 0; } catch {} } }, {once: true});
      container.append(video);
      return video;
    }
    const image = document.createElement('img');
    configureMedia(image, item, {current, mode});
    container.append(image);
    return image;
  }
  function renderFilmstrip() {
    if (!filmstrip || !session) return;
    filmstrip.replaceChildren();
    const total = session.items.length;
    const size = Math.min(10, total);
    const start = Math.max(0, Math.min(session.index - Math.floor(size / 2), total - size));
    session.thumbnailStart = start;
    session.items.slice(start, start + size).forEach((item, offset) => {
      const index = start + offset;
      const button = document.createElement('button');
      button.type = 'button';
      button.dataset.previewIndex = String(index);
      button.classList.toggle('is-active', index === session.index);
      button.setAttribute('aria-label', `${index + 1} / ${total}：${item.name || '素材'}`);
      const width = Math.max(60, Math.min(220, Math.round(100 * ratio(item))));
      button.style.width = `${width}px`;
      renderMedia(button, item, {mode: 'thumbnail'});
      button.addEventListener('click', () => selectIndex(index));
      filmstrip.append(button);
    });
  }
  function renderNeighbors() {
    const previous = session && session.index > 0 ? session.items[session.index - 1] : null;
    const next = session && session.index < session.items.length - 1 ? session.items[session.index + 1] : null;
    neighborPrev.hidden = !previous; neighborNext.hidden = !next;
    renderMedia(neighborPrev, previous, {mode: 'neighbor'}); renderMedia(neighborNext, next, {mode: 'neighbor'});
    sizeNeighbor(neighborPrev, previous);
    sizeNeighbor(neighborNext, next);
    preloadAround();
  }
  function sizeNeighbor(container, item) {
    if (!container) return;
    if (!item) {
      container.style.removeProperty('width');
      return;
    }
    // Figma's side media keeps its own aspect ratio. The side height remains
    // the existing 94% treatment, while width follows the source dimensions
    // instead of using the old symmetric 33% box.
    const stageHeight = stage?.getBoundingClientRect?.().height || 608;
    const mediaHeight = Math.max(1, stageHeight * 0.94);
    container.style.width = `${Math.max(1, Math.round(mediaHeight * ratio(item)))}px`;
  }
  function promoteNeighbor(item) {
    if (!item) return false;
    const source = [neighborPrev, neighborNext].find(container => container?.dataset.previewItemId === item.id);
    const media = source?.firstElementChild;
    if (!source || !media || source.dataset.previewSrc !== mediaSource(item, 'current')) return false;
    source.replaceChildren();
    delete source.dataset.previewItemId;
    delete source.dataset.previewSrc;
    currentHost.replaceChildren(media);
    currentHost.dataset.previewItemId = item.id || '';
    currentHost.dataset.previewSrc = mediaSource(item, 'current');
    configureMedia(media, item, {current: true, mode: 'current'});
    return true;
  }
  let switchTimer = null;
  function animateCurrent() {
    if (!session?.transitionDirection) return;
    const activeSession = session;
    clearTimeout(switchTimer);
    currentHost.classList.remove('is-initial');
    currentHost.dataset.direction = session.transitionDirection > 0 ? 'next' : 'previous';
    currentHost.classList.remove('is-switching');
    void currentHost.offsetWidth;
    currentHost.classList.add('is-switching');
    switchTimer = window.setTimeout(() => {
      if (session !== activeSession) return;
      currentHost.classList.remove('is-switching');
      currentHost.removeAttribute('data-direction');
    }, 320);
    session.transitionDirection = 0;
  }
  function renderCurrent() {
    const item = currentItem();
    if (!item) return;
    const opening = Boolean(session.isOpening);
    session.isOpening = false;
    const currentMedia = currentHost?.firstElementChild;
    const expectedUrl = mediaSource(item, 'current');
    const alreadyCurrent = currentHost?.dataset.previewItemId === item.id && currentHost?.dataset.previewSrc === expectedUrl && currentMedia;
    const promoted = !alreadyCurrent && promoteNeighbor(item);
    if (!alreadyCurrent && !promoted) renderMedia(currentHost, item, {current: true, mode: 'current'});
    if (opening) {
      const activeSession = session;
      currentHost.classList.remove('is-switching');
      currentHost.classList.add('is-initial');
      window.setTimeout(() => { if (session === activeSession) currentHost.classList.remove('is-initial'); }, 200);
    }
    animateCurrent();
    caption.textContent = item.name || '未命名素材';
    caption.title = item.name || '';
    updateMarker();
    const disableButton = toolbar?.querySelector('[data-preview-action="disable"]');
    if (disableButton) {
      const isDisabled = disabled(item);
      disableButton.dataset.tip = isDisabled ? '还原' : '禁用';
      disableButton.setAttribute('aria-label', isDisabled ? '还原' : '禁用');
      setIcon(disableButton, isDisabled ? '/icons/context-restore.svg' : '/icons/context-disable.svg');
    }
    const finalButton = toolbar?.querySelector('[data-preview-action="set-final"]');
    if (finalButton) {
      const state = appState();
      const targetVariant = session.targetVariantId
        ? state.variants?.find(v => v.id === session.targetVariantId)
        : null;
      if (targetVariant && session.source !== 'candidate') {
        finalButton.hidden = false;
        const isFinal = targetVariant.final_material_id === item.id;
        finalButton.disabled = isFinal;
        const label = isFinal ? '当前定稿' : `设为「${targetVariant.name || '变体'}」的定稿`;
        finalButton.dataset.tip = label;
        finalButton.setAttribute('aria-label', label);
        setIcon(finalButton, isFinal ? '/icons/asset-final-done.svg' : '/icons/asset-final.svg');
      } else {
        finalButton.hidden = true;
      }
    }
  }
  function render() {
    if (!session) return;
    renderFilmstrip(); renderCurrent(); renderNeighbors();
  }
  function selectIndex(index) {
    if (!session || index < 0 || index >= session.items.length || index === session.index) return;
    const previousIndex = session.index;
    overlay?.querySelectorAll('video').forEach(v => {
      try { v.pause(); v.removeAttribute('src'); v.load(); } catch {}
    });
    session.index = index;
    session.transitionDirection = index > previousIndex ? 1 : -1;
    render();
  }
  function step(delta) {
    if (!session) return;
    const next = Math.max(0, Math.min(session.items.length - 1, session.index + delta));
    if (next !== session.index) selectIndex(next);
  }
  function closeMarkMenu() {
    if (markMenu) markMenu.hidden = true;
    if (toolbar) toolbar.hidden = false;
  }
  function openMarkMenu() {
    if (!markMenu || !toolbar) return;
    toolbar.hidden = true; markMenu.hidden = false;
    markMenu.querySelector('[data-preview-category]')?.focus({preventScroll: true});
  }
  function lockPage() {
    savedScrollTop = window.scrollY || document.documentElement.scrollTop || 0;
    document.documentElement.classList.add('preview-locked');
    document.body.classList.add('preview-locked');
  }
  function unlockPage() {
    document.documentElement.classList.remove('preview-locked');
    document.body.classList.remove('preview-locked');
    window.scrollTo(0, savedScrollTop);
  }
  function closeUnifiedPreview({restoreFocus = true} = {}) {
    if (!session) return;
    clearTimeout(switchTimer);
    const wasDetail = Boolean(session.openedFromDetail);
    overlay?.querySelectorAll('video').forEach(v => {
      try { v.pause(); v.removeAttribute('src'); v.load(); } catch {}
    });
    preloadCache.clear();
    closeMarkMenu();
    currentHost.classList.remove('is-initial', 'is-switching');
    currentHost.removeAttribute('data-direction');
    session = null;
    overlay.hidden = true;
    unlockPage();
    if (wasDetail && restoreFocus) {
      const detailDialog = document.querySelector('#detail');
      if (detailDialog && !detailDialog.open) {
        detailDialog.showModal();
        if (typeof renderDetailMaterials === 'function') renderDetailMaterials();
      }
    } else if (restoreFocus && previousFocus && document.contains(previousFocus)) {
      try { previousFocus.focus({preventScroll: true}); } catch {}
    }
    previousFocus = null;
  }
  function openUnifiedPreview(item, options = {}) {
    // Historical asset buttons still call preview(materialId, variantId).
    // Normalize that signature so every entry point uses the same viewer.
    if (typeof item === 'string') {
      const currentState = appState();
      const targetVariantId = typeof options === 'string' ? options : options?.targetVariantId;
      item = (currentState.materials || []).find(value => value.id === item)
        || bridge()?.getById?.(item);
      options = typeof options === 'string' ? {targetVariantId} : (options || {});
    }
    if (!item) return;
    const source = setSource(item, options);
    const items = source.items;
    if (!items.length) return;
    const index = Math.max(0, items.findIndex(value => value.id === item.id));
    previousFocus = document.activeElement;
    const detailDialog = document.querySelector('#detail');
    const wasDetailOpen = Boolean(detailDialog && detailDialog.open);
    if (wasDetailOpen) {
      detailDialog.close();
    }
    session = {...source, items, index: index < 0 ? 0 : index, isOpening: true, transitionDirection: 0, openedFromDetail: wasDetailOpen};
    lockPage();
    overlay.hidden = false;
    closeMarkMenu();
    render();
    requestAnimationFrame(() => { if (session) overlay.focus({preventScroll: true}); });
  }
  async function patchCurrent(changes) {
    const {item, candidate, bridge: candidateBridge} = actionState();
    if (!item || !candidate || !candidateBridge?.patch) {
      toast('该素材没有可修改的候选记录。');
      return null;
    }
    const result = await candidateBridge.patch(candidate, changes);
    if (changes.name) { item.name = changes.name; candidate.name = changes.name; }
    if (changes.category) { item.category = changes.category; candidate.category = changes.category; }
    if (changes.status) { item.status = changes.status; item.disabled = changes.status === 'disabled'; candidate.status = changes.status; candidate.disabled = changes.status === 'disabled'; }
    candidateBridge.render?.();
    return result;
  }
  function reconcileAfterMutation(previousIndex) {
    if (!session) return;
    const previousId = session.items[previousIndex]?.id;
    const source = setSource(session.items[previousIndex] || currentItem(), session);
    let items = source.items;
    session.items = items;
    if (!items.length) { closeUnifiedPreview(); return; }
    const same = items.findIndex(item => item.id === previousId);
    session.index = same >= 0 ? same : Math.min(previousIndex, items.length - 1);
    render();
  }
  async function renameCurrent() {
    const item = currentItem(); if (!item) return;
    const name = window.prompt('素材名称', item.name || '');
    if (name === null || !name.trim() || name.trim() === item.name) return;
    await patchCurrent({name: name.trim()});
    render();
  }
  async function markCurrent(category) {
    const index = session?.index || 0;
    closeMarkMenu();
    await patchCurrent({category});
    if (session) reconcileAfterMutation(index);
  }
  async function toggleDisabled() {
    const index = session?.index || 0;
    const item = currentItem(); if (!item) return;
    closeMarkMenu();
    await patchCurrent({status: disabled(item) ? 'active' : 'disabled'});
    if (session) reconcileAfterMutation(index);
  }
  async function highlightCurrent() {
    const {item, candidate, bridge: candidateBridge} = actionState();
    if (!item || !candidate) { toast('该素材没有可高亮的候选记录。'); return; }
    if (candidateBridge?.highlight) candidateBridge.highlight(candidate);
    else await api(`/candidates/${encodeURIComponent(candidate.id)}/highlight`, json('POST', {duration_seconds: 60})).catch(fail);
    item.highlighted = true; item.highlightUntil = Date.now() + 60000; render();
  }
  function downloadCurrent() {
    const item = currentItem(); if (!item) return;
    const link = document.createElement('a'); link.href = itemUrl(item); link.download = item.name || '素材'; link.rel = 'noopener'; document.body.append(link); link.click(); link.remove();
  }
  async function setFinalCurrent() {
    const item = currentItem();
    const state = appState();
    const targetVariant = session?.targetVariantId
      ? state.variants?.find(v => v.id === session.targetVariantId)
      : null;
    if (!item || !targetVariant) return;
    try {
      const updated = await api(`/variants/${targetVariant.id}`, json('PATCH', {
        version: targetVariant.version,
        final_material_id: item.id
      }));
      targetVariant.final_material_id = item.id;
      if (updated?.version) targetVariant.version = updated.version;
      if (typeof detailId !== 'undefined' && detailId === targetVariant.id && typeof detailVersion !== 'undefined' && updated?.version) {
        detailVersion = updated.version;
      }
      toast(`已设为「${targetVariant.name || ''}」的定稿`);
      if (typeof refresh === 'function') refresh();
      render();
    } catch (err) {
      toast(err?.message || '设为定稿失败');
    }
  }
  function handleAction(event) {
    const action = event.currentTarget?.dataset.previewAction;
    if (action === 'rename') renameCurrent().catch(fail);
    if (action === 'mark') openMarkMenu();
    if (action === 'highlight') highlightCurrent().catch(fail);
    if (action === 'download') downloadCurrent();
    if (action === 'set-final') setFinalCurrent().catch(fail);
    if (action === 'disable') toggleDisabled().catch(fail);
    if (action === 'mark-back') closeMarkMenu();
  }
  function bind() {
    toolbar?.querySelectorAll('[data-preview-action]').forEach(button => button.addEventListener('click', handleAction));
    markMenu?.querySelectorAll('[data-preview-category]').forEach(button => button.addEventListener('click', () => markCurrent(button.dataset.previewCategory)));
    markMenu?.querySelector('[data-preview-action="mark-back"]')?.addEventListener('click', closeMarkMenu);
    neighborPrev?.addEventListener('click', () => step(-1));
    neighborNext?.addEventListener('click', () => step(1));
    overlay.addEventListener('click', event => { if (event.target === overlay || event.target.matches('[data-preview-dismiss]')) closeUnifiedPreview(); });
    overlay.addEventListener('wheel', event => {
      if (!session || Math.abs(event.deltaY) < 4) return;
      event.preventDefault();
      const now = Date.now(); if (now < wheelReadyAt) return;
      wheelReadyAt = now + 300; step(event.deltaY > 0 ? 1 : -1);
    }, {passive: false});
    document.addEventListener('keydown', event => {
      if (!session) return;
      if (event.key === 'Escape') { event.preventDefault(); closeUnifiedPreview(); return; }
      if (event.key === 'ArrowLeft') { event.preventDefault(); step(-1); }
      if (event.key === 'ArrowRight') { event.preventDefault(); step(1); }
    });
    // Header remains above the preview layer. Any navigation action closes the
    // current session automatically, then the existing header handler routes.
    document.addEventListener('click', event => {
      if (!session) return;
      if (event.target.closest('#candidateLibrary,#assetsLibrary,#storyLibrary,#mediaLibrary,#projectBtn,#historyBtn,#trashBtn')) closeUnifiedPreview({restoreFocus: false});
    }, true);
    document.addEventListener('change', event => {
      if (session && event.target.closest('#projectSelect')) closeUnifiedPreview({restoreFocus: false});
    }, true);
    window.addEventListener('resize', () => {
      if (!session) return;
      sizeNeighbor(neighborPrev, session.index > 0 ? session.items[session.index - 1] : null);
      sizeNeighbor(neighborNext, session.index < session.items.length - 1 ? session.items[session.index + 1] : null);
    }, {passive: true});
  }
  window.openUnifiedPreview = openUnifiedPreview;
  window.closeUnifiedPreview = closeUnifiedPreview;
  // App.js and the candidate renderer both call the historical global preview
  // function. Replacing it here keeps all old entry points on the new viewer.
  window.preview = openUnifiedPreview;
  app.registerPreviewController({open: openUnifiedPreview, close: closeUnifiedPreview});
  bind();
})();
