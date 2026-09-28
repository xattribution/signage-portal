"""Overlay persistence, narrow assets, playback isolation, and validation regressions."""
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image
from app import config, db, jobs, playlist
from app.presentation import Presentation, public_settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'worker'))
from graphics import render_graphic, sanitize_svg

WRITE = {'X-Signage': '1'}


def settings():
    result = Presentation().model_dump()
    result['top'].update(enabled=True, text='EX EX EX', mode='ticker')
    result['bottom'].update(enabled=True, mode='clock', timezone='Pacific/Honolulu', text='Office time')
    result['reserve_space'] = True
    return result


def put(admin, body=None, rev=0, sid=1):
    return admin.put(f'/api/streams/{sid}/presentation', json={'expected_revision': rev, 'settings': body or settings()}, headers=WRITE)


def ready_graphic(admin, payload, ext='.png'):
    response = admin.post('/api/graphics', files={'file': ('logo'+ext, payload)}, headers=WRITE)
    assert response.status_code == 201, response.text
    uid = response.json()['id']
    row = db.row('SELECT * FROM uploads WHERE id=?', (uid,))
    out = config.WORK_DIR / str(uid);out.mkdir()
    source = config.ORIGINALS_DIR / row['original_file']
    slides = render_graphic(source, out)
    (out / 'result.json').write_text(json.dumps({'job_token': row['job_token'], 'slides': slides}))
    jobs._ingest(uid)
    assert db.row('SELECT status FROM uploads WHERE id=?', (uid,))['status'] == 'ready'
    return uid, db.row('SELECT * FROM slides WHERE upload_id=?', (uid,))


@pytest.fixture
def png():
    out = io.BytesIO(); im = Image.new('RGBA', (64, 32), (0, 0, 0, 0))
    im.paste((255, 80, 40, 255), (15, 10, 30, 25)); im.save(out, 'PNG')
    return out.getvalue()


@pytest.fixture
def gif():
    out = io.BytesIO()
    frames = []
    for pos in (4, 12, 24):
        frame = Image.new('RGBA', (40, 32), (0, 0, 0, 0))
        frame.paste((255, 0, 0, 255), (pos, 5, pos+6, 20));frames.append(frame)
    frames[0].save(out, 'GIF', save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=0, disposal=2)
    return out.getvalue()


def test_defaults_and_migration_idempotent(admin):
    response=admin.get('/api/streams/1/presentation')
    assert response.status_code==200
    assert response.json()['settings']['top']['enabled'] is False
    assert response.json()['revision']==0
    key=db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key']
    assert put(admin).status_code==200
    db.init();db.init()
    assert db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key']==key
    assert admin.get('/api/streams/1/presentation').json()['settings']==settings()


def test_overlay_update_does_not_change_playlist_identity(admin):
    initial=admin.get('/api/player/stream/1').json()
    assert put(admin).status_code==200
    current=admin.get('/api/player/stream/1').json()
    assert initial['version']==current['version']
    assert initial['presentation']['version']!=current['presentation']['version']
    assert current['presentation']['settings']['top']['text']=='EX EX EX'
    assert 'presentation_json' not in json.dumps(current)
    assert 'asset_id' not in json.dumps(current)


def test_revision_guard_and_audit(admin):
    assert put(admin).status_code==200
    assert put(admin).status_code==409
    changed=settings();changed['top']['text']='Office notice'
    assert put(admin, changed, rev=1).json()['revision']==2
    assert any('banners and motion' in r['what'] for r in db.rows('SELECT * FROM audit'))


def test_auth_and_write_guards(client, admin):
    assert admin.put('/api/streams/1/presentation',json={'expected_revision':0,'settings':settings()}).status_code==403
    client.cookies.clear()
    assert client.get('/api/streams/1/presentation').status_code==401
    assert client.get('/api/presentation/presets').status_code==401
    assert client.post('/api/graphics',files={'file':('x.png',b'x')},headers=WRITE).status_code==401


