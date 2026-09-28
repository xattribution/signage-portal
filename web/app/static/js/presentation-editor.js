import { h, modal, toast } from '/static/js/ui.js';
import { api } from '/static/js/api.js';

const clone = value => JSON.parse(JSON.stringify(value));
const localDate = value => {
  if (value === null || value === undefined) return '';
  const date = new Date(value), pad = n => String(n).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};
let presentationFieldId = 0;
function field(label, input, note = '') {
  const control=input.matches('input,select,textarea')?input:input.querySelector('input,select,textarea');
  const id='presentation-field-'+(++presentationFieldId);
  if(control)control.id=id;
  const help=note?h('small',{class:'m',id:id+'-help'},note):null;
  if(control&&help)control.setAttribute('aria-describedby',help.id);
  return h('div',{class:'field'},h('label',{class:'field-label',for:control?id:null},label),input,help);
}
const checkbox = (label, value, change) => h('label', { class: 'presentation-check' }, h('input', { type:'checkbox', checked:value, onchange:e=>change(e.target.checked) }), h('span', null, label));
const select = (entries, value, change) => h('select', { onchange:e=>change(e.target.value) }, ...entries.map(([key,label])=>h('option',{value:key,selected:String(key)===String(value)},label)));
function contrast(a,b) {
  const luminance = hex => {
    const rgb=[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4);
    return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;
  };
  const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}

