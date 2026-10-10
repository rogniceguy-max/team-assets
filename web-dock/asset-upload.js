'use strict';

/** Asset and variant material uploads, injected with the legacy board services. */
function createAssetUploader(services) {
  const {
    getState,
    getProjectId,
    api,
    json,
    refresh,
    toast,
    node,
    button,
    newProject,
    setPendingFiles,
  } = services;
  const $ = selector => document.querySelector(selector);

  function upload(target, files) {
    const destination = typeof target === 'string' ? {type: 'variant', id: target} : target;
    if (!destination?.id || !files.length) return;

    const allowed = files.filter(file => ['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.type));
    if (allowed.length !== files.length) toast('第一版支持 PNG、JPEG、WebP 和 GIF 图片。');
    if (!allowed.length) return;

    const batches = [];
    let currentBatch = [];
    let currentBatchSize = 0;
    for (const file of allowed) {
      if (file.size > 28 * 1024 * 1024) {
        batches.push({type: 'large', files: [file]});
      } else {
        if (currentBatch.length && (currentBatch.length >= 20 || currentBatchSize + file.size > 25 * 1024 * 1024)) {
          batches.push({type: 'standard', files: currentBatch});
          currentBatch = [];
          currentBatchSize = 0;
        }
        currentBatch.push(file);
        currentBatchSize += file.size;
      }
    }
    if (currentBatch.length) batches.push({type: 'standard', files: currentBatch});

    const task = node('div', 'upload-task');
    const label = node('div', '', `正在上传 ${allowed.length} 张图片${batches.length > 1 ? ` (第 1/${batches.length} 批)` : ''}`);
    const progress = node('progress');
    const status = node('span');
    progress.max = 100;
    progress.value = 0;
    task.append(label, progress, status);
    $('#uploads').append(task);

    const showError = (message, retryable = true) => {
      label.textContent = '上传未完成';
      status.textContent = message;
      if (retryable) task.append(button('重试', run));
      task.append(button('关闭', () => task.remove()));
    };

    function sendStandardBatch(batchFiles) {
      const data = new FormData();
      batchFiles.forEach(file => data.append('files', file));
      data.append('source', 'web');
      const key = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', `/api/${destination.type === 'asset' ? 'assets' : 'variants'}/${destination.id}/materials`);
        xhr.setRequestHeader('Idempotency-Key', key);
        xhr.timeout = 300000;
        xhr.onerror = () => reject(new Error('网络中断，素材未上传。'));
        xhr.ontimeout = () => reject(new Error('上传超时，请重试。'));
        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve(JSON.parse(xhr.responseText || '{}'));
            return;
          }
          let message = `请求失败 (${xhr.status})`;
          try {
            const body = JSON.parse(xhr.responseText);
            message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail || body);
          } catch {}
          reject(new Error(message));
        };
        xhr.send(data);
      });
    }

    async function sendLargeFile(file) {
      const state = getState();
      const targetAssetId = destination.type === 'asset'
        ? destination.id
        : (state.variants.find(variant => variant.id === destination.id)?.asset_id || '');
      const targetVariantId = destination.type === 'variant' ? destination.id : null;
      const data = new FormData();
      data.append('files', file);
      data.append('source', 'web');
      data.append('project_id', getProjectId() || '');
      data.append('category', '其它');
      const result = await api('/candidates', {method: 'POST', body: data});
      const candidate = result?.candidates?.[0];
      if (!candidate?.id) throw new Error('大文件上传未返回候选素材');
      await api(`/candidates/${encodeURIComponent(candidate.id)}/references`, json('POST', {
        asset_id: targetAssetId,
        ...(targetVariantId ? {variant_id: targetVariantId} : {}),
      }));
    }

    async function run() {
      task.querySelectorAll('button').forEach(value => value.remove());
      status.textContent = '';
      try {
        for (let index = 0; index < batches.length; index += 1) {
          const batch = batches[index];
          label.textContent = `正在上传 ${allowed.length} 张图片${batches.length > 1 ? ` (第 ${index + 1}/${batches.length} 批)` : ''}`;
          progress.value = Math.round((index / batches.length) * 100);
          status.textContent = `${progress.value}%`;
          if (batch.type === 'large') await sendLargeFile(batch.files[0]);
          else await sendStandardBatch(batch.files);
        }
        label.textContent = `✓ ${allowed.length} 张图片已入库`;
        progress.value = 100;
        status.textContent = '团队页面已同步';
        refresh();
        setTimeout(() => task.remove(), 4500);
      } catch (error) {
        showError(error.message || String(error));
      }
    }

    run();
  }

  function chooseDrop(files) {
    if (!files.length) return;
    const projectId = getProjectId();
    if (!projectId) {
      toast('请先创建项目，再拖入图片。');
      newProject();
      return;
    }

    setPendingFiles(files);
    $('#dropSummary').textContent = `已选择 ${files.length} 个文件。上传后团队成员会同步看到。`;
    const select = $('#dropTarget');
    select.replaceChildren();
    for (const asset of getState().assets.filter(value => value.project_id === projectId)) {
      const option = node('option', '', asset.name);
      option.value = asset.id;
      select.append(option);
    }
    $('#dropUpload').disabled = !select.options.length;
    $('#dropDialog').showModal();
  }

  return Object.freeze({upload, chooseDrop});
}
