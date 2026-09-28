import json
import os
from pathlib import Path

import pytest
from app import config, db, jobs, playlist

WRITE = {"X-Signage": "1"}

def output(up, jpeg):
    root = config.WORK_DIR / str(up["id"]); root.mkdir()
    (root / "000.jpg").write_bytes(jpeg); (root / "000_t.jpg").write_bytes(jpeg)
    manifest = {"job_token": up["job_token"], "slides": [{"kind":"image", "file":"000.jpg", "thumb":"000_t.jpg"}]}
    (root / "result.json").write_text(json.dumps(manifest))
    return root, manifest

def test_auth_before_multipart(client):
    response = client.post("/api/uploads", content=b"not multipart", headers={**WRITE, "Content-Type":"multipart/form-data; boundary=x"})
    assert response.status_code == 401
    assert not list(config.ORIGINALS_DIR.iterdir())

@pytest.mark.parametrize("name,body", [("x.png",b"not png"),("x.avi",b"#EXTM3U\nsecret"),("x.html",b"<script>alert(1)</script>")])
def test_intake_rejects_disguised_files(admin, name, body):
    assert admin.post("/api/uploads", files={"file":(name,body)}, headers=WRITE).status_code == 400

def test_multiple_files_rejected(admin, jpeg):
    response = admin.post("/api/uploads", files=[("file",("a.jpg",jpeg)),("file",("b.jpg",jpeg))], headers=WRITE)
    assert response.status_code == 400

