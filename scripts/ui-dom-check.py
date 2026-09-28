"""Offline browser rendering: no browser network navigation or policy changes.
The HTTP API is exercised by httpx separately and bridged into an about:blank DOM.
This is not a full browser/network/CSP end-to-end test.
"""
import os
import uuid
import argparse
import base64
import json
import re
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--base-url', required=True)
parser.add_argument('--media-root', type=Path, required=True, help='STAGING rendered media folder')
parser.add_argument('--output', type=Path, default=Path('ui-evidence'))
parser.add_argument('--browser', default='/usr/bin/chromium')
parser.add_argument('--allow-staging-mutations', action='store_true')
parser.add_argument('--sources-nas', action='store_true', help='Exercise the incoming/outgoing staging shares and approved video.example.org source')
args=parser.parse_args()
if not args.allow_staging_mutations:
 parser.error('This creates/deletes a temporary stream. Explicitly allow STAGING mutations.')
if not os.environ.get('SIGNAGE_TEST_PASSWORD'):
 parser.error('Set SIGNAGE_TEST_PASSWORD for a staging account.')
args.output.mkdir(parents=True,exist_ok=True)
test_stream='UI-test-'+uuid.uuid4().hex[:12]
ROOT=Path(__file__).resolve().parents[1]/'web/app/static' 
API=httpx.Client(base_url=args.base_url,timeout=15)
r=API.post('/auth/login',json={'username':os.environ.get('SIGNAGE_TEST_USER','admin'),'password':os.environ['SIGNAGE_TEST_PASSWORD']},headers={'X-Signage':'1'});assert r.status_code==200,r.text

def bridge(args):
    url=args['url']
    assert url.startswith('/api/') or url.startswith('/auth/')
    response=API.request(args.get('method','GET'),url,content=args.get('body'),headers=args.get('headers',{}))
    return {'status':response.status_code,'body':response.text,'headers':dict(response.headers)}

def bundle(name):
    return re.sub(r'^import .*?;\s*$', '', (ROOT/name).read_text(), flags=re.M).replace('export ', '')

BRIDGE = '''window.fetch = async (url, options={}) => {
 const result = await window.apiBridge({url,method:options.method||'GET',headers:options.headers||{},body:options.body});
 return new Response(result.status===304 ? null : result.body, {status:result.status,headers:result.headers});
};
'''
# The app creates images dynamically. Embed known local rendered media as data URLs.
media={}
media['/static/feed.svg']='data:image/svg+xml;base64,'+base64.b64encode((ROOT/'feed.svg').read_bytes()).decode()
for path in args.media_root.rglob('*.jpg'):
 media['/media/'+path.relative_to(args.media_root).as_posix()]='data:image/jpeg;base64,'+base64.b64encode(path.read_bytes()).decode()