@pytest.mark.parametrize('path', ['/api/streams/1/presentation','/api/presentation/presets','/api/graphics'])
def test_player_host_blocks_editor(admin, monkeypatch, path):
    monkeypatch.setattr(config,'PLAYER_ONLY_HOSTS',['localhost'])
    assert admin.get(path).status_code==404
    assert admin.put(path,json={},headers=WRITE).status_code==404
    assert admin.get('/static/overlay.js').status_code==200
    assert admin.get('/static/overlay.css').status_code==200


@pytest.mark.parametrize('field,value',[
    ('background','red; background:url(https://evil.example)'),('foreground','<svg>'),
    ('height_pct',100),('height_pct',0),('font_pct',9),('speed',10000),('speed',True),
    ('mode','html'),('entry','flash'),('opacity',101),('opacity',-1),('enabled','yes'),
    ('text','x'*1001),('text','\x00hello'),('timezone','../../etc/passwd'),('target_at',-1),
    ('asset_id','1'),('asset_url','https://evil.example/logo.png')])
def test_rejects_unsafe_or_unbounded_settings(admin, field, value):
    body=settings();body['top'][field]=value
    assert put(admin, body).status_code==422


def test_countdown_requires_target_and_schedule_order(admin):
    body=settings();body['top']['mode']='countdown'
    assert put(admin, body).status_code==422
    body['top']['target_at']=db.now_ms()+60000
    body['top']['start_at']=db.now_ms()+90000
    body['top']['end_at']=db.now_ms()+30000
    assert put(admin, body).status_code==422
    body['top']['start_at']=None
    assert put(admin, body).status_code==200


def test_plain_text_not_a_template(admin):
    body=settings();body['top']['text']='<img src=x onerror=alert(1)> ${window.location}'
    assert put(admin, body).status_code==200
    assert admin.get('/api/player/stream/1').json()['presentation']['settings']['top']['text']==body['top']['text']


def test_presets_are_copies_not_live_links(admin):
    response=admin.post('/api/presentation/presets',json={'name':'Exercise','settings':settings()},headers=WRITE)
    assert response.status_code==201
    preset_id=response.json()['id']
    assert put(admin).status_code==200
    assert admin.post('/api/presentation/presets',json={'name':'exercise','settings':settings()},headers=WRITE).status_code==409
    assert admin.delete(f'/api/presentation/presets/{preset_id}',headers=WRITE).status_code==200
    assert admin.get('/api/streams/1/presentation').json()['settings']['top']['enabled']


def test_deadlines_do_not_replace_content_expiry(admin):
    body=settings();body['top']['end_at']=db.now_ms()+60000
    assert put(admin,body).status_code==200
    state=admin.get('/api/player/stream/1').json()
    assert state['next_change_ms'] is None
    assert state['presentation_change_ms']==body['top']['end_at']


def test_off_display_has_no_overlays(admin):
    put(admin)
    db.execute('UPDATE displays SET stream_id=NULL WHERE slug=?',('lobby-1',))
    state=admin.get('/api/player/display/lobby-1').json()
    assert state['mode']=='off'
    assert 'presentation' not in state


def test_corrupted_settings_fail_closed(admin):
    db.execute('UPDATE streams SET presentation_json=? WHERE id=1',('{"top":{"enabled":"bogus"}}',))
    state=admin.get('/api/player/stream/1').json()
    assert not state['presentation']['settings']['top']['enabled']


def test_new_process_preserves_overlay_and_absolute_timer(admin):
    body=settings();body['bottom'].update(mode='countdown',target_at=db.now_ms()+3600000)
    assert put(admin,body).status_code==200
    code="""import sys,json;sys.path.insert(0,sys.argv[1]);from app import config,db;config.DB_PATH=__import__('pathlib').Path(sys.argv[2]);r=db.row('SELECT presentation_json,presentation_revision FROM streams WHERE id=1');print(json.dumps(r))"""
    response=subprocess.run([sys.executable,'-c',code,str(ROOT/'web'),str(config.DB_PATH)],capture_output=True,text=True,check=True)
    value=json.loads(response.stdout)
    assert value['presentation_revision']==1
    assert json.loads(value['presentation_json'])==body


