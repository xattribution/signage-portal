"""Persistent per-stream overlays. No HTML, CSS, remote URLs or templates are accepted."""
import hashlib
import json
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, StrictBool, field_validator, model_validator

from . import auth, db
from .auth.base import Principal
from .schemas import ID, InputModel, Timestamp

Color = Annotated[str, Field(pattern=r'^#[0-9a-fA-F]{6}$')]
SmallFloat = Annotated[float, Field(allow_inf_nan=False)]


class Ribbon(InputModel):
    enabled: StrictBool = False
    mode: Literal['static', 'ticker', 'clock', 'countdown'] = 'static'
    text: str = Field(default='', max_length=1000)
    background: Color = '#17212b'
    foreground: Color = '#ffffff'
    opacity: int = Field(default=100, strict=True, ge=0, le=100)
    height_pct: SmallFloat = Field(default=8, ge=3, le=24)
    font_pct: SmallFloat = Field(default=3.6, ge=1, le=14)
    align: Literal['left', 'center', 'right'] = 'left'
    speed: int = Field(default=70, strict=True, ge=15, le=240)
    direction: Literal['left', 'right'] = 'left'
    timezone: str = Field(default='UTC', min_length=1, max_length=80)
    hour24: StrictBool = True
    seconds: StrictBool = False
    show_date: StrictBool = False
    target_at: Timestamp | None = None
    zero: Literal['hold', 'message', 'hide'] = 'message'
    done_text: str = Field(default='Starting now', max_length=160)
    start_at: Timestamp | None = None
    end_at: Timestamp | None = None
    asset_id: ID | None = None
    asset_side: Literal['left', 'right'] = 'left'
    entry: Literal['none', 'fade', 'slide'] = 'fade'
    entry_ms: int = Field(default=250, strict=True, ge=0, le=1500)

    @field_validator('text', 'done_text')
    @classmethod
    def plain_text(cls, value):
        if any(ord(ch) < 32 and ch not in '\t\n\r' for ch in value):
            raise ValueError('Control characters are not allowed')
        return ' '.join(value.split())

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Choose an IANA time zone, such as Pacific/Honolulu or UTC') from None
        return value

    @model_validator(mode='after')
    def consistent(self):
        if self.font_pct > self.height_pct * .8:
            raise ValueError('Text height must be at most 80% of the ribbon height')
        if self.start_at is not None and self.end_at is not None and self.end_at <= self.start_at:
            raise ValueError('Ribbon end must follow its start')
        if self.enabled and self.mode == 'countdown' and self.target_at is None:
            raise ValueError('Set a countdown target date and time')
        if self.enabled and self.mode in {'static', 'ticker'} and not self.text and self.asset_id is None:
            raise ValueError('Add ribbon text or a graphic')
        return self


class Transition(InputModel):
    effect: Literal['cut', 'fade', 'slide-left', 'slide-up'] = 'fade'
    duration_ms: int = Field(default=500, strict=True, ge=0, le=2000)


class ScreensaverGraphic(InputModel):
    timezone: str | None = Field(default=None, max_length=80)

    @field_validator('timezone')
    @classmethod
    def valid_timezone(cls, value):
        return Ribbon.valid_timezone(value) if value is not None else None

    asset_id: ID | None = None
    width_pct: SmallFloat = Field(default=22, ge=5, le=60)
    position: Literal['center', 'top-left', 'top-right', 'bottom-left', 'bottom-right'] = 'center'
    opacity: int = Field(default=100, strict=True, ge=0, le=100)


class Presentation(InputModel):
    top: Ribbon = Field(default_factory=Ribbon)
    bottom: Ribbon = Field(default_factory=Ribbon)
    reserve_space: StrictBool = False
    reduced_motion: StrictBool = False
    transition: Transition = Field(default_factory=Transition)
    screensaver: ScreensaverGraphic = Field(default_factory=ScreensaverGraphic)


class SavePresentation(InputModel):
    expected_revision: int = Field(strict=True, ge=0)
    settings: Presentation


class PresetBody(InputModel):
    name: str = Field(min_length=1, max_length=60)
    settings: Presentation


router = APIRouter()
User = Depends(auth.require_user)


def decode(raw):
    try:
        return Presentation.model_validate(json.loads(raw or '{}')).model_dump()
    except (ValueError, TypeError):
        # Corrupt or future settings fail closed instead of taking the player down.
        return Presentation().model_dump()


def asset_ids(settings):
    return {v['asset_id'] for v in (settings['top'], settings['bottom'], settings['screensaver']) if v['asset_id'] is not None}