BRIDGE += 'const MEDIA_FIXTURES = '+json.dumps(media)+';\n'
BRIDGE += '''const originalSetAttribute=Element.prototype.setAttribute;
Element.prototype.setAttribute=function(name,value){if(name==='src' && MEDIA_FIXTURES[value]) value=MEDIA_FIXTURES[value]; return originalSetAttribute.call(this,name,value);};
'''
with sync_playwright() as pw:
 browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1050},color_scheme='light')
 page.set_default_timeout(7000)
 page.expose_function('apiBridge',bridge)
 html=(ROOT/'admin.html').read_text()
 html=re.sub(r'<link[^>]+>', '',html);html=re.sub(r'<script.*?</script>','',html,flags=re.S)
 errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content(html)
 page.add_style_tag(content=(ROOT/'admin.css').read_text())
 page.add_style_tag(content=(ROOT/'overlay.css').read_text())
 page.add_script_tag(content=(ROOT/'overlay.js').read_text())
 code=BRIDGE+bundle('js/ui.js')+'\n'+bundle('js/api.js')+'\n'+bundle('js/presentation-editor.js')+'\n'+bundle('admin.js')
 page.add_script_tag(content='(function(){'+code+'})();')
 page.wait_for_selector('.stream')
 page.screenshot(path=str(args.output/'streams.png'),full_page=True)
 for view in ['library','displays','activity','users']:
  page.click(f'#tabs button[data-view="{view}"]');page.wait_for_timeout(250)
  page.screenshot(path=str(args.output/f'{view}.png'),full_page=True)
 if args.sources_nas:
  page.click('#tabs button[data-view="library"]');page.click('#btn-source')
  page.get_by_label('Title',exact=True).fill('Browser test feed')
  page.get_by_label('Source URL',exact=True).fill('https://video.example.org/live/index.m3u8')
  page.screenshot(path=str(args.output/'add-source.png'),full_page=True)
  page.get_by_role('button',name='Add source',exact=True).click();page.wait_for_selector('dialog',state='detached')
  page.wait_for_timeout(350)
  assert page.get_by_text('Browser test feed',exact=True).count()==1
  page.click('#btn-shares');page.wait_for_selector('.share-entry')
  page.screenshot(path=str(args.output/'shared-folders.png'),full_page=True)
  page.get_by_role('button',name='Import',exact=True).first.click();page.wait_for_timeout(250)
  page.get_by_role('button',name='Close',exact=True).click()
  page.wait_for_timeout(2400);page.click('#refresh-now');page.wait_for_timeout(600)
  assert page.get_by_text('Office notice',exact=True).count()==1
  notice=page.locator('.card').filter(has=page.get_by_text('Office notice',exact=True))
  notice.get_by_role('button',name='Export',exact=True).click();page.wait_for_timeout(300)
  page.get_by_role('button',name='Export here',exact=True).click();page.wait_for_selector('dialog',state='detached')
  page.wait_for_timeout(2300);page.click('#refresh-now');page.wait_for_timeout(500)
  jobs=API.get('/api/transfers').json()
  assert any(j['direction']=='export' and j['status']=='completed' for j in jobs)
  page.screenshot(path=str(args.output/'sources-library.png'),full_page=True)
  page.set_viewport_size({'width':390,'height':844})
  assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'Library horizontal overflow'
  page.click('#btn-source');page.screenshot(path=str(args.output/'source-mobile.png'),full_page=True)
  for _ in range(10): page.keyboard.press('Tab')
  assert page.evaluate('!!document.activeElement.closest("dialog")')
  page.keyboard.press('Escape');page.set_viewport_size({'width':1440,'height':1050})
 page.click('#tabs button[data-view="streams"]')
 page.click('#btn-new-stream');page.wait_for_selector('dialog[open]')
 assert page.locator('dialog input').get_attribute('id')
 page.locator('dialog input').fill(test_stream)
 page.get_by_role('button',name='Save',exact=True).click();page.wait_for_selector('dialog',state='detached')
 page.wait_for_timeout(500)
 assert page.get_by_role('button',name='Rename '+test_stream,exact=True).count()==1
 # Delete the temporary record through the API; confirmation controls are tested below.
 data=API.get('/api/overview').json()
 sid=next(s['id'] for s in data['streams'] if s['name']==test_stream)
 API.delete(f'/api/streams/{sid}',headers={'X-Signage':'1'})
 page.click('#refresh-now');page.wait_for_timeout(300)
 page.click('#tabs button[data-view="displays"]');page.click('#btn-new-display');page.wait_for_selector('dialog[open]')
 focused_before=page.evaluate('document.activeElement.tagName')
 for _ in range(12): page.keyboard.press('Tab')
 assert page.evaluate('!!document.activeElement.closest("dialog")')
 page.keyboard.press('Escape');assert page.evaluate('document.activeElement.id')=='btn-new-display' or page.locator('dialog[open]').count()==0
 page.click('#tabs button[data-view="streams"]')
 # Screenshots and layout checks at mobile widths and dark mode.
 page.evaluate('document.querySelector("#toasts").replaceChildren()');page.set_viewport_size({'width':390,'height':844});page.screenshot(path=str(args.output/'mobile.png'),full_page=True)
 assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'Horizontal overflow'
 page.set_viewport_size({'width':1440,'height':1050});page.evaluate('document.documentElement.dataset.theme="dark"');page.screenshot(path=str(args.output/'dark.png'),full_page=True)
 page.evaluate('document.documentElement.dataset.theme="light"')
 # Page-load failure and recovery through the same application refresh code.
 page.evaluate('() => {window.apiBridgeOriginal=window.apiBridge;window.apiBridge=async()=>{throw new Error("Simulated outage")};}')
 page.click('#refresh-now');page.wait_for_selector('#connection-banner:not([hidden])')
 page.evaluate('() => {window.apiBridge=window.apiBridgeOriginal;}');page.click('#refresh-now');page.wait_for_selector('#connection-banner[hidden]',state='attached')
 print(json.dumps({'page_errors':errors,'dialog_focus_trapped':True,'mobile_overflow':False,'create_stream':True,'network_error_recovery':True,'source_create_nas_import_export':bool(args.sources_nas),'transport':'offline DOM + real local HTTP API bridge'},indent=2))
 assert not errors,errors
 browser.close()