def test_png_alpha_sanitized_serving_and_deletion_guard(admin,png):
    uid,slide=ready_graphic(admin,png)
    rendered=config.RENDERED_DIR/slide['file']
    with Image.open(rendered) as im:
        assert im.mode=='RGBA'
        assert im.getpixel((0,0))[3]==0
        assert not im.info
    body=settings();body['top']['asset_id']=uid
    assert put(admin,body).status_code==200
    response=admin.get('/media/'+slide['file'])
    assert response.status_code==200
    assert response.headers['content-type']=='image/png'
    assert 'immutable' in response.headers['cache-control']
    assert 'sandbox' in response.headers['content-security-policy']
    assert admin.delete(f'/api/uploads/{uid}',headers=WRITE).status_code==409
    body['top']['asset_id']=None
    assert put(admin,body,rev=1).status_code==200
    assert admin.delete(f'/api/uploads/{uid}',headers=WRITE).status_code==200


def test_graphic_referenced_in_preset_cannot_disappear(admin,png):
    uid,_=ready_graphic(admin,png)
    body=settings();body['screensaver']['asset_id']=uid
    assert admin.post('/api/presentation/presets',json={'name':'Office','settings':body},headers=WRITE).status_code==201
    assert admin.delete(f'/api/uploads/{uid}',headers=WRITE).status_code==409


def test_only_ready_graphics_can_be_embedded(admin,uploaded):
    body=settings();body['top']['asset_id']=uploaded['id']
    assert put(admin,body).status_code==409


def test_gif_animation_and_transparency_survive(admin,gif):
    uid,slide=ready_graphic(admin,gif,'.gif')
    assert slide['file'].endswith('.gif')
    with Image.open(config.RENDERED_DIR/slide['file']) as im:
        assert im.n_frames==3
        assert im.info['loop']==0
        assert im.convert('RGBA').getpixel((0,0))[3]==0
        delays=[]
        for i in range(3):im.seek(i);delays.append(im.info['duration'])
        assert delays==[100,200,300]
    assert admin.get('/media/'+slide['file']).headers['content-type']=='image/gif'


def test_svg_becomes_static_transparent_png(admin):
    svg=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50"><path fill="#ff8800" d="M10 10 H80 V40 H10 Z"/></svg>'
    uid,slide=ready_graphic(admin,svg,'.svg')
    assert slide['file'].endswith('.png')
    with Image.open(config.RENDERED_DIR/slide['file']) as im:
        assert im.size==(2048,1024)
        assert im.getpixel((0,0))[3]==0
    assert admin.get('/media/'+slide['file'].replace('.png','.svg')).status_code==404


@pytest.mark.parametrize('content',[
    '<script>alert(1)</script>', '<foreignObject><div>hi</div></foreignObject>',
    '<image href="file:///etc/passwd"/>', '<image href="https://evil.example/pixel"/>',
    '<use href="#loop" id="loop"/>', '<animate attributeName="x"/>',
    '<path onload="alert(1)"/>','<style>@import "https://evil.example";</style>',
    '<path fill="url(https://evil.example)"/>', '<path style="fill:url(file:///secret)"/>',
    '<a href="https://evil.example"><path/></a>', '<rect style="fill:u\\72l(foo)"/>',
])
def test_svg_active_or_linked_content_rejected(content):
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">{content}</svg>'.encode()
    with pytest.raises(ValueError):sanitize_svg(svg)


@pytest.mark.parametrize('prefix',[
    '<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]>',
    '<?xml-stylesheet href="https://evil.example/test.css"?>'
])
def test_xml_entities_and_processing_instructions_rejected(prefix):
    from defusedxml.common import DefusedXmlException
    with pytest.raises((ValueError,DefusedXmlException)):
        sanitize_svg((prefix+'<svg width="10" height="10"/>').encode())


