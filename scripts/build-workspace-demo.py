"""Build an offline, in-memory preview using the delivered administrator UI.

Sample content only. No accounts, real uploads, NAS access or network requests.
The mock adapter is never loaded by the production app.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / 'web/app/static'
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--fixture', type=Path, default=ROOT/'docs/evidence/topnav-demo-fixture.json')
args = parser.parse_args()
fixture = json.loads(args.fixture.read_text())

def bundle(name: str) -> str:
    code = re.sub(r'^import .*?;\s*$', '', (STATIC/name).read_text(), flags=re.M)
    return code.replace('export ', '').replace('localStorage', 'demoLocalStorage')

def literal(obj: object) -> str:
    return json.dumps(obj, separators=(',', ':')).replace('<', '\\u003c')

shim = '\n'.join(f'const {name}={literal(fixture[key])};' for name,key in [
    ('DEMO_SEED','overview'),('DEMO_MEDIA','media'),
    ('DEMO_PRESENTATIONS','presentations'),('DEMO_PRESETS','presets')])
shim += '\n' + (ROOT/'scripts/workspace-demo-adapter.js').read_text()
code = shim + '\n' + '\n'.join(bundle(name) for name in [
    'js/ui.js','js/api.js','js/presentation-editor.js','admin.js'])
code += '\ndemoInstallControls();'
html = re.sub(r'<link[^>]+>', '', (STATIC/'admin.html').read_text())
html = re.sub(r'<script.*?</script>', '', html, flags=re.S)
html = html.replace('<html lang="en">', '<html lang="en" data-theme="light">')
html = html.replace('LIVE WORKSPACE','SAMPLE WORKSPACE')
html = html.replace('Arrange the rotation. Keep your displays in step.', 'Interactive preview. Sample content only; no connected displays.')
html = html.replace('<span id="workspace-date"></span>', '<span id="workspace-date"></span><button class="link" id="demo-reset">Reset demo</button>')
css = (STATIC/'admin.css').read_text()+'\n'+(STATIC/'overlay.css').read_text()+'''
.demo-stage {position:relative;aspect-ratio:16/9;background:#10151c;overflow:hidden;border-radius:6px;margin-bottom:12px;}
.demo-content-root {position:absolute;inset:0;overflow:hidden;}
.demo-content-root img {display:block;width:100%;height:100%;object-fit:contain;}
.demo-clock {height:100%;display:grid;place-items:center;font-size:clamp(28px,8vw,68px);color:#e0e6ed;}
.demo-overlays {position:absolute;inset:0;pointer-events:none;}
'''
# No live servers, external fonts, credentials, or third-party assets are embedded.
html = html.replace('</head>', '<style>'+css+'</style></head>')
script = (STATIC/'overlay.js').read_text()+'\n(function(){\n'+code+'\n})();'
html = html.replace('</body>', '<script>'+script.replace('</script','<\\/script')+'</script></body>')
args.output.parent.mkdir(parents=True,exist_ok=True)
args.output.write_text(html)
print(f'Created {args.output} ({args.output.stat().st_size:,} bytes)')
