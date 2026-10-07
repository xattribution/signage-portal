#!/bin/sh
# Run from any directory after installing requirements-dev.txt into a clean venv.
set -eu
cd "$(dirname "$0")/.."
python -m pip check
python -m compileall -q web worker
for file in web/app/static/admin.js web/app/static/login.js web/app/static/setup.js web/app/static/player.js web/app/static/overlay.js web/app/static/js/*.js; do
  node --check "$file"
done
python -m pytest -q