def test_svg_small_presentation_styles_supported(tmp_path):
    src=tmp_path/'x.svg';src.write_text('<svg viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8" style="fill:#ff0000;stroke:#ffffff;stroke-width:1"/></svg>')
    out=tmp_path/'out';out.mkdir()
    render_graphic(src,out)
    with Image.open(out/'000.png') as im:assert im.getpixel((1024,1024))[0]>200


def test_existing_content_path_not_widened_to_raw_svg(admin):
    response=admin.post('/api/uploads',files={'file':('x.svg',b'<svg width="1" height="1"/>')},headers=WRITE)
    assert response.status_code==400
    response=admin.post('/api/graphics',files={'file':('x.html',b'<html>bad</html>')},headers=WRITE)
    assert response.status_code==400


def test_graphic_size_rejected_before_parsing(admin):
    response=admin.post('/api/graphics',content=b'x',headers={**WRITE,'content-length':str(33*1048576),'content-type':'multipart/form-data; boundary=x'})
    assert response.status_code==413


def test_graphic_rendition_not_accepted_as_normal_job():
    manifest={'job_token':'a'*32,'slides':[{'kind':'image','file':'000.png','thumb':'000_t.jpg'}]}
    with pytest.raises(jobs.Reject):jobs._validate_manifest(manifest,{'result.json','000.png','000_t.jpg'},'a'*32)
    assert jobs._validate_manifest(manifest,{'result.json','000.png','000_t.jpg'},'a'*32,graphic=True)


def test_screensaver_art_inherited_but_not_its_ribbons(admin,png):
    uid,_=ready_graphic(admin,png)
    ss=db.row("SELECT id FROM streams WHERE kind='screensaver'")['id']
    body=settings();body['screensaver']['asset_id']=uid
    assert put(admin,body,sid=ss).status_code==200
    state=admin.get('/api/player/stream/1').json()
    assert state['source']=='screensaver'
    assert state['presentation']['settings']['screensaver']['asset_url']
    assert not state['presentation']['settings']['top']['enabled']


def test_in_use_graphic_placement_on_screensaver(admin,png):
    uid,_=ready_graphic(admin,png)
    ss=db.row("SELECT id FROM streams WHERE kind='screensaver'")['id']
    response=admin.post('/api/placements',json={'upload_id':uid,'stream_ids':[ss],'mode':'rotation','slide_seconds':8},headers=WRITE)
    assert response.status_code==200,response.text
    assert playlist.stream_state(ss)['items'][0]['url'].endswith('.png')

@pytest.mark.parametrize('ext', ['.png','.svg','.gif'])
def test_real_graphic_supervisor_process_and_ingest(admin,png,gif,ext):
    """Real subprocess/resource limits; not a Docker or network-namespace claim."""
    import os
    data=png if ext=='.png' else gif if ext=='.gif' else b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20"><rect width="20" height="10" fill="#abcdef"/></svg>'
    r=admin.post('/api/graphics',files={'file':('logo'+ext,data)},headers=WRITE)
    assert r.status_code==201,r.text
    uid=r.json()['id'];ticket=config.ORIGINALS_DIR/f'{uid}.job.json'
    env={**os.environ,'ORIGINALS_DIR':str(config.ORIGINALS_DIR),'WORK_DIR':str(config.WORK_DIR),'CONVERT_WALL_SECONDS':'15'}
    result=subprocess.run([sys.executable,'-c',f'import sys;sys.path.insert(0,{str(ROOT/"worker")!r});from pathlib import Path;import convert;convert.supervise(Path({str(ticket)!r}))'],env=env,capture_output=True,text=True,timeout=22)
    assert result.returncode==0,result.stderr
    assert (config.WORK_DIR/'.heartbeat').exists()
    assert (config.WORK_DIR/str(uid)/'result.json').is_file(),result.stdout+result.stderr
    jobs._ingest(uid)
    assert db.row('SELECT status FROM uploads WHERE id=?',(uid,))['status']=='ready'


