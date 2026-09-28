"""Permanent URLs, read-only boundaries and durable, confined NAS transfers."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException
from app import config, db, feeds, shares, storage_guard

WRITE = {'X-Signage': '1'}

@pytest.fixture
def origins(monkeypatch):
    monkeypatch.setattr(config, 'SOURCE_MEDIA_ORIGINS', ['https://video.example.org'])
    monkeypatch.setattr(config, 'SOURCE_FRAME_ORIGINS', ['https://board.example.org'])

@pytest.fixture
def share(admin, storage, monkeypatch):
    path = storage / 'shared'
    path.mkdir()
    (path / '.signage-volume-id').write_text('test-volume-00000001\n')
    item = shares.Share('office', 'Office NAS', path, 'test-volume-00000001', False, False)
    monkeypatch.setattr(shares, 'SHARES', {'office': item})
    return item

def getjob(id):
    return db.row('SELECT * FROM library_transfers WHERE id=?', (id,))

def feed(admin, kind='hls', url='https://video.example.org/live/index.m3u8'):
    result = admin.post('/api/feeds', headers=WRITE, json={'title': 'Broadcast', 'kind': kind, 'url': url})
    assert result.status_code == 201, result.text
    return result.json()['id']

def place(admin, uid, sid=1):
    result = admin.post('/api/placements', headers=WRITE, json={'upload_id':uid,'stream_ids':[sid], 'mode':'rotation','slide_seconds':10})
    assert result.status_code == 200, result.text


def test_channel_url_survives_rename_assignment_and_source_update(admin, origins):
    st = db.row('SELECT * FROM streams WHERE id=1')
    key = st['playback_key']
    uid = feed(admin); place(admin, uid)
    before = admin.get('/api/player/channel/' + key).json()
    assert before['items'][0]['url'].endswith('index.m3u8')
    assert admin.patch('/api/streams/1', headers=WRITE,json={'name':'Renamed'}).status_code == 200
    assert admin.put(f'/api/feeds/{uid}', headers=WRITE, json={'title':'Changed', 'kind':'hls','url':'https://video.example.org/other.m3u8'}).status_code == 200
    after = admin.get('/api/player/channel/' + key).json()
    assert after['items'][0]['url'].endswith('other.m3u8')
    assert after['version'] != before['version']
    assert db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key'] == key
    assert admin.patch('/api/displays/1', headers=WRITE,json={'stream_id':2}).status_code == 200
    assert admin.get('/api/player/display/lobby-1').json()['stream_id'] == 2
    assert admin.get('/display/lobby-1').status_code == 200


def test_retired_links_and_ids_not_recycled(admin):
    st = db.row('SELECT * FROM streams WHERE id=3')
    assert admin.delete('/api/streams/3', headers=WRITE).status_code == 200
    r = admin.post('/api/streams', headers=WRITE,json={'name':'New'}).json()
    assert r['id'] > 4
    assert admin.get('/api/player/channel/' + st['playback_key']).status_code == 404
    assert admin.patch('/api/displays/1',headers=WRITE,json={'slug':'changed'}).status_code == 409
    assert admin.delete('/api/displays/1',headers=WRITE).status_code == 200
    assert admin.post('/api/displays',headers=WRITE,json={'name':'New','slug':'lobby-1','stream_id':2}).status_code == 409


@pytest.mark.parametrize('path', ['/display/lobby-1','/stream/'+'a'*32,'/api/player/display/lobby-1','/api/player/stream/1'])
def test_playback_cannot_write_even_with_admin_cookie(admin,path):
    assert admin.post(path,headers=WRITE,json={'name':'bad'}).status_code == 405


def test_player_host_has_no_admin_surface(admin, monkeypatch):
    monkeypatch.setattr(config,'ALLOWED_HOSTS',['localhost','player.example.org'])
    monkeypatch.setattr(config,'PLAYER_ONLY_HOSTS',['player.example.org'])
    h = {'Host':'player.example.org'}
    for path in ['/','/auth/login','/api/overview','/api/shares','/api/transfers','/static/admin.js']:
        assert admin.get(path,headers=h).status_code == 404, path
    assert admin.get('/display/lobby-1',headers=h).status_code == 200
    assert admin.get('/api/player/display/lobby-1',headers=h).status_code == 200


def test_preview_redirects_to_fixed_player_origin(admin, monkeypatch):
    monkeypatch.setattr(config,'PLAYER_BASE_URL','https://play.example.org')
    r = admin.get('/preview/1?sound=1&unsafe=discard', follow_redirects=False)
    assert r.status_code == 307
    assert r.headers['location'] == 'https://play.example.org/preview/1?sound=1'


@pytest.mark.parametrize('url', ['file:///etc/passwd','http://127.0.0.1/x','https://video.example.org@evil.example/x',
 'https://video.example.org/x#fragment','https://user:password@video.example.org/x','https://video.example.org.evil/x',
 'https://video.example.org\\@evil/x','https://169.254.169.254/latest/meta-data/','https://unapproved.example/x'])
def test_unapproved_sources_rejected(admin, origins,url):
    assert admin.post('/api/feeds',headers=WRITE,json={'title':'No','kind':'hls','url':url}).status_code == 400


def test_source_approval_can_be_revoked(admin, origins,monkeypatch):
    uid = feed(admin); place(admin,uid)
    assert admin.get('/api/player/stream/1').json()['items']
    monkeypatch.setattr(config,'SOURCE_MEDIA_ORIGINS',[])
    assert not admin.get('/api/player/stream/1').json()['items']


def test_source_csp_admin_is_not_relaxed(admin,origins):
    public = admin.get('/display/lobby-1').headers['content-security-policy']
    private = admin.get('/').headers['content-security-policy']
    assert 'https://video.example.org' in public and 'https://board.example.org' in public
    assert 'https://video.example.org' not in private and 'https://board.example.org' not in private


def test_production_sources_require_separate_hostname(monkeypatch,origins):
    monkeypatch.setattr(config,'ALLOW_INSECURE_DEV',False)
    monkeypatch.setattr(config,'COOKIE_SECURE',True)
    monkeypatch.setattr(config,'PLAYER_BASE_URL','https://admin.example.org')
    monkeypatch.setattr(config,'ADMIN_ORIGINS',['https://admin.example.org'])
    with pytest.raises(RuntimeError,match='separate playback'):
        config.validate()


def test_no_anonymous_nas_or_feed_control(client):
    for path in ['/api/shares','/api/transfers']:
        assert client.get(path).status_code == 401
    assert client.post('/api/feeds',headers=WRITE,json={'title':'Test','kind':'hls','url':'https://video.example.org/x'}).status_code == 401


@pytest.mark.parametrize('path',['../secret','/etc','safe/../other','a//b','a\\b','.hidden','a/.hidden','a:b'])
def test_share_path_confinement(share,path):
    with pytest.raises(HTTPException):
        shares.parts(path)


def test_browse_and_snapshot_import(admin, share, jpeg):
    (share.path/'presentation.jpg').write_bytes(jpeg)
    (share.path/'hidden.txt').write_text('not allowed')
    (share.path/'shortcut.jpg').symlink_to(share.path/'presentation.jpg')
    r=admin.get('/api/shares/office/browse')
    assert r.status_code == 200, r.text
    assert [e['name'] for e in r.json()['entries']] == ['presentation.jpg']
    q=admin.post('/api/shares/office/import',headers=WRITE,json={'path':'presentation.jpg'})
    assert q.status_code == 202, q.text
    assert shares.process_one()
    job=getjob(q.json()['id']); assert job['status']=='completed', job
    uid=json.loads(job['result_json'])['id']
    up=db.row('SELECT * FROM uploads WHERE id=?',(uid,))
    assert (config.ORIGINALS_DIR/up['original_file']).read_bytes()==jpeg
    (share.path/'presentation.jpg').unlink()
    # Interrupted acknowledgement: recovery recognizes the previous import, even
    # if the NAS original has since disappeared. No duplicate upload is made.
    db.execute("UPDATE library_transfers SET status='running' WHERE id=?",(job['id'],))
    shares.recover(); shares.process_one()
    assert getjob(job['id'])['status']=='completed'
    assert db.row('SELECT COUNT(*) n FROM uploads')['n']==1


def test_missing_share_fails_closed_and_can_retry(admin,share,jpeg):
    (share.path/'presentation.jpg').write_bytes(jpeg)
    marker=share.path/'.signage-volume-id'; marker.unlink()
    r=admin.get('/api/shares/office/browse'); assert r.status_code==503
    q=admin.post('/api/shares/office/import',headers=WRITE,json={'path':'presentation.jpg'}).json()
    shares.process_one(); assert getjob(q['id'])['status']=='error'
    assert not marker.exists()
    assert not db.row('SELECT id FROM uploads')
    marker.write_text(share.volume_id)
    assert admin.post(f"/api/transfers/{q['id']}/retry",headers=WRITE).status_code==200
    shares.process_one(); assert getjob(q['id'])['status']=='completed'


def test_network_guard_rejects_local_directory_even_with_marker(share):
    with storage_guard.directory(share.path) as fd:
        with pytest.raises(OSError, match='NFS or SMB'):
            storage_guard.network_filesystem(fd)


def test_wrong_marker_and_symlink_rejected(share,storage):
    marker=share.path/'.signage-volume-id'; marker.write_text('wrong')
    with pytest.raises(OSError):
        with shares.share_dir(share): pass
    marker.unlink(); (storage/'outside').write_text(share.volume_id)
    marker.symlink_to(storage/'outside')
    with pytest.raises(OSError):
        with shares.share_dir(share): pass


def test_share_symlink_file_import_rejected(admin,share,jpeg,storage):
    (storage/'outside.jpg').write_bytes(jpeg)
    (share.path/'linked.jpg').symlink_to(storage/'outside.jpg')
    q=admin.post('/api/shares/office/import',headers=WRITE,json={'path':'linked.jpg'}).json()
    shares.process_one(); assert getjob(q['id'])['status']=='error'
    assert not db.row('SELECT id FROM uploads')


def test_export_original_idempotent_after_interrupted_ack(admin,share,uploaded,jpeg):
    q=admin.post('/api/shares/office/export',headers=WRITE,json={'upload_id':uploaded['id'],'path':''})
    assert q.status_code==202,q.text
    shares.process_one(); job=getjob(q.json()['id']); assert job['status']=='completed',job
    result=json.loads(job['result_json']); dest=share.path/result['name']
    assert dest.read_bytes()==jpeg
    db.execute("UPDATE library_transfers SET status='running' WHERE id=?",(job['id'],))
    shares.recover();shares.process_one();assert getjob(job['id'])['status']=='completed'
    assert len(list(share.path.glob('*.jpg')))==1


def test_export_never_overwrites(admin,share,uploaded):
    q=admin.post('/api/shares/office/export',headers=WRITE,json={'upload_id':uploaded['id']}).json()
    job=getjob(q['id']);dest=share.path/job['output_name'];dest.write_bytes(b'Existing file')
    shares.process_one();assert getjob(q['id'])['status']=='error'
    assert dest.read_bytes()==b'Existing file'


def test_readonly_share_blocks_export(admin,share,uploaded,monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(shares,'SHARES',{'office':replace(share,read_only=True)})
    assert admin.post('/api/shares/office/export',headers=WRITE,json={'upload_id':uploaded['id']}).status_code==403


def test_queue_cap(admin,share,jpeg):
    (share.path/'item.jpg').write_bytes(jpeg)
    for i in range(32):
        assert admin.post('/api/shares/office/import',headers=WRITE,json={'path':'item.jpg'}).status_code==202
    assert admin.post('/api/shares/office/import',headers=WRITE,json={'path':'item.jpg'}).status_code==429


def test_fresh_process_retains_channels_assignments_sources_and_queue(admin,share,origins,jpeg):
    uid=feed(admin);place(admin,uid)
    admin.patch('/api/displays/1',headers=WRITE,json={'stream_id':2})
    (share.path/'item.jpg').write_bytes(jpeg)
    jid=admin.post('/api/shares/office/import',headers=WRITE,json={'path':'item.jpg'}).json()['id']
    before=db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key']
    env={**os.environ,'DATA_DIR':str(config.DATA_DIR),'MEDIA_DIR':str(config.MEDIA_DIR),'DB_PATH':str(config.DB_PATH),
        'PYTHONPATH':str(Path(__file__).resolve().parents[1]/'web')}
    code="from app import db; import json; db.init(); print(json.dumps({'key': db.row('SELECT playback_key FROM streams WHERE id=1')['playback_key'], 'assignment':db.row('SELECT stream_id FROM displays WHERE id=1')['stream_id'], 'feed':db.row(\"SELECT COUNT(*) n FROM uploads WHERE kind='feed'\")['n'], 'queue':db.row(\"SELECT COUNT(*) n FROM library_transfers WHERE status='queued'\")['n']}))"
    run=subprocess.run([sys.executable,'-c',code],env=env,capture_output=True,text=True,timeout=20,check=True)
    assert json.loads(run.stdout)=={'key':before,'assignment':2,'feed':1,'queue':1}
    assert getjob(jid)['status']=='queued'


def test_shared_config_rejects_application_storage(admin, storage, monkeypatch):
    path=storage/'bad-shares.json'
    path.write_text(json.dumps([{'id':'bad','name':'Wrong root','path':str(config.ORIGINALS_DIR),
        'volume_id':'test-volume-00000001','require_network':False}]))
    monkeypatch.setattr(config,'SHARES_CONFIG',str(path))
    with pytest.raises(ValueError,match='overlap'):
        shares.load_config()


def test_worker_guard_rejects_local_fallback_before_creating_work(storage, monkeypatch):
    import importlib.util
    original=storage/'local-originals';original.mkdir()
    (original/'.signage-volume-id').write_text('test-volume-00000001')
    work=storage/'absent-work'
    monkeypatch.setenv('REQUIRE_MEDIA_MARKERS','true')
    monkeypatch.setenv('MEDIA_VOLUME_ID','test-volume-00000001')
    monkeypatch.setenv('ORIGINALS_DIR',str(original));monkeypatch.setenv('WORK_DIR',str(work))
    spec=importlib.util.spec_from_file_location('worker_storage_check',Path(__file__).resolve().parents[1]/'worker/storage_check.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with pytest.raises(OSError,match='mounted NAS'):
        module.verify()
    assert not work.exists()


def test_hls_installer_rejects_unverified_archive(storage):
    archive=storage/'invalid.zip';archive.write_bytes(b'not the pinned release')
    script=Path(__file__).resolve().parents[1]/'web/install_hls.py'
    result=subprocess.run([sys.executable,str(script),str(archive)],capture_output=True,text=True,timeout=10)
    assert result.returncode != 0
    assert 'checksum mismatch' in result.stderr


def test_database_requests_full_sync():
    c=db.connect()
    try: assert c.execute('PRAGMA synchronous').fetchone()[0] == 2
    finally: c.close()


def test_source_cannot_reuse_admin_cookie_hostname_on_another_port(admin,monkeypatch):
    monkeypatch.setattr(config,'ADMIN_ORIGINS',['https://admin.example.org'])
    monkeypatch.setattr(config,'SOURCE_MEDIA_ORIGINS',['https://admin.example.org:8443'])
    with pytest.raises(HTTPException,match='different hostname'):
        feeds.validate_url('https://admin.example.org:8443/stream.m3u8','hls')
    with pytest.raises(RuntimeError,match='different hostname'):
        config.validate()
