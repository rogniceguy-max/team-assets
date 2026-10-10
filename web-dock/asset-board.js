'use strict';

/** Asset board rows, candidate preview panels, folding, and filter transitions. */
function createAssetBoard(services) {
  const {
    getState,
    getProjectId,
    getCategory,
    setCategory,
    collapsed,
    assetMatches,
    visibleVariants,
    assetMats,
    node,
    button,
    thumb,
    preview,
    openDetail,
    newProject,
    newAsset,
    newVariant,
    editAsset,
    pickAssetFiles,
    upload,
    renderPage,
  } = services;
  const $ = selector => document.querySelector(selector);
  const candidateScroll = new Map();
  const assetBodyHeights = new Map();
  let candidateObserver = null;
  let bulkToggleToken = 0;
  let materializeFrame = 0;
  let filterTransitionToken = 0;

  function assetIcon(name) {
    const icon = node('span', 'asset-icon');
    icon.style.setProperty('--icon', `url('/icons/asset-${name}.svg')`);
    icon.setAttribute('aria-hidden', 'true');
    return icon;
  }

  function assetButton(text, icon, action, className) {
    const value = button('', action, className);
    value.append(assetIcon(icon), node('span', '', text));
    return value;
  }

  function candidateRows(materials, width) {
    const rows = [];
    const gap = 8;
    const target = 100;
    let row = [];
    let sum = 0;
    const flush = justify => {
      if (!row.length) return;
      const height = Math.min(justify ? Infinity : target, Math.max(1, (width - gap * (row.length - 1)) / sum));
      rows.push(row.map(item => ({material: item.material, width: item.ratio * height, height})));
      row = [];
      sum = 0;
    };

    for (const material of materials) {
      const ratio = material.width > 0 && material.height > 0 ? material.width / material.height : 16 / 9;
      if (row.length && (sum + ratio) * target + gap * row.length >= width) {
        const before = (width - gap * (row.length - 1)) / sum;
        const after = (width - gap * row.length) / (sum + ratio);
        if (Math.abs(before - target) < Math.abs(after - target)) flush(true);
      }
      row.push({material, ratio});
      sum += ratio;
      if (sum * target + gap * (row.length - 1) >= width) flush(true);
    }
    flush(false);
    return rows;
  }

  function layoutCandidates(scroll) {
    const width = scroll.clientWidth;
    if (width <= 0) return;
    const grid = scroll.querySelector('.candidate-grid');
    if (grid && grid.dataset.width !== String(width)) {
      grid.dataset.width = String(width);
      const existing = new Map([...grid.querySelectorAll('.thumb')].map(element => [element.dataset.materialId, element]));
      const rows = candidateRows(assetMats(scroll.dataset.assetId), width).map(items => {
        const row = node('div', 'candidate-row');
        for (const item of items) {
          const thumb = existing.get(item.material.id);
          if (!thumb) continue;
          thumb.style.width = `${item.width}px`;
          thumb.style.height = `${item.height}px`;
          row.append(thumb);
        }
        return row;
      });
      grid.replaceChildren(...rows);
    }
    scroll.classList.toggle('is-scrollable', scroll.scrollHeight > scroll.clientHeight + 1);
  }

  function renderCandidatePanel(asset) {
    const panel = node('aside', 'candidate-panel');
    panel.setAttribute('aria-label', `${asset.name} 的共享候选素材`);
    panel.append(node('div', 'candidate-heading', '候选素材'));

    const scroll = node('div', 'candidate-scroll');
    scroll.dataset.assetId = asset.id;
    scroll.tabIndex = 0;
    scroll.setAttribute('aria-label', `${asset.name} 候选素材，内部滚动`);
    const materials = assetMats(asset.id);
    if (materials.length) {
      const grid = node('div', 'candidate-grid');
      for (const material of materials) {
        const thumbButton = thumb(material);
        thumbButton.dataset.materialId = material.id;
        grid.append(thumbButton);
      }
      scroll.append(grid);
    } else {
      scroll.append(assetButton('拖动或点击上传', 'upload', () => pickAssetFiles(asset.id), 'candidate-empty'));
    }
    scroll.addEventListener('scroll', () => candidateScroll.set(asset.id, scroll.scrollTop), {passive: true});
    panel.addEventListener('dragover', event => {
      if ([...event.dataTransfer.types].includes('Files')) {
        event.preventDefault();
        event.stopPropagation();
        panel.classList.add('dropping');
      }
    });
    panel.addEventListener('dragleave', event => {
      if (!panel.contains(event.relatedTarget)) panel.classList.remove('dropping');
    });
    panel.addEventListener('drop', event => {
      event.preventDefault();
      event.stopPropagation();
      panel.classList.remove('dropping');
      upload({type: 'asset', id: asset.id}, [...event.dataTransfer.files]);
    });
    panel.append(scroll);
    return panel;
  }

  function buildAssetBody(asset) {
    const state = getState();
    const variants = visibleVariants(asset);
    const wrap = node('div', 'table-wrap');
    const table = node('table', 'variant-table');
    const head = node('thead');
    const headerRow = node('tr');
    wrap.style.setProperty('--variant-count', String(Math.max(1, variants.length)));
    for (const title of ['变体/状态', '使用集数', '设定说明']) headerRow.append(node('th', '', title));
    head.append(headerRow);
    table.append(head);
    const body = node('tbody');

    for (const variant of variants) {
      const row = node('tr', 'variant-row');
      row.dataset.variantId = variant.id;
      const finalCell = node('td');
      const finalMaterial = state.materials.find(material => material.id === variant.final_material_id && !material.deleted_at);
      const choose = button(
        '',
        () => finalMaterial ? preview(finalMaterial.id, variant.id) : openDetail(variant.id),
        `variant-final${finalMaterial ? ' has-final' : ' is-unfinalized'}`,
      );
      choose.setAttribute('aria-label', `${variant.name}：${finalMaterial ? '预览定稿' : '选择定稿'}`);
      if (finalMaterial) {
        const image = node('img');
        image.src = finalMaterial.thumbnail_url || finalMaterial.url;
        image.alt = `${variant.name} 定稿`;
        image.loading = 'lazy';
        choose.append(image);
      } else {
        choose.append(assetIcon('final'), node('span', '', '定稿'));
      }
      finalCell.append(choose);
      row.append(finalCell, node('td', 'variant-episodes', variant.episode_scope?.kind === 'unknown' ? '未知' : variant.episodes || '未知'));

      const description = node('td');
      const edit = button('', () => openDetail(variant.id), 'variant-description');
      edit.title = [variant.name, variant.description].filter(Boolean).join('\n');
      edit.append(node('span', 'variant-name', variant.name), node('span', 'variant-description-text', variant.description || ''));
      description.append(edit);
      row.append(description);
      body.append(row);
    }

    if (!variants.length) {
      const row = node('tr', 'variant-row');
      const cell = node('td', 'empty-row');
      cell.colSpan = 3;
      cell.append(node('p', '', '还没有变体'), button('新增变体', () => newVariant(asset.id)));
      row.append(cell);
      body.append(row);
    }
    table.append(body);
    wrap.append(table, renderCandidatePanel(asset));
    const shell = node('div', 'asset-body');
    shell.append(wrap);
    return shell;
  }

  function prepareCandidateRoot(root) {
    for (const scroll of root.querySelectorAll('.candidate-scroll')) {
      layoutCandidates(scroll);
      scroll.scrollTop = candidateScroll.get(scroll.dataset.assetId) || 0;
      candidateObserver?.observe(scroll);
    }
  }

  function rememberAssetBodyHeight(section, shell) {
    const height = shell.scrollHeight || shell.getBoundingClientRect().height;
    if (height > 0) assetBodyHeights.set(section.dataset.assetId, height);
    return height;
  }

  function removeCandidateObservers(shell) {
    for (const scroll of shell.querySelectorAll('.candidate-scroll')) {
      candidateScroll.set(scroll.dataset.assetId, scroll.scrollTop);
      candidateObserver?.unobserve(scroll);
    }
  }

  function removeAssetBody(section, shell) {
    rememberAssetBodyHeight(section, shell);
    removeCandidateObservers(shell);
    shell.remove();
  }

  function animateAssetBody(section, shell, opening) {
    const current = shell.getBoundingClientRect().height;
    for (const animation of shell.getAnimations?.() || []) animation.cancel();
    shell.style.height = `${current}px`;
    shell.inert = !opening;
    const target = opening ? shell.scrollHeight : 0;
    if (!('animate' in shell) || window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      shell.style.height = opening ? '' : '0px';
      if (!opening) removeAssetBody(section, shell);
      else rememberAssetBodyHeight(section, shell);
      return;
    }
    const animation = shell.animate([{height: `${current}px`}, {height: `${target}px`}], {duration: 200, easing: 'linear', fill: 'both'});
    section._foldAnimation = animation;
    animation.onfinish = () => {
      if (section._foldAnimation !== animation) return;
      section._foldAnimation = null;
      animation.cancel();
      if (opening) {
        shell.style.height = '';
        shell.inert = false;
        rememberAssetBodyHeight(section, shell);
      } else {
        removeAssetBody(section, shell);
      }
    };
  }

  function insertDeferredBody(section) {
    const placeholder = node('div', 'asset-body-placeholder');
    placeholder.dataset.assetId = section.dataset.assetId;
    placeholder.style.height = `${assetBodyHeights.get(section.dataset.assetId) || 0}px`;
    section.append(placeholder);
    section.dataset.deferredOpen = 'true';
  }

  function materializeAssetBody(asset, section, animate = false) {
    if (section.querySelector('.asset-body')) return section.querySelector('.asset-body');
    const placeholder = section.querySelector('.asset-body-placeholder');
    const shell = buildAssetBody(asset);
    if (placeholder) placeholder.replaceWith(shell);
    else section.append(shell);
    delete section.dataset.deferredOpen;
    prepareCandidateRoot(shell);
    rememberAssetBodyHeight(section, shell);
    if (animate) {
      shell.style.height = '0px';
      animateAssetBody(section, shell, true);
    }
    return shell;
  }

  function setAssetOpen(asset, section, open, {animate = true, defer = false} = {}) {
    const head = section.querySelector('.asset-head');
    head.setAttribute('aria-expanded', String(open));
    if (open) {
      collapsed.delete(asset.id);
      const body = section.querySelector('.asset-body');
      if (body) {
        if (animate) animateAssetBody(section, body, true);
        else {
          for (const animation of body.getAnimations?.() || []) animation.cancel();
          body.style.height = '';
          body.inert = false;
          rememberAssetBodyHeight(section, body);
        }
      } else if (defer) {
        insertDeferredBody(section);
      } else {
        materializeAssetBody(asset, section, animate);
      }
    } else {
      collapsed.add(asset.id);
      const body = section.querySelector('.asset-body');
      if (body) {
        if (animate) animateAssetBody(section, body, false);
        else {
          for (const animation of body.getAnimations?.() || []) animation.cancel();
          removeAssetBody(section, body);
        }
      }
      section.querySelector('.asset-body-placeholder')?.remove();
      delete section.dataset.deferredOpen;
    }
  }

  function toggleAsset(id, focus = true) {
    const state = getState();
    const asset = state.assets.find(value => value.id === id);
    const board = $('#board');
    const section = [...board.querySelectorAll('.asset')].find(value => value.dataset.assetId === id);
    if (!asset || !section) return;
    setAssetOpen(asset, section, collapsed.has(id), {animate: true});
    if (focus) section.querySelector('.asset-head').focus({preventScroll: true});
  }

  function scrollAnchor() {
    const viewport = window.innerHeight || document.documentElement.clientHeight || 800;
    const heads = [...$('#board').querySelectorAll('.asset-head')];
    for (const head of heads) {
      const rect = head.getBoundingClientRect();
      if (rect.bottom > 0 && rect.top < viewport) return {id: head.closest('.asset')?.dataset.assetId, top: rect.top};
    }
    return null;
  }

  function restoreScrollAnchor(anchor) {
    if (!anchor) return;
    const section = [...$('#board').querySelectorAll('.asset')].find(value => value.dataset.assetId === anchor.id);
    const head = section?.querySelector('.asset-head');
    if (!head) return;
    const delta = head.getBoundingClientRect().top - anchor.top;
    if (Math.abs(delta) > 0.5) window.scrollBy(0, delta);
  }

  function restoreFilterViewport(anchor, scrollTop) {
    const board = $('#board');
    const hasAnchor = anchor && [...board.querySelectorAll('.asset')].some(section => section.dataset.assetId === anchor.id);
    if (hasAnchor) {
      restoreScrollAnchor(anchor);
    } else {
      const max = Math.max(0, document.documentElement.scrollHeight - (window.innerHeight || document.documentElement.clientHeight || 0));
      window.scrollTo(0, Math.min(scrollTop, max));
    }
  }

  function transitionFilteredRender(update) {
    const board = $('#board');
    const anchor = scrollAnchor();
    const scrollTop = window.scrollY;
    const token = ++filterTransitionToken;
    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches || false;
    const commit = () => {
      if (token !== filterTransitionToken) return false;
      update();
      renderPage();
      restoreFilterViewport(anchor, scrollTop);
      return true;
    };
    board.classList.remove('filter-transition-out', 'filter-transition-in');
    if (reduced || typeof window.setTimeout !== 'function') {
      commit();
      return;
    }
    board.classList.add('filter-transition-out');
    board.getBoundingClientRect();
    if (!commit()) return;
    board.classList.remove('filter-transition-out');
    board.classList.add('filter-transition-in');
    let done = false;
    let timer;
    const finish = () => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      board.removeEventListener('animationend', onEnd);
      if (token === filterTransitionToken) board.classList.remove('filter-transition-in');
    };
    const onEnd = event => {
      if (event.target === board && event.animationName === 'board-filter-in') finish();
    };
    board.addEventListener('animationend', onEnd);
    timer = window.setTimeout(finish, 340);
  }

  function switchCategory(value) {
    if (getCategory() === value) return;
    transitionFilteredRender(() => setCategory(value));
  }

  function nearViewport(section, anchorIndex, index) {
    const viewport = window.innerHeight || document.documentElement.clientHeight || 800;
    const rect = section.getBoundingClientRect();
    return index >= anchorIndex && rect.bottom > -viewport && rect.top < viewport * 2;
  }

  function setAssetsCollapsed(shouldCollapse) {
    const state = getState();
    const projectId = getProjectId();
    const projectAssets = state.assets.filter(asset => asset.project_id === projectId);
    const sections = [...$('#board').querySelectorAll('.asset')];
    const anchor = scrollAnchor();
    const anchorIndex = Math.max(0, sections.findIndex(section => section.dataset.assetId === anchor?.id));
    const targetOpen = !shouldCollapse;
    const jobs = [];
    for (const asset of projectAssets) {
      const section = sections.find(value => value.dataset.assetId === asset.id);
      const wasOpen = !collapsed.has(asset.id);
      if (shouldCollapse) collapsed.add(asset.id);
      else collapsed.delete(asset.id);
      if (section && wasOpen !== targetOpen) {
        jobs.push({asset, section, animate: nearViewport(section, anchorIndex, sections.indexOf(section))});
      }
    }
    const token = ++bulkToggleToken;
    const run = () => {
      if (token !== bulkToggleToken) return;
      for (const job of jobs.filter(value => !value.animate)) {
        setAssetOpen(job.asset, job.section, targetOpen, {animate: false, defer: targetOpen});
      }
      restoreScrollAnchor(anchor);
      for (const job of jobs.filter(value => value.animate)) setAssetOpen(job.asset, job.section, targetOpen, {animate: true});
      restoreScrollAnchor(anchor);
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run);
    else run();
  }

  function materializeDeferredAssets() {
    const state = getState();
    const viewport = window.innerHeight || document.documentElement.clientHeight || 800;
    for (const section of $('#board').querySelectorAll('.asset[data-deferred-open]')) {
      const rect = section.getBoundingClientRect();
      if (rect.bottom > -viewport && rect.top < viewport * 2) {
        const anchor = scrollAnchor();
        const asset = state.assets.find(value => value.id === section.dataset.assetId);
        if (asset) materializeAssetBody(asset, section, false);
        restoreScrollAnchor(anchor);
      }
    }
  }

  function scheduleDeferredMaterialize() {
    if (materializeFrame) return;
    materializeFrame = requestAnimationFrame(() => {
      materializeFrame = 0;
      materializeDeferredAssets();
    });
  }

  function render(animateFold = false) {
    const state = getState();
    const projectId = getProjectId();
    const foldBefore = new Map();
    if (animateFold) {
      for (const asset of document.querySelectorAll('#board .asset')) {
        const shell = asset.querySelector('.asset-body');
        foldBefore.set(asset.dataset.assetId, {
          height: shell?.getBoundingClientRect().height || 0,
          open: asset.querySelector('.asset-head').getAttribute('aria-expanded') === 'true',
          running: !!shell?.getAnimations?.().length,
        });
      }
    }

    const projectAssets = state.assets.filter(asset => asset.project_id === projectId);
    const assets = projectAssets.filter(assetMatches);
    $('#counts').textContent = `${assets.length} 个资产 · ${assets.reduce((count, asset) => count + visibleVariants(asset).length, 0)} 个变体`;
    const board = $('#board');
    for (const scroll of board.querySelectorAll('.candidate-scroll')) candidateScroll.set(scroll.dataset.assetId, scroll.scrollTop);
    for (const shell of board.querySelectorAll('.asset-body')) {
      for (const animation of shell.getAnimations?.() || []) animation.cancel();
    }
    candidateObserver?.disconnect();
    board.replaceChildren();

    if (!assets.length) {
      const empty = node('div', 'empty');
      empty.append(
        node('h2', '', !projectId ? '创建团队项目' : projectAssets.length ? '没有匹配的资产' : '还没有资产'),
        node('p', '', !projectId ? '创建项目后，即可上传素材。' : projectAssets.length ? '试试其他关键词，或切换分类。' : '创建资产，或把图片拖到这里。'),
      );
      if (!projectAssets.length) {
        empty.append(button(!projectId ? '创建项目' : '创建资产', () => !projectId ? newProject() : newAsset(), 'primary'));
      }
      board.append(empty);
      return;
    }

    for (const asset of assets) {
      const section = node('section', 'asset');
      section.dataset.assetId = asset.id;
      const head = node('div', 'asset-head');
      head.tabIndex = 0;
      head.setAttribute('role', 'button');
      head.setAttribute('aria-label', `展开或折叠 ${asset.name}`);
      head.setAttribute('aria-expanded', String(!collapsed.has(asset.id)));
      const toggle = () => toggleAsset(asset.id);
      head.addEventListener('click', event => { if (!event.target.closest('button')) toggle(); });
      head.addEventListener('keydown', event => {
        if (event.target === head && (event.key === 'Enter' || event.key === ' ')) {
          event.preventDefault();
          toggle();
        }
      });
      head.append(node('h3', '', asset.name), node('span', 'role', asset.role || ''));
      const actions = node('div', 'asset-actions');
      actions.append(
        assetButton('新增变体', 'add-variant', () => newVariant(asset.id), 'add-variant'),
        assetButton('编辑', 'edit', () => editAsset(asset.id), 'edit-asset'),
      );
      head.append(actions);
      section.append(head);

      const previous = foldBefore.get(asset.id);
      const open = !collapsed.has(asset.id);
      const moving = animateFold && previous
        && (previous.open !== open || previous.running)
        && typeof Element.prototype.animate === 'function'
        && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (open || moving) {
        const shell = buildAssetBody(asset);
        section.append(shell);
        if (moving) {
          shell.dataset.foldFrom = String(previous.height);
          shell.dataset.foldOpen = String(open);
        }
      }
      board.append(section);
    }

    if (typeof ResizeObserver !== 'undefined') {
      candidateObserver = new ResizeObserver(entries => {
        for (const entry of entries) layoutCandidates(entry.target);
      });
    }
    for (const scroll of board.querySelectorAll('.candidate-scroll')) {
      layoutCandidates(scroll);
      scroll.scrollTop = candidateScroll.get(scroll.dataset.assetId) || 0;
      candidateObserver?.observe(scroll);
    }
    for (const shell of board.querySelectorAll('.asset-body[data-fold-from]')) {
      const opening = shell.dataset.foldOpen === 'true';
      const target = opening ? shell.getBoundingClientRect().height : 0;
      shell.inert = !opening;
      const animation = shell.animate([{height: `${shell.dataset.foldFrom}px`}, {height: `${target}px`}], {duration: 200, easing: 'linear', fill: 'both'});
      animation.onfinish = () => {
        if (!opening) removeAssetBody(shell.closest('.asset'), shell);
        animation.cancel();
      };
    }
  }

  return Object.freeze({render, transitionFilteredRender, switchCategory, setAssetsCollapsed, scheduleDeferredMaterialize});
}
