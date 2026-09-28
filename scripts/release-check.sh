#!/bin/sh
# Requires a configured staging .env, Docker, Node, the dev requirements and pip-audit.
# Downloads packages/images and builds locally; does not deploy services.
set -eu
cd "$(dirname "$0")/.."
./scripts/check.sh
python -m pip_audit -r web/requirements.txt
python -m pip_audit -r worker/requirements.txt
docker compose config --quiet
docker compose build --pull
# Save exact resolved dependencies from the built images for the release record.
mkdir -p release-evidence
docker run --rm --network none signage-web:modern python -m pip freeze > release-evidence/web-freeze.txt
docker run --rm --network none signage-worker:modern python -m pip freeze > release-evidence/worker-freeze.txt
printf '\nBuild and dependency checks complete. Image/OS vulnerability scanning and device acceptance are still required.\n'
