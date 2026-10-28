"""
Tests der Bilder-Web-App gegen ein nachgebautes ComfyUI (fake_comfy.py).

Laufen im App-Image (dort sind FastAPI/httpx installiert):  imagegen/webapp/run_tests.sh
Jeder Test bekommt eine frische Datenbank und leere Ein-/Ausgabeordner.
"""
import base64
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="webapp-test-")
OUT, IN = os.path.join(TMP, "output"), os.path.join(TMP, "input")
os.makedirs(OUT), os.makedirs(IN)
os.environ.update(OUTPUT_DIR=OUT, INPUT_DIR=IN, WEBAPP_DB=os.path.join(TMP, "start.db"),
                  WORKFLOW_DIR=os.environ.get("WORKFLOW_DIR", os.path.join(HERE, "..", "..", "workflows", "api")))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


FAKE_PORT = _free_port()
os.environ["COMFYUI_URL"] = f"http://127.0.0.1:{FAKE_PORT}"
os.environ["WEBAPP_LLM_URL"] = f"http://127.0.0.1:{FAKE_PORT}"         # nachgebautes Sprachmodell
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from starlette.requests import Request  # noqa: E402

import app as webapp  # noqa: E402
import fake_comfy  # noqa: E402

PLACEHOLDER = "bild_hier_hochladen.png"
PW = "test-passwort-123"


@pytest.fixture(scope="session", autouse=True)
def fake_server():
    srv = uvicorn.Server(uvicorn.Config(fake_comfy.app, host="127.0.0.1", port=FAKE_PORT, log_level="warning"))
    threading.Thread(target=srv.run, daemon=True).start()
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{FAKE_PORT}/queue")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield
    srv.should_exit = True


@pytest.fixture(scope="session")
def tc(fake_server):
    with TestClient(webapp.app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh(tc, monkeypatch, tmp_path):
    """Frische DB, leere Ordner (+ Platzhalter-Eingabebild), Fake-ComfyUI zurueckgesetzt."""
    monkeypatch.setattr(webapp, "DB_PATH", str(tmp_path / "webapp.db"))
    webapp.init_db()
    webapp._fails.clear()
    for d in (OUT, IN):
        shutil.rmtree(d)
        os.makedirs(d)
    with open(os.path.join(IN, PLACEHOLDER), "wb") as f:
        f.write(fake_comfy.png(1))
    httpx.post(f"http://127.0.0.1:{FAKE_PORT}/_ctl", json={"mode": "ok", "delay": 0.15, "restart": True,
                                                         "llm_reply": "", "llm_fail": False, "pm": None})
    tc.cookies.clear()
    yield


def ctl(**kw):
    httpx.post(f"http://127.0.0.1:{FAKE_PORT}/_ctl", json=kw)


class As:
    """Anfragen als ein bestimmter Benutzer (eigenes Session-Cookie) ueber den einen TestClient."""

    def __init__(self, tc, token=None):
        self.tc, self.token = tc, token

    def _h(self, headers=None):
        h = dict(headers or {})
        if self.token:
            h["cookie"] = f"{webapp.COOKIE}={self.token}"
        return h

    def __getattr__(self, method):
        def call(url, headers=None, **kw):
            self.tc.cookies.clear()
            r = getattr(self.tc, method)(url, headers=self._h(headers), **kw)
            tok = r.cookies.get(webapp.COOKIE)
            if tok:
                self.token = tok
            return r
        return call


def setup_admin(tc, name="admin") -> As:
    a = As(tc)
    r = a.post("/api/setup", json={"name": name, "password": PW})
    assert r.status_code == 200, r.text
    return a


def make_user(tc, admin: As, name="anna", is_admin=False) -> As:
    assert admin.post("/api/users", json={"name": name, "password": PW, "admin": is_admin}).status_code == 200
    u = As(tc)
    assert u.post("/api/login", json={"name": name, "password": PW}).status_code == 200
    return u


def wait_job(who: As, pid: str, timeout=10) -> dict:
    end = time.time() + timeout
    while time.time() < end:
        j = who.get(f"/api/job/{pid}").json()
        if j["state"] in ("done", "error", "lost"):
            return j
        time.sleep(0.1)
    raise AssertionError(f"Auftrag {pid} wurde nicht fertig: {j}")


def generate(who: As, workflow="anime_anima", prompt="a cat", **data):
    return who.post("/api/generate", data={"workflow": workflow, "prompt": prompt, **data})


def gen_done(who: As, **kw) -> dict:
    r = generate(who, **kw)
    assert r.status_code == 200, r.text
    j = wait_job(who, r.json()["id"])
    assert j["state"] == "done", j
    return j["images"][0]


# ------------------------------------------------------------------ Einrichtung, Seiten

def test_setup_flow(tc):
    a = As(tc)
    assert "Einrichtung" in a.get("/").text
    assert a.get("/").headers["cache-control"] == "no-store"
    assert a.get("/api/workflows").status_code == 401
    assert a.post("/api/setup", json={"name": "x", "password": PW}).status_code == 400          # Name zu kurz
    assert a.post("/api/setup", json={"name": "admin", "password": "kurz"}).status_code == 400  # PW zu kurz
    assert a.post("/api/setup", json={"name": "admin", "password": PW}).status_code == 200
    assert a.get("/api/me").json() == {"name": "admin", "admin": True, "nsfw": True, "version": webapp.VERSION}
    assert As(tc).post("/api/setup", json={"name": "boese", "password": PW}).status_code == 409
    assert "Anmelden" in As(tc).get("/").text
    assert "Verwaltung" in a.get("/admin").text


def test_pages_and_static(tc):
    a = setup_admin(tc)
    assert 'id="go"' in a.get("/").text
    assert a.get("/ui.js").status_code == 200 and a.get("/common.css").status_code == 200
    assert a.get("/app.py").status_code == 404          # kein Quelltext ueber die Static-Route
    assert a.get("/manifest.webmanifest").json()["name"] == "Bilder"


def test_i18n_script_and_translated_errors(tc):
    a = setup_admin(tc)
    js = a.get("/i18n.js")
    assert js.status_code == 200 and "window.I18N" in js.text and '"Meine Bilder": "My images"' in js.text
    assert generate(a, workflow="gibtsnicht").json()["detail"] == "Workflow unbekannt"
    en = {"X-ImageGen-Lang": "en"}
    r = a.post("/api/generate", data={"workflow": "gibtsnicht", "prompt": "a cat"}, headers=en)
    assert r.json()["detail"] == "Unknown workflow"
    r = a.post("/api/generate", data={"workflow": "anime_anima", "prompt": "x" * 4001}, headers=en)
    assert r.json()["detail"] == "Prompt (max. 4000) or variables (max. 60000 characters) too long"
    r = As(tc).get("/manifest.webmanifest", headers={"Accept-Language": "en-GB,en"})
    assert r.json()["name"] == "Images"


def test_all_texts_translated():
    import subprocess
    out = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "check_i18n.py")],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_security_headers(tc):
    r = As(tc).get("/")
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_session_cookie_flags(tc):
    r = As(tc).post("/api/setup", json={"name": "admin", "password": PW})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc and "secure" not in sc   # HTTP im Test


# ------------------------------------------------------------------ Login-Schutz

def test_login_wrong_and_unknown(tc):
    setup_admin(tc)
    assert As(tc).post("/api/login", json={"name": "admin", "password": "falsch-falsch"}).status_code == 401
    assert As(tc).post("/api/login", json={"name": "gibtsnicht", "password": PW}).status_code == 401
    assert As(tc).post("/api/login", json={"name": "ADMIN", "password": PW}).status_code == 200  # Name egal ob gross
    assert As(tc).post("/api/login", content=b"{kaputt", headers={"content-type": "application/json"}).status_code == 400


def test_login_rate_limit_ignores_spoofed_forwarded_for(tc, monkeypatch):
    monkeypatch.setattr(webapp, "FAIL_DELAY", 0)
    setup_admin(tc)
    for i in range(webapp.LOGIN_MAX_PER_IP):
        # wechselnde X-Forwarded-For von einem NICHT vertrauten Absender zaehlen nicht
        r = As(tc).post("/api/login", json={"name": f"user{i}", "password": "x" * 8},
                        headers={"x-forwarded-for": f"203.0.113.{i}"})
        assert r.status_code == 401
    r = As(tc).post("/api/login", json={"name": "admin", "password": PW}, headers={"x-forwarded-for": "198.51.100.7"})
    assert r.status_code == 429


