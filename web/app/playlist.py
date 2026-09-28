"""Deterministic playlists resolved from one consistent database snapshot."""
import hashlib
import json
from collections import defaultdict

from . import db, feeds, presentation


def _active(p: dict, now: int) -> bool:
    return bool(p["enabled"] and (p["start_at"] is None or p["start_at"] <= now)
                and (p["end_at"] is None or now < p["end_at"]))


class Snapshot:
    def __init__(self, streams, placements, slides, uploads):
        self.streams = {s["id"]: s for s in streams}
        self.uploads, self.all_slides = uploads, slides
        self.screensaver_id = next((s["id"] for s in streams if s["kind"] == "screensaver"), None)
        ready = {u["id"]: u for u in uploads if u["status"] == "ready"}
        self.placements = defaultdict(list)
        self.slides = defaultdict(list)
        for p in placements:
            if p["upload_id"] in ready:
                self.placements[p["stream_id"]].append({**p, "title": ready[p["upload_id"]]["title"]})
        for slide in slides:
            self.slides[slide["upload_id"]].append(slide)
        self.memo = {}

    @classmethod
    def load(cls):
        with db.tx() as c:
            c.execute("BEGIN")
            def rows(table, order):
                return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            return cls(rows("streams", "id"), rows("placements", "position,id"),
                       rows("slides", "upload_id,idx"), rows("uploads", "id"))

    def expand(self, placements):
        items = []
        for p in placements:
            for s in self.slides[p["upload_id"]]:
                duration = s["duration_ms"] if s["kind"] == "video" else p["slide_seconds"] * 1000
                source = feeds.public_source(s) if s["kind"] in feeds.KINDS else {
                    "url": f"/media/{s['file']}", "thumb": f"/media/{s['thumb']}"}
                if source is None:
                    continue
                items.append({"slide_id": s["id"], "placement_id": p["id"], "kind": s["kind"],
                              **source, "title": p["title"], "duration_ms": max(1000, int(duration or 1000))})
        return items

    def own(self, sid, now):
        key = (sid, now)
        if key in self.memo:
            return dict(self.memo[key])
        placements = self.placements[sid] + [p for p in self.placements[None] if p["mode"] == "override"]
        edges = [t for p in placements if p["enabled"] for t in (p["start_at"], p["end_at"]) if t is not None and t > now]
        overrides = [p for p in placements if p["mode"] == "override" and _active(p, now)]
        if overrides:
            def start(p):
                return p["start_at"] if p["start_at"] is not None else p["created_at"]
            selected = max(overrides, key=lambda p: (start(p), p["id"]))
            items, mode, anchor = self.expand([selected]), "override", start(selected)
        else:
            rotation = [p for p in placements if p["mode"] == "rotation" and _active(p, now)]
            rotation.sort(key=lambda p: (p["position"], p["id"]))
            items, mode, anchor = self.expand(rotation), "rotation", 0
        result = {"mode": mode if items else "empty", "anchor_ms": anchor, "items": items,
                  "next_change_ms": min(edges) if edges else None}
        self.memo[key] = result
        return dict(result)

    def screensaver(self, now):
        state = self.own(self.screensaver_id, now) if self.screensaver_id else {
            "mode": "empty", "anchor_ms": 0, "items": [], "next_change_ms": None}
        if state["mode"] == "empty":
            state["mode"] = "clock"
        return state

    def state(self, sid, now):
        result = self._state(sid, now)
        stream = self.streams.get(sid)
        if stream:
            ss = self.streams.get(self.screensaver_id)
            result['presentation'] = presentation.public_settings(stream, self.uploads, self.all_slides, ss)
            # Presentation deadlines are also enforced by the renderer when offline.
            edges = [t for side in ('top', 'bottom') for t in
                     (result['presentation']['settings'][side]['start_at'], result['presentation']['settings'][side]['end_at'])
                     if t is not None and t > now]
            result['presentation_change_ms'] = min(edges) if edges else None
        return result

    def _state(self, sid, now):
        stream = self.streams.get(sid)
        if not stream:
            return _finish(None, {"mode": "off", "anchor_ms": 0, "items": [], "next_change_ms": None}, "off")
        if stream["kind"] == "screensaver":
            return _finish(sid, self.screensaver(now), "screensaver")
        own = self.own(sid, now)
        if own["mode"] != "empty" or stream["fallback"] != "screensaver":
            return _finish(sid, own, "stream")
        fallback = self.screensaver(now)
        edges = [t for t in (own["next_change_ms"], fallback["next_change_ms"]) if t is not None]
        fallback["next_change_ms"] = min(edges) if edges else None
        return _finish(sid, fallback, "screensaver")


def _finish(sid, state, source):
    signature = [state["mode"], source, state["anchor_ms"],
                 [(i["slide_id"], i["placement_id"], i["kind"], i["url"], i["duration_ms"]) for i in state["items"]]]
    version = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()[:24]
    return {"stream_id": sid, "source": source, **state, "version": version}


def stream_state(stream_id, now=None):
    return Snapshot.load().state(stream_id, db.now_ms() if now is None else now)


def position(state: dict, now: int) -> dict | None:
    total = sum(i["duration_ms"] for i in state["items"])
    if not total:
        return None
    offset = (now - state["anchor_ms"]) % total
    for item in state["items"]:
        if offset < item["duration_ms"]:
            return item
        offset -= item["duration_ms"]
    return None
