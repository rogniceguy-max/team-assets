'use strict';

/** Project, asset, variant, and detail forms injected with app-owned state. */
function createEntityForms(services) {
  const {
    getState,
    getProjectId,
    setProjectId,
    getCategory,
    setCategory,
    getDetailId,
    setDetailId,
    getDetailVersion,
    setDetailVersion,
    api,
    json,
    refresh,
    toast,
    fail,
    node,
    button,
    mats,
    thumb,
    upload,
  } = services;
  const $ = selector => document.querySelector(selector);
  let formAction = null;
  let conflictVersion = null;

  function field(name, label, type = 'text', value = '', choices = []) {
    const wrapper = node('label', '', label);
    const input = node(type === 'textarea' ? 'textarea' : type === 'select' ? 'select' : 'input');
    input.name = name;
    if (type === 'select') {
      for (const choice of choices) {
        const option = node('option', '', choice.label || choice);
        option.value = choice.value || choice;
        input.append(option);
      }
    } else if (type !== 'textarea') {
      input.type = type;
    }
    input.value = value;
    input.required = ['name', 'asset_name', 'variant_name'].includes(name);
    input.maxLength = ['name', 'asset_name', 'variant_name'].includes(name)
      ? 200
      : name === 'description' ? 20000 : name === 'episodes' ? 2000 : name === 'role' ? 1000 : 500;
    if (name === 'episodes') input.placeholder = '例如 3-10、3,8,12-15、all、>35；留空为未知';
    wrapper.append(input);
    return wrapper;
  }

  function showForm(title, fields, action, submitLabel = '创建') {
    const dialog = $('#formDialog');
    $('#formTitle').textContent = title;
    $('#formFields').replaceChildren(...fields);
    $('#formError').textContent = '';
    $('#formSubmit').textContent = submitLabel;
    formAction = action;
    dialog.showModal();
  }

  function newProject() {
    showForm('创建团队项目', [field('name', '项目名称'), field('description', '项目说明', 'textarea')], async data => {
      const project = await api('/projects', json('POST', data));
      setProjectId(project.id);
    });
  }

  function newAsset(files = []) {
    let createdAsset = null;
    const targetProject = getProjectId();
    if (!targetProject) {
      toast('请先创建项目，再添加资产。');
      newProject();
      return;
    }
    showForm(files.length ? '新建资产并上传' : '新增资产', [
      field('name', '资产名称'),
      field('category', '资产分类', 'select', '人物', ['人物', '场景', '道具', '其他']),
      field('role', '角色定位 / 用途'),
      field('variant_name', '首个变体名称', 'text', '默认造型'),
      field('episodes', '使用集数'),
      field('description', '设定说明', 'textarea'),
    ], async data => {
      if (!createdAsset) {
        createdAsset = await api('/assets', json('POST', {
          project_id: targetProject,
          name: data.name,
          category: data.category,
          role: data.role,
        }));
        for (const key of ['name', 'category', 'role']) $('#entityForm').elements[key].disabled = true;
        const note = node('p', 'muted', '资产已创建，名称和分类已锁定；重试只会创建首个变体。取消后也可从资产表继续添加变体。');
        $('#formFields').prepend(note);
        refresh();
      }
      const variant = await api('/variants', json('POST', {
        asset_id: createdAsset.id,
        name: data.variant_name,
        episodes: data.episodes,
        description: data.description,
      }));
      if (files.length) upload(variant.id, files);
    });
  }

  function editAsset(id) {
    const asset = getState().assets.find(value => value.id === id);
    if (!asset) return;
    let version = asset.version;
    const choices = [...new Set(['人物', '场景', '道具', '其他', asset.category])];
    showForm('编辑资产', [
      field('name', '资产名称', 'text', asset.name),
      field('category', '资产分类', 'select', asset.category, choices),
      field('role', '角色定位 / 用途', 'text', asset.role || ''),
    ], async data => {
      try {
        await api(`/assets/${id}`, json('PATCH', {...data, version}));
        if (getCategory() !== '全部' && getCategory() !== data.category) setCategory(data.category);
      } catch (error) {
        if (error.status === 409 && !$('#assetConflict')) {
          const box = node('div', 'asset-conflict');
          box.id = 'assetConflict';
          box.append(node('p', '', '资产已被其他成员修改。上方输入已保留，请读取最新内容后手动合并。'));
          box.append(button('载入最新版本以对照', async () => {
            try {
              const latest = (await api('/state')).assets.find(value => value.id === id);
              if (!latest) throw new Error('资产不存在');
              box.replaceChildren(
                node('p', '', '请对照下方内容，手动调整上方输入后再确认。'),
                node('pre', '', `最新名称：${latest.name}\n最新分类：${latest.category}\n最新定位 / 用途：${latest.role}`),
                button('我已合并，采用此版本号', () => {
                  version = latest.version;
                  box.remove();
                  $('#formError').textContent = '已采用最新版本号，请点击保存修改提交合并结果。';
                }),
              );
            } catch (loadError) {
              $('#formError').textContent = loadError.message;
            }
          }));
          $('#formFields').append(box);
        }
        throw error;
      }
    }, '保存修改');
  }

  function newVariant(assetId) {
    showForm('新增变体', [
      field('name', '变体名称'),
      field('episodes', '使用集数'),
      field('description', '设定说明', 'textarea'),
    ], data => api('/variants', json('POST', {...data, asset_id: assetId})));
  }

  async function submitEntityForm(event) {
    event.preventDefault();
    const submit = $('#formSubmit');
    submit.disabled = true;
    try {
      await formAction(Object.fromEntries(new FormData(event.currentTarget)));
      $('#formDialog').close();
      await refresh();
      toast('已保存，团队成员将同步看到更新。');
    } catch (error) {
      $('#formError').textContent = error.message;
    } finally {
      submit.disabled = false;
    }
  }

  function openDetail(id) {
    const variant = getState().variants.find(value => value.id === id);
    if (!variant) return;
    setDetailId(id);
    setDetailVersion(variant.version);
    $('#detailTitle').textContent = variant.name;
    for (const key of ['name', 'episodes', 'description']) {
      $('#detailForm').elements[key].value = variant[key] || '';
    }
    $('#detailStatus').textContent = '修改后点击保存';
    $('#conflictBox').hidden = true;
    renderDetailMaterials();
    if (!$('#detail').open) $('#detail').showModal();
  }

  function renderDetailMaterials() {
    const detailId = getDetailId();
    const host = $('#detailMaterials');
    host.replaceChildren(...mats(detailId).map(material => thumb(material, false, detailId)));
    if (!host.children.length) host.append(node('p', 'muted', '暂无素材，上传第一张图片。'));
  }

  async function submitDetailForm(event) {
    event.preventDefault();
    const submit = event.currentTarget.querySelector('button');
    submit.disabled = true;
    $('#detailStatus').textContent = '正在保存…';
    try {
      const variant = await api(`/variants/${getDetailId()}`, json('PATCH', {
        ...Object.fromEntries(new FormData(event.currentTarget)),
        version: getDetailVersion(),
      }));
      setDetailVersion(variant.version);
      $('#detailStatus').textContent = '已保存';
      $('#conflictBox').hidden = true;
      await refresh();
    } catch (error) {
      $('#detailStatus').textContent = error.message + '。你的输入已保留。';
      if (error.status === 409) {
        $('#conflictBox').hidden = false;
        $('#latestFields').hidden = true;
        $('#mergeVersion').hidden = true;
      }
    } finally {
      submit.disabled = false;
    }
  }

  async function loadLatestVariant() {
    try {
      const latest = await api('/state');
      const variant = latest.variants.find(value => value.id === getDetailId());
      if (!variant) throw new Error('变体已不存在');
      conflictVersion = variant.version;
      $('#latestFields').textContent = `最新版本 ${variant.version}\n变体名称：${variant.name}\n使用集数：${variant.episode_scope?.kind === 'unknown' ? '未知' : variant.episodes || '未知'}\n设定说明：${variant.description || '—'}`;
      $('#latestFields').hidden = false;
      $('#mergeVersion').hidden = false;
      $('#detailStatus').textContent = '请对照下方最新内容，手动调整上方输入。完成后点击“我已合并”，再保存。';
    } catch (error) {
      fail(error);
    }
  }

  function adoptLatestVariantVersion() {
    setDetailVersion(conflictVersion);
    $('#conflictBox').hidden = true;
    $('#detailStatus').textContent = '已采用最新版本号；请确认上方合并结果，然后点击保存修改。';
  }

  $('#entityForm').addEventListener('submit', submitEntityForm);
  $('#detailForm').addEventListener('input', () => { $('#detailStatus').textContent = '有尚未保存的修改'; });
  $('#detailForm').addEventListener('submit', submitDetailForm);
  $('#loadLatest').onclick = loadLatestVariant;
  $('#mergeVersion').onclick = adoptLatestVariantVersion;

  return Object.freeze({field, form: showForm, newProject, newAsset, editAsset, newVariant, openDetail, renderDetailMaterials});
}
