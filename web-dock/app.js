'use strict';
const $ = s => document.querySelector(s);
const node = (tag, cls, text) => { const el = document.createElement(tag); if(cls) el.className=cls; if(text!==undefined) el.textContent=text; return el; };
const button = (text, fn, cls='') => { const b=node('button',cls,text); b.type='button'; b.addEventListener('click',fn); return b; };
let state={projects:[],assets:[],variants:[],materials:[]}, projectId=localStorage.getItem('projectId'), category='全部', query='', collapsed=new Set(), detailId=null, detailVersion=null, previewId=null, uploadTarget=null, pendingFiles=[], loading=false, reloadPending=false;
let toastTimer;function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,5000);}
function fail(e){toast(e.message||String(e));}
async function refresh(){if(loading){reloadPending=true;return;}loading=true;try{state=await api('/state');if(!state.projects.some(p=>p.id===projectId))projectId=state.projects[0]?.id||null;render();if($('#detail').open)renderDetailMaterials();}catch(e){fail(e);}finally{loading=false;if(reloadPending){reloadPending=false;refresh();}}}
function materialAssetId(m){return m.asset_id||state.variants.find(v=>v.id===m.variant_id)?.asset_id;}
function assetMats(assetId){return state.materials.filter(m=>materialAssetId(m)===assetId&&!m.deleted_at);}
function mats(id){const v=state.variants.find(v=>v.id===id);return v?assetMats(v.asset_id):[];}
let previewTargetId=null;
function thumb(m,final=false,targetVariantId=null){const b=button('',()=>preview(m.id,targetVariantId),'thumb'+(final?' final':''));b.title=m.name||'查看图片';b.setAttribute('aria-label',`查看 ${m.name||'图片'}`);const im=node('img');im.src=m.thumbnail_url||m.url;im.alt=m.name||'素材';im.loading='lazy';b.append(im);return b;}
const assetBoard = createAssetBoard({
 getState: () => state,
 getProjectId: () => projectId,
 getCategory: () => category,
 setCategory: value => { category = value; },
 collapsed,
 assetMatches: asset => assetMatches(asset),
 visibleVariants: asset => visibleVariants(asset),
 assetMats: id => assetMats(id),
 node,
 button,
 thumb: (...args) => thumb(...args),
 preview: (...args) => preview(...args),
 openDetail: (...args) => openDetail(...args),
 newProject: (...args) => newProject(...args),
 newAsset: (...args) => newAsset(...args),
 newVariant: (...args) => newVariant(...args),
 editAsset: (...args) => editAsset(...args),
 pickAssetFiles: (...args) => pickAssetFiles(...args),
 upload: (...args) => upload(...args),
 renderPage: () => render(),
});
function render(animateFold=false){
 const select=$('#projectSelect'),currentState=state;
 select.replaceChildren();
 if(!currentState.projects.length)select.append(node('option','','尚未创建项目'));
 for(const project of currentState.projects){const option=node('option','',project.name);option.value=project.id;select.append(option);}
 if(projectId){select.value=projectId;localStorage.setItem('projectId',projectId);}
 renderDock();
 assetBoard.render(animateFold);
}
function transitionFilteredRender(update){return assetBoard.transitionFilteredRender(update);}
function switchCategory(value){return assetBoard.switchCategory(value);}
function setAssetsCollapsed(shouldCollapse){return assetBoard.setAssetsCollapsed(shouldCollapse);}
function scheduleDeferredMaterialize(){return assetBoard.scheduleDeferredMaterialize();}
const entityForms = createEntityForms({
 getState: () => state,
 getProjectId: () => projectId,
 setProjectId: value => { projectId = value; },
 getCategory: () => category,
 setCategory: value => { category = value; },
 getDetailId: () => detailId,
 setDetailId: value => { detailId = value; },
 getDetailVersion: () => detailVersion,
 setDetailVersion: value => { detailVersion = value; },
 api: (...args) => api(...args),
 json: (...args) => json(...args),
 refresh: () => refresh(),
 toast: message => toast(message),
 fail: error => fail(error),
 node,
 button,
 mats: id => mats(id),
 thumb: (...args) => thumb(...args),
 upload: (...args) => upload(...args),
});
function field(...args){return entityForms.field(...args);}
function form(...args){return entityForms.form(...args);}
function newProject(...args){return entityForms.newProject(...args);}
function newAsset(...args){return entityForms.newAsset(...args);}
function editAsset(...args){return entityForms.editAsset(...args);}
function newVariant(...args){return entityForms.newVariant(...args);}
function openDetail(...args){return entityForms.openDetail(...args);}
function renderDetailMaterials(...args){return entityForms.renderDetailMaterials(...args);}
function preview(id,targetVariantId=null){const m=state.materials.find(m=>m.id===id);if(!m)return;previewId=id;const target=state.variants.find(v=>v.id===targetVariantId&&v.asset_id===materialAssetId(m));previewTargetId=target?.id||null;$('#previewTitle').textContent=m.name||'素材预览';$('#previewImage').src=m.url;$('#previewImage').alt=m.name||'素材预览';$('#download').href=m.url+'?download=1';$('#download').download=m.name||'image';$('#previewSource').textContent=`来源：${['web','manual'].includes(m.source)?'网页上传':m.source==='ai'?'AI 上传':m.source||'未知'}`;$('#setFinal').hidden=!target;$('#setFinal').disabled=target?.final_material_id===m.id;$('#setFinal').textContent=$('#setFinal').disabled?'✓ 当前定稿':`设为「${target?.name||''}」的定稿`;if(!$('#preview').open)$('#preview').showModal();}
function stepImage(offset){const m=state.materials.find(m=>m.id===previewId);if(!m)return;const list=assetMats(materialAssetId(m)),i=list.findIndex(x=>x.id===m.id);if(list.length)preview(list[(i+offset+list.length)%list.length].id,previewTargetId);}
$('#prevImage').onclick=()=>stepImage(-1);$('#nextImage').onclick=()=>stepImage(1);$('#setFinal').onclick=async()=>{const m=state.materials.find(m=>m.id===previewId),v=state.variants.find(v=>v.id===previewTargetId);if(!v||!m||v.asset_id!==materialAssetId(m))return;try{const updated=await api(`/variants/${v.id}`,json('PATCH',{version:v.version,final_material_id:m.id}));if(detailId===v.id)detailVersion=updated.version;await refresh();preview(m.id,v.id);toast('已设为定稿');}catch(e){fail(e);}};
$('#deleteMaterial').onclick=async()=>{try{await api(`/materials/${previewId}`,{method:'DELETE'});$('#preview').close();await refresh();toast('已移入回收站，可随时恢复。');}catch(e){fail(e);}};
function pickFiles(id){uploadTarget={type:'variant',id};$('#fileInput').click();}function pickAssetFiles(id){uploadTarget={type:'asset',id};$('#fileInput').click();}$('#fileInput').onchange=e=>{upload(uploadTarget,[...e.target.files]);e.target.value='';};$('#detailUpload').onclick=()=>{const v=state.variants.find(v=>v.id===detailId);if(v)pickAssetFiles(v.asset_id);};
const assetUploader = createAssetUploader({
 getState: () => state,
 getProjectId: () => projectId,
 api: (...args) => api(...args),
 json: (...args) => json(...args),
 refresh: () => refresh(),
 toast: message => toast(message),
 node,
 button,
 newProject: () => newProject(),
 setPendingFiles: files => { pendingFiles = files; },
});
function upload(target,files){return assetUploader.upload(target,files);}
function dropChoose(files){return assetUploader.chooseDrop(files);}

