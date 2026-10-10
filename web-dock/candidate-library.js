'use strict';

/*
 * Project-wide candidate library.
 *
 * The current web API exposes materials through the asset pool.  This view
 * consumes that shape today and also understands a future `state.candidates`
 * collection.  Mutations are sent to the candidate endpoints when present,
 * while the UI remains usable against older 8092/8094 servers.
 */
(() => {
  const app = window.TeamAssetsApp;
  const originalRender = render;
  const originalRenderDock = renderDock;
  const originalPreview = preview;
  let libraryMode = 'assets';
  let candidateDockMode = 'default';
  let candidateCategory = '全部';
  let candidateQuery = '';
  let candidateDisabledOnly = false;
  let candidateZoomed = false;
  let candidateLayoutTransitionPending = false;
  let candidateDraft = '';
  let candidateFileInputMode = 'files';
  let candidatePoolProject = null;
  let candidateFolderProject = null;
  let candidateFolderPath = '';
  let candidateViewProject = null;
  let candidateKnownProject = null;
  let candidateKnownReady = false;
  const candidateKnownIds = new Set();
  let candidateIngesting = 0;
  const candidatePool = new Map();
  const candidateFolders = new Map();
  const candidatePoolStatuses = new Set();
  const candidatePoolRevisions = new Map();
  const candidateLocal = new Map();
  const candidateTimers = new Map();
  const candidateRevealIds = new Set();
  const candidateRevealPlayedIds = new Set();
  let candidateRevealTimer = null;
  let candidateDisabledLoadToken = 0;
  // A filter change reveals cards that were already present in another view.
  // They must not be mistaken for newly uploaded candidates. Explicit IDs
  // added by candidateUpload remain eligible for the entrance animation.
  let candidateSuppressArrivalDetection = false;
  const candidateStorageKey = () => `team-assets-candidates:${projectId || 'none'}`;

  function isCandidateMode() { return libraryMode === 'candidates'; }
  function candidateViewStorageKey() { return `${candidateStorageKey()}:view`; }
  function loadCandidateView() {
    if (candidateViewProject === projectId) return;
    candidateViewProject = projectId;
    candidateCategory = '全部'; candidateQuery = ''; candidateDraft = ''; candidateDisabledOnly = false; candidateZoomed = false; candidateFolderPath = '';
    try {
      const saved = JSON.parse(localStorage.getItem(candidateViewStorageKey()) || '{}');
      if (['全部', '人物', '场景', '道具', '其它'].includes(saved.category)) candidateCategory = saved.category;
      candidateQuery = String(saved.query || '').trim().toLowerCase(); candidateDraft = candidateQuery;
      candidateDisabledOnly = Boolean(saved.disabledOnly); candidateZoomed = Boolean(saved.zoomed); candidateFolderPath = String(saved.folderPath || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    } catch { /* view preferences are disposable */ }
  }
  function saveCandidateView() {
    try { localStorage.setItem(candidateViewStorageKey(), JSON.stringify({category: candidateCategory, query: candidateQuery, disabledOnly: candidateDisabledOnly, zoomed: candidateZoomed, folderPath: candidateFolderPath})); } catch { /* storage can be unavailable */ }
  }
  function beginCandidateIngesting() { candidateIngesting += 1; document.body.classList.add('candidate-ingesting'); }
  function endCandidateIngesting() { candidateIngesting = Math.max(0, candidateIngesting - 1); if (!candidateIngesting) document.body.classList.remove('candidate-ingesting'); }
  function normalCategory(value) {
    if (value === '其他') return '其它';
    return ['人物', '场景', '道具', '其它'].includes(value) ? value : '其它';
  }
  function isVideo(item) {
    return String(item?.mime || item?.type || '').toLowerCase().startsWith('video/') || /\.(mp4|mov|m4v|webm|avi|mkv|wmv)$/i.test(item?.name || '');
  }
  function isSupportedMedia(file) {
    if (!file) return false;
    const type = String(file.type || '').toLowerCase();
    return type.startsWith('image/') || type.startsWith('video/') || /\.(png|jpe?g|webp|gif|avif|bmp|mp4|mov|m4v|webm|avi|mkv|wmv)$/i.test(file.name || '');
  }
  function folderPath(value) {
    const raw = String(value || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    const parts = raw.split('/').filter(part => part && part !== '.' && part !== '..');
    return parts.length > 1 ? parts.slice(0, -1).join('/') : '';
  }
  function loadCandidateLocal() {
    candidateLocal.clear();
    try {
      const saved = JSON.parse(localStorage.getItem(candidateStorageKey()) || '{}');
      Object.entries(saved).forEach(([id, value]) => {
        const highlightUntil = Number(value?.highlightUntil) || 0;
        if (highlightUntil > Date.now()) candidateLocal.set(id, {highlightUntil});
      });
      // Names, categories, and disabled status are server-owned. Older builds
      // cached them here and could shadow newer values returned by the API.
      saveCandidateLocal();
    } catch { /* local preferences are disposable */ }
  }
  function saveCandidateLocal() {
    const highlights = Object.fromEntries([...candidateLocal].flatMap(([id, value]) => {
      const highlightUntil = Number(value?.highlightUntil) || 0;
      return highlightUntil > Date.now() ? [[id, {highlightUntil}]] : [];
    }));
    try { localStorage.setItem(candidateStorageKey(), JSON.stringify(highlights)); } catch { /* storage can be unavailable in private mode */ }
  }
  function candidateSource() {
    // Active candidates are part of /state, which is refreshed through the
    // shared event stream. Keep that path live so another user's highlight is
    // visible immediately. Disabled candidates still come from their explicit
    // status request because /state intentionally contains active entries only.
    const source = candidateDisabledOnly
      ? (candidatePoolStatuses.has('disabled') ? [...candidatePool.values()] : (Array.isArray(state.candidates) ? state.candidates : state.materials))
      : (Array.isArray(state.candidates) ? state.candidates : (candidatePoolStatuses.has('active') ? [...candidatePool.values()] : state.materials));
    const projectAssets = new Set(state.assets.filter(a => a.project_id === projectId).map(a => a.id));
    return source.filter(m => !m.deleted_at && (m.project_id === projectId || projectAssets.has(m.asset_id) || (!m.project_id && !m.asset_id))).map(m => {
      const asset = state.assets.find(a => a.id === m.asset_id);
      const local = candidateLocal.get(m.id) || {};
      const remoteHighlight = Date.parse(m.highlighted_until || m.highlight_until || m.highlightedUntil || '') || 0;
      const disabled = local.status === 'disabled' ? true : local.status === 'active' ? false : Boolean(m.disabled || m.is_disabled || m.status === 'disabled' || local.disabled);
      const candidatePath = m.relative_path || m.path || '';
      return {...m, name: local.name ?? m.name, category: normalCategory(local.category ?? m.category ?? asset?.category), disabled, highlightUntil: Math.max(local.highlightUntil || 0, remoteHighlight), candidatePath, folderPath: m.folder_path || folderPath(candidatePath)};
    });
  }
  function scheduleCandidateHighlightExpiry(item) {
    const id = item?.id, until = Number(item?.highlightUntil) || 0;
    if (!id) return;
    clearTimeout(candidateTimers.get(id));
    candidateTimers.delete(id);
    if (until <= Date.now()) return;
    candidateTimers.set(id, setTimeout(() => {
      candidateTimers.delete(id);
      const local = candidateLocal.get(id);
      if (local?.highlightUntil && local.highlightUntil <= Date.now()) {
        local.highlightUntil = 0;
        candidateLocal.set(id, local);
        saveCandidateLocal();
      }
      if (isCandidateMode()) render();
    }, until - Date.now() + 80));
  }
  function visibleCandidates(source = candidateSource()) {
    const query = candidateQuery.trim().toLowerCase();
    return source.filter(item => {
      if (candidateCategory !== '全部' && item.category !== candidateCategory) return false;
      if (candidateDisabledOnly ? !item.disabled : item.disabled) return false;
      return !query || String(item.name || '').toLowerCase().includes(query);
    });
  }
  function noteCandidateArrivals(source) {
    if (candidateKnownProject !== projectId) {
      candidateKnownProject = projectId;
      candidateKnownIds.clear();
      candidateKnownReady = false;
      candidateRevealIds.clear();
      candidateRevealPlayedIds.clear();
    }
    if (!candidateKnownReady) {
      source.forEach(item => candidateKnownIds.add(item.id));
      candidateKnownReady = true;
      return;
    }
    let added = false;
    source.forEach(item => {
      if (!candidateKnownIds.has(item.id) && !candidateSuppressArrivalDetection) { candidateRevealIds.add(item.id); added = true; }
      candidateKnownIds.add(item.id);
    });
    if (added) scheduleCandidateRevealCleanup();
  }
  function transitionCandidateFilter(update) {
    const previous = candidateSuppressArrivalDetection;
    candidateSuppressArrivalDetection = true;
    try {
      if (typeof transitionFilteredRender === 'function') transitionFilteredRender(update);
      else { update(); render(); }
    } finally { candidateSuppressArrivalDetection = previous; }
  }
  function scheduleCandidateRevealCleanup() {
    clearTimeout(candidateRevealTimer);
    candidateRevealTimer = setTimeout(() => {
      candidateRevealIds.clear();
      candidateRevealPlayedIds.clear();
      document.querySelectorAll('.candidate-card.is-entering').forEach(card => card.classList.remove('is-entering'));
    }, 2800);
  }
  function candidateEntries(source) {
    ensureDerivedFolders(source);
    const files = visibleCandidates(source);
    const query = candidateQuery.trim();
    if (query) return files.map(item => ({kind: 'candidate', item}));
    const folder = candidateFolderPath;
    if (folder && ![...candidateFolders.values()].some(value => value.path === folder)) {
      candidateFolderPath = '';
    }
    const current = candidateFolderPath;
    const directFiles = [];
    const activePaths = new Set();
    for (const item of files) {
      const path = item.folderPath || folderPath(item.candidatePath);
      if (path === current) directFiles.push(item);
      if (path) {
        let p = path;
        while (p) {
          activePaths.add(p);
          const slash = p.lastIndexOf('/');
          p = slash === -1 ? '' : p.slice(0, slash);
        }
      }
    }
    const visibleFolders = [...candidateFolders.values()].filter(value => {
      if (value.parent_path !== current) return false;
      return activePaths.has(value.path);
    });
    const entries = [
      ...visibleFolders.map(folder => ({kind: 'folder', folder, sortOrder: Number(folder.sort_order) || 0})),
      ...directFiles.map(item => ({kind: 'candidate', item, sortOrder: Number(item.sort_order) || 0})),
    ];
    return entries.sort((a, b) => a.sortOrder - b.sortOrder);
  }
  function candidateFolderById(id) {
    return [...candidateFolders.values()].find(folder => String(folder.id) === String(id)) || null;
  }
  async function loadAllCandidateRecords() {
    if (!projectId) return [];
    const result = [], pageSize = 200;
    for (let offset = 0;;) {
      const body = await api(`/candidates?project_id=${encodeURIComponent(projectId)}&status=all&limit=${pageSize}&offset=${offset}`);
      const list = Array.isArray(body) ? body : body?.candidates || [];
      result.push(...list);
      if (!list.length || list.length < pageSize || result.length >= Number(body?.total || result.length)) break;
      offset += list.length;
    }
    return result;
  }
  async function reloadCandidateData() {
    candidatePoolProject = null;
    candidatePoolStatuses.clear(); candidatePoolRevisions.clear(); candidatePool.clear();
    candidateFolderProject = null; candidateFolders.clear();
    await Promise.all([loadCandidatePool('active'), loadCandidateFolders(true), candidateDisabledOnly ? loadCandidatePool('disabled') : Promise.resolve()]);
    await refresh();
    if (isCandidateMode()) render();
  }
  async function reorderCandidatePair(source, target) {
    const parentPath = source.kind === 'candidate' ? (source.item.folderPath || '') : (source.folder.parent_path || '');
    await loadCandidateFolders(true);
    const all = await loadAllCandidateRecords();
    const entries = [
      ...[...candidateFolders.values()].filter(folder => (folder.parent_path || '') === parentPath).map(folder => ({type: 'folder', id: folder.id, sort: Number(folder.sort_order) || 0})),
      ...all.filter(item => (item.folder_path || folderPath(item.relative_path)) === parentPath).map(item => ({type: 'candidate', id: item.id, sort: Number(item.sort_order) || 0})),
    ].sort((a, b) => a.sort - b.sort).map(({type, id}) => ({type, id}));
    const sourceEntry = {type: source.kind, id: source.kind === 'candidate' ? source.item.id : source.folder.id};
    const targetEntry = {type: target.kind, id: target.id};
    const sourceIndex = entries.findIndex(entry => entry.type === sourceEntry.type && entry.id === sourceEntry.id);
    const targetIndex = entries.findIndex(entry => entry.type === targetEntry.type && entry.id === targetEntry.id);
    if (sourceIndex < 0 || targetIndex < 0) throw new Error('目录内容已变化，请刷新后重试');
    [entries[sourceIndex], entries[targetIndex]] = [entries[targetIndex], entries[sourceIndex]];
    await api('/candidate-entries/reorder', json('POST', {project_id: projectId, parent_path: parentPath, entries}));
    await reloadCandidateData();
  }
  async function moveCandidateIntoFolder(source, target) {
    await api(`/candidates/${encodeURIComponent(source.item.id)}/move`, json('POST', {version: Number(source.item.version) || 1, target_folder_path: target.folder.path}));
    candidateFolderPath = target.folder.path;
    saveCandidateView();
    await reloadCandidateData();
  }
  async function applyCandidatePointerDrop(source, target) {
    if (source.kind === 'candidate' && target.kind === 'folder') await moveCandidateIntoFolder(source, target);
    else await reorderCandidatePair(source, target);
  }
  const candidatePointer = createCandidatePointer({
    document,
    isCandidateMode,
    findFolderById: candidateFolderById,
    onDrop: applyCandidatePointerDrop,
    onError: error => toast(error.message || '候选库排序失败'),
  });
  function bindCandidateLongPress(card, payload) { candidatePointer.bind(card, payload); }
  const candidateGridView = createCandidateGridView({
    document,
    node,
    button,
    isVideo,
    folderPath,
    getZoomed: () => candidateZoomed,
    setFolderPath: value => { candidateFolderPath = value; },
    saveView: () => saveCandidateView(),
    renderLibrary: () => render(),
    openPreview: item => openCandidatePreview(item),
    openCandidateContext: (...args) => openCandidateContext(...args),
    openFolderContext: (...args) => openCandidateFolderContext(...args),
    bindLongPress: (...args) => bindCandidateLongPress(...args),
    scheduleHighlightExpiry: item => scheduleCandidateHighlightExpiry(item),
    getRevealIds: () => candidateRevealIds,
    getRevealPlayedIds: () => candidateRevealPlayedIds,
  });
  function renderCandidateBreadcrumb(section) {
    const trail = node('nav', 'candidate-breadcrumb');
    trail.setAttribute('aria-label', '候选库目录');
    const parts = candidateFolderPath ? candidateFolderPath.split('/').filter(Boolean) : [];
    const root = button('候选库', () => { candidateFolderPath = ''; saveCandidateView(); render(); }, 'candidate-breadcrumb-item');
    trail.append(root);
    let path = '';
    parts.forEach(part => {
      path = path ? `${path}/${part}` : part;
      trail.append(node('span', 'candidate-breadcrumb-separator', '/'));
      const target = path;
      trail.append(button(part, () => { candidateFolderPath = target; saveCandidateView(); render(); }, 'candidate-breadcrumb-item'));
    });
    section.prepend(trail);
  }
  function renderCandidateLibrary() {
    document.body.classList.add('candidate-mode');
    loadCandidateLocal();
    const board = $('#board'); if (!board) return;
    loadCandidateView();
    const layoutTransition = candidateLayoutTransitionPending;
    const previousLayout = layoutTransition ? candidateGridView.layoutSnapshot(board) : null;
    const previousViewport = layoutTransition ? null : candidateGridView.viewportSnapshot(board);
    candidateLayoutTransitionPending = false;
    const currentRevision = Number(state.revision) || 0;
    if (candidateDisabledOnly && candidatePoolStatuses.has('disabled') && candidatePoolRevisions.get('disabled') !== currentRevision) {
      candidatePoolStatuses.delete('disabled');
      candidatePoolRevisions.delete('disabled');
      candidatePool.clear();
      loadCandidatePool('disabled').then(() => { if (isCandidateMode() && candidateDisabledOnly) renderCandidateLibrary(); });
    }
    const source = candidateSource();
    ensureDerivedFolders(source);
    noteCandidateArrivals(source);
    saveCandidateView();
    const files = visibleCandidates(source);
    const entries = candidateEntries(source);
    $('#counts').textContent = `${files.length} 个候选素材`;
    candidateGridView.dispose(board);
    board.replaceChildren();
    const section = node('section', 'candidate-library');
    const caption = node('div', 'candidate-library-caption');
    caption.append(node('span', '', `${files.length} 个候选素材`));
    section.append(caption);
    renderCandidateBreadcrumb(section);
    const scroll = node('div', 'candidate-grid-scroll'); const grid = candidateGridView.render(entries, scroll); section.append(scroll); board.append(section);
    grid?._candidatePaint?.();
    if (layoutTransition) candidateGridView.animateLayout(board, previousLayout); else candidateGridView.restoreViewport(board, previousViewport);
    syncCandidateHeader();
    renderCandidateDock();
  }
  function candidateDockButton(icon, label, fn, active, key) {
    const b = button('', fn, `dock-button${active ? ' is-active' : ''}`); b.dataset.tip = label; b.dataset.candidateDockKey = key || label; b.setAttribute('aria-label', label); b.setAttribute('aria-pressed', String(Boolean(active)));
    const mark = node('span', `dock-icon${/-f$/.test(icon) ? ' figma-dock-icon' : ''}`); mark.style.setProperty('--icon', `url('/icons/${icon}.svg')`); mark.setAttribute('aria-hidden', 'true'); b.append(mark); return b;
  }
  function renderCandidateDock() {
    const host = $('#dock'); if (!host) return;
    host.dataset.mode = `candidate-${candidateDockMode}`; host.replaceChildren();
    if (candidateDockMode === 'search') {
      const form = node('form', 'dock-form'); form.setAttribute('aria-label', '按文件名搜索候选素材');
      const lead = node('span', 'dock-lead'), icon = node('span', 'dock-icon figma-dock-icon'); icon.style.setProperty('--icon', "url('/icons/search-f.svg')"); lead.append(icon); form.append(lead);
      const input = node('input'); input.value = candidateDraft; input.placeholder = '搜索文件名…'; input.setAttribute('aria-label', '搜索文件名'); input.autocomplete = 'off'; form.append(input);
      const actions = node('div', 'dock-actions');
      const reset = candidateDockButton('reset', '重置', () => {
        const changed = Boolean(candidateQuery);
        candidateDraft = ''; candidateDockMode = 'default';
        if (changed) transitionCandidateFilter(() => { candidateQuery = ''; }); else renderCandidateDock();
      }, false, 'candidate-reset');
      const back = candidateDockButton('back', '返回', () => {
        const raw = input.value, next = raw.trim().toLowerCase(), changed = next !== candidateQuery;
        candidateDraft = raw; candidateDockMode = 'default';
        if (changed) transitionCandidateFilter(() => { candidateQuery = next; }); else renderCandidateDock();
      }, false, 'candidate-back');
      actions.append(reset, back); form.append(actions); form.addEventListener('submit', e => { e.preventDefault(); back.click(); }); host.append(form); input.focus(); return;
    }
    host.append(candidateDockButton('search-f', '搜索', () => { candidateDraft = candidateQuery; candidateDockMode = 'search'; renderCandidateDock(); }, Boolean(candidateQuery), 'candidate-search'));
    for (const [value, label, icon] of [['全部', '全部', 'all-f'], ['人物', '人物', 'person-f'], ['场景', '场景', 'scene-f'], ['道具', '道具', 'prop-f'], ['其它', '其它', 'other-f']]) host.append(candidateDockButton(icon, label, () => {
      if (candidateCategory === value) return;
      transitionCandidateFilter(() => { candidateCategory = value; });
    }, candidateCategory === value, `candidate-category-${value}`));
    host.append(candidateDockButton('unfinalized-f', '仅显示禁用', () => {
      const next = !candidateDisabledOnly, token = ++candidateDisabledLoadToken;
      if (next && !candidatePoolStatuses.has('disabled')) {
        // Wait for the first disabled page before switching the board. This
        // avoids a transition to an empty fallback followed by a late refill.
        loadCandidatePool('disabled').then(() => {
          if (token !== candidateDisabledLoadToken || !isCandidateMode() || candidateDisabledOnly !== false) return;
          transitionCandidateFilter(() => { candidateDisabledOnly = true; });
        });
        return;
      }
      transitionCandidateFilter(() => { candidateDisabledOnly = next; });
    }, candidateDisabledOnly, 'candidate-disabled'));
    host.append(candidateDockButton('zoom-f', '放大', () => { candidateLayoutTransitionPending = true; candidateZoomed = !candidateZoomed; render(); }, candidateZoomed, 'candidate-zoom'));
  }
  function syncCandidateHeader() {
    document.body.classList.toggle('candidate-mode', isCandidateMode());
    for (const [id, active] of [['assetsLibrary', !isCandidateMode()], ['candidateLibrary', isCandidateMode()]]) { const el = $(`#${id}`); if (!el) continue; el.classList.toggle('is-active', active); el.setAttribute('aria-current', active ? 'page' : 'false'); }
  }
  async function loadCandidatePool(status = 'active') {
    if (!projectId || (candidatePoolProject === projectId && candidatePoolStatuses.has(status))) return;
    let loaded = false;
    try {
      const pageSize = 200;
      let offset = 0;
      let total = Infinity;
      while (offset < total) {
        const result = await api(`/candidates?project_id=${encodeURIComponent(projectId)}&status=${status}&limit=${pageSize}&offset=${offset}`);
        const list = Array.isArray(result) ? result : result?.candidates || result?.materials || result?.items;
        if (!Array.isArray(list)) break;
        list.forEach(item => candidatePool.set(item.id, item));
        loaded = true;
        total = Number.isFinite(Number(result?.total)) ? Number(result.total) : list.length;
        offset += list.length;
        if (list.length < pageSize) break;
      }
    } catch { /* 8092 before candidate API rollout: use state.materials */ }
    candidatePoolProject = projectId;
    if (loaded) {
      candidatePoolStatuses.add(status);
      candidatePoolRevisions.set(status, Number(state.revision) || 0);
    }
  }
  async function loadCandidateFolders(force = false) {
    if (!projectId || (!force && candidateFolderProject === projectId)) return;
    candidateFolders.clear();
    try {
      const result = await api(`/candidate-folders?project_id=${encodeURIComponent(projectId)}&status=all`);
      const list = Array.isArray(result) ? result : result?.folders || [];
      list.forEach(folder => { if (folder?.id && folder.path !== undefined) candidateFolders.set(folder.id, folder); });
    } catch {
      // Older servers do not expose virtual folders. The renderer can still
      // derive active folder nodes from each candidate's relative_path.
    }
    candidateFolderProject = projectId;
  }
  function ensureDerivedFolders(source) {
    if (!Array.isArray(source)) return;
    const knownPaths = new Set([...candidateFolders.values()].map(f => f.path));
    source.forEach(item => {
      let parent = '';
      const folder = item.folderPath || folderPath(item.relative_path || item.path || '');
      const parts = String(folder).split('/').filter(Boolean);
      parts.forEach(segment => {
        const path = parent ? `${parent}/${segment}` : segment;
        if (!knownPaths.has(path)) {
          knownPaths.add(path);
          candidateFolders.set(`derived:${path}`, {id: `derived:${path}`, project_id: projectId, path, parent_path: parent, name: segment, status: 'active', disabled: false, sort_order: candidateFolders.size + 1});
        }
        parent = path;
      });
    });
    if (candidateFolderPath && !knownPaths.has(candidateFolderPath)) {
      candidateFolderPath = '';
    }
  }
  function switchLibrary(mode) {
    if (libraryMode === mode) return;
    libraryMode = mode; candidateDockMode = 'default';
    if (mode === 'candidates') { loadCandidateLocal(); Promise.all([loadCandidatePool('active'), loadCandidateFolders()]).finally(() => render()); return; }
    render();
  }
  function patchCandidate(item, changes) {
    const id = item.id;
    const prevLocal = candidateLocal.has(id) ? {...candidateLocal.get(id)} : null;
    const prevItemVersion = item.version;
    const prevItemProps = {name: item.name, category: item.category, status: item.status, disabled: item.disabled};

    const normalized = {...changes};
    if (changes.status === 'disabled') normalized.disabled = true;
    if (changes.status === 'active') normalized.disabled = false;
    const local = {...(candidateLocal.get(id) || {}), ...normalized};
    candidateLocal.set(id, local);

    return api(`/candidates/${encodeURIComponent(id)}`, json('PATCH', {version: Number(item.version) || 1, ...changes}))
      .then(updated => {
        if (updated) {
          Object.assign(item, updated);
          const curLocal = candidateLocal.get(id) || {};
          Object.keys(normalized).forEach(k => delete curLocal[k]);
          if (changes.status) delete curLocal.disabled;
          if (Object.keys(curLocal).length === 0) {
            candidateLocal.delete(id);
          } else {
            candidateLocal.set(id, curLocal);
          }
          saveCandidateLocal();
        }
        return updated;
      })
      .catch(error => {
        if (prevLocal) {
          candidateLocal.set(id, prevLocal);
        } else {
          candidateLocal.delete(id);
        }
        Object.assign(item, prevItemProps);
        item.version = prevItemVersion;
        saveCandidateLocal();
        if (isCandidateMode()) render();
        const msg = error?.message || '候选素材更新失败';
        toast(error?.status === 409 ? `修改冲突：${msg}` : msg);
        throw error;
      });
  }
  function openCandidatePreview(item) {
    if (window.openUnifiedPreview) { window.openUnifiedPreview(item, {source: 'candidate'}); return; }
    const dialog = $('#preview'), image = $('#previewImage'); if (!dialog || !item) return;
    dialog.querySelector('.candidate-preview-video')?.remove();
    $('#previewTitle').textContent = item.name || '候选素材';
    $('#previewSource').textContent = item.candidatePath ? `来源：${item.candidatePath}` : '候选库';
    $('#download').href = item.url || item.original_url || '';
    $('#download').download = item.name || 'candidate';
    $('#setFinal').hidden = true; $('#deleteMaterial').hidden = true;
    if (isVideo(item)) {
      image.hidden = true;
      const video = document.createElement('video'); video.className = 'candidate-preview-video'; video.src = item.url || ''; video.controls = true; video.autoplay = true; video.muted = true; video.playsInline = true; dialog.querySelector('.preview-actions').before(video);
    } else { image.hidden = false; image.src = item.url || item.original_url || item.thumbnail_url || ''; image.alt = item.name || '候选素材'; }
    previewId = item.id; previewTargetId = null; if (!dialog.open) dialog.showModal();
  }
  function restoreStandardPreview() {
    const dialog = $('#preview'), image = $('#previewImage');
    dialog?.querySelector('.candidate-preview-video')?.remove();
    if (image) image.hidden = false;
    $('#setFinal')?.removeAttribute('hidden');
    $('#deleteMaterial')?.removeAttribute('hidden');
  }
  const candidateContextMenu = createCandidateContextMenu({
    document,
    getCandidateById: id => candidateSource().find(item => item.id === id) || null,
    findFolderById: candidateFolderById,
    onAction: (action, context) => handleCandidateContextAction(action, context),
    onMark: (item, category) => candidateMark(item, category),
  });
  function openCandidateContext(...args) { return candidateContextMenu.openCandidate(...args); }
  function openCandidateFolderContext(...args) { return candidateContextMenu.openFolder(...args); }
  function closeCandidateContext() { candidateContextMenu.close(); }
  function contextItem() { return candidateContextMenu.currentCandidate(); }
  function contextFolder() { return candidateContextMenu.currentFolder(); }
  async function candidateRename(item) {
    const name = window.prompt('候选素材名称', item.name || ''); if (name === null || !name.trim() || name.trim() === item.name) return;
    const next = name.trim(); const request = patchCandidate(item, {name: next}); item.name = next; render(); await request;
  }
  async function candidateMark(item, category) { const request = patchCandidate(item, {category}); render(); await request; }
  function candidateHighlight(item) {
    const until = Date.now() + 60000; candidateLocal.set(item.id, {...(candidateLocal.get(item.id) || {}), highlightUntil: until}); saveCandidateLocal();
    api(`/candidates/${encodeURIComponent(item.id)}/highlight`, json('POST', {duration_seconds: 60})).catch(() => null);
    scheduleCandidateHighlightExpiry({...item, highlightUntil: until}); render();
  }
  async function candidateDisable(item) { const request = patchCandidate(item, {status: 'disabled'}); render(); await request; }
  async function candidateRestore(item) { const request = patchCandidate(item, {status: 'active'}); render(); await request; }
  async function candidateRenameFolder(folder) {
    const name = window.prompt('文件夹名称', folder.name || '');
    if (name === null || !name.trim() || name.trim() === folder.name) return;
    const updated = await api(`/candidate-folders/${encodeURIComponent(folder.id)}`, json('PATCH', {version: Number(folder.version) || 1, name: name.trim()}));
    if (updated?.id) candidateFolders.set(updated.id, updated);
    if (candidateFolderPath === folder.path) candidateFolderPath = updated?.path || candidateFolderPath;
    else if (candidateFolderPath.startsWith(folder.path + '/')) candidateFolderPath = `${updated?.path || folder.path}${candidateFolderPath.slice(folder.path.length)}`;
    saveCandidateView();
    await reloadCandidateData();
  }
  async function candidateDisableFolder(folder) {
    await api(`/candidate-folders/${encodeURIComponent(folder.id)}`, json('PATCH', {version: Number(folder.version) || 1, status: 'disabled'}));
    await reloadCandidateData();
  }
  async function candidateRestoreFolder(folder) {
    if (folder.disabled_by_parent) { toast('父文件夹仍处于禁用状态'); return; }
    await api(`/candidate-folders/${encodeURIComponent(folder.id)}`, json('PATCH', {version: Number(folder.version) || 1, status: 'active'}));
    await reloadCandidateData();
  }
  async function handleCandidateContextAction(action, context) {
    const contextKind = context.kind, item = context.item, folder = context.folder;
    if (contextKind === 'folder') {
      if (!folder) return;
      try {
        if (action === 'rename') await candidateRenameFolder(folder);
        if (action === 'disable') await candidateDisableFolder(folder);
        if (action === 'restore') await candidateRestoreFolder(folder);
      } catch (error) { toast(error.message || '文件夹操作失败'); }
      return;
    }
    if (!item) return;
    if (action === 'rename') await candidateRename(item);
    if (action === 'highlight') candidateHighlight(item);
    if (action === 'disable') await candidateDisable(item);
    if (action === 'restore') await candidateRestore(item);
  }
  function bindCandidateContext() { candidateContextMenu.bind(); }
  const candidateUploader = createCandidateUploader({
    isSupportedMedia,
    getProjectId: () => projectId,
    beginIngesting: beginCandidateIngesting,
    endIngesting: endCandidateIngesting,
    invalidatePool: () => { candidatePoolStatuses.clear(); candidatePoolRevisions.clear(); candidatePool.clear(); },
    markCreated: items => items.forEach(item => { if (item?.id) candidateRevealIds.add(item.id); }),
    refresh: () => refresh(),
    refreshFolders: () => loadCandidateFolders(true),
    isCandidateMode,
    render: () => render(),
    scheduleRevealCleanup: () => scheduleCandidateRevealCleanup(),
    toast: message => toast(message),
  });
  function candidateUploadContext() {
    return {folder: candidateFolderPath, projectId, category: candidateCategory};
  }
  async function candidateDrop(dataTransfer) {
    const target = candidateUploadContext();
    const files = await candidateUploader.filesFromTransfer(dataTransfer);
    await candidateUploader.upload(files, target);
  }
  async function createCandidateReference(materialId, assetId, variantId = null) {
    try { await api(`/candidates/${encodeURIComponent(materialId)}/references`, json('POST', {asset_id: assetId, ...(variantId ? {variant_id: variantId} : {})})); toast('已创建候选素材引用'); refresh(); }
    catch { toast('候选素材引用接口尚未连接，当前版本未修改原素材。'); }
  }
  function bindCandidateDropCapture() {
    const hasFilePayload = dataTransfer => Boolean(dataTransfer && ([...(dataTransfer.items || [])].some(item => item.kind === 'file') || dataTransfer.files?.length || [...(dataTransfer.types || [])].some(type => /^image\//i.test(type) || /^video\//i.test(type))));
    document.addEventListener('dragover', e => { if (isCandidateMode() && hasFilePayload(e.dataTransfer)) { e.preventDefault(); e.stopPropagation(); document.body.classList.add('candidate-dragging'); } }, true);
    document.addEventListener('drop', e => {
      if (isCandidateMode() && hasFilePayload(e.dataTransfer)) { e.preventDefault(); e.stopImmediatePropagation(); document.body.classList.remove('candidate-dragging'); candidateDrop(e.dataTransfer); }
    }, true);
    document.addEventListener('dragleave', e => { if (!e.relatedTarget) document.body.classList.remove('candidate-dragging'); }, true);
    document.addEventListener('paste', e => {
      if (!isCandidateMode()) return;
      const target = candidateUploadContext();
      const files = candidateUploader.filesFromClipboard(e.clipboardData);
      const media = files.filter(isSupportedMedia);
      if (!media.length) return;
      e.preventDefault(); e.stopPropagation();
      candidateUploader.upload(media, target);
    }, true);
  }
  function bindInputs() {
    const files = $('#fileInput'), folder = $('#candidateFolderInput');
    if (files) files.onchange = e => {
      const target = candidateUploadContext();
      const picked = [...e.target.files];
      e.target.value = '';
      if (isCandidateMode()) candidateUploader.upload(picked, target);
      else if (typeof upload === 'function') upload(uploadTarget, picked);
    };
    if (folder) folder.onchange = e => {
      const target = candidateUploadContext();
      const picked = [...e.target.files];
      e.target.value = '';
      candidateUploader.upload(picked, target);
    };
  }
  // The preview is rendered outside this closure, but its data rules must stay
  // identical to the candidate board: current folder, filename search,
  // category and disabled-only filters all apply before the preview opens.
  function candidatePreviewItems() {
    const source = candidateSource();
    const files = visibleCandidates(source);
    const query = candidateQuery.trim();
    if (query) return files;
    const current = candidateFolderPath;
    return files.filter(item => (item.folderPath || folderPath(item.candidatePath)) === current);
  }
  function candidateStatus(item) {
    if (!item) return {disabled: false, category: '其它'};
    const local = candidateLocal.get(item.id) || {};
    const asset = state.assets.find(a => a.id === item.asset_id);
    return {
      disabled: local.status === 'disabled' ? true : local.status === 'active' ? false : Boolean(item.disabled || item.is_disabled || item.status === 'disabled' || local.disabled),
      category: normalCategory(local.category ?? item.category ?? asset?.category),
      highlightUntil: Math.max(local.highlightUntil || 0, Date.parse(item.highlighted_until || item.highlight_until || '') || 0),
    };
  }
  app.registerCandidatePreview({
    getItems: candidatePreviewItems,
    getById: id => candidateSource().find(item => item.id === id) || state.materials.find(item => item.id === id) || null,
    getStatus: candidateStatus,
    patch: patchCandidate,
    highlight: candidateHighlight,
    render: () => { if (isCandidateMode()) renderCandidateLibrary(); },
    isCandidateMode,
    getFolderPath: () => candidateFolderPath,
  });
  // Switch the existing renderer only while the new tab is active.
  const originalRefresh = typeof refresh === 'function' ? refresh : null;
  if (originalRefresh) {
    refresh = async function candidateAwareRefresh(...args) {
      const res = await originalRefresh(...args);
      if (isCandidateMode()) {
        await loadCandidateFolders(true);
        render();
      }
      return res;
    };
  }
  render = function candidateAwareRender(...args) { if (isCandidateMode()) renderCandidateLibrary(); else { candidateGridView.dispose($('#board')); document.body.classList.remove('candidate-mode'); originalRender(...args); syncCandidateHeader(); } };
  renderDock = function candidateAwareDock() { if (isCandidateMode()) renderCandidateDock(); else originalRenderDock(); };
  preview = function candidateAwarePreview(...args) { if (window.openUnifiedPreview) return window.openUnifiedPreview(...args); restoreStandardPreview(); originalPreview(...args); };
  $('#assetsLibrary')?.addEventListener('click', () => switchLibrary('assets'));
  $('#candidateLibrary')?.addEventListener('click', () => switchLibrary('candidates'));
  $('#projectSelect')?.addEventListener('change', () => { candidatePoolProject = null; candidatePoolStatuses.clear(); candidatePoolRevisions.clear(); candidatePool.clear(); candidateFolderProject = null; candidateFolders.clear(); candidateFolderPath = ''; candidateViewProject = null; candidateKnownProject = null; candidateKnownReady = false; candidateKnownIds.clear(); candidateRevealIds.clear(); candidateRevealPlayedIds.clear(); if (isCandidateMode()) Promise.all([loadCandidatePool('active'), loadCandidateFolders(true)]).finally(() => render()); });
  bindCandidateContext(); bindCandidateDropCapture(); bindInputs(); syncCandidateHeader();
})();
