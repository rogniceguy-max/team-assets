'use strict';

const $ = selector => document.querySelector(selector);
const originInput = $('#serverOrigin');
const projectSelect = $('#projectSelect');
const assetList = $('#assetList');
const assetPanel = $('#assetPanel');
const emptyState = $('#emptyState');
const notice = $('#notice');
const form = $('#assetForm');

let serverOrigin = localStorage.getItem('team-assets-api-origin') || originInput.value;
let projects = [];
let assets = [];
let selectedAsset = null;
let eventSource = null;
let noticeTimer = null;
let refreshQueued = false;

originInput.value = serverOrigin;

function apiUrl(path) {
  return `${serverOrigin.replace(/\/+$/, '')}/api/v1${path}`;
}

function fileUrl(path) {
  return new URL(path, `${serverOrigin.replace(/\/+$/, '')}/`).toString();
}

class ApiError extends Error {
  constructor(status, detail, message) {
    super(message);
    this.status = status;
    this.detail = detail;
    this.current = detail && typeof detail === 'object' ? detail.current : undefined;
  }
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  let response;
  try {
    response = await fetch(apiUrl(path), {...options, headers, credentials: 'omit'});
  } catch (error) {
    throw new ApiError(0, null, `无法连接 API：${error.message}`);
  }
  if (!response.ok) {
    let body;
    try { body = await response.json(); } catch { body = null; }
    const detail = body?.detail ?? body;
    const message = typeof detail === 'string'
      ? detail
      : detail?.message || `请求失败（HTTP ${response.status}）`;
    throw new ApiError(response.status, detail, message);
  }
  if (response.status === 204) return null;
  return response.json();
}

function setConnectionState(message, kind = '') {
  const element = $('#connectionState');
  element.textContent = message;
  element.classList.toggle('is-connected', kind === 'connected');
  element.classList.toggle('is-error', kind === 'error');
}

function showNotice(message, kind = '') {
  notice.textContent = message;
  notice.hidden = false;
  notice.classList.toggle('is-error', kind === 'error');
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice.hidden = true; }, 5200);
}

function escapeText(value) {
  return String(value ?? '');
}

function renderProjects() {
  const previous = projectSelect.value || localStorage.getItem('team-assets-project-id');
  projectSelect.replaceChildren();
  for (const project of projects) {
    const option = document.createElement('option');
    option.value = project.id;
    option.textContent = project.name;
    projectSelect.append(option);
  }
  if (!projects.length) {
    const option = document.createElement('option');
    option.textContent = '没有可用项目';
    projectSelect.append(option);
    return;
  }
  const selected = projects.some(project => project.id === previous) ? previous : projects[0].id;
  projectSelect.value = selected;
  localStorage.setItem('team-assets-project-id', selected);
}

function renderAssets() {
  assetList.replaceChildren();
  $('#assetListEmpty').hidden = assets.length > 0;
  for (const asset of assets) {
    const item = document.createElement('li');
    const button = document.createElement('button');
    button.type = 'button';
    button.setAttribute('aria-current', selectedAsset?.id === asset.id ? 'true' : 'false');
    const dot = document.createElement('span');
    dot.className = 'asset-dot';
    const name = document.createElement('span');
    name.textContent = asset.name;
    const category = document.createElement('span');
    category.className = 'asset-category';
    category.textContent = asset.category;
    button.append(dot, name, category);
    button.addEventListener('click', () => loadAsset(asset.id));
    item.append(button);
    assetList.append(item);
  }
}

function renderVariants(asset) {
  const list = $('#variantList');
  list.replaceChildren();
  $('#variantEmpty').hidden = asset.variants.length > 0;
  for (const variant of asset.variants) {
    const item = document.createElement('li');
    item.className = 'variant-chip';
    item.textContent = `${variant.name}${variant.episodes ? ` · ${variant.episodes}` : ''}${variant.final_material_id ? ' · 已定稿' : ''}`;
    list.append(item);
  }
}

