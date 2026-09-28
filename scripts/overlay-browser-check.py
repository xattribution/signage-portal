"""Offline browser DOM + real in-process FastAPI API checks, in disposable storage.
Native browser networking is unavailable in the authoring environment. This does
not establish actual HTTP/CSP/TLS/codec or physical display compatibility.
"""
import argparse
import base64
import io
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output',type=Path,default=Path('overlay-browser-evidence'))
args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
ROOT=Path(__file__).resolve().parents[1];STATIC=ROOT/'web/app/static'
tmp=tempfile.TemporaryDirectory(prefix='signage-overlay-browser-')
os.environ.update(DATA_DIR=tmp.name+'/data',MEDIA_DIR=tmp.name+'/media',ALLOW_INSECURE_DEV='true',COOKIE_SECURE='false',AUTH_MODE='local',ADMIN_PASSWORD='temporary-browser-fixture-only-123',ALLOWED_HOSTS='localhost,127.0.0.1,testserver',INGEST_ENABLED='false',TRANSFERS_ENABLED='false')
sys.path[:0]=[str(ROOT/'web'),str(ROOT/'worker')]
from fastapi.testclient import TestClient
from app.main import app
from app import config,db,jobs
from app.presentation import Presentation
from graphics import render_graphic
API=TestClient(app,base_url='http://localhost')
W={'X-Signage':'1'}
assert API.post('/auth/login',json={'username':'admin','password':os.environ['ADMIN_PASSWORD']},headers=W).status_code==200

def ready(data,name,graphic=False,title='Office content'):
    r=API.post('/api/graphics' if graphic else '/api/uploads',files={'file':(name,data)},data={'title':title},headers=W);assert r.status_code==201,r.text
    uid=r.json()['id'];up=db.row('SELECT * FROM uploads WHERE id=?',(uid,));out=config.WORK_DIR/str(uid);out.mkdir()
    if graphic:slides=render_graphic(config.ORIGINALS_DIR/up['original_file'],out)
    else:
        (out/'000.jpg').write_bytes(data)
        im=Image.open(io.BytesIO(data));im.thumbnail((480,270));im.save(out/'000_t.jpg','JPEG')
        slides=[{'kind':'image','file':'000.jpg','thumb':'000_t.jpg','duration_ms':None}]
    (out/'result.json').write_text(json.dumps({'job_token':up['job_token'],'slides':slides}));jobs._ingest(uid)
    assert db.row('SELECT status FROM uploads WHERE id=?',(uid,))['status']=='ready'
    return uid

# Fictional office content; system fonts are rasterized into the screenshot, never distributed.
im=Image.new('RGB',(1920,1080),'#eeeae3');d=ImageDraw.Draw(im)
def font(size,bold=False):
    name='/usr/share/fonts/truetype/dejavu/DejaVuSans'+('-Bold' if bold else '')+'.ttf'
    return ImageFont.truetype(name,size)
