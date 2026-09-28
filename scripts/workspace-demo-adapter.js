/* Preview-only, in-memory adapter. Never included in the production application. */
const demoInitial = JSON.parse(JSON.stringify(DEMO_SEED));
let demoState, demoAudit, demoNext, demoPresentations, demoPresets;
const demoLocalStorage = (() => {const values=new Map();return {getItem:key=>values.get(key)||null,setItem:(key,value)=>values.set(key,String(value))};})();
const imageSource = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src');
Object.defineProperty(HTMLImageElement.prototype,'src',{get(){return imageSource.get.call(this);},set(value){imageSource.set.call(this,DEMO_MEDIA[value]||value);},configurable:true});
const setAttribute = Element.prototype.setAttribute;
Element.prototype.setAttribute=function(key,value){return setAttribute.call(this,key,key==='src'&&this.tagName==='IMG'?(DEMO_MEDIA[value]||value):value);};
function demoResetState() {
  demoState = JSON.parse(JSON.stringify(demoInitial));
  const delta = Date.now() - demoState.server_time_ms;
  for (const key of ['streams','displays','uploads','placements']) for (const row of demoState[key]) {
    if (row.created_at) row.created_at += delta;
    if (row.start_at) row.start_at += delta;
    if (row.end_at) row.end_at += delta;
  }
  demoState.player_base_url = 'https://signage.example.invalid';
  demoState.me = {username:'preview',provider:'local'};
  demoState.worker_alive = false;
  demoAudit = [{id:1,at:Date.now(),who:'preview',what:'Sample workspace loaded. No server or physical displays connected.'}];
  demoNext = 2000;
  demoPresentations=JSON.parse(JSON.stringify(DEMO_PRESENTATIONS));
  demoPresets=JSON.parse(JSON.stringify(DEMO_PRESETS));
}
demoResetState();
function demoLog(text) { demoAudit.unshift({id:demoNext++,at:Date.now(),who:'preview',what:text}); }
function demoOwn(sid, now) {
  const places = demoState.placements.filter(p => p.stream_id === sid || (p.stream_id === null && p.mode === 'override'));
  const active = places.filter(p => p.enabled && (p.start_at == null || p.start_at <= now) && (p.end_at == null || p.end_at > now) && demoState.uploads.some(u => u.id===p.upload_id && u.status==='ready'));
  const ovs = active.filter(p => p.mode==='override').sort((a,b) => (b.start_at ?? b.created_at)-(a.start_at ?? a.created_at) || b.id-a.id);
  const selected = ovs.length ? [ovs[0]] : active.filter(p=>p.mode==='rotation').sort((a,b)=>a.position-b.position || a.id-b.id);
  const items = selected.flatMap(p => {
    const u = demoState.uploads.find(u=>u.id===p.upload_id);
    return u.slides.map(s=>({slide_id:s.id,placement_id:p.id,kind:s.kind,url:DEMO_MEDIA['/media/'+s.file],thumb:DEMO_MEDIA[s.thumb]||s.thumb,title:u.title,duration_ms:s.kind==='video'?s.duration_ms:p.slide_seconds*1000}));
  });
  const edges = places.flatMap(p=>p.enabled?[p.start_at,p.end_at]:[]).filter(t=>t!=null&&t>now);
  return {mode:items.length?(ovs.length?'override':'rotation'):'empty',anchor_ms:ovs.length?(ovs[0].start_at??ovs[0].created_at):0,items,next_change_ms:edges.length?Math.min(...edges):null};
}
function demoResolve(sid, now=Date.now()) {
  const s = demoState.streams.find(s=>s.id===sid);
  if (!s) return {mode:'off',source:'off',anchor_ms:0,items:[],next_change_ms:null};
  let own=demoOwn(sid,now), source=s.kind==='screensaver'?'screensaver':'stream';
  if (own.mode==='empty' && (s.kind==='screensaver'||s.fallback==='screensaver')) {
    const ss=demoState.streams.find(x=>x.kind==='screensaver');
    const fallback=demoOwn(ss.id,now);
    const edges=[own.next_change_ms,fallback.next_change_ms].filter(t=>t!=null);
    own={...fallback,mode:fallback.items.length?fallback.mode:'clock',next_change_ms:edges.length?Math.min(...edges):null}; source='screensaver';
  }
  return {...own,source};
}
function demoCurrent(state,now) {
  const total=state.items.reduce((sum,i)=>sum+i.duration_ms,0); if(!total) return null;
  let offset=((now-state.anchor_ms)%total+total)%total;
  for(const item of state.items){if(offset<item.duration_ms)return item;offset-=item.duration_ms;}
  return state.items[0];
}
function demoOverview() {
  const out=JSON.parse(JSON.stringify(demoState)),now=Date.now();out.server_time_ms=now;
  for(const s of out.streams){const st=demoResolve(s.id,now),cur=demoCurrent(st,now);Object.assign(s,{mode:st.mode,source:st.source,now_thumb:cur?.thumb||null,now_title:cur?.title||null,next_change_ms:st.next_change_ms,loop_ms:st.items.reduce((a,i)=>a+i.duration_ms,0),item_count:st.items.length});}
  for(const u of out.uploads) for(const slide of u.slides) slide.thumb=DEMO_MEDIA[slide.thumb]||slide.thumb;
  for(const d of out.displays){const stream=out.streams.find(s=>s.id===d.stream_id);d.last_seen=d.online?now-2000:null;d.now_thumb=d.online?stream?.now_thumb:null;}
  out.placements.sort((a,b)=>a.position-b.position||a.id-b.id);
  return out;
}
function demoFail(message){throw new Error(message);}
function demoRow(collection,id){const r=demoState[collection].find(x=>x.id===id);if(!r)demoFail('This sample record no longer exists.');return r;}
function demoName(value,max=120){if(typeof value!=='string'||!value.trim()||value.trim().length>max)demoFail('Enter a valid name.');return value.trim();}
function demoPlacementValidate(p){
  if(!Number.isInteger(p.slide_seconds)||p.slide_seconds<2||p.slide_seconds>3600)demoFail('Duration must be between 2 and 3600 seconds.');
  if(p.end_at!=null&&p.start_at!=null&&p.end_at<=p.start_at)demoFail('End must be after start.');
  if(p.mode==='override'&&p.end_at==null)demoFail('Overrides need an end time.');
}
window.fetch=async function(url,options={}) {
  await new Promise(resolve=>setTimeout(resolve,65));
  if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
  const path=String(url).split('?')[0],method=options.method||'GET';
  const answer=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json','X-Server-Time':String(Date.now())}});
  try {
    const body=options.body?JSON.parse(options.body):{};
    if(method==='GET'&&path==='/api/overview')return answer(demoOverview());
    if(method==='GET'&&path==='/api/audit')return answer(demoAudit);
    if(method==='GET'&&path==='/api/users')return answer([{id:1,username:'preview',created_at:demoState.streams[0].created_at}]);
    if(path.startsWith('/api/users'))return answer({detail:'Account changes require the real server. Do not enter real credentials into a demo.'},409);
    if(path.startsWith('/auth/'))return answer({detail:'This preview has no login session. Use Reset demo to start again.'},409);
    if(method==='GET'&&path==='/api/transfers')return answer([]);
    if(method==='GET'&&path==='/api/shares')return answer([]);
    const appearance=path.match(/^\/api\/streams\/(\d+)\/presentation$/);
    if(appearance){
      const sid=appearance[1];
      if(!demoState.streams.some(s=>String(s.id)===sid))return answer({detail:'Stream not found'},404);
      if(!demoPresentations[sid])demoPresentations[sid]={revision:0,settings:JSON.parse(JSON.stringify(DEMO_PRESENTATIONS['2'].settings))};
      if(method==='GET')return answer(demoPresentations[sid]);
      if(method==='PUT'){
        if(body.expected_revision!==demoPresentations[sid].revision)return answer({detail:'Reopen the editor before saving.'},409);
        if(['top','bottom'].some(k=>body.settings[k].enabled&&body.settings[k].mode==='countdown'&&!body.settings[k].target_at))return answer({detail:'Set a countdown target before applying.'},422);
        demoPresentations[sid]={revision:body.expected_revision+1,settings:body.settings};demoLog('Updated sample banners.');return answer(demoPresentations[sid]);
      }
    }
    if(path==='/api/presentation/presets'){
      if(method==='GET')return answer(demoPresets);
      if(method==='POST'){const record={id:demoNext++,name:demoName(body.name),settings:body.settings};demoPresets.push(record);return answer(record,201);}
    }
    if(path.startsWith('/api/presentation/presets/')&&method==='DELETE'){demoPresets=demoPresets.filter(p=>p.id!==Number(path.split('/').pop()));return answer({ok:true});}
    const parts=path.split('/').filter(Boolean),collection=parts[1],id=Number(parts[2]);
    if(!['streams','displays','uploads','placements'].includes(collection))return answer({detail:'Not available in the offline preview.'},404);
    if(method==='POST'&&collection==='streams'&&parts[3]==='order'){
      demoRow('streams',id);body.placement_ids.forEach((pid,i)=>{const p=demoRow('placements',pid);if(p.stream_id!==id||p.mode!=='rotation')demoFail('Invalid rotation.');p.position=i;});demoLog('Reordered a sample stream.');return answer({ok:true});
    }
    if(method==='POST'&&collection==='streams'){
      const row={id:demoNext++,name:demoName(body.name),kind:'normal',fallback:'screensaver',color:'',created_at:Date.now()};row.playback_key=String(row.id).padStart(32,'0');row.player_path='/stream/'+row.playback_key;demoState.streams.splice(demoState.streams.length-1,0,row);demoLog('Created stream “'+row.name+'”.');return answer(row);
    }
    if(method==='POST'&&collection==='displays'){
      if(!/^[a-z0-9][a-z0-9-]{0,63}$/.test(body.slug)||demoState.displays.some(d=>d.slug===body.slug))demoFail('Use a unique lowercase display slug.');
      if(body.stream_id!=null)demoRow('streams',body.stream_id);
      const row={id:demoNext++,name:demoName(body.name,60),slug:body.slug,stream_id:body.stream_id??null,online:false,last_seen:null,last_ip:null,now_slide:null,now_thumb:null,created_at:Date.now()};demoState.displays.push(row);demoLog('Added sample display “'+row.name+'”.');return answer(row);
    }
    if(method==='POST'&&collection==='placements'){
      demoRow('uploads',body.upload_id);
      const ids=body.all_streams?(body.mode==='override'?[null]:demoState.streams.map(s=>s.id)):body.stream_ids;
      if(!Array.isArray(ids)||!ids.length)demoFail('Choose a stream.');
      const rows=ids.map(sid=>{if(sid!==null)demoRow('streams',sid);const p={id:demoNext++,upload_id:body.upload_id,stream_id:sid,mode:body.mode,position:demoState.placements.length,slide_seconds:body.slide_seconds,start_at:body.start_at??(body.mode==='override'?Date.now():null),end_at:body.end_at??null,enabled:1,created_by:'preview',created_at:Date.now()};demoPlacementValidate(p);return p;});
      demoState.placements.push(...rows);demoLog('Placed sample content in '+body.mode+'.');return answer({ok:true});
    }
    if(method==='PATCH'){
      const row=demoRow(collection,id),next={...row};
      if(collection==='streams'){if(body.name!==undefined)next.name=demoName(body.name);if(body.fallback!==undefined){if(!['screensaver','blank'].includes(body.fallback))demoFail('Invalid fallback.');next.fallback=body.fallback;}}
      if(collection==='displays'){if(body.name!==undefined)next.name=demoName(body.name,60);if(body.clear_stream)next.stream_id=null;else if(body.stream_id!=null){demoRow('streams',body.stream_id);next.stream_id=body.stream_id;}}
      if(collection==='uploads'&&body.title!==undefined)next.title=demoName(body.title);
      if(collection==='placements'){for(const k of ['slide_seconds','start_at','end_at','enabled'])if(body[k]!==undefined)next[k]=body[k];if(body.clear_start)next.start_at=null;if(body.clear_end)next.end_at=null;demoPlacementValidate(next);}
      Object.assign(row,next);demoLog('Updated '+collection.slice(0,-1)+' “'+(row.name||row.title||row.id)+'”.');return answer({ok:true});
    }
    if(method==='DELETE'){
      const row=demoRow(collection,id);if(collection==='streams'&&row.kind==='screensaver')demoFail('The screensaver cannot be deleted.');
      demoState[collection]=demoState[collection].filter(r=>r.id!==id);
      if(collection==='streams'){demoState.placements=demoState.placements.filter(p=>p.stream_id!==id);for(const d of demoState.displays)if(d.stream_id===id)d.stream_id=null;}
      if(collection==='uploads')demoState.placements=demoState.placements.filter(p=>p.upload_id!==id);
      demoLog('Removed sample '+collection.slice(0,-1)+'.');return answer({ok:true});
    }
    return answer({detail:'Uploads and conversion require the running backend; nothing is sent from this preview.'},409);
  } catch(error){return answer({detail:error.message},400);}
};
function demoPlayback(sid) {
  const s=demoState.streams.find(s=>s.id===sid);
  const stage=h('div',{class:'demo-stage'}),caption=h('p',{class:'note'},'Local playback illustration. Not connected to a display or converter.');
  const contentRoot=h('div',{class:'demo-content-root'}),layer=h('div',{class:'demo-overlays'});stage.append(contentRoot,layer);
  const renderer=SignageOverlay.create(layer,()=>Date.now(),insets=>{contentRoot.style.top=insets.top+'%';contentRoot.style.bottom=insets.bottom+'%';});
  const settings=demoPresentations[String(sid)]?.settings;
  if(settings){const resolved=JSON.parse(JSON.stringify(settings));for(const part of ['top','bottom','screensaver']){const u=demoState.uploads.find(u=>u.id===resolved[part].asset_id&&u.kind==='graphic'),slide=u?.slides[0];resolved[part].asset_url=slide?'/media/'+slide.file:null;resolved[part].poster_url=slide?.thumb||null;}renderer.set(resolved,s?.kind==='screensaver'?'clock':'rotation');}
  let timer,last='';
  const paint=()=>{const now=Date.now(),state=demoResolve(sid,now),item=demoCurrent(state,now);const key=item?item.slide_id:state.mode+Math.floor(now/1000);if(key===last)return;last=key;contentRoot.replaceChildren();const content=item?h('img',{src:item.url,alt:item.title}):state.mode==='clock'?h('div',{class:'demo-clock'},new Date(now).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})):h('div',{class:'demo-clock'},'Off');content.classList.add('demo-content');contentRoot.append(content);};
  modal({title:'Preview · '+(s?.name||'Unassigned display'),body:h('div',null,stage,caption),wide:true,actions:[c=>h('button',{class:'btn',onclick:()=>c()},'Close')],onClose:()=>{clearInterval(timer);renderer.destroy();}});paint();timer=setInterval(paint,250);
}
function demoInstallControls(){
  document.querySelector('#demo-reset').addEventListener('click',()=>{document.querySelectorAll('dialog').forEach(d=>{d.close();d.remove();});demoResetState();refresh(true).then(()=>show('streams'));toast('Sample workspace reset.');});
  document.addEventListener('click',e=>{
    const button=e.target.closest('button'),a=e.target.closest('a');
    if(button&&(button.id==='btn-upload'||button.id==='dropzone'||button.id==='btn-graphic'||button.id==='btn-source'||button.textContent==='Upload graphic')){e.preventDefault();e.stopImmediatePropagation();toast('Uploads and conversion need the real backend. This offline preview never sends files.');return;}
    if(button&&(button.id==='btn-new-user'||button.closest('#users'))){e.preventDefault();e.stopImmediatePropagation();toast('Account changes are disabled in the preview. No credentials are collected.');return;}
    if(button&&(button.id==='signout'||(button.closest('.mobile-account')&&button.textContent.includes('Sign out')))){e.preventDefault();e.stopImmediatePropagation();toast('No login session in this demo. Use Reset demo to start again.');return;}
    if(!a)return;
    if(a.classList.contains('brand')){e.preventDefault();e.stopImmediatePropagation();show('streams');return;}
    const href=a.getAttribute('href')||'',match=href.match(/\/preview\/(\d+)/),display=href.match(/\/display\/([a-z0-9-]+)/),channel=href.match(/\/stream\/([a-f0-9]+)/);
    if(match||display||channel){e.preventDefault();e.stopImmediatePropagation();demoPlayback(match?Number(match[1]):channel?demoState.streams.find(s=>s.playback_key===channel[1])?.id:demoState.displays.find(d=>d.slug===display[1])?.stream_id);}
  },true);
  document.querySelector('#view-library').addEventListener('drop',e=>{if(e.dataTransfer?.files?.length){e.preventDefault();e.stopImmediatePropagation();toast('File uploads are disabled in this offline preview.');}},true);
  document.querySelector('#file-input').disabled=true;
  document.querySelector('#worker-down').textContent='Preview only · converter not connected';
  document.querySelector('#auth-provider').textContent='Demo account';
  document.querySelector('#graphic-input').disabled=true;
}