async function renderMaterials(asset) {
  const host = $('#materials');
  host.replaceChildren();
  const response = await request(`/assets/${encodeURIComponent(asset.id)}/materials?limit=200&offset=0`);
  $('#materialCount').textContent = `${response.total} 项素材`;
  $('#materialEmpty').hidden = response.materials.length > 0;
  for (const material of response.materials) {
    const card = document.createElement('button');
    card.className = 'material-card';
    card.type = 'button';
    const image = document.createElement('img');
    image.loading = 'lazy';
    image.src = fileUrl(material.thumbnail_url);
    image.alt = material.name;
    const label = document.createElement('span');
    label.textContent = material.name;
    card.append(image, label);
    card.addEventListener('click', () => openPreview(material));
    host.append(card);
  }
}

function openPreview(material) {
  $('#previewImage').src = fileUrl(material.url);
  $('#previewImage').alt = material.name;
  $('#previewName').textContent = material.name;
  $('#imagePreview').showModal();
}

function fillAssetForm(asset = null) {
  form.dataset.mode = asset ? 'edit' : 'create';
  $('#assetName').value = asset?.name || '';
  $('#assetCategory').value = asset?.category || '人物';
  $('#assetRole').value = asset?.role || '';
  $('#saveAssetButton').textContent = asset ? '保存修改' : '创建资产';
  form.hidden = false;
  $('#assetName').focus();
}

function renderAssetPanel(asset) {
  selectedAsset = asset;
  emptyState.hidden = true;
  assetPanel.hidden = false;
  $('#assetPath').textContent = asset.path || '';
  $('#assetTitle').textContent = asset.name;
  renderVariants(asset);
  renderAssets();
  renderMaterials(asset).catch(error => showNotice(error.message, 'error'));
}

async function loadProjects() {
  const result = await request('/projects?limit=200&offset=0&sort_by=position');
  projects = result.projects;
  renderProjects();
}

async function loadAssets(preferredId = selectedAsset?.id) {
  const projectId = projectSelect.value;
  if (!projectId) {
    assets = [];
    selectedAsset = null;
    renderAssets();
    assetPanel.hidden = true;
    emptyState.hidden = false;
    return;
  }
  localStorage.setItem('team-assets-project-id', projectId);
  const result = await request(`/projects/${encodeURIComponent(projectId)}/assets?limit=200&offset=0&sort_by=position`);
  assets = result.assets;
  renderAssets();
  const nextId = assets.some(asset => asset.id === preferredId) ? preferredId : assets[0]?.id;
  if (nextId) await loadAsset(nextId);
  else {
    selectedAsset = null;
    assetPanel.hidden = true;
    emptyState.hidden = false;
  }
}

async function loadAsset(assetId) {
  const asset = await request(`/assets/${encodeURIComponent(assetId)}`);
  renderAssetPanel(asset);
}

async function refreshWorkspace() {
  await loadProjects();
  await loadAssets(selectedAsset?.id);
}

function queueRefresh() {
  if (refreshQueued) return;
  refreshQueued = true;
  setTimeout(async () => {
    refreshQueued = false;
    try { await refreshWorkspace(); }
    catch (error) { showNotice(error.message, 'error'); }
  }, 140);
}

function connectEvents() {
  eventSource?.close();
  eventSource = new EventSource(apiUrl('/events'));
  eventSource.onopen = () => setConnectionState('实时连接', 'connected');
  eventSource.onmessage = event => {
    try {
      const data = JSON.parse(event.data);
      if (typeof data.revision === 'number') queueRefresh();
    } catch { /* A malformed event does not stop the stream. */ }
  };
  eventSource.onerror = () => setConnectionState('正在重连…');
}

async function connect() {
  serverOrigin = originInput.value.trim().replace(/\/+$/, '');
  if (!serverOrigin) throw new Error('请输入 API 地址');
  localStorage.setItem('team-assets-api-origin', serverOrigin);
  await refreshWorkspace();
  connectEvents();
}