d.rectangle((0,0,24,1080),fill='#b06a3a')
d.text((145,155),'RECEPTION  /  OFFICE ANNOUNCEMENTS',font=font(25),fill='#68665e')
d.text((140,335),'Good work starts\nwith good company.',font=font(83,True),fill='#1c2b33',spacing=20)
d.line((145,660,1770,660),fill='#d0cbc1',width=2)
d.text((145,720),'ALL-HANDS BRIEFING',font=font(24,True),fill='#1c2b33')
d.text((145,774),'Conference room A  ·  14:00',font=font(33),fill='#585a57')
d.text((145,926),'Sample content — not a live office notice',font=font(21),fill='#73716a')
blob=io.BytesIO();im.save(blob,'JPEG',quality=90);uid=ready(blob.getvalue(),'office.jpg',title='Office briefing')
logo=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="0" y="0" width="27" height="27" rx="5" fill="#ffffff"/><rect x="37" y="0" width="27" height="27" rx="5" fill="#ffffff"/><rect x="0" y="37" width="27" height="27" rx="5" fill="#ffffff"/><rect x="37" y="37" width="27" height="27" rx="5" fill="#ffffff"/></svg>'
gid=ready(logo,'office-mark.svg',True,'Office mark')
animation=io.BytesIO();frames=[Image.new('RGBA',(32,32),c) for c in ('#c6713b','#324958','#eeeae3')]
frames[0].save(animation,'GIF',save_all=True,append_images=frames[1:],duration=[200,200,200],loop=0)
animated_id=ready(animation.getvalue(),'animated-mark.gif',True,'Animated mark')
assert API.patch('/api/streams/1',headers=W,json={'name':'Reception'}).status_code==200
assert API.post('/api/placements',headers=W,json={'upload_id':uid,'stream_ids':[1],'mode':'rotation','slide_seconds':20}).status_code==200
cfg=Presentation().model_dump();cfg['top'].update(enabled=True,text='EX EX EX  ·  EXERCISE IN PROGRESS',background='#885025',height_pct=7,font_pct=3.0,asset_id=gid)
cfg['bottom'].update(enabled=True,mode='ticker',text='Office announcements continue  ·  This is an exercise  ·  Follow local instructions',height_pct=7,font_pct=3.0)
cfg['reserve_space']=True
assert API.put('/api/streams/1/presentation',headers=W,json={'expected_revision':0,'settings':cfg}).status_code==200
assert API.post('/api/presentation/presets',headers=W,json={'name':'Exercise ribbon','settings':cfg}).status_code==201
key=db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key']
media={}
for f in config.RENDERED_DIR.rglob('*'):
    if f.is_file() and f.suffix in ('.jpg','.png','.gif'):
        mime={'.jpg':'image/jpeg','.png':'image/png','.gif':'image/gif'}[f.suffix]
        media['/media/'+f.relative_to(config.RENDERED_DIR).as_posix()]='data:'+mime+';base64,'+base64.b64encode(f.read_bytes()).decode()

def bridge(req):
    assert req['url'].startswith(('/api/','/auth/'))
    r=API.request(req.get('method','GET'),req['url'],content=req.get('body'),headers=req.get('headers',{}))
    return {'status':r.status_code,'body':r.text,'headers':dict(r.headers)}

def bundle(path):
    return re.sub(r'^import .*?;\s*$', '', (STATIC/path).read_text(), flags=re.M).replace('export ','')

image_bridge='''(function(){
 const map=MEDIA, src=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src');
 const get=Element.prototype.getAttribute,set=Element.prototype.setAttribute,remove=Element.prototype.removeAttribute;
 Object.defineProperty(HTMLImageElement.prototype,'src',{get(){return src.get.call(this);},set(v){this._localFixtureURL=v;src.set.call(this,map[v]||v);},configurable:true});
 Element.prototype.setAttribute=function(k,v){if(k==='src'&&this.tagName==='IMG'){this._localFixtureURL=v;v=map[v]||v;}return set.call(this,k,v);};
 Element.prototype.getAttribute=function(k){return k==='src'&&this._localFixtureURL?this._localFixtureURL:get.call(this,k);};
 Element.prototype.removeAttribute=function(k){if(k==='src')delete this._localFixtureURL;return remove.call(this,k);};
})();'''.replace('MEDIA',json.dumps(media))
api_bridge='''window.fetch=async(url,options={})=>{const r=await window.apiBridge({url,method:options.method||'GET',headers:options.headers||{},body:options.body});return new Response(r.status===304?null:r.body,{status:r.status,headers:r.headers});};'''
# Export only fictional, non-secret fixture fields for the optional offline editor demo.
_overview=API.get('/api/overview').json()
_fixture={
  'overview':{'streams':[{k:v for k,v in st.items() if k in {'id','name','kind','now_thumb'}} for st in _overview['streams']],
              'uploads':[{k:v for k,v in up.items() if k in {'id','title','kind','status','slides'}} for up in _overview['uploads']],
              'server_time_ms':db.now_ms(),'clock_24h':True},
  'presentations':{str(st['id']):API.get(f"/api/streams/{st['id']}/presentation").json() for st in _overview['streams']},
  'presets':API.get('/api/presentation/presets').json(),'media':media}