def check_assets(conn, settings):
    for uid in asset_ids(settings):
        row = conn.execute("SELECT id FROM uploads WHERE id=? AND kind='graphic' AND status='ready'", (uid,)).fetchone()
        if not row:
            raise HTTPException(409, 'A selected graphic is unavailable. Choose a ready graphic from the Library.')


def references(conn, uid):
    used = []
    for row in conn.execute('SELECT name,presentation_json FROM streams'):
        if uid in asset_ids(decode(row['presentation_json'])):
            used.append('stream ' + row['name'])
    for row in conn.execute('SELECT name,settings_json FROM presentation_presets'):
        if uid in asset_ids(decode(row['settings_json'])):
            used.append('preset ' + row['name'])
    return used


def _audit(conn, who, message):
    conn.execute('INSERT INTO audit(at,who,what) VALUES (?,?,?)', (db.now_ms(), who, message))


@router.get('/api/streams/{sid}/presentation')
def get_presentation(sid: int, me: Principal = User):
    row = db.row('SELECT presentation_json,presentation_revision FROM streams WHERE id=?', (sid,))
    if not row:
        raise HTTPException(404, 'No such stream')
    return {'revision': row['presentation_revision'], 'settings': decode(row['presentation_json'])}


@router.put('/api/streams/{sid}/presentation')
def save_presentation(sid: int, body: SavePresentation, me: Principal = User):
    settings = body.settings.model_dump()
    with db.tx() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT name,presentation_revision FROM streams WHERE id=?', (sid,)).fetchone()
        if not row:
            raise HTTPException(404, 'No such stream')
        if row['presentation_revision'] != body.expected_revision:
            raise HTTPException(409, 'Another editor changed these settings. Reopen the editor before saving.')
        check_assets(conn, settings)
        revision = row['presentation_revision'] + 1
        conn.execute('UPDATE streams SET presentation_json=?,presentation_revision=? WHERE id=?',
                     (json.dumps(settings, separators=(',', ':')), revision, sid))
        _audit(conn, me.username, f"updated banners and motion for '{row['name']}'")
    return {'revision': revision, 'settings': settings}


@router.get('/api/presentation/presets')
def list_presets(me: Principal = User):
    return [{'id': row['id'], 'name': row['name'], 'settings': decode(row['settings_json'])}
            for row in db.rows('SELECT * FROM presentation_presets ORDER BY name COLLATE NOCASE,id')]


@router.post('/api/presentation/presets', status_code=201)
def save_preset(body: PresetBody, me: Principal = User):
    settings = body.settings.model_dump()
    with db.tx() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT COUNT(*) FROM presentation_presets').fetchone()[0] >= 100:
            raise HTTPException(409, 'At most 100 presets are supported. Remove unused presets first.')
        check_assets(conn, settings)
        pid = conn.execute('INSERT INTO presentation_presets(name,settings_json,created_at) VALUES (?,?,?)',
                           (body.name, json.dumps(settings, separators=(',', ':')), db.now_ms())).lastrowid
        _audit(conn, me.username, f"saved presentation preset '{body.name}'")
    return {'id': pid}


@router.delete('/api/presentation/presets/{pid}')
def delete_preset(pid: int, me: Principal = User):
    with db.tx() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT name FROM presentation_presets WHERE id=?', (pid,)).fetchone()
        if not row:
            raise HTTPException(404, 'No such preset')
        conn.execute('DELETE FROM presentation_presets WHERE id=?', (pid,))
        _audit(conn, me.username, f"deleted presentation preset '{row['name']}'")
    return {'ok': True}


def public_settings(stream, uploads, slides, screensaver_stream=None):
    """Resolve validated raster references from the same snapshot as the playlist."""
    settings = decode(stream.get('presentation_json'))
    if screensaver_stream is not None and stream['id'] != screensaver_stream['id']:
        settings['screensaver'] = decode(screensaver_stream.get('presentation_json'))['screensaver']
    ready = {u['id'] for u in uploads if u['kind'] == 'graphic' and u['status'] == 'ready'}
    available = {s['upload_id']: s for s in slides if s['upload_id'] in ready and s['idx'] == 0 and s['kind'] == 'image'}
    for field in ('top', 'bottom', 'screensaver'):
        part = settings[field]
        slide = available.get(part.pop('asset_id', None))
        part['asset_url'] = '/media/' + slide['file'] if slide else None
        # Animated GIFs get a non-animated poster for reduced-motion mode.
        part['poster_url'] = '/media/' + slide['thumb'] if slide else None
    raw = json.dumps(settings, sort_keys=True, separators=(',', ':'))
    return {'settings': settings, 'version': hashlib.sha256(raw.encode()).hexdigest()[:24]}
