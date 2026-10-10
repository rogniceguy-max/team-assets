'use strict';
let dockMode='default', dockRange=null, dockUnfinalized=false, dockCollapsed=false, dockHover=false, dockScrollTimer;
const dockDraft={search:'',start:'',end:''};
function episodeMatches(scope,range){
 if(!range)return true;
 if(!scope||scope.kind==='unknown')return false;
 if(scope.kind==='all')return true;
 return (scope.ranges||[]).some(r=>r.start<=range[1]&&(r.end===null||r.end>=range[0]));
}
function assetMatches(asset){
 if(category!=='全部'&&asset.category!==category)return false;
 const variants=state.variants.filter(v=>v.asset_id===asset.id);
 if(dockRange&&!variants.some(v=>episodeMatches(v.episode_scope,dockRange)))return false;
 if(query&&![asset.name,asset.role,...variants.flatMap(v=>[v.name,v.episodes,v.description])].join(' ').toLowerCase().includes(query))return false;
 return !dockUnfinalized||variants.some(v=>!v.final_material_id);
}
function visibleVariants(asset){
 return state.variants.filter(v=>v.asset_id===asset.id&&(!dockUnfinalized||!v.final_material_id));
}
function dockButton(icon,label,action,active=false,key=label){
 const b=button('',action,'dock-button'+(active?' is-active':''));b.setAttribute('aria-label',label);b.setAttribute('aria-pressed',String(active));b.dataset.tip=label;
 b.dataset.dockKey=key;
 const mark=node('span','dock-icon');mark.style.setProperty('--icon',`url('/icons/${icon}.svg')`);mark.setAttribute('aria-hidden','true');b.append(mark);return b;
}
function syncDockDefault(host){
 const set=(key,active)=>{const b=host.querySelector(`[data-dock-key="${key}"]`);if(!b)return;b.classList.toggle('is-active',!!active);b.setAttribute('aria-pressed',String(!!active));};
 set('timeline',!!dockRange);set('search',!!query);
 for(const value of ['全部','人物','场景','道具','其他'])set(value,category===value);
 set('unfinalized',dockUnfinalized);set('collapse',dockCollapsed);
 const collapse=host.querySelector('[data-dock-key="collapse"]');
 if(collapse){const label=dockCollapsed?'全部展开':'全部收起';collapse.setAttribute('aria-label',label);collapse.dataset.tip=label;}
}
function renderDock(){
 const host=$('#dock');
 // Live updates must not replace a focused input or discard its draft.
 if(host.dataset.mode===dockMode){if(dockMode!=='default')return;if(host.children.length){syncDockDefault(host);return;}}
 host.dataset.mode=dockMode;host.replaceChildren();
 if(dockMode==='default'){
  host.append(dockButton('timeline','时间线',()=>{dockDraft.start=dockRange?.[0]||'';dockDraft.end=dockRange?.[1]||'';dockMode='timeline';renderDock();host.querySelector('input').focus();},!!dockRange,'timeline'),dockButton('search','搜索',()=>{dockDraft.search=query;dockMode='search';renderDock();host.querySelector('input').focus();},!!query,'search'));
  for(const [value,label,icon] of [['全部','全部','all'],['人物','人物','person'],['场景','场景','scene'],['道具','道具','prop'],['其他','其它','other']])host.append(dockButton(icon,label,()=>switchCategory(value),category===value,value));
  host.append(dockButton('unfinalized','仅显示尚未定稿',()=>transitionFilteredRender(()=>{dockUnfinalized=!dockUnfinalized;}),dockUnfinalized,'unfinalized'));
  host.append(dockButton('collapse',dockCollapsed?'全部展开':'全部收起',()=>{dockCollapsed=!dockCollapsed;setAssetsCollapsed(dockCollapsed);syncDockDefault(host);},dockCollapsed,'collapse'));
 }else{
  const formEl=node('form','dock-form');formEl.setAttribute('aria-label',dockMode==='search'?'搜索筛选':'时间线筛选');
  const mark=node('span','dock-icon');mark.style.setProperty('--icon',`url('/icons/${dockMode}.svg')`);mark.setAttribute('aria-hidden','true');const lead=node('span','dock-lead');lead.append(mark);formEl.append(lead);
  const input=(key,label,placeholder)=>{const el=node('input');el.value=dockDraft[key];el.placeholder=placeholder;el.setAttribute('aria-label',label);el.autocomplete='off';el.addEventListener('input',()=>dockDraft[key]=el.value);return el;};
  if(dockMode==='search'){const el=input('search','搜索资产、变体或设定','搜索资产、变体或设定…');el.maxLength=500;formEl.append(el);}
  else{const group=node('div','dock-range');for(const [key,label] of [['start','起始集数'],['end','结束集数']]){if(key==='end')group.append(node('span','dock-slash','/'));const el=input(key,label,key==='start'?'起始':'结束');el.inputMode='numeric';el.pattern='[0-9]*';group.append(el);}formEl.append(group);}
  const apply=()=>{if(dockMode==='search'){const value=dockDraft.search.trim().toLowerCase();return ()=>{query=value;};}const a=Number(dockDraft.start),b=Number(dockDraft.end);if(!dockDraft.start&&!dockDraft.end)return ()=>{dockRange=null;};if(!/^\d+$/.test(dockDraft.start)||!/^\d+$/.test(dockDraft.end)||a<1||b<a||!Number.isSafeInteger(b)){toast('请输入完整集数范围，例如 3 / 10，起始不能大于结束。');return null;}return ()=>{dockRange=[a,b];};};
  const back=()=>{const update=apply();if(!update)return;transitionFilteredRender(()=>{update();dockMode='default';});};
  const actions=node('div','dock-actions');actions.append(dockButton('reset','重置',()=>{const searchMode=dockMode==='search';transitionFilteredRender(()=>{if(searchMode){query='';dockDraft.search='';}else{dockRange=null;dockDraft.start='';dockDraft.end='';}dockMode='default';});},false,'reset'),dockButton('back','返回',back,false,'back'));formEl.append(actions);formEl.addEventListener('submit',e=>{e.preventDefault();back();});host.append(formEl);
 }
}
document.addEventListener('DOMContentLoaded',()=>{
 const host=$('#dock');host.addEventListener('pointerenter',()=>{dockHover=true;host.classList.remove('is-scrolling');});host.addEventListener('pointerleave',()=>dockHover=false);
 window.addEventListener('scroll',()=>{clearTimeout(dockScrollTimer);if(!dockHover&&!(host.contains(document.activeElement)&&(document.activeElement.matches('input')||document.activeElement.matches(':focus-visible'))))host.classList.add('is-scrolling');dockScrollTimer=setTimeout(()=>host.classList.remove('is-scrolling'),300);},{passive:true});
});