(args.output/'demo-fixture.json').write_text(json.dumps(_fixture,separators=(',',':')))
result={'scope':'offline Chromium DOM; real in-process FastAPI API; synthetic time and media transport. No native network/CSP/TLS or physical-device assertion.'}
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1050},color_scheme='light');page.set_default_timeout(7000)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.expose_function('apiBridge',bridge)
    html=re.sub(r'<script.*?</script>','',(STATIC/'admin.html').read_text(),flags=re.S);html=re.sub(r'<link[^>]+>','',html)
    page.set_content(html);page.add_style_tag(content=(STATIC/'admin.css').read_text());page.add_style_tag(content=(STATIC/'overlay.css').read_text());page.add_script_tag(content=(STATIC/'overlay.js').read_text())
    page.add_script_tag(content=image_bridge+api_bridge+'(function(){'+ '\n'.join(bundle(n) for n in ['js/ui.js','js/api.js','js/presentation-editor.js','admin.js'])+'})();')
    page.wait_for_selector('.stream')
    page.get_by_role('button',name='Banners & motion',exact=True).first.click();page.wait_for_selector('.presentation-dialog')
    page.wait_for_timeout(400)
    assert 'EXERCISE IN PROGRESS' in page.locator('.signage-ribbon-top').inner_text()
    page.screenshot(path=str(args.output/'editor-desktop.png'),full_page=True)
    page.evaluate('document.documentElement.dataset.theme="dark"');page.screenshot(path=str(args.output/'editor-dark.png'),full_page=True)
    page.evaluate('document.documentElement.dataset.theme="light"')
    # Cancel is a draft operation; applying is an authenticated persisted write.
    page.get_by_label('Message',exact=True).fill('DRAFT NOT PUBLISHED')
    assert API.get('/api/streams/1/presentation').json()['settings']['top']['text']!= 'DRAFT NOT PUBLISHED'
    page.get_by_role('button',name='Cancel',exact=True).click()
    page.get_by_role('button',name='Banners & motion',exact=True).first.click();page.wait_for_selector('.presentation-dialog')
    page.get_by_role('button',name='Bottom',exact=True).click()
    page.get_by_label('Content',exact=True).select_option('countdown')
    page.get_by_label('Countdown target',exact=False).fill('2030-01-01T14:00')
    page.get_by_label('Label (optional)',exact=True).fill('Next briefing')
    page.get_by_role('button',name='Apply to stream',exact=True).click();page.wait_for_selector('dialog',state='detached')
    saved=API.get('/api/streams/1/presentation').json();assert saved['settings']['bottom']['mode']=='countdown';assert saved['settings']['bottom']['target_at']>db.now_ms()
    result['draft_cancel_and_persisted_countdown']=True
    page.get_by_role('button',name='Banners & motion',exact=True).first.click();page.wait_for_selector('.presentation-dialog')
    page.get_by_role('button',name='Layout & motion',exact=True).click()
    page.get_by_label('Slide transition',exact=True).select_option('slide-left')
    page.get_by_role('button',name='Preview transition',exact=True).click()
    page.screenshot(path=str(args.output/'motion-controls.png'),full_page=True)
    page.get_by_role('button',name='Save as preset',exact=True).click();page.get_by_label('Preset name',exact=True).fill('Briefing countdown')
    page.get_by_role('button',name='Save preset',exact=True).click();page.wait_for_timeout(150)
    assert any(v['name']=='Briefing countdown' for v in API.get('/api/presentation/presets').json())
    page.get_by_role('button',name='Load',exact=True).click()
    page.get_by_role('button',name='Bottom',exact=True).click()
    assert page.get_by_label('Countdown target',exact=False).input_value()==''
    result['preset_saved_and_old_dates_cleared_on_load']=True
    page.set_viewport_size({'width':390,'height':844});page.get_by_role('button',name='Top',exact=True).click()
    page.screenshot(path=str(args.output/'editor-mobile.png'),full_page=True)
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),'Page overflow'
    assert not page.evaluate('document.querySelector(".presentation-dialog").scrollWidth>document.querySelector(".presentation-dialog").clientWidth'),'Dialog overflow'
    for _ in range(12):page.keyboard.press('Tab')
    assert page.evaluate('!!document.activeElement.closest("dialog")')
    result['mobile_no_horizontal_overflow_and_focus_contained']=True
    page.keyboard.press('Escape');page.set_viewport_size({'width':1440,'height':1050})
    # Restored fixture used for the playback screenshot and motion tests.
    rev=API.get('/api/streams/1/presentation').json()['revision'];assert API.put('/api/streams/1/presentation',headers=W,json={'expected_revision':rev,'settings':cfg}).status_code==200
    manifest=API.get('/api/player/channel/'+key).json();manifest['poll_seconds']=2
    player=browser.new_page(viewport={'width':1920,'height':1080},color_scheme='light')
    player.on('pageerror',lambda e:errors.append(str(e)))
    player_html=re.sub(r'<script.*?</script>','',(STATIC/'player.html').read_text(),flags=re.S);player_html=re.sub(r'<link[^>]+>','',player_html)
    player.set_content(player_html);player.add_style_tag(content=(STATIC/'player.css').read_text());player.add_style_tag(content=(STATIC/'overlay.css').read_text())
    player.add_script_tag(content=image_bridge+(STATIC/'overlay.js').read_text())
    player.add_script_tag(content='window.manifest='+json.dumps(manifest)+''';window.clockJump=0;window.originalNow=Date.now;Date.now=()=>originalNow()+clockJump;
      window.fetch=async()=>({ok:true,status:200,json:async()=>({...window.manifest,server_time_ms:Date.now()})});
      HTMLMediaElement.prototype.play=function(){return Promise.resolve();};HTMLMediaElement.prototype.pause=function(){};HTMLMediaElement.prototype.load=function(){};
    ''')
    code=(STATIC/'player.js').read_text().replace(r'var path = location.pathname.replace(/\/+$/, "");','var path="/stream/'+key+'";')
    player.add_script_tag(content=code);player.wait_for_timeout(700)
    assert player.locator('#stage img.on').count()==1
    assert player.locator('#overlays .signage-ribbon-top').is_visible();assert player.locator('#overlays .signage-ribbon-bottom').is_visible()
    assert player.locator('#stage').evaluate('(el)=>el.style.top')=='7%'
    tr1=player.locator('.signage-ribbon-bottom .signage-ribbon-text').evaluate('(el)=>getComputedStyle(el).transform');player.wait_for_timeout(350)
    tr2=player.locator('.signage-ribbon-bottom .signage-ribbon-text').evaluate('(el)=>getComputedStyle(el).transform');assert tr1!=tr2
    player.screenshot(path=str(args.output/'player-ribbons.png'))
    result['actual_ticker_transform_and_reserved_content_space']=True
    # Plain text cannot create browser nodes or trigger handler code.
    player.evaluate('window.manifest.presentation.settings.top.text="<img src=x onerror=alert(1)>";window.manifest.presentation.version="test-xss";window.firstImage=document.querySelector("#stage img.on")')
    player.wait_for_timeout(2200)
    assert '<img' in player.locator('.signage-ribbon-top').inner_text()
    assert player.locator('.signage-ribbon-top .signage-ribbon-text img').count()==0
    assert player.evaluate('document.querySelector("#stage img.on")===window.firstImage')
    result['plain_text_no_dom_injection_and_image_not_restarted']=True
    # Simulated local video object stays alive when only the appearance changes.
    player.evaluate('window.manifest.version="local-video";window.manifest.items=[{slide_id:900,kind:"video",url:"/media/999/000.mp4",duration_ms:1000000}];window.manifest.anchor_ms=Date.now()')
    player.wait_for_selector('#stage video.on');player.evaluate('window.firstVideo=document.querySelector("#stage video.on");window.manifest.presentation.settings.top.text="DEFCON 3 · EXERCISE"')
    player.wait_for_timeout(2200)
    assert player.evaluate('document.querySelector("#stage video.on")===window.firstVideo')
    result['overlay_edit_keeps_existing_video_element']=True
    # Absolute expiry/countdown are enforced by renderer without a successful poll.
    player.evaluate('window.manifest.presentation.settings.top.end_at=Date.now()+5000;window.manifest.presentation.settings.bottom.mode="countdown";window.manifest.presentation.settings.bottom.target_at=Date.now()+5000;window.manifest.presentation.settings.bottom.zero="message";window.manifest.presentation.settings.bottom.done_text="Starting now"')
    player.wait_for_timeout(2300)
    player.evaluate('window.fetch=async()=>{throw new Error("offline fixture")};window.clockJump+=10000')
    player.wait_for_timeout(1200)
    assert player.locator('.signage-ribbon-top').is_hidden()
    assert 'Starting now' in player.locator('.signage-ribbon-bottom').inner_text()
    assert player.locator('#stage').evaluate('(el)=>el.style.top')=='0%'
    result['absolute_countdown_and_banner_expiry_while_offline']=True
    # Reduced motion follows both stream settings and the OS preference.
    player.evaluate('window.manifest.presentation.settings.top.end_at=null;window.manifest.presentation.settings.bottom.mode="ticker";window.manifest.presentation.settings.reduced_motion=true;window.fetch=async()=>({ok:true,status:200,json:async()=>({...window.manifest,server_time_ms:Date.now()})})')
    player.wait_for_timeout(2300)
    assert player.locator('.is-ticker').count()==0
    result['reduced_motion_disables_ticker']=True
    player.set_viewport_size({'width':3840,'height':2160});player.wait_for_timeout(150)
    assert abs(player.locator('.signage-ribbon-bottom').bounding_box()['height']-2160*.07)<1
    result['4k_proportional_ribbon_height']=True
    # Inspect the real Web Animations transition and release of the outgoing frame.
    first=manifest['items'][0]
    player.evaluate('''(item)=>{window.manifest.presentation.settings.reduced_motion=false;
      window.manifest.presentation.settings.transition={effect:'slide-left',duration_ms:1200};
      window.manifest.version='transition-check';window.manifest.items=[{...item,slide_id:501,duration_ms:100000}];
      window.manifest.anchor_ms=Date.now();}''',first)
    player.wait_for_selector('#stage img.on[data-slide="501"]')
    assert player.evaluate("document.querySelector('#stage img.on[data-slide=\"501\"]')._transition.effect.getTiming().duration")==1200
    assert 'translateX(100%)' in player.evaluate("document.querySelector('#stage img.on[data-slide=\"501\"]')._transition.effect.getKeyframes()[0].transform")
    player.wait_for_timeout(1400);assert player.locator('#stage video').count()==0
    result['slide_transition_duration_and_outgoing_cleanup']=True
    # A graphic in the full-screen rotation switches to its poster in reduced motion.
    animated=db.row('SELECT * FROM slides WHERE upload_id=?',(animated_id,))
    gif_item={'slide_id':502,'kind':'image','url':'/media/'+animated['file'],'thumb':'/media/'+animated['thumb'],'duration_ms':100000}
    player.evaluate('''item=>{window.manifest.version='gif-check';window.manifest.items=[item];window.manifest.anchor_ms=Date.now();}''',gif_item)
    player.wait_for_selector('#stage img.on[data-slide="502"]')
    assert player.locator('#stage img.on[data-slide="502"]').evaluate('(el)=>el.getAttribute("src")').endswith('.gif')
    player.wait_for_function("Array.from(document.images).some(i=>i.dataset.slide==='502' && i.naturalWidth>0)")
    player.evaluate('window.manifest.presentation.settings.reduced_motion=true')
    player.wait_for_function('document.querySelector("#stage img.on").getAttribute("src").endsWith("_t.jpg")')
    result['gif_load_and_playlist_reduced_motion_poster']=True
    # Artwork and clock on the empty Screensaver stream, using its real public state.
    ss=db.row("SELECT id FROM streams WHERE kind='screensaver'")['id']
    ss_cfg=Presentation().model_dump();ss_cfg['screensaver'].update(asset_id=gid,width_pct=15,timezone='Pacific/Honolulu')
    ss_cfg['bottom'].update(enabled=True,mode='clock',timezone='Pacific/Honolulu',text='Office time',show_date=True)
    assert API.put(f'/api/streams/{ss}/presentation',headers=W,json={'expected_revision':0,'settings':ss_cfg}).status_code==200
    ss_state=API.get(f'/api/player/stream/{ss}').json();ss_state['poll_seconds']=2
    player.evaluate('value=>window.manifest=value',ss_state)
    player.set_viewport_size({'width':1920,'height':1080})
    player.wait_for_selector('.signage-screensaver-art:not([hidden])')
    player.wait_for_timeout(500);player.screenshot(path=str(args.output/'screensaver-artwork.png'))
    assert player.locator('#clock').is_visible();assert player.locator('#stage video').count()==0
    result['screensaver_clock_artwork']=True
    result['page_errors']=errors;assert not errors,errors
    print(json.dumps(result,indent=2));(args.output/'browser-results.json').write_text(json.dumps(result,indent=2)+'\n')
    browser.close()
API.close();tmp.cleanup()
