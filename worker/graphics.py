"""Small, alpha-preserving graphics. Only run inside the network-less worker.

SVG is an input format, never a browser output. Reject active/linked/XML content,
then rasterize with a deny-all resource loader. PNG/GIF are decoded and re-encoded
with bounded dimensions, frames, duration, and aggregate pixel work.
"""
import io
import math
import re
from pathlib import Path
from xml.etree.ElementTree import tostring

from PIL import Image, ImageOps
from defusedxml.ElementTree import fromstring

MAX_INPUT = 32 * 1048576
MAX_SVG = 2 * 1048576
MAX_PIXELS = 8_000_000
MAX_FRAMES = 120
MAX_FRAME_PIXELS = 32_000_000
SVG_NS = 'http://www.w3.org/2000/svg'
TAGS = {'svg', 'g', 'defs', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
        'linearGradient', 'radialGradient', 'stop', 'clipPath', 'title', 'desc', 'text', 'tspan'}
ATTRS = {'id', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height',
         'd', 'points', 'viewBox', 'preserveAspectRatio', 'transform', 'fill', 'fill-rule', 'fill-opacity',
         'stroke', 'stroke-width', 'stroke-opacity', 'stroke-linecap', 'stroke-linejoin', 'stroke-miterlimit',
         'stroke-dasharray', 'stroke-dashoffset', 'opacity', 'clip-path', 'clip-rule', 'clipPathUnits',
         'gradientUnits', 'gradientTransform', 'spreadMethod', 'fx', 'fy', 'offset', 'stop-color', 'stop-opacity',
         'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline', 'dx', 'dy',
         'color', 'version'}
STYLE_ATTRS = {'fill', 'fill-rule', 'fill-opacity', 'stroke', 'stroke-width', 'stroke-opacity', 'stroke-linecap',
               'stroke-linejoin', 'stroke-miterlimit', 'stroke-dasharray', 'stroke-dashoffset', 'opacity',
               'clip-path', 'clip-rule', 'stop-color', 'stop-opacity', 'font-family', 'font-size', 'font-weight',
               'font-style', 'text-anchor', 'dominant-baseline', 'color'}
LOCAL_REF = re.compile(r'url\(\s*#[A-Za-z_][A-Za-z0-9_.-]{0,79}\s*\)')


def _value(value):
    if len(value) > 100_000 or any(c in value for c in ('\\', '@', ':', '&', '\x00', '<', '>')):
        raise ValueError('SVG contains an unsupported attribute value')
    # Local paint/clip references only. All resource loads are also denied below.
    rest = LOCAL_REF.sub('', value)
    if 'url' in rest.lower() or re.search(r'(?i)(https?|file|data)\s*\(', rest):
        raise ValueError('SVG links and external resources are not supported')
    return value


def sanitize_svg(data):
    if not 0 < len(data) <= MAX_SVG:
        raise ValueError('SVG must be at most 2 MB')
    try:
        # Require UTF-8. This avoids alternate-encoding bypasses of the PI guard.
        text = data.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('Export the SVG as UTF-8') from None
    without_decl = re.sub(r'^\s*<\?xml\s[^?]*\?>', '', text, count=1)
    if '<?' in without_decl:
        raise ValueError('SVG processing instructions are not supported')
    root = fromstring(data, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag not in ('svg', '{' + SVG_NS + '}svg'):
        raise ValueError('Not an SVG document')
    stack = [(root, 0)]
    count = 0
    ids = set()
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > 5000 or depth > 32:
            raise ValueError('SVG is too complex')
        local = node.tag
        if local.startswith('{' + SVG_NS + '}'):
            local = local.split('}', 1)[1]
        if local not in TAGS:
            raise ValueError('Unsupported SVG element: export a static, self-contained logo using paths')
        for key, value in list(node.attrib.items()):
            if key == 'style':
                # A deliberately small CSS grammar: presentation declarations only.
                for declaration in value.split(';'):
                    if not declaration.strip():
                        continue
                    if declaration.count(':') != 1:
                        raise ValueError('Unsupported SVG style')
                    prop, val = (v.strip() for v in declaration.split(':', 1))
                    if prop not in STYLE_ATTRS:
                        raise ValueError('Unsupported SVG style property')
                    node.set(prop, _value(val))
                del node.attrib[key]
                continue
            if key not in ATTRS:
                raise ValueError('SVG event handlers, links, and unsupported attributes are not allowed')
            _value(value)
            if key == 'id':
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,79}', value) or value in ids:
                    raise ValueError('SVG IDs must be short and unique')
                ids.add(value)
        stack.extend((child, depth + 1) for child in node)
    view = root.get('viewBox')
    if view:
        values = [float(v) for v in re.split(r'[\s,]+', view.strip())]
        if len(values) != 4 or not all(math.isfinite(v) and abs(v) < 1e7 for v in values):
            raise ValueError('Invalid SVG viewBox')
        width, height = values[2:]
    else:
        def dimension(name):
            raw = root.get(name, '').removesuffix('px')
            if not re.fullmatch(r'\d+(?:\.\d+)?', raw):
                raise ValueError('SVG needs a viewBox or explicit pixel dimensions')
            return float(raw)
        width, height = dimension('width'), dimension('height')
    if not 0 < width <= 1e6 or not 0 < height <= 1e6:
        raise ValueError('Invalid SVG dimensions')
    scale = min(2048 / width, 2048 / height)
    out_w, out_h = max(1, round(width * scale)), max(1, round(height * scale))
    return tostring(root), out_w, out_h


