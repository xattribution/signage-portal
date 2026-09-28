"""Offline DOM control-flow check, NOT a codec, network, or Cast Pro test.
Uses the actual player source with a fixed test path and a fake HLS adapter.
"""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / 'web/app/static'
html = (ROOT / 'player.html').read_text().replace('<script src="/static/player.js"></script>', '')
code = (ROOT / 'player.js').read_text().replace(r'var path = location.pathname.replace(/\/+$/, "");', 'var path = "/stream/'+'a'*32+'";')
setup = r'''
window.testClockOffset=0;
const realNow=Date.now;
Date.now=()=>realNow()+window.testClockOffset;
window.adapterCreated=0;window.adapterDestroyed=0;
class FakeHls {
 static isSupported(){return true;}
 static Events={ERROR:'error',MANIFEST_PARSED:'manifest'};
 constructor(options){window.adapterCreated++;window.adapterOptions=options;this.handlers={};}
 on(name,cb){this.handlers[name]=cb;}
 loadSource(url){window.lastHlsSource=url;}
 attachMedia(el){window.attachedVideo=el;}
 destroy(){window.adapterDestroyed++;}
}
window.Hls=FakeHls;
HTMLMediaElement.prototype.canPlayType=()=>'';
HTMLMediaElement.prototype.play=function(){return Promise.resolve();};
HTMLMediaElement.prototype.pause=function(){};
HTMLMediaElement.prototype.load=function(){};
window.serverState={version:'v1',items:[{slide_id:1,kind:'hls',url:'https://video.example.org/live/index.m3u8',duration_ms:2000}],anchor_ms:0,mode:'rotation',media_origins:['https://video.example.org'],frame_origins:['https://board.example.org'],poll_seconds:2};
window.fetch=async function(url){window.endpointSeen=url;return {ok:true,status:200,json:async()=>({...window.serverState,server_time_ms:Date.now()})};};
'''
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    page = browser.new_page()
    errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
    page.set_content(html)
    page.add_style_tag(content=(ROOT/'overlay.css').read_text())
    page.add_script_tag(content=(ROOT/'overlay.js').read_text())
    page.add_script_tag(content=setup)
    page.add_script_tag(content=code)
    page.wait_for_timeout(300)
    assert page.evaluate('window.adapterCreated') == 1
    assert page.evaluate('window.endpointSeen').startswith('/api/player/channel/')
    assert page.evaluate('window.lastHlsSource') == 'https://video.example.org/live/index.m3u8'
    page.evaluate('window.firstVideo=document.querySelector("video");window.testClockOffset+=2500;')
    page.wait_for_timeout(300)
    assert page.evaluate('document.querySelector("video")===window.firstVideo')
    assert page.evaluate('window.adapterCreated') == 1
    assert page.evaluate('''()=>{try{window.adapterOptions.xhrSetup({},'https://evil.example/key');return false;}catch(e){return true;}}''')
    page.evaluate('window.serverState={...window.serverState,version:"v2",items:[{slide_id:2,kind:"web",url:"https://board.example.org/notice",duration_ms:10000}]};')
    page.wait_for_timeout(2400)
    frame=page.locator('#stage iframe'); assert frame.count()==1
    assert frame.get_attribute('sandbox')=='allow-scripts'
    assert frame.get_attribute('referrerpolicy')=='no-referrer'
    assert page.evaluate('window.adapterDestroyed') == 1
    # Retired channel response clears all content, never retains a stale cached broadcast.
    page.evaluate('window.fetch=async()=>({ok:false,status:404,json:async()=>({detail:"Not found"})});')
    page.wait_for_timeout(2300)
    assert page.locator('#stage > *').count()==0
    assert not errors, errors
    result={'channel_endpoint':True,'single_live_feed_not_rewound_or_recreated':True,'hls_child_origin_guard':True,'hls_destroy_on_switch':True,'iframe_sandbox':True,'deleted_channel_clears_content':True,'page_errors':errors,'scope':'offline DOM, mocked HLS engine and manifest; no decoding/network/physical hardware assertion'}
    print(json.dumps(result,indent=2))
    browser.close()
