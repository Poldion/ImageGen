"""Texte aus den Workflow-Daten für scripts/check_i18n.py: Die Oberfläche zeigt Namen, Arten, Zusammenfassungen,
Größen und Options-Stufen aus workflows/api/index.json mit tr(wert) an."""
import json
import os

# feste Größen der Oberfläche (index.html, SIZES und „Wie Bild“)
SIZES = ["Quadrat", "Quer", "Hoch", "Quer 3:2", "Hoch 2:3", "Wie Bild"]


def texts(root: str) -> list[str]:
    out = list(SIZES)
    path = os.path.join(root, "..", "workflows", "api", "index.json")
    if not os.path.exists(path):        # nur die Web-App ausgecheckt (z. B. im Test-Container)
        return out
    with open(path, encoding="utf-8") as f:
        index = json.load(f)
    for w in index:
        out += [w.get(k) for k in ("title", "group", "variant", "summary", "summary_nsfw")]
        out += [s[0] for s in w.get("sizes") or []]
        for opt in ("motion", "action"):
            for lv in (w.get(opt) or {}).get("levels", []):
                out += [lv.get("label"), lv.get("hint")]
    return [t for t in out if t]