def _deny_resource(*_args, **_kwargs):
    raise ValueError('SVG resources cannot be fetched')


def svg_image(data):
    from cairosvg.surface import PNGSurface
    clean, width, height = sanitize_svg(data)
    png = PNGSurface.convert(bytestring=clean, output_width=width, output_height=height,
                             unsafe=False, url_fetcher=_deny_resource)
    with Image.open(io.BytesIO(png), formats=['PNG']) as image:
        return image.convert('RGBA')


def _clean_rgba(image):
    # copy pixel bytes into a metadata-free image; do not copy comments/ICC/EXIF.
    rgba = image.convert('RGBA')
    return Image.frombytes('RGBA', rgba.size, rgba.tobytes())


def _gif_palette(frame):
    alpha = frame.getchannel('A')
    result = frame.convert('RGB').quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    palette = result.getpalette()[:765] + [0, 0, 0]
    result.putpalette(palette)
    result.paste(255, mask=alpha.point(lambda value: 255 if value < 128 else 0))
    result.info.clear()
    return result


def render_graphic(src: Path, out: Path):
    if src.stat().st_size > MAX_INPUT:
        raise ValueError('Graphic exceeds 32 MB')
    frames, durations = [], []
    if src.suffix.lower() == '.svg':
        if src.stat().st_size > MAX_SVG:
            raise ValueError('SVG exceeds 2 MB')
        frames = [_clean_rgba(svg_image(src.read_bytes()))]
    else:
        expected = 'GIF' if src.suffix.lower() == '.gif' else 'PNG'
        with Image.open(src, formats=[expected]) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError('Graphic exceeds 8 megapixels')
            if expected == 'PNG' and getattr(image, 'is_animated', False):
                raise ValueError('Use GIF for animation; animated PNG is not supported')
            total_pixels = 0
            for index in range(MAX_FRAMES + 1):
                try:
                    image.seek(index)
                except EOFError:
                    break
                if index >= MAX_FRAMES:
                    raise ValueError('GIF exceeds 120 frames')
                frame = _clean_rgba(ImageOps.exif_transpose(image) if expected == 'PNG' else image)
                bound = 1280 if expected == 'GIF' else 2048
                frame.thumbnail((bound, bound), Image.Resampling.LANCZOS)
                total_pixels += frame.width * frame.height
                if total_pixels > MAX_FRAME_PIXELS:
                    raise ValueError('GIF exceeds the decoded-frame budget; reduce dimensions or frame count')
                frames.append(frame)
                duration = int(image.info.get('duration', 100) or 100)
                durations.append(max(50, min(10000, duration)))
                if sum(durations) > 60000:
                    raise ValueError('GIF must be at most 60 seconds per loop')
                if expected == 'PNG':
                    break
    if not frames:
        raise ValueError('Graphic contains no frames')
    poster = frames[0]
    if len(frames) == 1:
        name = '000.png'
        poster.save(out / name, format='PNG', compress_level=6)
    else:
        name = '000.gif'
        palette_frames = [_gif_palette(frame) for frame in frames]
        palette_frames[0].save(out / name, format='GIF', save_all=True, append_images=palette_frames[1:],
                               loop=0, duration=durations, disposal=2, transparency=255, optimize=False)
    thumbnail = poster.copy()
    thumbnail.thumbnail((480, 480), Image.Resampling.LANCZOS)
    background = Image.new('RGB', thumbnail.size, '#17212b')
    background.paste(thumbnail, mask=thumbnail.getchannel('A'))
    background.save(out / '000_t.jpg', 'JPEG', quality=85)
    if (out / name).stat().st_size > 20 * 1048576:
        raise ValueError('Normalized graphic exceeds 20 MB')
    return [{'kind': 'image', 'file': name, 'thumb': '000_t.jpg', 'duration_ms': None}]
