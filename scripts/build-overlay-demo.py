"""Build a single-file, offline banner-editor demo from the browser test fixtures.
Only fictional sample media and in-memory settings are included; no backend.
Run overlay-browser-check.py first. The application does not use this demo shim.
"""
import argparse
import json
import re
from pathlib import Path

root=Path(__file__).resolve().parents[1];static=root/'web/app/static'
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--fixture',type=Path,default=root/'docs/evidence/overlays-ui/demo-fixture.json')
p.add_argument('--output',type=Path,required=True)
a=p.parse_args();data=json.loads(a.fixture.read_text())

def bundle(path):
    return re.sub(r'^import .*?;\s*$', '', (static/path).read_text(), flags=re.M).replace('export ','')

script='const INITIAL='+json.dumps(data,separators=(',',':')).replace('<','\\u003c')+';\n'
script+=r'''
let demo=JSON.parse(JSON.stringify(INITIAL));let nextPreset=1000;
const imageSrc=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src');
const originalSet=Element.prototype.setAttribute;
Object.defineProperty(HTMLImageElement.prototype,'src',{get(){return imageSrc.get.call(this);},set(v){imageSrc.set.call(this,demo.media[v]||v);},configurable:true});
Element.prototype.setAttribute=function(k,v){return originalSet.call(this,k,k==='src'&&this.tagName==='IMG'?(demo.media[v]||v):v);};
window.fetch=async (url,options={})=>{
  let body={};try{body=JSON.parse(options.body||'{}');}catch(_){}
  const method=options.method||'GET',match=url.match(/^\/api\/streams\/(\d+)\/presentation$/);
  let result,status=200;
  if(url==='/api/overview')result={...demo.overview,server_time_ms:Date.now()};
  else if(match){
    const id=match[1];
    if(method==='GET')result=demo.presentations[id];
    else if(method==='PUT'){
      if(body.expected_revision!==demo.presentations[id].revision){status=409;result={detail:'Reopen the editor before saving.'};}
      else if(['top','bottom'].some(k=>body.settings[k].enabled&&body.settings[k].mode==='countdown'&&!body.settings[k].target_at)){
        status=422;result={detail:'Set a countdown target before applying.'};
      } else result=demo.presentations[id]={revision:body.expected_revision+1,settings:body.settings};
    }
  } else if(url==='/api/presentation/presets'){
    if(method==='GET')result=demo.presets;
    else{const record={id:nextPreset++,name:body.name,settings:body.settings};demo.presets.push(record);result={id:record.id};status=201;}
  } else if(url.startsWith('/api/presentation/presets/')&&method==='DELETE'){
    const id=Number(url.split('/').pop());demo.presets=demo.presets.filter(p=>p.id!==id);result={ok:true};
  } else {status=409;result={detail:'This offline preview does not upload or convert files. Use the installed portal.'};}
  return new Response(JSON.stringify(result||{}),{status,headers:{'Content-Type':'application/json'}});
};
'''.replace(r'\\/',r'\/').replace(r'\\d',r'\d')
script+='\n'+'\n'.join(bundle(n) for n in ['js/ui.js','js/api.js','js/presentation-editor.js'])
script+=r'''
const picker=document.querySelector('#demo-stream');
for(const stream of demo.overview.streams){const option=document.createElement('option');option.value=stream.id;option.textContent=stream.name;picker.appendChild(option);}
async function openEditor(){
  const stream=demo.overview.streams.find(s=>s.id===Number(picker.value));
  await presentationEditor(stream,{...demo.overview,server_time_ms:Date.now()},async()=>{});
  for(const button of document.querySelectorAll('dialog button'))if(button.textContent==='Upload graphic'){
    button.disabled=true;button.title='Uploads and conversion require the installed portal.';
  }
}
document.querySelector('#demo-open').onclick=openEditor;
document.querySelector('#demo-theme').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='dark'?'light':'dark';};
document.querySelector('#demo-reset').onclick=()=>{demo=JSON.parse(JSON.stringify(INITIAL));toast('Demo settings reset.');};
openEditor();
'''
css=(static/'admin.css').read_text()+'\n'+(static/'overlay.css').read_text()+'''
body {display:block;min-height:100vh;padding:clamp(18px,5vw,70px);box-sizing:border-box;}
.demo-shell {max-width:1040px;margin:auto;}
.demo-kicker {font-size:12px;text-transform:uppercase;letter-spacing:.13em;color:var(--muted);}
.demo-title {font-size:clamp(28px,4vw,48px);letter-spacing:-.045em;margin:18px 0;}
.demo-note {max-width:690px;color:var(--muted);font-size:15px;line-height:1.75;}
.demo-toolbar {display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:26px;}
.demo-toolbar select {width:auto;min-width:170px;}
'''
html='''<!doctype html><html lang="en" data-theme="light"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Signage · Banner editor preview</title><style>'''+css+'''</style></head><body><section class="demo-shell"><div class="demo-kicker">Signage / Stream studio</div><h1 class="demo-title">One stream. Two ribbons. Your message.</h1><p class="demo-note">Interactive, offline preview of the actual banner editor and overlay renderer. Try text, scrolling, clocks, countdowns, colors, graphics presets, and motion. Sample artwork is included. Changes last only for this open page; nothing is sent to a server or office display. Uploading and conversion require the installed portal.</p><div class="demo-toolbar"><select id="demo-stream" aria-label="Demo stream"></select><button class="btn primary" id="demo-open">Open banner editor</button><button class="btn" id="demo-theme">Light / dark</button><button class="btn" id="demo-reset">Reset demo</button></div></section><div id="modal-root"></div><div id="toasts" role="status"></div><script>'''+(static/'overlay.js').read_text()+'</script><script>'+script.replace('</script','<\\/script')+'</script></body></html>'
a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(html)
print(f'Created {a.output} ({a.output.stat().st_size:,} bytes)')
