'use strict';

/** Shared history and recycle-bin dialog, backed by the app's stable API. */
(() => {
  const app = window.TeamAssetsApp;
  if (!app) return;

  const $ = selector => document.querySelector(selector);
  const element = (tag, className, text) => {
    const value = document.createElement(tag);
    if (className) value.className = className;
    if (text !== undefined) value.textContent = text;
    return value;
  };
  const button = (label, action) => {
    const value = element('button', '', label);
    value.type = 'button';
    value.addEventListener('click', action);
    return value;
  };

  function historyText(item) {
    const labels = {
      create_project: '创建项目',
      create_asset: '创建资产',
      update_asset: '修改资产',
      create_variant: '创建变体',
      update_variant: '更新变体',
      upload_materials: '上传素材',
      delete_material: '删除素材',
      restore_material: '恢复素材',
    };
    const state = app.getState() || {};
    const target = [
      ...(state.projects || []),
      ...(state.assets || []),
      ...(state.variants || []),
      ...(state.materials || []),
    ].find(value => value.id === item.object_id);
    const name = target?.name || item.details?.name || item.details?.before?.name || '';
    return (labels[item.action] || '资产更新')
      + (name ? ` · ${name}` : '')
      + (item.action === 'upload_materials' ? ` · ${item.details?.materials?.length || 0} 张` : '');
  }

  async function showList(kind) {
    const dialog = $('#listDialog');
    const title = $('#listTitle');
    const content = $('#listContent');
    dialog.dataset.kind = kind;
    title.textContent = kind === 'trash' ? '回收站' : '最近更新';
    content.replaceChildren(element('p', '', '正在加载…'));
    dialog.showModal();

    try {
      const data = await app.request(`/${kind}`);
      const items = kind === 'trash' ? data.materials : data.events;
      content.replaceChildren();
      if (!items?.length) {
        content.append(element('p', '', kind === 'trash' ? '回收站是空的。' : '还没有更新记录。'));
        return;
      }

      for (const item of items) {
        const row = element('div', 'list-item');
        const text = element('div', 'list-text');
        text.append(
          element('div', '', item.name || item.summary || historyText(item)),
          element('small', '', new Date(item.deleted_at || item.created_at || item.timestamp).toLocaleString('zh-CN')),
        );
        row.append(text);

        if (kind === 'trash') {
          row.append(button('恢复', async () => {
            try {
              await app.request(`/materials/${item.id}/restore`, {method: 'POST'});
              row.remove();
              if (!content.children.length) content.append(element('p', '', '回收站是空的。'));
              await app.refresh();
              app.toast('素材已恢复');
            } catch (error) {
              app.fail(error);
            }
          }));
        }
        content.append(row);
      }
    } catch (error) {
      content.textContent = error.message;
    }
  }

  $('#historyBtn').onclick = () => showList('history');
  $('#trashBtn').onclick = () => showList('trash');
})();