export async function presentationEditor(stream, overview, onSave) {
  const [saved, initialPresets] = await Promise.all([
    api('GET', `/api/streams/${stream.id}/presentation`), api('GET', '/api/presentation/presets')
  ]);
  let draft=clone(saved.settings), revision=saved.revision, presets=initialPresets;
  let uploads=overview.uploads, selectedTab='top', closed=false, previewMode=stream.kind==='screensaver'?'clock':'rotation';
  const timeOffset=overview.server_time_ms-Date.now();
  const now=()=>Date.now()+timeOffset;
  let renderer;
  const error=h('div',{class:'err-text',role:'alert'});
  const warning=h('div',{class:'presentation-warning',role:'status'});
  const previewContent=h('div',{class:'presentation-content'});
  const previewClock=h('div',{class:'presentation-preview-clock'},h('strong',null,'09:41'),h('span',null,'Monday, September 28'));
  if(stream.now_thumb) previewContent.append(h('img',{src:stream.now_thumb,alt:'Current content thumbnail'}));
  else previewContent.append(h('div',{class:'presentation-placeholder'},h('span',null,'OFFICE ANNOUNCEMENTS'),h('strong',null,'Your content\nkeeps playing.'),h('span',null,'Images · Presentations · Video')));
  const overlayRoot=h('div');
  const preview=h('div',{class:'presentation-preview'},previewContent,previewClock,overlayRoot);
  const controls=h('div',{class:'presentation-controls'}),tabs=h('div',{class:'presentation-tabs',role:'group','aria-label':'Presentation layer'});
  const status=h('span',{class:'presentation-draft'},'Unpublished preview');
  const presetSelect=select([['','Saved presets'],...presets.map(p=>[p.id,p.name])],'',()=>{});
  presetSelect.setAttribute('aria-label','Saved presets');
  const name=h('input',{placeholder:'Preset name',maxlength:60,'aria-label':'Preset name'});
  const saveName=h('div',{class:'presentation-save-preset',hidden:true},name,
    h('button',{class:'btn small',onclick:async()=>{
      error.textContent='';
      try {
        if(!name.value.trim())throw new Error('Name the preset first.');
        const result=await api('POST','/api/presentation/presets',{name:name.value.trim(),settings:draft});
        await reloadPresets(result.id);saveName.hidden=true;name.value='';toast('Preset saved. The live stream is unchanged.');
      }catch(e){error.textContent=e.message;}
    }},'Save preset'));
  async function reloadPresets(selected='') {
    presets=await api('GET','/api/presentation/presets');
    presetSelect.replaceChildren(h('option',{value:''},'Saved presets'),...presets.map(p=>h('option',{value:p.id},p.name)));
    presetSelect.value=String(selected);
  }
  function changed(){status.textContent='Unpublished changes';error.textContent='';updatePreview();}
  function updatePreview(){
    if(!renderer)return;
    const settings=clone(draft);
    for(const part of ['top','bottom','screensaver']) {
      const graphic=uploads.find(u=>u.id===settings[part].asset_id&&u.kind==='graphic'&&u.status==='ready');
      const slide=graphic?.slides[0];
      settings[part].asset_url=slide?'/media/'+slide.file:null;
      settings[part].poster_url=slide?.thumb||null;
      delete settings[part].asset_id;
    }
    renderer.set(settings,previewMode);
    previewClock.hidden=previewMode!=='clock';previewContent.hidden=previewMode==='clock';
    const messages=[];
    for(const side of ['top','bottom']) {
      const ribbon=draft[side];
      if(!ribbon.enabled)continue;
      if(contrast(ribbon.background,ribbon.foreground)<4.5)messages.push(`${side==='top'?'Top':'Bottom'}: low text contrast.`);
      if(ribbon.opacity<85)messages.push(`${side==='top'?'Top':'Bottom'}: check readability over bright content.`);
      if(ribbon.mode==='countdown'&&!ribbon.target_at)messages.push('Set a countdown target.');
      if(ribbon.end_at!==null&&ribbon.end_at<=now())messages.push(`${side==='top'?'Top':'Bottom'}: its end time is in the past.`);
    }
    const textNodes=overlayRoot.querySelectorAll('.signage-ribbon-text:not(.is-ticker)');
    textNodes.forEach(node=>{if(node.clientWidth&&node.scrollWidth>node.clientWidth+1)messages.push('Text is clipped. Reduce the type size or use a ticker.');});
    warning.textContent=[...new Set(messages)].join(' ');warning.hidden=!messages.length;
  }
  function number(label,obj,key,min,max,step=1,unit='') {
    const output=h('output',null,obj[key]+unit);
    const range=h('input',{type:'range',min,max,step,value:obj[key],onchange:()=>{if(key==='height_pct')renderControls();},oninput:e=>{
      obj[key]=Number(e.target.value);output.textContent=obj[key]+unit;
      if(key==='height_pct'&&obj.font_pct>obj.height_pct*.8){obj.font_pct=Math.floor(obj.height_pct*.8*10)/10;}
      changed();
    }});
    return field(label,h('div',{class:'presentation-range'},range,output));
  }
  function chooseGraphic(obj) {
    return field('Graphic',select([['','None'],...uploads.filter(u=>u.kind==='graphic'&&u.status==='ready').map(u=>[u.id,u.title])],obj.asset_id||'',v=>{obj.asset_id=v?Number(v):null;changed();}));
  }
  function dateField(label,obj,key,note='') {
    return field(label,h('input',{type:'datetime-local',value:localDate(obj[key]),onchange:e=>{obj[key]=e.target.value?new Date(e.target.value).getTime():null;changed();}}),note);
  }
  function basicRibbon(side) {
    const ribbon=draft[side];
    const textField=field(ribbon.mode==='clock'||ribbon.mode==='countdown'?'Label (optional)':'Message',h('textarea',{rows:2,maxlength:1000,placeholder:ribbon.mode==='ticker'?'EX EX EX  ·  Exercise in progress': 'Type your message',oninput:e=>{ribbon.text=e.target.value;changed();}},ribbon.text));
    return [
      h('div',{class:'presentation-control-head'},h('h3',null,side==='top'?'Top ribbon':'Bottom ribbon'),checkbox('Enabled',ribbon.enabled,v=>{ribbon.enabled=v;changed();})),
      field('Content',select([['static','Static text'],['ticker','Scrolling text'],['clock','Clock'],['countdown','Countdown']],ribbon.mode,v=>{ribbon.mode=v;renderControls();changed();})),
      textField,
      ribbon.mode==='ticker'?h('div',{class:'presentation-pair'},number('Speed',ribbon,'speed',15,240,5,' px/s'),field('Direction',select([['left','Right to left'],['right','Left to right']],ribbon.direction,v=>{ribbon.direction=v;changed();}))):null,
      ribbon.mode==='clock'?h('div',{class:'presentation-section'},field('Time zone',h('input',{value:ribbon.timezone,maxlength:80,list:'presentation-zones',onchange:e=>{ribbon.timezone=e.target.value.trim();changed();}})),
        h('datalist',{id:'presentation-zones'},...['UTC','Pacific/Honolulu','America/Los_Angeles','America/Denver','America/Chicago','America/New_York','Europe/London','Asia/Tokyo'].map(v=>h('option',{value:v}))),
        h('div',{class:'presentation-checks'},checkbox('24-hour clock',ribbon.hour24,v=>{ribbon.hour24=v;changed();}),checkbox('Seconds',ribbon.seconds,v=>{ribbon.seconds=v;changed();}),checkbox('Date',ribbon.show_date,v=>{ribbon.show_date=v;changed();}))):null,
      ribbon.mode==='countdown'?h('div',{class:'presentation-section'},dateField('Countdown target',ribbon,'target_at','Entered in this browser’s time zone; saved as an absolute time.'),
        field('At zero',select([['message','Show a message'],['hold','Hold at 00:00:00'],['hide','Hide the ribbon']],ribbon.zero,v=>{ribbon.zero=v;renderControls();changed();})),
        ribbon.zero==='message'?field('Finished message',h('input',{value:ribbon.done_text,maxlength:160,oninput:e=>{ribbon.done_text=e.target.value;changed();}})):null):null,
      h('div',{class:'presentation-pair'},field('Background',h('input',{type:'color',value:ribbon.background,oninput:e=>{ribbon.background=e.target.value;changed();}})),field('Text color',h('input',{type:'color',value:ribbon.foreground,oninput:e=>{ribbon.foreground=e.target.value;changed();}}))),
      number('Ribbon height',ribbon,'height_pct',3,24,.5,'%'),
      number('Type size',ribbon,'font_pct',1,Math.min(14,ribbon.height_pct*.8),.1,'%'),
      number('Background opacity',ribbon,'opacity',0,100,5,'%'),
      field('Alignment',select([['left','Left'],['center','Center'],['right','Right']],ribbon.align,v=>{ribbon.align=v;changed();})),
      h('div',{class:'presentation-pair'},chooseGraphic(ribbon),field('Graphic position',select([['left','Left'],['right','Right']],ribbon.asset_side,v=>{ribbon.asset_side=v;changed();}))),
      h('details',{class:'presentation-details'},h('summary',null,'Timing & entrance'),
        h('div',{class:'presentation-section'},h('div',{class:'presentation-pair'},dateField('Start (optional)',ribbon,'start_at'),dateField('End (optional)',ribbon,'end_at')),
          h('p',{class:'note'},'An end time prevents a temporary notice from staying up. Times use this browser’s time zone.'),
          field('Entrance',select([['none','None'],['fade','Fade in'],['slide','Slide in']],ribbon.entry,v=>{ribbon.entry=v;changed();})),
          number('Entrance duration',ribbon,'entry_ms',0,1500,50,' ms')))
    ];
  }
  function transitionPreview() {
    const transition=draft.transition;const el=previewContent;
    if(renderer.reduced()||transition.effect==='cut')return;
    const frames=transition.effect==='slide-left'?[{transform:'translateX(100%)'},{transform:'translateX(0)'}]:transition.effect==='slide-up'?[{transform:'translateY(100%)'},{transform:'translateY(0)'}]:[{opacity:0},{opacity:1}];
    el.animate(frames,{duration:transition.duration_ms,easing:transition.effect==='fade'?'linear':'ease-out'});
  }
  function renderControls(){
    tabs.replaceChildren(...[['top','Top'],['bottom','Bottom'],['motion','Layout & motion'],...(stream.kind==='screensaver'?[['screensaver','Clock artwork']]:[])].map(([key,label])=>h('button',{class:key===selectedTab?'active':'','aria-pressed':String(key===selectedTab),onclick:()=>{selectedTab=key;renderControls();}},label)));
    let fields;
    if(selectedTab==='top'||selectedTab==='bottom')fields=basicRibbon(selectedTab);
    else if(selectedTab==='motion')fields=[h('h3',null,'Content & motion'),
      checkbox('Reserve space for ribbons',draft.reserve_space,v=>{draft.reserve_space=v;changed();}),
      h('p',{class:'note'},'Fit the content between active ribbons rather than cover the top or bottom of a slide.'),
      field('Slide transition',select([['cut','Cut'],['fade','Crossfade'],['slide-left','Slide left'],['slide-up','Slide up']],draft.transition.effect,v=>{draft.transition.effect=v;changed();})),
      number('Transition duration',draft.transition,'duration_ms',0,2000,50,' ms'),
      h('button',{class:'btn',onclick:transitionPreview},'Preview transition'),
      checkbox('Reduce motion on this stream',draft.reduced_motion,v=>{draft.reduced_motion=v;changed();}),
      h('p',{class:'note'},'Stops scrolling and slide animation; animated graphics use their still poster. Device reduced-motion preferences are also respected.'),
      h('p',{class:'note'},'Slide dwell time stays in the rotation settings. These transitions do not restore animations inside a PowerPoint deck.')];
    else fields=[h('h3',null,'Clock artwork'),field('Clock time zone',h('input',{value:draft.screensaver.timezone||'',placeholder:'Device local time',onchange:e=>{draft.screensaver.timezone=e.target.value.trim()||null;changed();}}),'Leave empty to use the display’s time zone.'),chooseGraphic(draft.screensaver),number('Artwork width',draft.screensaver,'width_pct',5,60,1,'%'),
      field('Position',select([['center','Above the clock'],['top-left','Top left'],['top-right','Top right'],['bottom-left','Bottom left'],['bottom-right','Bottom right']],draft.screensaver.position,v=>{draft.screensaver.position=v;changed();})),
      number('Artwork opacity',draft.screensaver,'opacity',0,100,5,'%'),
      h('p',{class:'note'},'Shown with the built-in clock when the Screensaver rotation is empty. To show a graphic full-screen, place it in the Screensaver rotation from the Library.')];
    controls.replaceChildren(...fields.filter(Boolean));
  }
  async function uploadGraphic(file) {
    if(!file)return;
    if(!/\.(png|svg|gif)$/i.test(file.name)||file.size>(/\.svg$/i.test(file.name)?2:32)*1048576) {error.textContent='Choose a PNG, SVG or GIF up to 32 MB (SVG: 2 MB).';return;}
    const form=new FormData();form.append('file',file);
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),120000);
    try {
      status.textContent='Uploading graphic…';
      const response=await fetch('/api/graphics',{method:'POST',headers:{'X-Signage':'1'},body:form,credentials:'same-origin',signal:controller.signal});
      const data=await response.json();if(!response.ok)throw new Error(typeof data.detail==='string'?data.detail:'Graphic upload failed');
      status.textContent='Graphic queued for conversion';toast('Graphic uploaded. Use Refresh graphics after conversion finishes.');
    }catch(e){error.textContent=e.name==='AbortError'?'Upload timed out. Check the Library before retrying.':e.message;}
    finally{clearTimeout(timer);}
  }
  const graphicInput=h('input',{type:'file',accept:'.png,.svg,.gif',hidden:true,onchange:e=>{const file=e.target.files[0];e.target.value='';return uploadGraphic(file);}});
  const presetBar=h('div',{class:'presentation-presets'},presetSelect,
    h('button',{class:'btn small',onclick:()=>{
      const preset=presets.find(p=>String(p.id)===presetSelect.value);if(!preset)return;
      draft=clone(preset.settings);
      for(const side of ['top','bottom']){draft[side].start_at=null;draft[side].end_at=null;draft[side].target_at=null;}
      renderControls();changed();toast('Preset loaded into the draft. Schedule and countdown dates were cleared.');
    }},'Load'),
    h('button',{class:'btn small',onclick:()=>{saveName.hidden=!saveName.hidden;if(!saveName.hidden)name.focus();}},'Save as preset'),
    h('button',{class:'link danger',onclick:async()=>{
      const preset=presets.find(p=>String(p.id)===presetSelect.value);if(!preset)return;
      // Explicit second action prevents accidental removal; streams keep their own copied settings.
      const confirmation=h('div',null,`Delete “${preset.name}”? Live stream settings will not change.`);
      modal({title:'Delete preset',body:confirmation,actions:[close=>h('button',{class:'btn',onclick:close},'Cancel'),close=>h('button',{class:'btn danger',onclick:async()=>{await api('DELETE',`/api/presentation/presets/${preset.id}`);await reloadPresets();close();}},'Delete preset')]});
    }},'Delete'));
  const left=h('div',{class:'presentation-monitor'},h('div',{class:'presentation-preview-label'},h('strong',null,'Stream preview'),status),preview,
    h('div',{class:'presentation-preview-options'},select([['rotation','Content'],['clock','Screensaver clock']],previewMode,v=>{previewMode=v;updatePreview();}),
      h('span',{class:'m'},'Same player renderer · not published')),
    warning,h('div',{class:'presentation-asset-actions'},h('button',{class:'btn small',onclick:()=>graphicInput.click()},'Upload graphic'),
      h('button',{class:'link',onclick:async()=>{const fresh=await api('GET','/api/overview');uploads=fresh.uploads;renderControls();updatePreview();toast('Graphics refreshed');}},'Refresh graphics'),graphicInput),
    h('p',{class:'note'},'PNG transparency · static SVG · animated GIF. Graphics are processed before use.'),
    presetBar,saveName,error);
  const right=h('div',{class:'presentation-inspector'},tabs,controls);
  const dialog=modal({title:`Banners & motion · ${stream.name}`,wide:true,body:h('div',{class:'presentation-editor'},left,right),
    onClose:()=>{closed=true;renderer?.destroy();clearInterval(clockTimer);},
    actions:[close=>h('button',{class:'btn',onclick:close},'Cancel'),
      ()=>h('button',{class:'btn',onclick:()=>{draft.top.enabled=false;draft.bottom.enabled=false;renderControls();changed();}},'Disable both ribbons'),
      close=>h('button',{class:'btn primary',onclick:async()=>{
        error.textContent='';
        try {
          const result=await api('PUT',`/api/streams/${stream.id}/presentation`,{expected_revision:revision,settings:draft});
          revision=result.revision;close();await onSave();toast('Stream updated. Playback URL unchanged.');
        }catch(e){error.textContent=e.message;}
      }},'Apply to stream')]
  });
  dialog.box.classList.add('presentation-dialog');
  renderer=window.SignageOverlay.create(overlayRoot,now,insets=>{previewContent.style.top=insets.top+'%';previewContent.style.bottom=insets.bottom+'%';});
  function updateClock(){if(closed)return;const date=new Date(now());const zone=draft.screensaver.timezone?{timeZone:draft.screensaver.timezone}:{};try{previewClock.firstChild.textContent=date.toLocaleTimeString('en-US',{hour:'2-digit',minute:'2-digit',hour12:overview.clock_24h===false,...zone});previewClock.lastChild.textContent=date.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric',...zone});}catch(_){previewClock.firstChild.textContent='Check time zone';}}
  const clockTimer=setInterval(updateClock,1000);updateClock();
  renderControls();updatePreview();
}