def test_login_rate_limit_per_name(tc, monkeypatch):
    monkeypatch.setattr(webapp, "FAIL_DELAY", 0)
    setup_admin(tc)
    webapp._fails["name:admin"] = [time.time()] * webapp.LOGIN_MAX_PER_NAME
    assert As(tc).post("/api/login", json={"name": "admin", "password": PW}).status_code == 429


def _req(peer, headers):
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
             "client": (peer, 1234), "scheme": "http", "server": ("x", 80), "query_string": b""}
    return Request(scope)


def test_client_ip_parsing():
    # direkter Client: Header wird ignoriert
    assert webapp.client_ip(_req("203.0.113.5", {"x-forwarded-for": "1.2.3.4"})) == "203.0.113.5"
    # ueber vertrauten Proxy: rechte, nicht vertraute Adresse zaehlt (vorne gefaelscht)
    assert webapp.client_ip(_req("10.0.0.2", {"x-forwarded-for": "6.6.6.6, 203.0.113.9"})) == "203.0.113.9"
    assert webapp.client_ip(_req("10.0.0.2", {"x-forwarded-for": "203.0.113.9, 192.168.1.1"})) == "203.0.113.9"
    assert webapp.client_ip(_req("10.0.0.2", {})) == "10.0.0.2"
    assert webapp._is_https(_req("10.0.0.2", {"x-forwarded-proto": "https"})) is True
    assert webapp._is_https(_req("203.0.113.5", {"x-forwarded-proto": "https"})) is False


def test_origin_check(tc):
    a = setup_admin(tc)
    assert a.post("/api/users", json={"name": "bob", "password": PW},
                  headers={"origin": "https://evil.example"}).status_code == 403
    assert a.post("/api/users", json={"name": "bob", "password": PW},
                  headers={"origin": "http://testserver"}).status_code == 200


# ------------------------------------------------------------------ Benutzerverwaltung

def test_user_admin_rules(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    assert u.get("/api/users").status_code == 403
    assert u.get("/admin").status_code == 403
    assert a.post("/api/users", json={"name": "Anna", "password": PW}).status_code == 409   # doppelt
    me = [x for x in a.get("/api/users").json() if x["me"]][0]
    assert a.patch(f"/api/users/{me['id']}", json={"admin": False}).status_code == 400    # letzter Admin
    assert a.delete(f"/api/users/{me['id']}").status_code == 400                          # eigenes Konto
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    assert a.patch(f"/api/users/{uid}", json={"admin": True}).status_code == 200
    assert u.get("/api/me").json()["admin"] is True
    assert a.patch(f"/api/users/{me['id']}", json={"admin": False}).status_code == 200    # jetzt gibt es 2
    assert a.get("/api/users").status_code == 403


def test_password_reset_ends_sessions(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    assert a.patch(f"/api/users/{uid}", json={"password": "neues-passwort-1"}).status_code == 200
    assert u.get("/api/me").status_code == 401
    assert As(tc).post("/api/login", json={"name": "anna", "password": "neues-passwort-1"}).status_code == 200
    assert a.get("/api/me").status_code == 200          # Admin selbst bleibt angemeldet


def test_own_password_change_logs_out_other_devices(tc):
    a = setup_admin(tc)
    handy = As(tc)
    assert handy.post("/api/login", json={"name": "admin", "password": PW}).status_code == 200
    assert a.post("/api/me/password", json={"old": "falsch-123", "new": "x" * 10}).status_code == 400
    assert a.post("/api/me/password", json={"old": PW, "new": "kurz"}).status_code == 400
    assert a.post("/api/me/password", json={"old": PW, "new": "neues-passwort-2"}).status_code == 200
    assert a.get("/api/me").status_code == 200 and handy.get("/api/me").status_code == 401


def test_delete_user_keeps_images_and_drops_jobs(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    img = gen_done(u)
    ctl(mode="hold")
    assert generate(u).status_code == 200                # offener Auftrag
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    assert a.delete(f"/api/users/{uid}").status_code == 200
    with webapp.db() as con:
        assert con.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ?", (uid,)).fetchone()[0] == 0
    items = a.get("/api/admin/images", params={"owner": "none"}).json()["items"]
    assert any(i["filename"] == img["filename"] for i in items)
    # neuer Benutzer (evtl. gleiche ID) erbt nichts
    b = make_user(tc, a, "bert")
    assert b.get("/api/recent").json() == [] and b.get("/api/jobs/active").json() == []


# ------------------------------------------------------------------ Erzeugen

def test_workflows_listing(tc):
    a = setup_admin(tc)
    wfs = {w["file"]: w for w in a.get("/api/workflows").json()}
    assert {"gezeichnet_dnd", "bearbeiten_qwen21", "charakter_qwen21"} <= set(wfs)
    assert wfs["bearbeiten_qwen21"]["mode"] == "edit" and wfs["bearbeiten_qwen21"]["default_size"] is None
    assert "fields" not in wfs["gezeichnet_dnd"]           # Interna bleiben auf dem Server


def test_generate_validation(tc):
    a = setup_admin(tc)
    assert generate(a, prompt="   ").status_code == 400
    assert generate(a, prompt="x" * 4001).status_code == 400
    assert generate(a, prompt="x", variables="a = 1\n" * 10001).status_code == 400
    assert generate(a, workflow="gibtsnicht").status_code == 404
    assert generate(a, width=100, height=100).status_code == 400
    assert generate(a, width=4096, height=1024).status_code == 400
    assert generate(a, workflow="bearbeiten_qwen21").status_code == 400     # Bild fehlt


def test_generate_sets_prompt_size_seed_and_unique_name(tc):
    a = setup_admin(tc)
    r = generate(a, prompt="  ein Fuchs  ", width=1350, height=770, seed=42)
    pid = r.json()["id"]
    wf = fake_comfy.S["prompts"][pid]
    assert wf["10"]["inputs"]["value"] == "ein Fuchs"
    assert (wf["20"]["inputs"]["width"], wf["20"]["inputs"]["height"]) == (1344, 768)   # auf 16 abgerundet
    assert wf["30"]["inputs"]["seed"] == 42
    prefix = wf["50"]["inputs"]["filename_prefix"]
    assert prefix.startswith("Anime/Anima_") and len(prefix) > len("Anime/Anima_") + 10
    j = wait_job(a, pid)
    assert j["images"][0]["v"] and j["prompt"] == "ein Fuchs" and j["seed"] == 42


def test_regenerate_after_delete_gives_new_file(tc):
    """Frueherer Fehler: nach Loeschen gleicher Dateiname -> Browser zeigte altes Bild."""
    a = setup_admin(tc)
    i1 = gen_done(a, seed=1)
    assert a.post("/api/my/delete", json={"items": [i1]}).status_code == 200
    i2 = gen_done(a, seed=2)
    assert i2["filename"] != i1["filename"]


def test_active_job_limit(tc, monkeypatch):
    monkeypatch.setattr(webapp, "MAX_ACTIVE_JOBS", 3)
    a = setup_admin(tc)
    ctl(mode="hold")
    for _ in range(webapp.MAX_ACTIVE_JOBS):
        assert generate(a).status_code == 200
    r = generate(a)
    assert r.status_code == 429 and "Aufträge" in r.json()["detail"]
    assert len(a.get("/api/jobs/active").json()) == webapp.MAX_ACTIVE_JOBS
    ctl(release=True)
    for j in a.get("/api/jobs/active").json():
        assert wait_job(a, j["prompt_id"])["state"] == "done"
    assert generate(a).status_code == 200


def test_error_and_lost_jobs(tc, monkeypatch):
    a = setup_admin(tc)
    ctl(mode="error")
    j = wait_job(a, generate(a).json()["id"])
    assert j["state"] == "error" and "Simulierter Fehler" in j["error"]
    # ComfyUI-Neustart waehrend der Auftrag wartet -> "verloren" statt endlos "laeuft"
    monkeypatch.setattr(webapp, "LOST_AFTER", 0.3)
    ctl(mode="hold")
    pid = generate(a).json()["id"]
    ctl(restart=True)
    time.sleep(0.4)
    j = wait_job(a, pid)
    assert j["state"] == "lost" and "neu gestartet" in j["error"]
    assert a.get("/api/jobs/active").json() == []


def test_reconcile_without_polling(tc):
    """Seite geschlossen: Bild muss trotzdem beim Benutzer landen."""
    a = setup_admin(tc)
    u = make_user(tc, a)
    pid = generate(u).json()["id"]
    time.sleep(0.5)                                    # niemand fragt /api/job ab
    rec = u.get("/api/recent").json()                  # loest den Abgleich aus
    assert len(rec) == 1 and rec[0]["prompt"] == "a cat"
    assert a.get("/api/recent").json() == []           # nicht beim Admin
    assert u.get("/api/jobs/active").json() == [] and pid


def test_comfyui_down_gives_503(tc, monkeypatch):
    a = setup_admin(tc)
    monkeypatch.setattr(webapp, "COMFY", "http://127.0.0.1:1")
    r = generate(a)
    assert r.status_code == 503 and "nicht erreichbar" in r.json()["detail"]
    assert a.get("/api/recent").status_code == 200     # Galerie geht trotzdem


# ------------------------------------------------------------------ Eingabebilder

def test_upload_validation_and_cleanup(tc):
    a = setup_admin(tc)
    bad = {"image": ("x.png", b"<html>kein bild</html>", "image/png")}
    r = a.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "x"}, files=bad)
    assert r.status_code == 400
    good = {"image": ("foto.jpg", fake_comfy.png(5), "image/jpeg")}       # Inhalt zaehlt, nicht Endung
    r = a.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "make it night"}, files=good)
    assert r.status_code == 200, r.text
    ref = fake_comfy.S["prompts"][r.json()["id"]]["6"]["inputs"]["image"]
    assert ref.startswith("webapp/") and ref.endswith(".png")
    assert os.path.isfile(os.path.join(IN, ref))
    assert wait_job(a, r.json()["id"])["state"] == "done"
    assert not os.path.exists(os.path.join(IN, ref))                      # Upload danach entfernt
    assert os.path.isfile(os.path.join(IN, PLACEHOLDER))


