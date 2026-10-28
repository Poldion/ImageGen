#!/usr/bin/env bash
# Tests der Bilder-Web-App im App-Image (gleiche Python-/Paketversionen wie im Betrieb).
# Braucht nur Docker; ComfyUI wird nachgebaut (tests/fake_comfy.py), echte Daten bleiben unberuehrt.
# Die Workflows baut der Test wie die App beim Start, mit der Test-Erweiterung tests/extras/.
#   imagegen/webapp/run_tests.sh [pytest-Optionen, z.B. -k login -x]
set -euo pipefail
cd "$(dirname "$0")"
docker build -q -t imagegen-webapp:test -f Dockerfile .. >/dev/null
docker run --rm \
  -v "$PWD":/src:ro -e PYTHONDONTWRITEBYTECODE=1 \
  -w /tmp imagegen-webapp:test \
  sh -c 'pip install -q --disable-pip-version-check --no-cache-dir pytest==8.3.4 >/dev/null 2>&1 &&
         python /app/build_workflows.py --api-only --extras /src/tests/extras /tmp/wf >/dev/null &&
         cp -r /src /tmp/webapp && cd /tmp/webapp && WORKFLOW_DIR=/tmp/wf/api python -m pytest -q -p no:cacheprovider tests "$@"' sh "$@"
