'use strict';

/**
 * Candidate file ingestion and upload queue. The library supplies the current
 * folder/category/project and lifecycle callbacks so this module does not own
 * board state or its renderer.
 */
function createCandidateUploader(services) {
  const {
    isSupportedMedia,
    getProjectId,
    beginIngesting,
    endIngesting,
    invalidatePool,
    markCreated,
    refresh,
    refreshFolders,
    isCandidateMode,
    render,
    scheduleRevealCleanup,
    toast,
  } = services;
  let uploadQueue = Promise.resolve();

  async function readEntry(entry, prefix = '') {
    if (entry.isFile) {
      return new Promise(resolve => entry.file(file => {
        try {
          Object.defineProperty(file, 'candidateRelativePath', {
            value: `${prefix}${file.name}`,
            configurable: true,
          });
        } catch {}
        resolve(file);
      }, () => resolve(null)));
    }
    if (!entry.isDirectory) return [];

    const reader = entry.createReader();
    const entries = [];
    const readBatch = () => new Promise(resolve => reader.readEntries(batch => {
      if (batch.length) {
        entries.push(...batch);
        readBatch().then(resolve);
      } else {
        resolve();
      }
    }, () => resolve()));
    await readBatch();
    const nested = await Promise.all(entries.map(child => readEntry(child, `${prefix}${entry.name}/`)));
    return nested.flatMap(value => Array.isArray(value) ? value : [value]).filter(Boolean);
  }

  async function filesFromTransfer(dataTransfer) {
    const files = [];
    const items = [...(dataTransfer?.items || [])];
    const directFiles = [...(dataTransfer?.files || [])];
    const addFile = file => { if (file) files.push(file); };

    // Prefer getAsFile() for ordinary drops: Chromium may invalidate file
    // entries after the drop handler returns. Directory entries still recurse
    // so each file keeps its relative path.
    for (const item of items) {
      if (item.kind !== 'file') continue;
      const entry = item.webkitGetAsEntry?.();
      if (entry?.isDirectory) {
        const value = await readEntry(entry);
        (Array.isArray(value) ? value : [value]).forEach(addFile);
      } else {
        const file = item.getAsFile?.();
        if (file) {
          addFile(file);
        } else if (entry) {
          const value = await readEntry(entry);
          (Array.isArray(value) ? value : [value]).forEach(addFile);
        }
      }
    }

    // Keep duplicate uploads. Append any FileList tail that was not exposed
    // through DataTransferItemList.
    if (directFiles.length > files.length) directFiles.slice(files.length).forEach(addFile);
    return files.filter(Boolean);
  }

  function clipboardExtension(type) {
    const value = String(type || '').toLowerCase().split('/')[1] || 'png';
    return value === 'jpeg' ? 'jpg' : value.replace(/[^a-z0-9]+/g, '') || 'png';
  }

  function normalizeClipboardFile(file, index) {
    if (!file) return null;
    const currentName = String(file.name || '');
    if (currentName && currentName !== 'blob' && currentName !== 'image') return file;
    const name = `clipboard-${Date.now()}-${index + 1}.${clipboardExtension(file.type)}`;
    try {
      return new File([file], name, {
        type: file.type || 'application/octet-stream',
        lastModified: file.lastModified || Date.now(),
      });
    } catch {
      try { Object.defineProperty(file, 'name', {value: name, configurable: true}); } catch {}
      return file;
    }
  }

  function filesFromClipboard(clipboardData) {
    const files = [];
    for (const item of [...(clipboardData?.items || [])]) {
      if (item.kind !== 'file') continue;
      const file = item.getAsFile?.();
      if (file) files.push(file);
    }
    if (!files.length) files.push(...(clipboardData?.files || []));
    return files.map(normalizeClipboardFile).filter(Boolean);
  }

  function sendBatch(batch, category, projectId) {
    const data = new FormData();
    batch.forEach(file => data.append(
      'files',
      file,
      file.candidateRelativePath || file.webkitRelativePath || file.name,
    ));
    data.append('source', 'web');
    data.append('project_id', projectId || '');
    data.append('category', category);
    data.append('relative_paths', JSON.stringify(batch.map(file =>
      file.candidateRelativePath || file.webkitRelativePath || file.name
    )));

    const key = crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`;
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/candidates');
      xhr.setRequestHeader('Idempotency-Key', key);
      xhr.timeout = 30 * 60 * 1000;
      xhr.onerror = () => reject(Object.assign(new Error('网络中断，候选素材未上传。'), {retryable: true}));
      xhr.ontimeout = () => reject(Object.assign(new Error('上传超时，候选素材未上传。'), {retryable: true}));
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          let body = null;
          try { body = JSON.parse(xhr.responseText || 'null'); } catch {}
          resolve(body);
          return;
        }
        let detail = '';
        try {
          const body = JSON.parse(xhr.responseText || '{}');
          detail = typeof body.detail === 'string' ? body.detail : body.detail?.message || '';
        } catch {}
        reject(Object.assign(
          new Error(`候选库上传失败 (${xhr.status})${detail ? `：${detail}` : ''}`),
          {status: xhr.status},
        ));
      };
      xhr.send(data);
    });
  }

  function upload(files, target = {}) {
    const uploadProjectId = target.projectId || getProjectId();
    const uploadFolder = target.folder || '';
    const uploadCategory = target.category === '全部' ? '其它' : (target.category || '其它');
    const allowed = [...(files || [])].filter(isSupportedMedia).map(file => {
      const existing = file.candidateRelativePath || file.webkitRelativePath || file.name;
      if (!uploadFolder || existing === uploadFolder || String(existing).startsWith(`${uploadFolder}/`)) return file;
      try {
        Object.defineProperty(file, 'candidateRelativePath', {
          value: `${uploadFolder}/${existing}`,
          configurable: true,
        });
      } catch {}
      return file;
    });
    if (!allowed.length) return Promise.resolve();

    const batches = [];
    for (let index = 0; index < allowed.length; index += 20) {
      batches.push(allowed.slice(index, index + 20));
    }
    beginIngesting();

    const run = async () => {
      try {
        for (const batch of batches) {
          const result = await sendBatch(batch, uploadCategory, uploadProjectId);
          const created = Array.isArray(result?.candidates) ? result.candidates : [];
          markCreated(created);
          invalidatePool();
          await refresh();
          await refreshFolders();
          if (isCandidateMode()) render();
        }
        scheduleRevealCleanup();
      } finally {
        endIngesting();
      }
    };

    uploadQueue = uploadQueue.catch(() => {}).then(run).catch(error => toast(error.message));
    return uploadQueue;
  }

  return Object.freeze({filesFromTransfer, filesFromClipboard, upload});
}