def test_source_must_be_own_image(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    mine, theirs = gen_done(u), gen_done(a)
    ref = lambda i: f"{i['subfolder']}/{i['filename']}"
    data = {"workflow": "charakter_qwen21", "prompt": "at the beach"}
    assert u.post("/api/generate", data={**data, "source": ref(theirs)}).status_code == 403
    assert u.post("/api/generate", data={**data, "source": "../../etc/passwd"}).status_code == 400
    assert u.post("/api/generate", data={**data, "source": ref(mine) + " [input]"}).status_code == 400
    r = u.post("/api/generate", data={**data, "source": ref(mine), "width": 1024, "height": 1024})
    assert r.status_code == 200
    assert fake_comfy.S["prompts"][r.json()["id"]]["6"]["inputs"]["image"] == ref(mine) + " [output]"


# ------------------------------------------------------------------ Bilder ansehen

def test_image_access_and_cache(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    img = gen_done(u)
    p = {"filename": img["filename"], "subfolder": img["subfolder"]}
    assert u.get("/api/image", params={**p, "v": img["v"]}).headers["cache-control"] == "private, max-age=86400"
    assert u.get("/api/image", params=p).headers["cache-control"] == "private, no-cache"
    other = make_user(tc, a, "bert")
    assert other.get("/api/image", params=p).status_code == 404
    assert a.get("/api/image", params=p).status_code == 200
    assert u.get("/api/image", params={"kind": "input", "filename": PLACEHOLDER}).status_code == 404
    assert a.get("/api/image", params={"kind": "input", "filename": PLACEHOLDER}).status_code == 200
    assert a.get("/api/image", params={"filename": "x.png", "subfolder": "../.."}).status_code == 400


def test_recent_pagination_and_isolation(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    for s in range(5):
        gen_done(u, seed=s)
    gen_done(a)
    assert len(u.get("/api/recent", params={"limit": 3}).json()) == 3
    assert len(u.get("/api/recent", params={"limit": 3, "offset": 3}).json()) == 2
    created = [i["created"] for i in u.get("/api/recent").json()]
    assert created == sorted(created, reverse=True) and len(created) == 5


# ------------------------------------------------------------------ Loeschen

def test_user_delete_own_only(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    mine, theirs = gen_done(u), gen_done(a)
    assert u.post("/api/my/delete", json={"items": [theirs]}).status_code == 403
    assert u.post("/api/my/delete", json={"items": [mine, theirs]}).status_code == 403   # alles oder nichts
    assert os.path.isfile(os.path.join(OUT, mine["subfolder"], mine["filename"]))
    assert u.post("/api/my/delete", json={"items": "kaputt"}).status_code == 400
    assert u.post("/api/my/delete", json={"items": [mine]}).json() == {"deleted": 1}
    assert not os.path.exists(os.path.join(OUT, mine["subfolder"], mine["filename"]))
    assert u.get("/api/recent").json() == []
    hist = httpx.get(f"http://127.0.0.1:{FAKE_PORT}/history").json()
    assert not any(mine["filename"] in str(v) for v in hist.values())       # auch aus dem Verlauf


def test_admin_delete_selection_all_and_protection(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    imgs = [gen_done(u, seed=s) for s in range(3)]
    own = gen_done(a)
    # ComfyUI-Bild ohne Besitzer
    os.makedirs(os.path.join(OUT, "comfy"), exist_ok=True)
    with open(os.path.join(OUT, "comfy", "direkt.png"), "wb") as f:
        f.write(fake_comfy.png(9))
    listing = a.get("/api/admin/images", params={"limit": 200}).json()
    assert listing["total"] == 5 and listing["size"] > 0
    assert {i["owner"] for i in listing["items"]} == {"anna", "admin", None}
    assert u.post("/api/admin/delete", json={"items": imgs[:1]}).status_code == 403
    assert a.post("/api/admin/delete", json={"items": [{"subfolder": "..", "filename": "start.db"}]}).status_code == 400
    assert a.post("/api/admin/delete", json={"items": imgs[:1]}).json()["deleted"] == 1
    assert a.post("/api/admin/delete", json={"all": True, "owner": "none"}).status_code == 400   # ohne Bestaetigung
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    r = a.post("/api/admin/delete", json={"all": True, "owner": str(uid), "confirm": "LÖSCHEN"})
    assert r.json()["deleted"] == 2                     # nur annas restliche
    left = {i["filename"] for i in a.get("/api/admin/images", params={"limit": 200}).json()["items"]}
    assert left == {own["filename"], "direkt.png"}
    # Eingabebilder: Platzhalter ist geschuetzt
    with open(os.path.join(IN, "alt.png"), "wb") as f:
        f.write(fake_comfy.png(3))
    r = a.post("/api/admin/delete", json={"kind": "input", "all": True, "confirm": "LÖSCHEN"})
    assert r.json() == {"deleted": 1, "skipped": 1}
    assert os.listdir(IN) == [PLACEHOLDER]
    assert a.post("/api/admin/delete", json={"kind": "models", "all": True, "confirm": "LÖSCHEN"}).status_code == 400


# ------------------------------------------------------------------ Variablen und Serien

def test_expand_prompts_unit():
    import random as _r
    v = webapp.parse_variables("haar = red hair | blue hair | silver hair | grey hair\n# Kommentar\n\nOrt: forest|beach")
    assert v == {"haar": ["red hair", "blue hair", "silver hair", "grey hair"], "ort": ["forest", "beach"]}
    ps, unknown = webapp.expand_prompts("A girl with [haar] in a [ORT], [haar] braid, [tiny|huge] hat, [masterpiece]",
                                        v, 4, _r.Random(1))
    assert unknown == ["masterpiece"]
    assert len({p.split(" in a")[0] for p in ps}) == 4          # gleichmaessig: 4 Farben, 4 Bilder, keine doppelt
    for p in ps:
        hair = p.split("A girl with ")[1].split(" in a")[0]
        assert f"{hair} braid" in p                             # gleiche Variable = gleicher Wert im Bild
        assert "[haar]" not in p and "[ORT]" not in p and "|" not in p
        assert "[masterpiece]" in p                             # keine Variable -> bleibt Text
    ps, _ = webapp.expand_prompts("Line one [a|b].\nLine two  with  spaces", {}, 1)
    assert ps[0].endswith(".\nLine two with spaces")                 # Zeilen bleiben, doppelte Leerzeichen nicht
    for bad in ("nur text", "x = ", "= a|b", "[]=a|b"):
        with pytest.raises(webapp.HTTPException):
            webapp.parse_variables(bad)


def test_expand_endpoint(tc):
    a = setup_admin(tc)
    r = a.post("/api/expand", json={"prompt": "a [red|blue] car, [tipfehler]", "variables": "", "count": 3}).json()
    assert len(r["prompts"]) == 3 and r["unknown"] == ["tipfehler"]
    assert all(p in ("a red car, [tipfehler]", "a blue car, [tipfehler]") for p in r["prompts"])
    assert a.post("/api/expand", json={"prompt": "x", "variables": "kaputt"}).status_code == 400
    assert a.post("/api/expand", json={"prompt": "x", "count": "viele"}).status_code == 400


def test_batch_sends_only_finished_prompts(tc):
    """Kernforderung: ComfyUI/das Modell bekommt nur fertige Prompts ohne Variablen-Syntax."""
    a = setup_admin(tc)
    variables = "haar = red hair | blue hair | silver hair | grey hair"
    r = generate(a, prompt="A woman with [haar] on a [sunny|rainy] day", variables=variables, count=4, seed=100)
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) == 4 and [j["seed"] for j in jobs] == [100, 101, 102, 103]
    sent = [fake_comfy.S["prompts"][j["id"]]["10"]["inputs"]["value"] for j in jobs]
    assert all("[" not in s and "|" not in s for s in sent)
    assert {s.split("with ")[1].split(" on")[0] for s in sent} == {"red hair", "blue hair", "silver hair", "grey hair"}
    assert [j["prompt"] for j in jobs] == sent
    for j in jobs:
        assert wait_job(a, j["id"])["state"] == "done"
    rec = a.get("/api/recent").json()
    assert len(rec) == 4
    assert {i["prompt"] for i in rec} == set(sent)                       # gespeichert: fertiger Prompt
    assert all(i["template"] == "A woman with [haar] on a [sunny|rainy] day" and i["variables"] == variables
               for i in rec)                                             # + Vorlage fuer "Prompt uebernehmen"
    names = [fake_comfy.S["prompts"][j["id"]]["50"]["inputs"]["filename_prefix"] for j in jobs]
    assert len(set(names)) == 4                                          # jedes Bild eigener Dateiname


def test_batch_limits(tc, monkeypatch):
    a = setup_admin(tc)
    assert generate(a, count=0).status_code == 400
    assert generate(a, count=webapp.MAX_BATCH + 1).status_code == 400
    assert generate(a, prompt="[a|b]", variables="x = ").status_code == 400     # kaputte Variablen
    monkeypatch.setattr(webapp, "MAX_ACTIVE_JOBS", 5)
    ctl(mode="hold")
    assert generate(a, count=3).status_code == 200
    r = generate(a, count=3)                                                     # 3 + 3 > 5
    assert r.status_code == 429 and "höchstens 5" in r.json()["detail"]
    assert generate(a, count=2).status_code == 200


def test_batch_shared_upload_removed_after_last(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    files = {"image": ("foto.png", fake_comfy.png(7), "image/png")}
    r = a.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "make it [day|night]", "count": 3},
               files=files)
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    refs = {fake_comfy.S["prompts"][j["id"]]["6"]["inputs"]["image"] for j in jobs}
    assert len(refs) == 1                                   # einmal hochgeladen, von allen genutzt
    ref = refs.pop()
    ctl(release=True)
    assert wait_job(a, jobs[0]["id"])["state"] == "done"
    assert all(wait_job(a, j["id"])["state"] == "done" for j in jobs)
    assert not os.path.exists(os.path.join(IN, ref))        # erst nach dem letzten entfernt


def test_batch_upload_kept_while_others_open(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    files = {"image": ("foto.png", fake_comfy.png(8), "image/png")}
    jobs = a.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "x", "count": 2},
                  files=files).json()["jobs"]
    ref = fake_comfy.S["prompts"][jobs[0]["id"]]["6"]["inputs"]["image"]
    with webapp.db() as con:
        meta = con.execute("SELECT * FROM jobs WHERE prompt_id = ?", (jobs[0]["id"],)).fetchone()
    webapp._finish(meta, 1, [])                             # erster fertig, zweiter offen
    assert os.path.isfile(os.path.join(IN, ref))


# ------------------------------------------------------------------ Herunterladen, alles loeschen

def _zip_names(resp) -> list[str]:
    import io
    import zipfile
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    return sorted(zipfile.ZipFile(io.BytesIO(resp.content)).namelist())


def test_user_zip_only_own(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    assert u.get("/api/my/zip").status_code == 404                     # noch keine Bilder
    mine = [gen_done(u, seed=s) for s in range(3)]
    theirs = gen_done(a)
    r = u.get("/api/my/zip")
    assert _zip_names(r) == sorted(f"{i['subfolder']}/{i['filename']}" for i in mine)
    assert "anna" in r.headers["content-disposition"] and r.headers["x-image-count"] == "3"
    assert u.get("/api/my/summary").json()["count"] == 3
    # Auswahl: nur eigene
    assert u.post("/api/my/zip", json={"items": [theirs]}).status_code == 403
    assert _zip_names(u.post("/api/my/zip", json={"items": mine[:1]})) == [f"{mine[0]['subfolder']}/{mine[0]['filename']}"]
    assert As(tc).get("/api/my/zip").status_code == 401
    # Dateien danach nicht liegen gelassen (temp)
    import glob
    import tempfile as _t
    assert not glob.glob(os.path.join(_t.gettempdir(), "bilder-*.zip"))


def test_user_zip_skips_missing_file(tc):
    a = setup_admin(tc)
    i1, i2 = gen_done(a, seed=1), gen_done(a, seed=2)
    os.remove(os.path.join(OUT, i1["subfolder"], i1["filename"]))      # z.B. parallel geloescht
    assert _zip_names(a.get("/api/my/zip")) == [f"{i2['subfolder']}/{i2['filename']}"]


def test_admin_zip_filters(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    ui = gen_done(u)
    ai = gen_done(a)
    assert u.get("/api/admin/zip").status_code == 403
    assert len(_zip_names(a.get("/api/admin/zip"))) == 2
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    r = a.get("/api/admin/zip", params={"owner": uid})
    assert _zip_names(r) == [f"{ui['subfolder']}/{ui['filename']}"] and "anna" in r.headers["content-disposition"]
    assert _zip_names(a.get("/api/admin/zip", params={"kind": "input"})) == [PLACEHOLDER]
    assert _zip_names(a.post("/api/admin/zip", json={"items": [ai]})) == [f"{ai['subfolder']}/{ai['filename']}"]
    assert a.post("/api/admin/zip", json={"items": [{"subfolder": "..", "filename": "x.png"}]}).status_code == 404
    assert a.get("/api/admin/zip", params={"kind": "models"}).status_code == 400


def test_user_delete_all(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    mine = [gen_done(u, seed=s) for s in range(3)]
    theirs = gen_done(a)
    assert u.post("/api/my/delete", json={"all": True}).status_code == 400            # ohne Bestaetigung
    assert u.post("/api/my/delete", json={"all": True, "confirm": "LÖSCHEN"}).json() == {"deleted": 3}
    assert all(not os.path.exists(os.path.join(OUT, i["subfolder"], i["filename"])) for i in mine)
    assert os.path.isfile(os.path.join(OUT, theirs["subfolder"], theirs["filename"]))   # fremde bleiben
    assert u.get("/api/recent").json() == [] and len(a.get("/api/recent").json()) == 1


# ------------------------------------------------------------------ Grosse Serien, Abbrechen

def test_large_batch_and_status_endpoint(tc):
    a = setup_admin(tc)
    ctl(delay=0.01)
    r = generate(a, prompt="cat [a|b|c]", count=25, width=512, height=512)
    assert r.status_code == 200, r.text
    ids = [j["id"] for j in r.json()["jobs"]]
    assert len(ids) == 25
    end = time.time() + 15
    while time.time() < end:
        st = a.post("/api/jobs/status", json={"ids": ids}).json()
        if all(v["state"] == "done" for v in st.values()):
            break
        time.sleep(0.2)
    assert len(st) == 25 and all(v["state"] == "done" and len(v["images"]) == 1 for v in st.values())
    assert len(a.get("/api/recent", params={"limit": 100}).json()) == 25
    other = make_user(tc, a, "bert")
    assert other.post("/api/jobs/status", json={"ids": ids}).json() == {}      # fremde Auftraege: nichts


def test_batch_limit_is_only_typo_guard(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    assert generate(a, count=50).status_code == 200                          # frueher max. 10
    assert generate(a, count=webapp.MAX_BATCH + 1).status_code == 400


def test_cancel_batch(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    ctl(mode="hold")
    foreign = generate(a, prompt="fremd").json()["id"]                       # laeuft gerade (vorne)
    ids = [j["id"] for j in generate(u, count=4).json()["jobs"]]
    assert u.post("/api/jobs/cancel", json={"ids": [foreign]}).json() == {"cancelled": 0}   # fremd: nein
    assert u.post("/api/jobs/cancel", json={"ids": ids}).json() == {"cancelled": 4}
    st = u.post("/api/jobs/status", json={"ids": ids}).json()
    assert {v["state"] for v in st.values()} == {"cancelled"}
    q = httpx.get(f"http://127.0.0.1:{FAKE_PORT}/queue").json()
    left = [x[1] for x in q["queue_running"] + q["queue_pending"]]
    assert left == [foreign]                                                 # fremder Auftrag laeuft weiter
    assert u.get("/api/jobs/active").json() == []
    assert u.post("/api/jobs/cancel", json={"ids": ids}).json() == {"cancelled": 0}         # schon erledigt


def test_cancel_interrupts_only_own_running_job(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    ids = [j["id"] for j in generate(a, count=3).json()["jobs"]]             # erster laeuft
    ctl(release=False)
    # die ersten beiden fertig werden lassen, dann abbrechen: fertige bleiben
    with fake_comfy.LOCK:
        for pid in ids[:1]:
            fake_comfy.S["queue"] = [q for q in fake_comfy.S["queue"] if q[1] != pid]
            fake_comfy._finish(pid, True)
    assert wait_job(a, ids[0])["state"] == "done"
    r = a.post("/api/jobs/cancel", json={"ids": ids}).json()
    assert r == {"cancelled": 2}
    assert fake_comfy.S.get("interrupted", [])[-1:] == [ids[1]]             # der laufende gezielt unterbrochen
    st = a.post("/api/jobs/status", json={"ids": ids}).json()
    assert [st[i]["state"] for i in ids] == ["done", "cancelled", "cancelled"]
    assert len(a.get("/api/recent").json()) == 1                             # fertiges Bild bleibt


def test_running_elapsed_counts_from_real_start(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    ids = [j["id"] for j in generate(a, count=2).json()["jobs"]]
    time.sleep(0.3)
    st = a.post("/api/jobs/status", json={"ids": ids}).json()
    assert st[ids[0]]["state"] == "running" and st[ids[0]]["elapsed"] < 0.2   # erst jetzt als laufend gesehen
    assert st[ids[1]]["state"] == "queued" and st[ids[1]]["ahead"] == 1


# ------------------------------------------------------------------ Mehrere Eingabebilder

def test_edit_multiple_images_same_prompt(tc):
    a = setup_admin(tc)
    own = gen_done(a)
    files = [("image", (f"foto{i}.png", fake_comfy.png(20 + i), "image/png")) for i in range(3)]
    data = {"workflow": "bearbeiten_qwen21", "prompt": "make it [day|night]", "count": "2",
            "source": f"{own['subfolder']}/{own['filename']}"}
    r = a.post("/api/generate", data=data, files=files)
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    assert len(jobs) == 8                                       # (3 Uploads + 1 eigenes) x 2
    refs = [fake_comfy.S["prompts"][j["id"]]["6"]["inputs"]["image"] for j in jobs]
    uploads = sorted({x for x in refs if x.startswith("webapp/")})
    assert len(uploads) == 3 and refs.count(f"{own['subfolder']}/{own['filename']} [output]") == 2
    assert all(refs.count(u) == 2 for u in uploads)             # jedes Bild 2 Varianten
    sent = [fake_comfy.S["prompts"][j["id"]]["10"]["inputs"]["value"] for j in jobs]
    assert set(sent) == {"make it day", "make it night"}
    for j in jobs:
        assert wait_job(a, j["id"])["state"] == "done"
    assert all(not os.path.exists(os.path.join(IN, u)) for u in uploads)   # Uploads danach weg


def test_edit_multiple_validation_nothing_left_behind(tc):
    a = setup_admin(tc)
    u = make_user(tc, a)
    theirs = gen_done(a)
    good = ("image", ("ok.png", fake_comfy.png(1), "image/png"))
    bad = ("image", ("kaputt.png", b"kein bild", "image/png"))
    r = u.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "x"}, files=[good, bad])
    assert r.status_code == 400 and "kaputt.png" in r.json()["detail"]
    r = u.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "x",
                                      "source": f"{theirs['subfolder']}/{theirs['filename']}"}, files=[good])
    assert r.status_code == 403
    assert not os.path.isdir(os.path.join(IN, "webapp")) or os.listdir(os.path.join(IN, "webapp")) == []
    many = [("image", (f"f{i}.png", fake_comfy.png(i), "image/png")) for i in range(webapp.MAX_INPUTS + 1)]
    assert u.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": "x"}, files=many).status_code == 400


def test_generate_mode_ignores_input_images(tc):
    a = setup_admin(tc)
    r = a.post("/api/generate", data={"workflow": "anime_anima", "prompt": "x"},
               files=[("image", ("a.png", fake_comfy.png(1), "image/png"))])
    assert r.status_code == 200 and len(r.json()["jobs"]) == 1
    assert not os.path.isdir(os.path.join(IN, "webapp"))


# ------------------------------------------------------------------ Verschachtelte Variablen

def test_nested_variables_users_example():
    """Beispiel aus der Anfrage: Variable A enthaelt Variable B."""
    import random as _r
    v = webapp.parse_variables("A = [B] in einem Schönen Wald | [B] Auf einer Schönen Wiese\n"
                               "B = Ein Junge | Ein Mädchen")
    assert v == {"a": ["[B] in einem Schönen Wald", "[B] Auf einer Schönen Wiese"], "b": ["Ein Junge", "Ein Mädchen"]}
    ps, unknown = webapp.expand_prompts("[A]", v, 4, _r.Random(3))
    assert unknown == []
    assert all("[" not in p and "]" not in p for p in ps)                       # nichts Unaufgeloestes ans Modell
    assert set(ps) <= {f"{b} {o}" for b in ("Ein Junge", "Ein Mädchen")
                       for o in ("in einem Schönen Wald", "Auf einer Schönen Wiese")}
    ps, _ = webapp.expand_prompts("[A]", v, 40, _r.Random(4))
    assert len(set(ps)) == 4                                                    # alle Kombinationen kommen vor


def test_nested_same_variable_same_value_per_image():
    import random as _r
    v = webapp.parse_variables("a = [b] im Wald\nb = Junge | Mädchen")
    for p in webapp.expand_prompts("[a], und [b] lacht", v, 10, _r.Random(5))[0]:
        who = p.split(" im Wald")[0]
        assert p == f"{who} im Wald, und {who} lacht"                           # b ueberall gleich


def test_pipe_inside_brackets_in_variable_value():
    import random as _r
    v = webapp.parse_variables("haar = [red|blue] hair | green hair")
    assert v == {"haar": ["[red|blue] hair", "green hair"]}                     # | in [ ] trennt nicht
    ps, _ = webapp.expand_prompts("a girl with [haar]", v, 30, _r.Random(6))
    assert set(ps) == {"a girl with red hair", "a girl with blue hair", "a girl with green hair"}


def test_nested_inline_choices_and_literals():
    import random as _r
    ps, unknown = webapp.expand_prompts("[a [red|blue] ball|a dog], [masterpiece:1.2]", {}, 30, _r.Random(7))
    assert set(ps) == {"a red ball, [masterpiece:1.2]", "a blue ball, [masterpiece:1.2]", "a dog, [masterpiece:1.2]"}
    assert unknown == ["masterpiece:1.2"]
    ps, _ = webapp.expand_prompts("unclosed [bracket", {}, 1)
    assert ps == ["unclosed [bracket"]                                          # kaputte Klammer bleibt Text


def test_variable_cycle_is_reported():
    v = webapp.parse_variables("a = [b] x\nb = [a] y")
    with pytest.raises(webapp.HTTPException) as e:
        webapp.expand_prompts("[a]", v, 1)
    assert "verweist auf sich selbst" in e.value.detail and "a -> b -> a" in e.value.detail


def test_nested_via_api_reaches_comfy_resolved(tc):
    a = setup_admin(tc)
    r = generate(a, prompt="[A]", count=4,
                 variables="A = [B] in einem Schönen Wald | [B] Auf einer Schönen Wiese\nB = Ein Junge | Ein Mädchen")
    assert r.status_code == 200, r.text
    sent = [fake_comfy.S["prompts"][j["id"]]["10"]["inputs"]["value"] for j in r.json()["jobs"]]
    assert all("[" not in s for s in sent) and len(sent) == 4
    assert generate(a, prompt="[a]", variables="a = [a]!").status_code == 400


# ------------------------------------------------------------------ Videos (Wan 2.2)

def test_video_workflows_listed(tc):
    a = setup_admin(tc)
    wfs = {w["file"]: w for w in a.get("/api/workflows").json()}
    t2v, i2v = wfs["video_wan22_t2v"], wfs["video_wan22_i2v"]
    assert t2v["kind"] == "video" and t2v["mode"] == "generate" and t2v["durations"] == [3, 5, 7]
    assert i2v["mode"] == "edit" and i2v["auto_size"] is True and ["Hoch", 480, 832] in i2v["sizes"]
    hq = wfs["video_wan22_i2v_hq"]
    assert hq["mode"] == "edit" and hq["auto_size"] is True and hq["durations"] == [3, 5, 7] and hq["default_duration"] == 3
    assert wfs["anime_anima"]["kind"] == "image"


def test_text_to_video_length_size_and_output(tc):
    a = setup_admin(tc)
    r = generate(a, workflow="video_wan22_t2v", prompt="a fox runs", seconds=3, width=480, height=832)
    assert r.status_code == 200, r.text
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    lat = wf["20"]["inputs"]
    assert (lat["width"], lat["height"], lat["length"]) == (480, 832, 49)          # 3 s * 16 + 1
    assert wf["30"]["inputs"]["noise_seed"] == r.json()["seed"] and wf["31"]["inputs"]["add_noise"] == "disable"
    j = wait_job(a, r.json()["id"])
    vid = j["images"][0]
    assert vid["filename"].endswith(".mp4")
    assert generate(a, workflow="video_wan22_t2v", prompt="x", seconds=11).status_code == 400
    # Standardlaenge 5 s
    r = generate(a, workflow="video_wan22_t2v", prompt="x")
    assert fake_comfy.S["prompts"][r.json()["id"]]["20"]["inputs"]["length"] == 81
    # Auslieferung mit Range (Vorspulen), Typ video/mp4
    p = {"filename": vid["filename"], "subfolder": vid["subfolder"], "v": vid["v"]}
    full = a.get("/api/image", params=p)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    part = a.get("/api/image", params=p, headers={"range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100
    assert a.get("/api/image", params={**p, "preview": 1}).headers["content-type"] == "video/mp4"
    # Galerie, ZIP, Admin, Loeschen kennen Videos
    assert any(i["filename"] == vid["filename"] for i in a.get("/api/recent").json())
    assert f"{vid['subfolder']}/{vid['filename']}" in _zip_names(a.get("/api/my/zip"))
    assert any(i["filename"] == vid["filename"] for i in a.get("/api/admin/images", params={"limit": 200}).json()["items"])
    assert a.post("/api/my/delete", json={"items": [{"subfolder": vid["subfolder"], "filename": vid["filename"]}]}).json() == {"deleted": 1}


def test_video_estimate_scales_with_length_and_size(tc):
    a = setup_admin(tc)
    ctl(mode="hold")
    ids = [generate(a, workflow="video_wan22_t2v", prompt="x", **kw).json()["id"]
           for kw in ({}, {"seconds": 3}, {"seconds": 5, "width": 1280, "height": 720})]
    st = a.post("/api/jobs/status", json={"ids": ids}).json()
    base = next(w for w in a.get("/api/workflows").json() if w["file"] == "video_wan22_t2v")["seconds"]
    assert [st[i]["seconds"] for i in ids] == [base, round(base * 3 / 5), round(base * 1280 * 720 / (832 * 480))]
    ctl(release=True)


def test_video_motion_levels(tc):
    a = setup_admin(tc)
    t2v = next(w for w in a.get("/api/workflows").json() if w["file"] == "video_wan22_t2v")
    assert t2v["motion"]["labels"] == ["Ruhig", "Wenig", "Normal", "Viel", "Sehr viel"] and t2v["motion"]["default"] == 3
    assert "levels" not in t2v["motion"]                       # was eine Stufe tut, bleibt am Server
    assert next(w for w in a.get("/api/workflows").json() if w["file"] == "anime_anima").get("motion") is None
    levels = webapp.find_workflow("video_wan22_t2v")["motion"]["levels"]
    for lvl in (1, 5):
        r = generate(a, workflow="video_wan22_t2v", prompt="a [fox|cat] runs", motion=lvl)
        assert r.status_code == 200, r.text
        wf = fake_comfy.S["prompts"][r.json()["id"]]
        sent = wf["10"]["inputs"]["value"]
        assert sent.startswith("a ") and "[" not in sent and sent.endswith(levels[lvl - 1]["suffix"])
        for node, key, value in levels[lvl - 1]["set"]:
            assert wf[node]["inputs"][key] == value
        j = wait_job(a, r.json()["id"])
        assert j["motion"] == lvl and j["prompt"] == sent and j["template"] == "a [fox|cat] runs"
        assert next(i for i in a.get("/api/recent").json() if i["filename"] == j["images"][0]["filename"])["motion"] == lvl
    # ohne Angabe: Standardstufe (Normal = ohne Zusatz); Unsinn wird abgelehnt
    r = generate(a, workflow="video_wan22_t2v", prompt="a fox runs")
    assert fake_comfy.S["prompts"][r.json()["id"]]["10"]["inputs"]["value"] == "a fox runs" + levels[2]["suffix"]
    assert generate(a, workflow="video_wan22_t2v", prompt="x", motion=6).status_code == 400
    assert generate(a, workflow="video_wan22_t2v", prompt="x", motion=0).status_code == 200   # 0 = Standard
    # Bilder-Workflows ignorieren die Angabe
    r = generate(a, prompt="a cat", motion=5)
    assert r.status_code == 200 and fake_comfy.S["prompts"][r.json()["id"]]["10"]["inputs"]["value"] == "a cat"


def _jpeg(w, h):
    """Minimaler JPEG-Kopf mit SOF0 (reicht fuer die Groessenerkennung)."""
    sof = b"\xff\xc0\x00\x11\x08" + h.to_bytes(2, "big") + w.to_bytes(2, "big") + b"\x03" + b"\x00" * 9
    return b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + sof + b"\xff\xd9"


def test_image_dims():
    assert webapp.image_dims(fake_comfy.png(1)) == (8, 8)
    assert webapp.image_dims(_jpeg(1080, 1920)) == (1080, 1920)
    assert webapp.image_dims(b"RIFF\x00\x00\x00\x00WEBPVP8X" + b"\x00" * 8 + (1279).to_bytes(3, "little")
                             + (719).to_bytes(3, "little")) == (1280, 720)
    assert webapp.image_dims(b"kaputt") is None


def test_image_to_video_auto_size_per_image(tc):
    a = setup_admin(tc)
    files = [("image", ("hoch.jpg", _jpeg(1080, 1920), "image/jpeg")),
             ("image", ("quer.jpg", _jpeg(1920, 1080), "image/jpeg")),
             ("image", ("quadrat.jpg", _jpeg(1000, 1000), "image/jpeg"))]
    r = a.post("/api/generate", data={"workflow": "video_wan22_i2v", "prompt": "she smiles", "seconds": "5"}, files=files)
    assert r.status_code == 200, r.text
    sizes = [(fake_comfy.S["prompts"][j["id"]]["21"]["inputs"]["width"], fake_comfy.S["prompts"][j["id"]]["21"]["inputs"]["height"])
             for j in r.json()["jobs"]]
    assert sizes == [(480, 832), (832, 480), (640, 640)]                  # passend zum jeweiligen Startbild
    # feste Groesse gewinnt
    r = a.post("/api/generate", data={"workflow": "video_wan22_i2v", "prompt": "x", "width": "1280", "height": "720"},
               files=files[:1])
    assert fake_comfy.S["prompts"][r.json()["id"]]["21"]["inputs"]["width"] == 1280


def test_video_cannot_be_input(tc):
    a = setup_admin(tc)
    vid = gen_done(a, workflow="video_wan22_t2v", prompt="a fox")
    r = a.post("/api/generate", data={"workflow": "video_wan22_i2v", "prompt": "x",
                                      "source": f"{vid['subfolder']}/{vid['filename']}"})
    assert r.status_code == 400 and "Videos" in r.json()["detail"]


# ------------------------------------------------------------------ Ausformulieren

def test_enhance_text_prompt_uses_workflow_rules(tc):
    a = setup_admin(tc)
    assert all(w["enhance"] for w in a.get("/api/workflows").json())
    r = a.post("/api/enhance", data={"workflow": "gezeichnet_dnd", "prompt": "  A female tiefling bard  "})
    assert r.status_code == 200, r.text
    assert r.json() == {"prompt": "EXPANDED A female tiefling bard images=0", "warning": None}   # "Prompt:" entfernt
    sent = fake_comfy.S["llm"]
    system = sent["messages"][0]["content"]
    assert sent["think"] is False and sent["model"] == "default"
    assert "text-to-image" in system and "tiefling" in system and "bright, clearly lit" in system   # D&D-Regel
    assert "Wan 2.2" not in system
    # Video: eigene Regeln (keine Verneinungen), Text-zu-Video ohne Bild
    a.post("/api/enhance", data={"workflow": "video_wan22_t2v", "prompt": "a fox runs"})
    vs = fake_comfy.S["llm"]["messages"][0]["content"]
    assert "Wan 2.2" in vs and "Never use negations" in vs and "start image" not in vs
    assert a.post("/api/enhance", data={"workflow": "gezeichnet_dnd", "prompt": "  "}).status_code == 400
    assert a.post("/api/enhance", data={"workflow": "gibtsnicht", "prompt": "x"}).status_code == 404


def test_enhance_sends_start_image_for_edit_and_i2v(tc):
    a = setup_admin(tc)
    files = {"image": ("start.png", fake_comfy.png(3), "image/png")}
    r = a.post("/api/enhance", data={"workflow": "video_wan22_i2v_hq", "prompt": "make her dance"}, files=files)
    assert r.status_code == 200 and r.json()["prompt"].endswith("images=1")
    msg = fake_comfy.S["llm"]["messages"]
    assert "start image" in msg[0]["content"] and base64.b64decode(msg[1]["images"][0]) == fake_comfy.png(3)
    # eigenes Ergebnis als Quelle; fremdes Bild ist tabu
    own = gen_done(a)
    r = a.post("/api/enhance", data={"workflow": "bearbeiten_qwen21", "prompt": "make it winter",
                                     "source": f"{own['subfolder']}/{own['filename']}"})
    assert r.status_code == 200 and r.json()["prompt"].endswith("images=1")
    assert "image-editing" in fake_comfy.S["llm"]["messages"][0]["content"]
    u = make_user(tc, a, "eve")
    r = u.post("/api/enhance", data={"workflow": "bearbeiten_qwen21", "prompt": "x",
                                     "source": f"{own['subfolder']}/{own['filename']}"})
    assert r.status_code == 403
    assert a.post("/api/enhance", data={"workflow": "bearbeiten_qwen21", "prompt": "x"},
                  files={"image": ("x.png", b"kein bild", "image/png")}).status_code == 400


def test_enhance_placeholders_and_failures(tc):
    a = setup_admin(tc)
    ctl(llm_reply="A [haar] woman in a forest")
    r = a.post("/api/enhance", data={"workflow": "anime_anima", "prompt": "eine [haar] Frau im [ort]"}).json()
    assert r["prompt"] == "A [haar] woman in a forest" and r["warning"] == "Platzhalter fehlen jetzt: [ort]"
    ctl(llm_fail=True)
    r = a.post("/api/enhance", data={"workflow": "anime_anima", "prompt": "x"})
    assert r.status_code == 502 and "nicht erreichbar" in r.json()["detail"]


def test_video_action_lora_and_trigger(tc):
    a = setup_admin(tc)
    wfs = {w["file"]: w for w in a.get("/api/workflows").json()}
    assert wfs["video_wan22_i2v"]["action"]["labels"][:3] == ["Ohne", "Stufe A", "Stufe B"]   # tests/extras
    assert wfs["video_wan22_i2v_hq"]["action"]["default"] == 1 and "action" not in wfs["video_wan22_t2v"]
    levels = webapp.find_workflow("video_wan22_i2v_hq")["action"]["levels"]
    start = {"image": ("s.png", fake_comfy.png(5), "image/png")}
    # Standard "Ohne": LoRA bleibt auf Staerke 0, Prompt unveraendert
    r = a.post("/api/generate", data={"workflow": "video_wan22_i2v_hq", "prompt": "she dances"}, files=start)
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    assert wf["60"]["inputs"]["strength_model"] == 0 and wf["61"]["inputs"]["strength_model"] == 0
    assert wf["10"]["inputs"]["value"].startswith("she dances")
    # Stufe B: Trigger vorn, Bewegung hinten, beide LoRAs gesetzt
    r = a.post("/api/generate", data={"workflow": "video_wan22_i2v_hq", "prompt": "they [kiss|smile]", "action": "3",
                                      "motion": "4"}, files=start)
    assert r.status_code == 200, r.text
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    lv = levels[2]
    sent = wf["10"]["inputs"]["value"]
    motion_suffix = webapp.find_workflow("video_wan22_i2v_hq")["motion"]["levels"][3]["suffix"]
    assert sent.startswith(lv["prefix"]) and sent.endswith(motion_suffix) and "[" not in sent
    for node, key, value in lv["set"]:
        assert wf[node]["inputs"][key] == value
    assert wf["60"]["inputs"]["lora_name"] == "test_action_high.safetensors" and wf["61"]["inputs"]["strength_model"] == 1.0
    j = wait_job(a, r.json()["id"])
    assert j["action"] == 3 and j["motion"] == 4 and j["template"] == "they [kiss|smile]"
    assert next(i for i in a.get("/api/recent").json() if i["filename"] == j["images"][0]["filename"])["action"] == 3
    assert a.post("/api/generate", data={"workflow": "video_wan22_i2v_hq", "prompt": "x", "action": "99"},
                  files=start).status_code == 400


def test_nsfw_only_for_unlocked_users(tc):
    a = setup_admin(tc)
    assert a.get("/api/me").json()["nsfw"] is True                 # Einrichtungs-Admin hat die Freigabe
    u = make_user(tc, a)
    assert u.get("/api/me").json()["nsfw"] is False
    wfs = {w["file"]: w for w in u.get("/api/workflows").json()}
    assert "action" not in wfs["video_wan22_i2v"] and "action" not in wfs["video_wan22_i2v_hq"]
    assert "motion" in wfs["video_wan22_i2v"]                       # Bewegung bleibt fuer alle
    assert "nsfw" not in wfs["anime_anima"]["summary"].lower()
    assert "nsfw" in {w["file"]: w for w in a.get("/api/workflows").json()}["anime_anima"]["summary"].lower()
    start = {"image": ("s.png", fake_comfy.png(5), "image/png")}
    r = u.post("/api/generate", data={"workflow": "video_wan22_i2v", "prompt": "x", "action": "3"}, files=start)
    assert r.status_code == 403
    r = u.post("/api/generate", data={"workflow": "video_wan22_i2v", "prompt": "she waves"}, files=start)
    assert r.status_code == 200, r.text
    assert fake_comfy.S["prompts"][r.json()["id"]]["60"]["inputs"]["strength_model"] == 0
    # Admin schaltet frei -> Auswahl erscheint
    uid = [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["id"]
    assert a.patch(f"/api/users/{uid}", json={"nsfw": True}).status_code == 200
    assert [x for x in a.get("/api/users").json() if x["name"] == "anna"][0]["nsfw"] is True
    assert "action" in {w["file"]: w for w in u.get("/api/workflows").json()}["video_wan22_i2v"]
    assert u.patch(f"/api/users/{uid}", json={"nsfw": True}).status_code == 403       # nicht selbst


def test_token_from_image_prompt_optional(tc):
    a = setup_admin(tc)
    w = {x["file"]: x for x in a.get("/api/workflows").json()}["dnd_token_bild"]
    assert (w["mode"], w["group"], w["variant"], w["tab"]) == ("edit", "Gezeichnet - D&D", "Token aus Bild", "dnd")
    assert w["default_prompt"]
    start = {"image": ("s.png", fake_comfy.png(5), "image/png")}
    for name in ("token_rahmen.png", "token_maske.png"):     # liegen im Betrieb im Input-Ordner
        with open(os.path.join(IN, name), "wb") as f:
            f.write(fake_comfy.png(2))
    r = a.post("/api/generate", data={"workflow": "dnd_token_bild", "prompt": "  "}, files=start)
    assert r.status_code == 200, r.text
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    assert wf["10"]["inputs"]["value"] == w["default_prompt"] and wf["50"]["inputs"]["images"] == ["74", 0]
    # andere Workflows brauchen weiter einen Prompt
    assert a.post("/api/generate", data={"workflow": "bearbeiten_qwen21", "prompt": " "},
                  files=start).status_code == 400


def _key(tc, who: As, name="test") -> dict:
    r = who.post("/api/me/keys", json={"name": name})
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    tc.cookies.clear()
    return {"authorization": f"Bearer {key}"}


def test_api_keys_and_rights(tc):
    a = setup_admin(tc)
    h = _key(tc, a)
    keys = a.get("/api/me/keys").json()
    assert len(keys) == 1 and keys[0]["name"] == "test" and h["authorization"][7:] not in json.dumps(keys)
    tc.cookies.clear()
    assert tc.get("/api/v1/workflows", headers=h).status_code == 200
    assert tc.get("/api/me", headers=h).json()["name"] == "admin"
    # Schluessel ist keine Anmeldung: keine Verwaltung, keine neuen Schluessel, kein Passwort
    assert tc.get("/api/users", headers=h).status_code == 403
    assert tc.post("/api/me/keys", headers=h, json={}).status_code == 403
    assert tc.post("/api/me/password", headers=h, json={"old": PW, "new": "x" * 10}).status_code == 403
    assert tc.get("/api/v1/workflows", headers={"authorization": "Bearer ig_falsch"}).status_code == 401
    assert a.delete(f"/api/me/keys/{keys[0]['id']}").status_code == 200
    tc.cookies.clear()
    assert tc.get("/api/v1/workflows", headers=h).status_code == 401
    # fremde Schluessel nicht loeschbar, NSFW-Rechte gelten auch per Schluessel
    u = make_user(tc, a, "eve")
    hu = _key(tc, u)
    _key(tc, a, "zweiter")
    assert u.delete(f"/api/me/keys/{a.get('/api/me/keys').json()[0]['id']}").status_code == 404
    tc.cookies.clear()
    wfs = {w["file"]: w for w in tc.get("/api/v1/workflows", headers=hu).json()}
    assert "action" not in wfs["video_wan22_i2v"]


def test_api_generate_with_enhance_and_wait(tc):
    a = setup_admin(tc)
    h = _key(tc, a)
    r = tc.post("/api/v1/generate", headers=h, json={"workflow": "gezeichnet_dnd", "prompt": "a dwarf", "wait": 10})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["prompt_used"].startswith("EXPANDED a dwarf")                  # ausformuliert
    assert "D&D book art" in fake_comfy.S["llm"]["messages"][0]["content"]       # Regel des Workflows
    res = j["results"][0]
    assert res["state"] == "done" and res["images"][0]["url"].startswith("http")
    img = tc.get(res["images"][0]["url"].replace("http://testserver", ""), headers=h)
    assert img.status_code == 200 and img.content.startswith(b"\x89PNG")
    # ohne Ausformulieren, ohne Warten; Status spaeter abfragen
    r = tc.post("/api/v1/generate", headers=h, json={"workflow": "anime_anima", "prompt": "a cat", "enhance": False})
    j = r.json()
    assert j["prompt_used"] == "a cat" and "results" not in j
    assert tc.get(f"/api/v1/jobs/{j['id']}", headers=h).json()["state"] in ("queued", "running", "done")
    # Sprachmodell weg -> trotzdem erzeugt, mit Hinweis
    ctl(llm_fail=True)
    j = tc.post("/api/v1/generate", headers=h, json={"workflow": "anime_anima", "prompt": "a dog"}).json()
    assert j["prompt_used"] == "a dog" and "Nicht ausformuliert" in j["warning"]
    # Bild als Base64 (Token aus Bild, Text leer = Standardtext)
    for name in ("token_rahmen.png", "token_maske.png"):
        with open(os.path.join(IN, name), "wb") as f:
            f.write(fake_comfy.png(2))
    r = tc.post("/api/v1/generate", headers=h, json={"workflow": "dnd_token_bild", "prompt": "",
                                                       "image": base64.b64encode(fake_comfy.png(4)).decode()})
    assert r.status_code == 200, r.text
    assert tc.post("/api/v1/generate", headers=h, json={"workflow": "dnd_token_bild", "image": "@@@"}).status_code == 400


def test_api_openai_compatible(tc):
    a = setup_admin(tc)
    h = _key(tc, a)
    models = tc.get("/v1/models", headers=h).json()["data"]
    ids = {m["id"] for m in models}
    assert "realistisch_qwen21" in ids and not any(i.startswith("video") for i in ids)
    r = tc.post("/v1/images/generations", headers=h, json={"model": "realistisch_qwen21", "prompt": "a fox",
                                                           "size": "768x1024", "n": 2})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data) == 2 and base64.b64decode(data[0]["b64_json"]).startswith(b"\x89PNG")
    assert data[0]["revised_prompt"].startswith("EXPANDED a fox")
    pid = [k for k, v in fake_comfy.S["prompts"].items() if v.get("20", {}).get("inputs", {}).get("width") == 768]
    assert pid
    r = tc.post("/v1/images/generations", headers=h, json={"model": "dall-e-3", "prompt": "x",
                                                           "response_format": "url", "enhance": False})
    assert r.status_code == 200 and r.json()["data"][0]["url"].startswith("http")
    r = tc.post("/v1/images/edits", headers=h, data={"model": "bearbeiten_qwen21", "prompt": "make it winter"},
                files={"image": ("a.png", fake_comfy.png(3), "image/png")})
    assert r.status_code == 200, r.text
    assert tc.post("/v1/images/generations", headers=h, json={"model": "gibtsnicht", "prompt": "x"}).status_code == 404


def test_proxy_module_endpoints(tc):
    info = tc.get("/proxy-module").json()
    assert info["name"] == "ImageGen" and set(info["capabilities"]) == {"memory", "free"}
    assert info["links"][0]["path"] == "/"
    # leeres ComfyUI (nur Framework-RAM) -> nichts freizugeben
    m = tc.get("/proxy-module/memory").json()
    assert m["reclaimable"] is False and m["held"] == 0 and m["rss"] == 3 * 1024 ** 3
    ctl(pm={"rss": 20 * 1024 ** 3, "gpu": 2 * 1024 ** 3, "loaded_models": 2, "busy": True})
    m = tc.get("/proxy-module/memory").json()
    assert m["reclaimable"] and m["busy"] and m["held"] == 22 * 1024 ** 3 and "2 Modelle" in m["detail"]
    before = fake_comfy.S.get("freed", 0)
    assert tc.post("/proxy-module/free").status_code == 200 and fake_comfy.S["freed"] == before + 1
    ctl(pm=None)


def test_dnd_variants_grouped(tc):
    a = setup_admin(tc)
    wfs = a.get("/api/workflows").json()
    dnd = [(w["file"], w["variant"]) for w in wfs if w.get("group") == "Gezeichnet - D&D" and w["mode"] == "generate"]
    assert dnd == [("gezeichnet_dnd", "Illustration"), ("dnd_ort", "Ort"), ("dnd_token", "Token"),
                   ("dnd_battlemap", "Battlemap"), ("dnd_ortskarte", "Ortskarte")]
    # Ortskarte: eigene, groessere Formate fuer Staedte
    ok = next(w for w in wfs if w["file"] == "dnd_ortskarte")
    assert ["Groß 4:3", 1536, 1152] in ok["sizes"] and ok["default_size"] == [1280, 960]
    r = generate(a, workflow="dnd_ortskarte", prompt="a walled city", width=1536, height=1152)
    lat = fake_comfy.S["prompts"][r.json()["id"]]["20"]["inputs"]
    assert (lat["width"], lat["height"]) == (1536, 1152)
    assert all(w.get("group") is None for w in wfs if w["file"].startswith(("anime", "video", "realistisch")))
    # Token: feste Groesse (Rahmen/Maske sind 1024er Vorlagen) - Groessenangaben werden ignoriert
    tok = next(w for w in wfs if w["file"] == "dnd_token")
    assert tok["default_size"] is None
    for name in ("token_rahmen.png", "token_maske.png"):     # liegen im Betrieb im Input-Ordner
        with open(os.path.join(IN, name), "wb") as f:
            f.write(fake_comfy.png(2))
    r = generate(a, workflow="dnd_token", prompt="a dwarf cleric", width=1344, height=768)
    assert r.status_code == 200, r.text
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    assert wf["20"]["inputs"]["width"] == 1024 and wf["50"]["inputs"]["images"] == ["74", 0]
    assert wf["70"]["inputs"]["image"] == "token_rahmen.png" and wf["73"]["inputs"]["image"] == "token_maske.png"
    # Battlemap: Mapcraft-Trigger steht fest vor dem Prompt, Prompt selbst bleibt unveraendert
    r = generate(a, workflow="dnd_battlemap", prompt="a small chapel")
    wf = fake_comfy.S["prompts"][r.json()["id"]]
    assert wf["10"]["inputs"]["value"] == "a small chapel" and "mapcraft" in wf["11"]["inputs"]["string_a"]
    assert wf["5"]["inputs"]["lora_name"] == "mapcraft_anima_v1.safetensors"
    # die Vorlagen sind als Workflow-Vorgabe geschuetzt (Admin "Alle loeschen" laesst sie liegen)
    assert {("", "token_rahmen.png"), ("", "token_maske.png")} <= webapp.protected_inputs()
