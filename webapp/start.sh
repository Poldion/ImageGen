#!/bin/sh
# Start der Web-App im Container:
#  1. Workflows aus dem Code im Image bauen (plus eigene Erweiterungen aus /extras) -> immer passend
#     zur Version der App, kein veralteter Stand aus einem Git-Klon
#  2. Vorlagen (z.B. Token-Rahmen) in den Eingabeordner von ComfyUI legen, falls sie fehlen oder alt sind
#  3. App starten
set -e
OUT=/tmp/workflows
python /app/build_workflows.py --api-only --extras "${IMAGEGEN_EXTRAS:-/extras}" "$OUT"
export WORKFLOW_DIR="${WORKFLOW_DIR_OVERRIDE:-$OUT/api}"
if [ -d "${INPUT_DIR:-/input}" ] && [ -w "${INPUT_DIR:-/input}" ]; then
  for f in /app/assets/*.png; do
    [ -e "$f" ] && cp -u "$f" "${INPUT_DIR:-/input}/" || true
  done
fi
exec python /app/app.py