document.addEventListener('dragover',e=>{if([...e.dataTransfer.types].includes('Files'))e.preventDefault();});document.addEventListener('drop',e=>{if(e.dataTransfer.files.length){e.preventDefault();dropChoose([...e.dataTransfer.files]);}});$('#dropUpload').onclick=()=>{upload({type:'asset',id:$('#dropTarget').value},pendingFiles);$('#dropDialog').close();};$('#dropCreate').onclick=()=>{$('#dropDialog').close();newAsset(pendingFiles);};
$('#projectBtn').onclick=newProject;$('#projectSelect').onchange=e=>{projectId=e.target.value;render();};document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());document.addEventListener('keydown',e=>{if($('#preview').open&&e.key==='ArrowLeft')stepImage(-1);if($('#preview').open&&e.key==='ArrowRight')stepImage(1);});
 window.addEventListener('scroll',scheduleDeferredMaterialize,{passive:true});
 refresh();const events=new EventSource('/api/events');events.onopen=()=>refresh();events.addEventListener('revision',()=>refresh());events.onmessage=()=>refresh();
// Stable boundary for feature scripts. The wrappers intentionally resolve the
// current functions at call time while legacy board/candidate adapters migrate.
let candidatePreviewAdapter = null;
let previewController = null;
window.TeamAssetsApp = Object.freeze({
 getState: () => state,
 getProjectId: () => projectId,
 setProjectId: id => { projectId = id; render(); },
 request: (...args) => api(...args),
 json: (...args) => json(...args),
 toast: (...args) => toast(...args),
 fail: (...args) => fail(...args),
 refresh: (...args) => refresh(...args),
 render: (...args) => render(...args),
 renderDock: (...args) => renderDock(...args),
 registerCandidatePreview: adapter => { candidatePreviewAdapter = adapter; },
 getCandidatePreview: () => candidatePreviewAdapter,
 registerPreviewController: controller => { previewController = controller; },
 getPreviewController: () => previewController,
});
