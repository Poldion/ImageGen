"""
Nachgebautes ComfyUI fuer die Tests der Web-App. Bildet nur das nach, was die App nutzt:
/upload/image, /prompt, /queue, /history (lesen + loeschen), /view.

Steuerung ueber POST /_ctl:
  {"mode": "ok"|"error"|"hold"}  wie neue Auftraege enden (hold = bleiben in der Warteschlange)
  {"release": true}              gehaltene Auftraege fertigstellen
  {"restart": true}              Neustart nachspielen: Warteschlange + Verlauf weg
  {"delay": 0.2}                 Rechenzeit pro Auftrag
"""
import os
import struct
import threading
import time
import uuid
import zlib

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

OUTPUT = os.environ["OUTPUT_DIR"]
INPUT = os.environ["INPUT_DIR"]
app = FastAPI()
S = {"mode": "ok", "delay": 0.2, "queue": [], "history": {}, "prompts": {}, "counter": {}}
LOCK = threading.Lock()


def png(seed: int) -> bytes:
    """Kleines gueltiges PNG, Inhalt haengt vom Seed ab (Bilder unterscheidbar)."""
    raw = b"".join(b"\x00" + bytes([(seed + x + y) % 256 for x in range(8) for _ in range(3)]) for y in range(8))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _finish(pid: str, ok: bool):
    wf = S["prompts"][pid]
    if not ok:
        S["history"][pid] = {"status": {"status_str": "error", "completed": False, "messages": [
            ["execution_error", {"exception_message": "Simulierter Fehler"}]]}, "outputs": {}}
        return
    outputs = {}
    for nid, node in wf.items():
        if node["class_type"] not in ("SaveImage", "SaveVideo"):
            continue
        prefix = node["inputs"]["filename_prefix"]
        sub, _, base = prefix.rpartition("/")
        n = S["counter"].get(prefix, 0) + 1
        S["counter"][prefix] = n
        video = node["class_type"] == "SaveVideo"
        name = f"{base}_{n:05d}_.{'mp4' if video else 'png'}"
        os.makedirs(os.path.join(OUTPUT, sub), exist_ok=True)
        seed = next((v["inputs"].get("seed", v["inputs"].get("noise_seed")) for v in wf.values()
                     if "seed" in v["inputs"] or "noise_seed" in v["inputs"]), 0)
        with open(os.path.join(OUTPUT, sub, name), "wb") as f:
            # Video: nur ein MP4-Kopf + Fuelldaten (die App prueft keine Videoinhalte)
            f.write(b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 40 if video else png(int(seed) % 251))
        outputs[nid] = {"images": [{"filename": name, "subfolder": sub, "type": "output"}]}
    S["history"][pid] = {"status": {"status_str": "success", "completed": True, "messages": []}, "outputs": outputs}


def _worker(pid: str, mode: str):
    time.sleep(S["delay"])
    with LOCK:
        if mode == "hold" or pid not in [q[1] for q in S["queue"]]:
            return
        S["queue"] = [q for q in S["queue"] if q[1] != pid]
        _finish(pid, mode == "ok")


@app.post("/prompt")
async def prompt(request: Request):
    wf = (await request.json())["prompt"]
    for nid, node in wf.items():
        if node["class_type"] == "LoadImage":
            ref = node["inputs"]["image"]
            path = (os.path.join(OUTPUT, ref[:-len(" [output]")]) if ref.endswith(" [output]")
                    else os.path.join(INPUT, ref))
            if not os.path.isfile(path):
                return JSONResponse({"error": {"message": "Prompt outputs failed validation"},
                                     "node_errors": {nid: f"Invalid image file: {ref}"}}, status_code=400)
    pid = str(uuid.uuid4())
    with LOCK:
        S["prompts"][pid] = wf
        S["queue"].append([len(S["queue"]), pid, wf, {}, []])
    threading.Thread(target=_worker, args=(pid, S["mode"]), daemon=True).start()
    return {"prompt_id": pid, "number": 0, "node_errors": {}}


@app.get("/queue")
async def queue():
    with LOCK:
        return {"queue_running": S["queue"][:1], "queue_pending": S["queue"][1:]}


@app.post("/queue")
async def queue_delete(request: Request):
    body = await request.json()
    with LOCK:
        running = S["queue"][:1]
        S["queue"] = running + [q for q in S["queue"][1:] if q[1] not in body.get("delete", [])]
    return {}


@app.post("/interrupt")
async def interrupt(request: Request):
    pid = (await request.json()).get("prompt_id")
    with LOCK:
        if S["queue"] and S["queue"][0][1] == pid:          # nur, wenn genau dieser gerade laeuft
            S["queue"].pop(0)
            S["history"][pid] = {"status": {"status_str": "error", "completed": False, "messages": [
                ["execution_interrupted", {}]]}, "outputs": {}}
            S["interrupted"] = S.get("interrupted", []) + [pid]
    return {}


@app.get("/history/{pid}")
async def history_one(pid: str):
    return {pid: S["history"][pid]} if pid in S["history"] else {}


@app.get("/history")
async def history_all(max_items: int = 100):
    return dict(list(S["history"].items())[-max_items:])


@app.post("/history")
async def history_delete(request: Request):
    body = await request.json()
    for pid in body.get("delete", []):
        S["history"].pop(pid, None)
    if body.get("clear"):
        S["history"].clear()
    return {}


@app.post("/upload/image")
async def upload(image: UploadFile = File(...), subfolder: str = Form(""), type: str = Form("input")):
    os.makedirs(os.path.join(INPUT, subfolder), exist_ok=True)
    with open(os.path.join(INPUT, subfolder, image.filename), "wb") as f:
        f.write(await image.read())
    return {"name": image.filename, "subfolder": subfolder, "type": type}


@app.get("/view")
async def view(filename: str, subfolder: str = "", type: str = "output", preview: str = ""):
    p = os.path.join(OUTPUT if type == "output" else INPUT, subfolder, filename)
    if not os.path.isfile(p):
        return JSONResponse({}, status_code=404)
    return FileResponse(p, media_type="image/png")


@app.post("/api/chat")
async def llm_chat(request: Request):
    """Nachgebautes Sprachmodell (Ollama /api/chat) fuer "Ausformulieren"."""
    body = await request.json()
    S["llm"] = body
    if S.get("llm_fail"):
        return JSONResponse({"error": "kaputt"}, status_code=500)
    user = body["messages"][-1]
    return {"message": {"role": "assistant", "content": S.get("llm_reply") or
                        f"Prompt: EXPANDED {user['content']} images={len(user.get('images', []))}"}}


@app.get("/proxy/memory")
async def proxy_memory():
    """Custom-Node proxy_memory: was ComfyUI haelt (ueber /_ctl {"pm": {...}} einstellbar)."""
    return S.get("pm") or {"rss": 3 * 1024 ** 3, "gpu": 0, "loaded_models": 0, "busy": False}


@app.post("/proxy/free")
async def proxy_free():
    S["freed"] = S.get("freed", 0) + 1
    return {"rss_before": 1, "rss_after": 0}


@app.post("/_ctl")
async def ctl(request: Request):
    body = await request.json()
    with LOCK:
        if "mode" in body:
            S["mode"] = body["mode"]
        for k in ("llm_reply", "llm_fail", "pm"):
            if k in body:
                S[k] = body[k]
        if "delay" in body:
            S["delay"] = body["delay"]
        if body.get("release"):
            for q in list(S["queue"]):
                _finish(q[1], True)
            S["queue"].clear()
        if body.get("restart"):
            S["queue"].clear()
            S["history"].clear()
    return {"ok": True}