def test_declared_upload_limit(admin, monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_MB", 1)
    assert admin.post("/api/uploads", content=b"a", headers={**WRITE,"Content-Length":str(2*1048576)}).status_code == 413

def test_valid_ingest_and_media_headers(admin, uploaded, jpeg):
    output(uploaded, jpeg); jobs._ingest(uploaded["id"])
    assert db.row("SELECT status FROM uploads")["status"] == "ready"
    slide = db.row("SELECT * FROM slides")
    assert uploaded["job_token"] in slide["file"]
    response = admin.get("/media/"+slide["file"])
    assert response.status_code == 200 and response.content == jpeg
    assert response.headers["content-type"] == "image/jpeg"
    assert "immutable" in response.headers["cache-control"]
    assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
    partial = admin.get("/media/"+slide["file"], headers={"Range":"bytes=0-7"})
    assert partial.status_code == 206 and partial.content == jpeg[:8]
    assert admin.get("/media/"+slide["file"], headers={"Range":"bytes=0-1,2-3"}).status_code == 416

@pytest.mark.parametrize("attack", ["symlink","fifo","hardlink","html","extra","path","bad_entry","stale","directory_symlink","nonobject"])
def test_hostile_ingest_rejected(uploaded, jpeg, storage, attack):
    root, manifest = output(uploaded, jpeg)
    target = root / "000.jpg"
    secret = storage / "secret"; secret.write_bytes(jpeg); secret.chmod(0o600)
    if attack in {"symlink","fifo","hardlink"}:
        target.unlink()
        if attack == "symlink": target.symlink_to(secret)
        elif attack == "fifo": os.mkfifo(target)
        else: os.link(secret, target)
    elif attack == "html": target.write_bytes(b"<html>not an image</html>")
    elif attack == "extra": (root / "index.html").write_text("unexpected")
    elif attack == "path": manifest["slides"][0]["file"] = "../../secret"
    elif attack == "bad_entry": manifest["slides"] = [None]
    elif attack == "stale": manifest["job_token"] = "0"*32
    elif attack == "nonobject": manifest = []
    elif attack == "directory_symlink":
        other = root.with_name("other");root.rename(other);root.symlink_to(other, target_is_directory=True)
    (root / "result.json").write_text(json.dumps(manifest))
    jobs._ingest(uploaded["id"])
    assert db.row("SELECT status FROM uploads")["status"] == "error"
    assert not db.rows("SELECT * FROM slides")
    assert secret.read_bytes() == jpeg and secret.stat().st_mode & 0o777 == 0o600

def test_aggregate_budget(uploaded, jpeg, monkeypatch):
    root,_ = output(uploaded, jpeg)
    monkeypatch.setattr(config, "MAX_INGEST_MB", 0)
    jobs._ingest(uploaded["id"])
    assert db.row("SELECT status FROM uploads")["status"] == "error"

def test_id_not_reused(admin, uploaded, jpeg):
    first = uploaded["id"]
    assert admin.delete(f"/api/uploads/{first}", headers=WRITE).status_code == 200
    response = admin.post("/api/uploads", files={"file":("again.jpg",jpeg)}, headers=WRITE)
    assert response.status_code == 201 and response.json()["id"] > first

def test_retry_rotates_generation(admin, uploaded):
    uid=uploaded["id"]
    assert admin.post(f"/api/uploads/{uid}/retry",headers=WRITE).status_code == 409
    db.execute("UPDATE uploads SET status='error' WHERE id=?",(uid,))
    assert admin.post(f"/api/uploads/{uid}/retry",headers=WRITE).status_code == 200
    assert db.row("SELECT job_token FROM uploads WHERE id=?",(uid,))["job_token"] != uploaded["job_token"]

@pytest.mark.parametrize("path", ["/media/1/index.html","/media/1/../secret","/media/1/%2e%2e%2fsecret","/media/1/000.jpg.html","/media/1/"])
def test_traversal_and_nonmedia_404(client, path):
    assert client.get(path).status_code == 404

def test_atomic_display_update(admin):
    before=db.row("SELECT * FROM displays LIMIT 1")
    response=admin.patch(f"/api/displays/{before['id']}", json={"name":"Changed","stream_id":999999},headers=WRITE)
    assert response.status_code == 404
    assert db.row("SELECT name FROM displays WHERE id=?",(before["id"],))["name"] == before["name"]

@pytest.mark.parametrize("body", [{"name":"   "},{"name":"ok","unexpected":"x"}])
def test_strict_stream_input(admin, body):
    assert admin.post("/api/streams",json=body,headers=WRITE).status_code == 422

def test_clear_flags_conflict(admin):
    did=db.row("SELECT id FROM displays")["id"]
    assert admin.patch(f"/api/displays/{did}",json={"stream_id":1,"clear_stream":True},headers=WRITE).status_code == 422

def test_overview_etag(admin):
    first=admin.get("/api/overview");assert first.status_code == 200
    second=admin.get("/api/overview",headers={"If-None-Match":first.headers["etag"]})
    assert second.status_code == 304 and second.headers["x-server-time"]
    assert "original_file" not in first.text and "job_token" not in first.text

def test_exact_rotation_reorder(admin, uploaded):
    sid=db.row("SELECT id FROM streams WHERE kind='normal'")["id"]
    response=admin.post("/api/placements",json={"upload_id":uploaded["id"],"stream_ids":[sid],"mode":"rotation"},headers=WRITE)
    ids=response.json()["ids"]
    for invalid in ([],ids+ids,[9999]):
        assert admin.post(f"/api/streams/{sid}/order",json={"placement_ids":invalid},headers=WRITE).status_code == 409
    assert admin.post(f"/api/streams/{sid}/order",json={"placement_ids":ids},headers=WRITE).status_code == 200

def test_schedule_merged_validation(admin, uploaded):
    sid=db.row("SELECT id FROM streams WHERE kind='normal'")["id"]
    created=admin.post("/api/placements",json={"upload_id":uploaded["id"],"stream_ids":[sid],"mode":"rotation","start_at":1000,"end_at":5000},headers=WRITE)
    pid=created.json()["ids"][0]
    assert admin.patch(f"/api/placements/{pid}",json={"start_at":6000},headers=WRITE).status_code == 400

def test_fallback_off_screensaver_clock():
    sid=db.row("SELECT id FROM streams WHERE kind='normal'")["id"]
    assert playlist.stream_state(sid,0)["mode"] == "clock"
    assert playlist.stream_state(None,0)["mode"] == "off"
    db.execute("UPDATE streams SET fallback='blank' WHERE id=?",(sid,))
    assert playlist.stream_state(sid,0)["mode"] == "empty"

def test_override_epoch_and_boundary(uploaded,jpeg):
    output(uploaded,jpeg);jobs._ingest(uploaded["id"])
    sid=db.row("SELECT id FROM streams WHERE kind='normal'")["id"]
    db.execute("INSERT INTO placements(upload_id,stream_id,mode,slide_seconds,start_at,end_at,created_at) VALUES (?,?,'override',10,0,5000,100)", (uploaded["id"],sid))
    state=playlist.stream_state(sid,2000)
    assert state["mode"] == "override" and state["anchor_ms"] == 0 and state["next_change_ms"] == 5000
    assert playlist.stream_state(sid,5000)["mode"] == "clock"