$('#connectionForm').addEventListener('submit', async event => {
  event.preventDefault();
  try {
    setConnectionState('连接中…');
    await connect();
  } catch (error) {
    setConnectionState('连接失败', 'error');
    showNotice(error.message, 'error');
  }
});

$('#refreshButton').addEventListener('click', async () => {
  try { await refreshWorkspace(); showNotice('已刷新'); }
  catch (error) { showNotice(error.message, 'error'); }
});

projectSelect.addEventListener('change', async () => {
  try { await loadAssets(null); }
  catch (error) { showNotice(error.message, 'error'); }
});

$('#newAssetButton').addEventListener('click', () => {
  if (!projectSelect.value) return showNotice('请先选择项目', 'error');
  if (!selectedAsset) {
    assetPanel.hidden = false;
    emptyState.hidden = true;
    $('#assetTitle').textContent = '新建资产';
    $('#assetPath').textContent = projects.find(project => project.id === projectSelect.value)?.name || '';
  }
  fillAssetForm();
});

$('#editAssetButton').addEventListener('click', () => fillAssetForm(selectedAsset));
$('#cancelAssetButton').addEventListener('click', () => {
  form.hidden = true;
  if (selectedAsset) loadAsset(selectedAsset.id).catch(error => showNotice(error.message, 'error'));
  else {
    assetPanel.hidden = true;
    emptyState.hidden = false;
  }
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const mode = form.dataset.mode;
  const input = {
    name: $('#assetName').value.trim(),
    category: $('#assetCategory').value,
    role: $('#assetRole').value.trim(),
  };
  try {
    $('#saveAssetButton').disabled = true;
    if (mode === 'edit' && selectedAsset) {
      await request(`/assets/${encodeURIComponent(selectedAsset.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({...input, version: selectedAsset.version}),
      });
      showNotice('资产信息已更新');
      form.hidden = true;
      await loadAssets(selectedAsset.id);
    } else {
      const created = await request('/assets', {
        method: 'POST',
        body: JSON.stringify({...input, project_id: projectSelect.value}),
      });
      showNotice('资产已创建');
      form.hidden = true;
      await loadAssets(created.id);
    }
  } catch (error) {
    if (error.status === 409 && error.current) {
      selectedAsset = error.current;
      $('#assetName').value = error.current.name || '';
      $('#assetCategory').value = error.current.category || '人物';
      $('#assetRole').value = error.current.role || '';
      showNotice('资产已被其他客户端修改，表单已更新为服务器当前版本；请检查后再保存。', 'error');
      await loadAssets(error.current.id);
      fillAssetForm(selectedAsset);
    } else {
      showNotice(error.message, 'error');
    }
  } finally {
    $('#saveAssetButton').disabled = false;
  }
});

$('#imageInput').addEventListener('change', async event => {
  const files = [...event.target.files];
  event.target.value = '';
  if (!selectedAsset || !files.length) return;
  try {
    for (const file of files) {
      const formData = new FormData();
      formData.append('files', file, file.name);
      await request(`/assets/${encodeURIComponent(selectedAsset.id)}/materials`, {
        method: 'POST',
        headers: {'Idempotency-Key': `standalone-${Date.now()}-${Math.random().toString(16).slice(2)}`},
        body: formData,
      });
    }
    showNotice(`已上传 ${files.length} 张图片`);
    await renderMaterials(selectedAsset);
  } catch (error) {
    showNotice(error.message, 'error');
  }
});

$('#closePreview').addEventListener('click', () => $('#imagePreview').close());
$('#imagePreview').addEventListener('click', event => {
  if (event.target === $('#imagePreview')) $('#imagePreview').close();
});

try {
  setConnectionState('连接中…');
  connect().catch(error => {
    setConnectionState('连接失败', 'error');
    showNotice(error.message, 'error');
  });
} catch (error) {
  setConnectionState('连接失败', 'error');
}
