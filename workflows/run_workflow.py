#!/usr/bin/env python3
"""
Fuehrt einen API-Workflow ueber ComfyUI aus und misst die Zeit.

Nutzung:  python3 run_workflow.py api/anime_anima.json "a girl with silver hair ..." [seed] [http://127.0.0.1:8188]
          python3 run_workflow.py api/bearbeiten_qwen21.json "Anweisung" [seed] [url] --image bild.png
Setzt den Prompt (Knoten 10, "value") und den Seed (KSampler "seed" bzw. RandomNoise
"noise_seed"), optional das Eingabebild (alle LoadImage-Knoten; Datei muss im
ComfyUI-input-Ordner liegen), wartet auf das Ergebnis und gibt Dauer und Bilddatei(en) aus.
"""
import json
import sys
import time
import urllib.request

args = sys.argv[1:]
image = None
if "--image" in args:
    i = args.index("--image")
    image = args[i + 1]
    del args[i:i + 2]
path, prompt = args[0], args[1]
seed = int(args[2]) if len(args) > 2 else int(time.time()) % 2**31
COMFY = (args[3] if len(args) > 3 else "http://127.0.0.1:8188").rstrip("/")

wf = json.load(open(path, encoding="utf-8"))
wf["10"]["inputs"]["value"] = prompt
for node in wf.values():
    for key in ("seed", "noise_seed"):
        if key in node["inputs"] and not isinstance(node["inputs"][key], list):
            node["inputs"][key] = seed
    if image and node["class_type"] == "LoadImage":
        node["inputs"]["image"] = image


def call(route, body=None):
    req = urllib.request.Request(f"{COMFY}{route}", data=json.dumps(body).encode() if body else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


t0 = time.time()
try:
    pid = call("/prompt", {"prompt": wf})["prompt_id"]
except urllib.error.HTTPError as e:
    sys.exit(f"ABGELEHNT: {e.read().decode()[:800]}")
while True:
    time.sleep(1)
    h = call(f"/history/{pid}")
    if pid in h:
        h = h[pid]
        break
st = h.get("status", {})
if st.get("status_str") != "success":
    msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
    sys.exit(f"FEHLER nach {time.time() - t0:.1f} s: {json.dumps(msgs)[:800]}")
files = [i["subfolder"] + "/" + i["filename"] for o in h["outputs"].values() for i in o.get("images", [])]
print(f"OK {time.time() - t0:.1f} s | seed {seed} | {', '.join(files)}")