def test_nas_graphic_import_choice_persists_and_exports_original(admin,storage,monkeypatch):
    from app import shares
    folder=storage/'shared';folder.mkdir();(folder/'.signage-volume-id').write_text('graphic-test-volume-001\n')
    source=b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="#ffffff"/></svg>'
    (folder/'logo.svg').write_bytes(source)
    item=shares.Share('office','Office',folder,'graphic-test-volume-001',False,False)
    monkeypatch.setattr(shares,'SHARES',{'office':item})
    assert [e['name'] for e in admin.get('/api/shares/office/browse').json()['entries']]==['logo.svg']
    assert admin.post('/api/shares/office/import',headers=WRITE,json={'path':'logo.svg'}).status_code==400
    response=admin.post('/api/shares/office/import',headers=WRITE,json={'path':'logo.svg','as_graphic':True})
    assert response.status_code==202,response.text
    jid=response.json()['id'];db.init();shares.recover()
    assert db.row('SELECT as_graphic FROM library_transfers WHERE id=?',(jid,))['as_graphic']==1
    assert shares.process_one()
    job=db.row('SELECT * FROM library_transfers WHERE id=?',(jid,));assert job['status']=='completed',job
    uid=json.loads(job['result_json'])['id'];up=db.row('SELECT * FROM uploads WHERE id=?',(uid,))
    assert up['kind']=='graphic';assert (config.ORIGINALS_DIR/up['original_file']).read_bytes()==source
    q=admin.post('/api/shares/office/export',headers=WRITE,json={'upload_id':uid,'path':''});assert q.status_code==202,q.text
    assert shares.process_one()
    export=db.row('SELECT * FROM library_transfers WHERE id=?',(q.json()['id'],));assert export['status']=='completed',export
    assert (folder/export['output_name']).read_bytes()==source


def test_graphic_input_pixel_limit(tmp_path):
    src=tmp_path/'large.png';Image.new('RGBA',(3000,3000)).save(src)
    with pytest.raises(ValueError,match='8 megapixels'):render_graphic(src,tmp_path)


def test_gif_decoded_frame_budget(tmp_path,monkeypatch,gif):
    import graphics
    src=tmp_path/'animation.gif';src.write_bytes(gif)
    monkeypatch.setattr(graphics,'MAX_FRAME_PIXELS',2000)
    with pytest.raises(ValueError,match='decoded-frame budget'):render_graphic(src,tmp_path)


def test_gif_frame_count_limit(tmp_path,monkeypatch,gif):
    import graphics
    src=tmp_path/'animation.gif';src.write_bytes(gif)
    monkeypatch.setattr(graphics,'MAX_FRAMES',2)
    with pytest.raises(ValueError,match='120 frames'):render_graphic(src,tmp_path)


def test_svg_node_complexity_limit():
    data=b'<svg viewBox="0 0 10 10">'+b'<rect width="1" height="1"/>'*5001+b'</svg>'
    with pytest.raises(ValueError,match='complex'):sanitize_svg(data)


def test_nginx_example_allows_overlay_player_assets():
    text=(ROOT/'deploy/nginx.example.conf').read_text()
    assert 'static/(player|overlay)\\.(js|css)$' in text


def test_screensaver_clock_timezone_validated(admin):
    body=settings();body['screensaver']['timezone']='Pacific/Honolulu'
    assert put(admin,body).status_code==200
    ss=db.row("SELECT id FROM streams WHERE kind='screensaver'")['id']
    assert put(admin,body,sid=ss).status_code==200
    state=admin.get('/api/player/stream/1').json()
    assert state['presentation']['settings']['screensaver']['timezone']=='Pacific/Honolulu'
    body['screensaver']['timezone']='not/a-time-zone'
    assert put(admin,body,rev=1).status_code==422
