#!/usr/bin/env python3
"""
Bilder-Web-App: schlanke Oberflaeche vor ComfyUI, mit Benutzern.

Erzeugen oder Bearbeiten waehlen, Workflow aus dem Dropdown, Prompt + Groesse, fertig.
Die Workflows kommen aus imagegen/workflows/api/ (index.json von build_workflows.py);
der Prompt geht immer in den Knoten "Prompt - dein Motiv", Stil-/Qualitaetszusaetze
haengen die Workflows selbst an.

Benutzer: beim ersten Aufruf legt ein Einrichtungsassistent den Admin an. Der Admin legt
weitere Benutzer an. Jeder sieht und loescht nur seine eigenen Bilder (Zuordnung in
SQLite, geprueft bei jedem Zugriff). Der Admin sieht alle Bilder im ComfyUI-Ausgabeordner
(auch die direkt in ComfyUI erzeugten) und die Eingabebilder und kann sie loeschen.

ComfyUI selbst hat keinen Login und bleibt im LAN, nur diese App ist fuer den Zugriff
von aussen gedacht (hinter einem Reverse-Proxy mit HTTPS). Tests: tests/test_app.py.
"""
import asyncio
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import random
import re
import secrets
import sqlite3
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager, closing
from urllib.parse import quote, urlsplit

import httpx
import uvicorn
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile as StarletteUpload
from starlette.exceptions import HTTPException as StarletteHTTPException

import i18n

COMFY = os.environ.get("COMFYUI_URL", "http://comfyui:8188").rstrip("/")
from version import VERSION  # noqa: E402
WORKFLOWS = os.environ.get("WORKFLOW_DIR", "/workflows")
OUTPUT = os.path.realpath(os.environ.get("OUTPUT_DIR", "/output"))   # ComfyUI-Ausgabeordner (fuer Admin)
INPUT = os.path.realpath(os.environ.get("INPUT_DIR", "/input"))      # Eingabebilder/Importe (fuer Admin)
FOLDERS = {"output": OUTPUT, "input": INPUT}
DB_PATH = os.environ.get("WEBAPP_DB", "/data/webapp.db")
SESSION_DAYS = int(os.environ.get("WEBAPP_SESSION_DAYS", "30"))
# Reverse-Proxys, deren X-Forwarded-For/-Proto/-Host geglaubt wird (IPs oder Netze, Komma-
# getrennt). Default: private Netze + localhost. Von allen anderen zaehlt nur die echte
# Verbindungs-IP - sonst koennte jeder per Header die Login-Sperre umgehen.
TRUSTED_PROXIES = [ipaddress.ip_network(n.strip(), strict=False) for n in os.environ.get(
    "WEBAPP_TRUSTED_PROXIES", "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,::1/128").split(",") if n.strip()]
COOKIE = "imagegen_session"
LANG_HEADER = "x-imagegen-lang"     # Sprache der Oberflaeche fuer Server-Meldungen (sonst Accept-Language)
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
UPLOAD_SUBFOLDER = "webapp"
MAX_UPLOAD = 30 * 1024 * 1024
MAX_PROMPT = 4000
MAX_VARIABLES = 60000
# Nur Sicherungen gegen Tippfehler (z.B. 10000 statt 100), keine echte Begrenzung
MAX_BATCH = int(os.environ.get("WEBAPP_MAX_BATCH", "1000"))                  # Bilder pro Serie
MAX_ACTIVE_JOBS = int(os.environ.get("WEBAPP_MAX_ACTIVE_JOBS", "2000"))      # pro Benutzer gleichzeitig
MAX_INPUTS = 100         # Eingabebilder pro Auftrag (Bearbeiten mehrerer Bilder)
LOST_AFTER = 30          # s: Auftrag weder in Warteschlange noch Verlauf -> verloren (ComfyUI-Neustart)
SIZE_LIMIT = 2048
PBKDF2_ROUNDS = 600_000
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXT = (".mp4", ".webm")
MEDIA_EXT = IMAGE_EXT + VIDEO_EXT      # was in Galerie, ZIP, Loeschen zaehlt
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{2,32}$")
LOGIN_MAX_PER_IP = 10    # Fehlversuche je 10 Min.
LOGIN_MAX_PER_NAME = 20  # Fehlversuche je 10 Min. auf einen Namen (egal von wo)
FAIL_DELAY = 1.0         # s Wartezeit nach falschem Passwort (bremst Durchprobieren)
_DUMMY_HASH = None       # gleiche Rechenzeit fuer unbekannte Namen (kein Ausforschen von Namen)

client: httpx.AsyncClient | None = None

# ------------------------------------------------------------------ Prompt ausformulieren
# Ein Sprachmodell (ueber den Ollama-Proxy) macht aus einer kurzen Idee einen ausfuehrlichen Prompt.
# Gemessen 2026-10-26 (Gemma4, ~3 s): "A female tiefling bard in a tavern" ergab mit Qwen 2.1 eine
# normale Frau mit Stab, ausformuliert eine rote Tieflingin mit Hoernern, Schwanz und Laute.
# Wan 2.2 versteht keine Verneinungen und ist auf beschreibende Saetze trainiert ("Make her ...,
# don't let her speak" -> Mund bewegt sich); Wan empfiehlt selbst die Erweiterung per Sprachmodell.
LLM_URL = os.environ.get("WEBAPP_LLM_URL", "").rstrip("/")     # Ollama-kompatibel; leer = Knopf aus
LLM_MODEL = os.environ.get("WEBAPP_LLM_MODEL", "")              # leer = Standardmodell des Proxys

_COMMON = """
- Output ONLY the prompt as one paragraph, no preamble, no quotes, no markdown, never ask questions.
  If the idea is vague, choose a fitting, natural interpretation{img}.
- Keep everything the user asked for; never drop, soften or censor content. Adult or NSFW requests stay as
  explicit as written.
- The user may write German or English, as an instruction ("Make a ...") or as keywords; always write English.
- Text in [square brackets] are placeholders: copy every one of them unchanged, brackets included.
- Spell out what the model cannot know, e.g. for Dungeons & Dragons races and classes their visible traits
  (tiefling: horns, unusual skin color such as red or purple, solid-color eyes, tail; dwarf: short, stocky,
  broad, long braided beard; half-orc: greenish-grey skin, tusks; dragonborn: dragon head, scales; elf:
  pointed ears; gnome/halfling: very small; bard: musical instrument; wizard: robes, spellbook; cleric:
  holy symbol; rogue: leather armor, hood, daggers)."""

SYS_IMAGE = """You turn a short image idea into one detailed English prompt for a text-to-image model.
Rules:
- 60-120 words. Describe subject, appearance, clothing, pose or action, setting, lighting and composition
  concretely. Do not name an art style or medium; that is added separately.""" + _COMMON

SYS_EDIT = """You turn a short image-editing request into a clear English instruction for an image-editing
model that receives the image shown to you.
Rules:
- 20-70 words. Say exactly what to change (and how it should look afterwards) and what stays the same
  (identity, face, pose, background, style), based on the image.""" + _COMMON

SYS_VIDEO = """You turn a short video idea into one English prompt for the Wan 2.2 video model{i2v}.
Rules:
- 50-100 words, present tense, describing what visibly happens.
- Describe the motion concretely and in order: which body parts move, how, direction, speed and rhythm;
  then the camera (static, slow push-in, pan, tracking).
- Never use negations ("don't", "no", "without"): the model ignores them and may do the opposite. State the
  desired state positively instead (e.g. "her lips stay gently closed" instead of "don't let her speak").""" + _COMMON

I2V = (" that animates the start image shown to you: name the main subject briefly as it appears in the image and"
       " focus on the motion; keep it consistent with what the image shows")

CLIENT_ID = secrets.token_hex(16)


# --------------------------------------------------------------------------- Datenbank

def db() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with closing(db()) as con, con:
        con.executescript("""
        PRAGMA journal_mode = WAL;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            pw TEXT NOT NULL, admin INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS jobs (
            prompt_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, workflow TEXT, title TEXT,
            prompt TEXT, seed INTEGER, seconds INTEGER, started REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY, user_id INTEGER, prompt_id TEXT, subfolder TEXT NOT NULL,
            filename TEXT NOT NULL, workflow TEXT, prompt TEXT, seed INTEGER, created REAL NOT NULL,
            UNIQUE (subfolder, filename));
        CREATE INDEX IF NOT EXISTS images_user ON images (user_id, created);
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            hash TEXT NOT NULL UNIQUE, name TEXT, hint TEXT, created REAL NOT NULL, last_used REAL);
        """)
        # Nachtraeglich hinzugekommene Spalten: Upload eines Auftrags (wird danach entfernt),
        # Vorlage + Variablen (Serien, "Prompt uebernehmen"), Fehlertext, echter Start/Ende.
        # Typ wichtig: Zeitstempel als REAL, sonst speichert SQLite sie als Text.
        for table, col, typ in (("jobs", "upload", "TEXT"), ("jobs", "template", "TEXT"),
                                ("jobs", "variables", "TEXT"), ("jobs", "error", "TEXT"),
                                ("jobs", "run_started", "REAL"), ("jobs", "finished", "REAL"),
                                ("images", "template", "TEXT"), ("images", "variables", "TEXT"),
                                ("jobs", "motion", "INTEGER"), ("images", "motion", "INTEGER"),
                                ("jobs", "action", "INTEGER"), ("images", "action", "INTEGER")):
            if col not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        # NSFW-Freigabe je Benutzer (Aktion-LoRAs, NSFW-Hinweise); beim Nachruesten bekommen sie die Admins
        if "nsfw" not in {r["name"] for r in con.execute("PRAGMA table_info(users)")}:
            con.execute("ALTER TABLE users ADD COLUMN nsfw INTEGER NOT NULL DEFAULT 0")
            con.execute("UPDATE users SET nsfw = admin")
    # Job-Status: 0 = offen, 1 = fertig, 2 = Fehler, 3 = verloren (ComfyUI-Neustart), 4 = abgebrochen


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        _, rounds, salt, h = stored.split("$")
    except ValueError:
        return False
    test = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), int(rounds)).hex()
    return hmac.compare_digest(test, h)


# PBKDF2 dauert ~0,5 s: im Thread rechnen, sonst steht die ganze App so lange still
async def hash_pw_async(pw: str) -> str:
    return await asyncio.to_thread(hash_pw, pw)


async def check_pw_async(pw: str, stored: str) -> bool:
    return await asyncio.to_thread(check_pw, pw, stored)


def user_count() -> int:
    with closing(db()) as con:
        return con.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def validate_new(name: str, pw: str):
    if not NAME_RE.match(name or ""):
        raise HTTPException(400, "Name: 2-32 Zeichen, nur Buchstaben, Ziffern, . _ -")
    if not 8 <= len(pw or "") <= 200:
        raise HTTPException(400, "Passwort: mindestens 8 Zeichen")


# --------------------------------------------------------------------------- Lebenszyklus, Middleware

async def _background():
    while True:
        await asyncio.sleep(20)
        try:
            await reconcile_jobs()
            with closing(db()) as con, con:
                con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))
        except Exception as e:  # nie den Loop sterben lassen
            print("[hintergrund]", repr(e), flush=True)


@asynccontextmanager
async def lifespan(_app):
    global client, _DUMMY_HASH
    init_db()
    _DUMMY_HASH = await hash_pw_async(secrets.token_hex(8))
    client = httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5))
    task = asyncio.create_task(_background())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def ui_lang(request: Request) -> str:
    return i18n.request_lang(request.headers, LANG_HEADER)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    # Meldungen entstehen auf Deutsch; Englisch, wenn die Oberflaeche (Kopfzeile) oder der Client es wuenscht
    return JSONResponse({"detail": i18n.translate(exc.detail, ui_lang(request))}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


def _trusted(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in n for n in TRUSTED_PROXIES)


def client_ip(request: Request) -> str:
    """Echte Client-IP: X-Forwarded-For nur von vertrauten Proxys, von rechts gelesen
    (der eigene Proxy haengt die echte IP hinten an; vorne kann der Client alles schreiben)."""
    peer = request.client.host if request.client else "?"
    if not _trusted(peer):
        return peer
    chain = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    for ip in reversed(chain):
        if not _trusted(ip):
            return ip
    return chain[0] if chain else peer


def _is_https(request: Request) -> bool:
    if request.client and _trusted(request.client.host):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


def _host(request: Request) -> str:
    if request.client and _trusted(request.client.host) and request.headers.get("x-forwarded-host"):
        return request.headers["x-forwarded-host"].split(",")[0].strip().lower()
    return request.headers.get("host", "").lower()


@app.middleware("http")
async def guard(request: Request, call_next):
    # Aenderungen nur von der eigenen Seite (Origin passt zum Host) - Schutz gegen fremde
    # Webseiten, die im Namen eines angemeldeten Benutzers Anfragen schicken
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc.lower() != _host(request):
            return JSONResponse({"detail": i18n.translate("Anfrage von fremder Seite abgelehnt", ui_lang(request))},
                                status_code=403)
    try:
        resp = await call_next(request)
    except httpx.HTTPError:
        # ComfyUI nicht erreichbar / Zeitueberschreitung -> verstaendliche Meldung statt 500
        resp = JSONResponse({"detail": i18n.translate("ComfyUI ist gerade nicht erreichbar. Bitte gleich nochmal versuchen.",
                                                      ui_lang(request))}, status_code=503)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return resp


# --------------------------------------------------------------------------- Sitzungen

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(request: Request, user_id: int) -> JSONResponse:
    token = secrets.token_urlsafe(32)
    with closing(db()) as con, con:
        con.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                    (_token_hash(token), user_id, time.time() + SESSION_DAYS * 86400))
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=_is_https(request))
    return resp


def current_user(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with closing(db()) as con:
        row = con.execute("SELECT u.id, u.name, u.admin, u.nsfw, s.token FROM sessions s JOIN users u ON u.id = s.user_id "
                          "WHERE s.token = ? AND s.expires > ?", (_token_hash(token), time.time())).fetchone()
    return dict(row) if row else None


# API-Schluessel (Authorization: Bearer ig_...) fuer andere Programme, siehe Abschnitt "API"
API_KEY_PREFIX = "ig_"


def _key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_user(request: Request) -> dict | None:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    key = auth[7:].strip()
    if not key.startswith(API_KEY_PREFIX):
        return None
    with closing(db()) as con, con:
        row = con.execute("SELECT u.id, u.name, u.admin, u.nsfw, k.id AS key_id FROM api_keys k "
                          "JOIN users u ON u.id = k.user_id WHERE k.hash = ?", (_key_hash(key),)).fetchone()
        if row:
            con.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (time.time(), row["key_id"]))
    # token = None: Schluessel ist keine Sitzung; via_key sperrt Verwaltungsfunktionen
    return dict(row, token=None, via_key=True) if row else None


def need_user(request: Request) -> dict:
    u = current_user(request) or key_user(request)
    if not u:
        raise HTTPException(401, "nicht angemeldet")
    return u


def need_session(user: dict = Depends(need_user)) -> dict:
    if user.get("via_key"):
        raise HTTPException(403, "Mit API-Schlüssel nicht erlaubt")
    return user


def need_admin(user: dict = Depends(need_user)) -> dict:
    if not user["admin"] or user.get("via_key"):
        raise HTTPException(403, "nur fuer Admins")
    return user


def page(name: str) -> FileResponse:
    # no-store: sonst zeigt der Browser nach dem Login die alte Login-Seite aus dem Cache
    return FileResponse(os.path.join(STATIC, name), headers={"Cache-Control": "no-store"})


# --------------------------------------------------------------------------- Einrichtung, Login

_fails: dict[str, list[float]] = {}


def _recent_fails(key: str) -> list[float]:
    return [t for t in _fails.get(key, []) if t > time.time() - 600]


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungültige Anfrage")
    if not isinstance(body, dict):
        raise HTTPException(400, "Ungültige Anfrage")
    return body


@app.post("/api/setup")
async def setup(request: Request):
    body = await _json(request)
    name, pw = str(body.get("name", "")).strip(), str(body.get("password", ""))
    validate_new(name, pw)
    pw_hash = await hash_pw_async(pw)
    with closing(db()) as con, con:
        # nur solange es noch niemanden gibt (in einer Transaktion geprueft)
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
            raise HTTPException(409, "Ist schon eingerichtet")
        uid = con.execute("INSERT INTO users (name, pw, admin, nsfw, created) VALUES (?, ?, 1, 1, ?)",
                          (name, pw_hash, time.time())).lastrowid
    return start_session(request, uid)


@app.post("/api/login")
async def login(request: Request):
    body = await _json(request)
    name = str(body.get("name", "")).strip()[:64]
    ip_key, name_key = "ip:" + client_ip(request), "name:" + name.lower()
    if len(_recent_fails(ip_key)) >= LOGIN_MAX_PER_IP or len(_recent_fails(name_key)) >= LOGIN_MAX_PER_NAME:
        raise HTTPException(429, "Zu viele Fehlversuche, bitte 10 Minuten warten")
    with closing(db()) as con:
        row = con.execute("SELECT id, pw FROM users WHERE name = ?", (name,)).fetchone()
    ok = await check_pw_async(str(body.get("password", ""))[:200], row["pw"] if row else _DUMMY_HASH)
    if not row or not ok:
        for k in (ip_key, name_key):
            _fails[k] = _recent_fails(k) + [time.time()]
        await asyncio.sleep(FAIL_DELAY)
        raise HTTPException(401, "Name oder Passwort falsch")
    _fails.pop(ip_key, None)
    return start_session(request, row["id"])


@app.post("/api/logout")
async def logout(request: Request):
    token = request.cookies.get(COOKIE)
    if token:
        with closing(db()) as con, con:
            con.execute("DELETE FROM sessions WHERE token = ?", (_token_hash(token),))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@app.get("/api/me")
async def me(user: dict = Depends(need_user)):
    return {"name": user["name"], "admin": bool(user["admin"]), "nsfw": bool(user["nsfw"]), "version": VERSION}


@app.post("/api/me/password")
async def change_own_password(request: Request, user: dict = Depends(need_session)):
    body = await _json(request)
    with closing(db()) as con:
        stored = con.execute("SELECT pw FROM users WHERE id = ?", (user["id"],)).fetchone()["pw"]
    if not await check_pw_async(str(body.get("old", "")), stored):
        raise HTTPException(400, "Altes Passwort falsch")
    new = str(body.get("new", ""))
    validate_new(user["name"], new)
    pw_hash = await hash_pw_async(new)
    with closing(db()) as con, con:
        con.execute("UPDATE users SET pw = ? WHERE id = ?", (pw_hash, user["id"]))
        # andere Geraete abmelden (z.B. verlorenes Handy), diese Sitzung bleibt
        con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (user["id"], user["token"]))
    return {"ok": True}


# --------------------------------------------------------------------------- Benutzerverwaltung (Admin)

@app.get("/api/users")
async def users(admin: dict = Depends(need_admin)):
    with closing(db()) as con:
        rows = con.execute("SELECT u.id, u.name, u.admin, u.nsfw, u.created, "
                           "(SELECT COUNT(*) FROM images i WHERE i.user_id = u.id) AS images "
                           "FROM users u ORDER BY u.name COLLATE NOCASE").fetchall()
    return [dict(r, admin=bool(r["admin"]), nsfw=bool(r["nsfw"]), me=r["id"] == admin["id"]) for r in rows]


@app.post("/api/users")
async def create_user(request: Request, admin: dict = Depends(need_admin)):
    body = await _json(request)
    name, pw = str(body.get("name", "")).strip(), str(body.get("password", ""))
    validate_new(name, pw)
    pw_hash = await hash_pw_async(pw)
    try:
        with closing(db()) as con, con:
            con.execute("INSERT INTO users (name, pw, admin, nsfw, created) VALUES (?, ?, ?, ?, ?)",
                        (name, pw_hash, 1 if body.get("admin") else 0, 1 if body.get("nsfw") else 0, time.time()))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Name ist schon vergeben")
    return {"ok": True}


def _admins(con) -> int:
    return con.execute("SELECT COUNT(*) FROM users WHERE admin = 1").fetchone()[0]


@app.patch("/api/users/{uid}")
async def update_user(uid: int, request: Request, admin: dict = Depends(need_admin)):
    body = await _json(request)
    with closing(db()) as con:
        row = con.execute("SELECT name, admin FROM users WHERE id = ?", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "Benutzer nicht gefunden")
    pw_hash = None
    if "password" in body:
        validate_new(row["name"], str(body["password"]))
        pw_hash = await hash_pw_async(str(body["password"]))
    with closing(db()) as con, con:
        if pw_hash:
            con.execute("UPDATE users SET pw = ? WHERE id = ?", (pw_hash, uid))
            # alle Sitzungen dieses Benutzers beenden (beim eigenen Konto: ausser dieser)
            con.execute("DELETE FROM sessions WHERE user_id = ? AND token != ?", (uid, admin["token"]))
        if "admin" in body:
            make = bool(body["admin"])
            if not make and row["admin"] and _admins(con) <= 1:
                raise HTTPException(400, "Der letzte Admin kann nicht herabgestuft werden")
            con.execute("UPDATE users SET admin = ? WHERE id = ?", (1 if make else 0, uid))
        if "nsfw" in body:
            con.execute("UPDATE users SET nsfw = ? WHERE id = ?", (1 if body["nsfw"] else 0, uid))
    return {"ok": True}


@app.delete("/api/users/{uid}")
async def delete_user(uid: int, admin: dict = Depends(need_admin)):
    if uid == admin["id"]:
        raise HTTPException(400, "Das eigene Konto kann nicht gelöscht werden")
    with closing(db()) as con, con:
        row = con.execute("SELECT admin FROM users WHERE id = ?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "Benutzer nicht gefunden")
        if row["admin"] and _admins(con) <= 1:
            raise HTTPException(400, "Der letzte Admin kann nicht gelöscht werden")
        # Bilder bleiben erhalten (Admin sieht sie als "ohne Besitzer"). Auftraege weg: SQLite
        # vergibt die ID evtl. neu, ein neuer Benutzer wuerde sonst offene Auftraege erben
        con.execute("UPDATE images SET user_id = NULL WHERE user_id = ?", (uid,))
        con.execute("DELETE FROM jobs WHERE user_id = ?", (uid,))
        con.execute("DELETE FROM users WHERE id = ?", (uid,))
    return {"ok": True}


# --------------------------------------------------------------------------- Workflows, Erzeugen

def load_index() -> list[dict]:
    with open(os.path.join(WORKFLOWS, "index.json"), encoding="utf-8") as f:
        return json.load(f)


def find_workflow(file: str) -> dict:
    for e in load_index():
        if e["file"] == file:
            return e
    raise HTTPException(404, "Workflow unbekannt")


# Auswahlen je Video-Workflow (index.json): Bewegung (Zusatz hinten + Shift), Aktion (LoRA + Trigger vorn).
# Je Stufe: "prefix"/"suffix" am Prompt und "set" = [[Knoten, Eingang, Wert], ...]
OPTIONS = ("motion", "action")


@app.get("/api/workflows")
async def workflows(user: dict = Depends(need_user)):
    keys = ("file", "title", "mode", "summary", "seconds", "default_size", "kind", "fps", "sizes",
            "durations", "default_duration", "auto_size", "group", "variant", "default_prompt", "tab")
    out = []
    for e in load_index():
        d = {k: e.get(k) for k in keys}
        d["enhance"] = bool(LLM_URL)
        if user["nsfw"] and e.get("summary_nsfw"):
            d["summary"] = e["summary_nsfw"]
        for opt in OPTIONS:             # nur Beschriftung + Standard; was eine Stufe tut, bleibt am Server
            if (m := e.get(opt)) and (user["nsfw"] or not m.get("nsfw")):
                d[opt] = {"default": m["default"], "labels": [lv["label"] for lv in m["levels"]],
                          "hints": [lv.get("hint", "") for lv in m["levels"]]}
        out.append(d)
    return out


def _set(wf: dict, field: dict, value):
    wf[field["node"]]["inputs"][field["key"]] = value


def _image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return None


async def _read_image(img: UploadFile) -> tuple[bytes, str]:
    """Upload lesen und pruefen: Groesse und echter Inhalt (nicht nur die Endung)."""
    data = await img.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"Bild zu groß: {img.filename} (max. 30 MB)")
    ext = _image_type(data)
    if not ext:
        raise HTTPException(400, f"Kein PNG/JPG/WebP: {img.filename} (bei iPhone-Fotos: als JPG teilen)")
    return data, ext


def image_dims(data: bytes) -> tuple[int, int] | None:
    """Breite/Hoehe aus dem Dateikopf von PNG/JPEG/WebP (ohne Bildbibliothek)."""
    try:
        if data.startswith(b"\x89PNG"):
            return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
        if data.startswith(b"\xff\xd8"):
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker, seg = data[i + 1], int.from_bytes(data[i + 2:i + 4], "big")
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    return int.from_bytes(data[i + 7:i + 9], "big"), int.from_bytes(data[i + 5:i + 7], "big")
                i += 2 + seg
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            chunk = data[12:16]
            if chunk == b"VP8 ":
                return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
            if chunk == b"VP8L":
                b = int.from_bytes(data[21:25], "little")
                return (b & 0x3FFF) + 1, ((b >> 14) & 0x3FFF) + 1
            if chunk == b"VP8X":
                return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    except (IndexError, ValueError):
        pass
    return None


def size_for(dims, sizes) -> tuple[int, int] | None:
    """Passende Video-Groesse zum Seitenverhaeltnis des Startbilds (nur die schnellen Groessen)."""
    import math
    if not dims or not dims[0] or not dims[1]:
        return None
    base = [s_ for s_ in sizes if s_[1] * s_[2] <= 832 * 480 * 1.1] or sizes
    ar = dims[0] / dims[1]
    best = min(base, key=lambda s_: abs(math.log((s_[1] / s_[2]) / ar)))
    return best[1], best[2]


async def _send_upload(data: bytes, ext: str) -> str:
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}{ext}"
    r = await client.post(f"{COMFY}/upload/image", files={"image": (name, data)},
                          data={"subfolder": UPLOAD_SUBFOLDER, "type": "input"})
    r.raise_for_status()
    j = r.json()
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


def _drop_upload(ref: str | None):
    """Hochgeladenes Eingabebild nach dem Auftrag entfernen (der Browser hat es noch)."""
    if not ref:
        return
    sub, _, name = ref.rpartition("/")
    try:
        p = _safe_path("input", sub, name)
        if sub.split("/")[0] == UPLOAD_SUBFOLDER and os.path.isfile(p):
            os.remove(p)
    except (HTTPException, OSError):
        pass


def _split_ref(ref: str) -> tuple[str, str]:
    if ".." in ref or ref.startswith("/") or "\\" in ref or "[" in ref:
        raise HTTPException(400, "Ungültiger Bildverweis")
    sub, _, name = ref.rpartition("/")
    return sub, name


def may_see(user: dict, subfolder: str, filename: str) -> bool:
    if user["admin"]:
        return True
    with closing(db()) as con:
        return con.execute("SELECT 1 FROM images WHERE subfolder = ? AND filename = ? AND user_id = ?",
                           (subfolder, filename, user["id"])).fetchone() is not None


# ---- Variablen im Prompt -------------------------------------------------------------
# [rot|blau|gruen]  -> pro Bild eine der Moeglichkeiten
# [haar]            -> benannte Variable aus dem Variablen-Feld ("haar = rot | blau")
# [sonstwas]        -> keine Variable: bleibt als Text stehen (z.B. Gewichtungen bei NoobAI)
# Verschachtelt geht auch: "a = [b] im Wald | [b] am See", "b = Junge | Maedchen",
# "[ein [rot|blau]er Ball|ein Hund]". Alles wird HIER aufgeloest, bevor etwas an ComfyUI geht:
# das Modell sieht nur den fertigen Prompt.

VAR_NAME_RE = re.compile(r"^[\w äöüÄÖÜß.-]{1,40}$")
MAX_PROMPT_OUT = 20000      # Schutz gegen ausufernde Verschachtelung


def _split_top(s: str) -> list[str]:
    """An '|' trennen, aber nicht innerhalb von [ ]: 'a | [x|y] b' -> ['a ', ' [x|y] b']."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch == "[":
            depth += 1
        elif ch == "]" and depth:
            depth -= 1
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _matching(s: str, i: int) -> int:
    """Index der zu s[i]=='[' gehoerenden ']' oder -1."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "[":
            depth += 1
        elif s[j] == "]":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _parse(s: str, names: set[str], unknown: list[str]) -> list:
    """Text -> Knoten: str | ("choice", [alternativen]) | ("var", name) | ("lit", knoten)."""
    out, i, buf = [], 0, []
    while i < len(s):
        j = _matching(s, i) if s[i] == "[" else -1
        if j < 0:
            buf.append(s[i])
            i += 1
            continue
        if buf:
            out.append("".join(buf))
            buf = []
        inner = s[i + 1:j]
        alts = _split_top(inner)
        if len(alts) > 1:
            out.append(("choice", [_parse(a.strip(), names, unknown) for a in alts]))
        elif inner.strip().lower() in names:
            out.append(("var", inner.strip().lower()))
        else:
            if inner.strip() and "[" not in inner and inner.strip() not in unknown:
                unknown.append(inner.strip())
            out.append(("lit", _parse(inner, names, unknown)))
        i = j + 1
    if buf:
        out.append("".join(buf))
    return out


def parse_variables(text: str) -> dict[str, list[str]]:
    """Zeilen 'name = a | b | c' (oder 'name: ...'). Leere Zeilen und # Kommentare zaehlen nicht.
    Moeglichkeiten duerfen selbst [Variablen] und [x|y] enthalten."""
    out: dict[str, list[str]] = {}
    for n, line in enumerate((text or "").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([^=:\[\]]+)[=:](.*)$", line)
        if not m:
            raise HTTPException(400, f"Variablen, Zeile {n}: Form 'name = a | b | c' erwartet")
        name = m.group(1).strip().lower()
        opts = [o.strip() for o in _split_top(m.group(2)) if o.strip()]
        if not VAR_NAME_RE.match(name):
            raise HTTPException(400, f"Variablen, Zeile {n}: ungültiger Name '{name}'")
        if not opts:
            raise HTTPException(400, f"Variablen, Zeile {n}: keine Möglichkeiten angegeben")
        out[name] = opts
    return out


class Deck:
    """Zieht Moeglichkeiten gleichmaessig: jede kommt einmal dran, bevor sich eine wiederholt."""

    def __init__(self, n: int, rng: random.Random):
        self.n, self.rng, self.left = n, rng, []

    def draw(self) -> int:
        if not self.left:
            self.left = list(range(self.n))
            self.rng.shuffle(self.left)
        return self.left.pop()


def expand_prompts(template: str, variables: dict[str, list[str]], count: int,
                   rng: random.Random | None = None) -> tuple[list[str], list[str]]:
    """count fertige Prompts aus der Vorlage; dazu Namen in [..], die keine Variable sind."""
    rng = rng or random.Random()
    names = set(variables)
    unknown: list[str] = []
    tree = _parse(template, names, unknown)
    values = {k: [_parse(o, names, unknown) for o in opts] for k, opts in variables.items()}
    decks: dict = {}                     # je Auswahl-Knoten / Variable ein Deck, ueber alle Bilder

    def deck(key, n):
        if key not in decks:
            decks[key] = Deck(n, rng)
        return decks[key]

    def render(nodes, picked, stack) -> str:
        parts = []
        for node in nodes:
            if isinstance(node, str):
                parts.append(node)
            elif node[0] == "choice":
                parts.append(render(node[1][deck(id(node), len(node[1])).draw()], picked, stack))
            elif node[0] == "var":
                name = node[1]
                if name in stack:
                    chain = " -> ".join(stack + [name])
                    raise HTTPException(400, f"Variable [{name}] verweist auf sich selbst ({chain})")
                if name not in picked:          # gleiche Variable im selben Bild = gleicher Wert
                    opts = values[name]
                    picked[name] = render(opts[deck("var:" + name, len(opts)).draw()], picked, stack + [name])
                parts.append(picked[name])
            else:
                parts.append("[" + render(node[1], picked, stack) + "]")
        out = "".join(parts)
        if len(out) > MAX_PROMPT_OUT:
            raise HTTPException(400, "Prompt wird nach dem Einsetzen zu lang")
        return out

    prompts = [re.sub(r"[ \t]{2,}", " ", render(tree, {}, [])).strip() for _ in range(count)]
    return prompts, unknown


@app.post("/api/expand")
async def expand(request: Request, user: dict = Depends(need_user)):
    """Vorschau: so koennten die fertigen Prompts aussehen (beim Erzeugen wird neu gewuerfelt)."""
    body = await _json(request)
    try:
        count = max(1, min(int(body.get("count") or 1), MAX_BATCH))
    except (TypeError, ValueError):
        raise HTTPException(400, "Anzahl muss eine Zahl sein")
    prompts, unknown = expand_prompts(str(body.get("prompt", "")), parse_variables(str(body.get("variables", ""))), count)
    return {"prompts": prompts, "unknown": unknown}


def _enhance_system(entry: dict) -> str:
    video, edit = entry.get("kind") == "video", entry["mode"] == "edit"
    img = " (use the image you are shown)" if edit else ""
    if video:
        s = SYS_VIDEO.format(i2v=I2V if edit else "", img=img)
    else:
        s = (SYS_EDIT if edit else SYS_IMAGE).format(img=img)
    if hint := entry.get("enhance_hint"):
        s += "\n- " + hint
    return s


@app.post("/api/enhance")
async def enhance(workflow: str = Form(...), prompt: str = Form(...), source: str = Form(""),
                  image: UploadFile | None = File(None), user: dict = Depends(need_user)):
    """Kurze Idee -> ausfuehrlicher Prompt (Sprachmodell). Bei Bearbeiten/Video aus Bild sieht das
    Modell das (erste) Eingabebild mit."""
    if not LLM_URL:
        raise HTTPException(503, "Ausformulieren ist nicht eingerichtet (WEBAPP_LLM_URL)")
    entry = find_workflow(workflow)
    text = prompt.strip()
    if not text:
        raise HTTPException(400, "Bitte erst eine kurze Idee eingeben")
    if len(text) > MAX_PROMPT:
        raise HTTPException(400, f"Prompt zu lang (max. {MAX_PROMPT} Zeichen)")
    data = None
    if entry["mode"] == "edit":
        if image is not None and image.filename:
            data, _ = await _read_image(image)
        elif source:
            sub, name = _split_ref(source)
            if not name.lower().endswith(IMAGE_EXT):
                raise HTTPException(400, "Als Eingabe gehen nur Bilder, keine Videos")
            if not may_see(user, sub, name):
                raise HTTPException(403, "Kein Zugriff auf dieses Bild")
            try:
                with open(_safe_path("output", sub, name), "rb") as fh:
                    data = fh.read(MAX_UPLOAD + 1)
            except OSError:
                raise HTTPException(404, "Bild nicht gefunden")
    out, warning = await enhance_text(entry, text, data)
    return {"prompt": out, "warning": warning}


async def enhance_text(entry: dict, text: str, data: bytes | None) -> tuple[str, str | None]:
    """Sprachmodell formuliert den Prompt aus (Regeln je Workflow); bei Bearbeiten mit Eingabebild."""
    msg = {"role": "user", "content": text}
    if data:
        msg["images"] = [base64.b64encode(data).decode()]
    body = {"model": LLM_MODEL or "default", "stream": False, "think": False, "options": {"temperature": 0.4},
            "messages": [{"role": "system", "content": _enhance_system(entry)}, msg]}
    try:
        r = await client.post(f"{LLM_URL}/api/chat", json=body, timeout=180)
        r.raise_for_status()
        out = r.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, ValueError) as e:
        raise HTTPException(502, f"Sprachmodell nicht erreichbar: {type(e).__name__}")
    out = re.sub(r"^\s*(?:\*\*)?(?:prompt|output)\s*:?(?:\*\*)?\s*", "", out, flags=re.I).strip().strip('"').strip()
    if not out:
        raise HTTPException(502, "Sprachmodell lieferte nichts")
    if len(out) > MAX_PROMPT:
        out = out[:MAX_PROMPT]
    missing = sorted(set(re.findall(r"\[[^\]]+\]", text)) - set(re.findall(r"\[[^\]]+\]", out)))
    return out, (f"Platzhalter fehlen jetzt: {' '.join(missing)}" if missing else None)


@app.post("/api/generate")
async def generate(workflow: str = Form(...), prompt: str = Form(...), variables: str = Form(""),
                   count: int = Form(1), width: int | None = Form(None), height: int | None = Form(None),
                   seconds: int | None = Form(None), motion: int | None = Form(None),
                   action: int | None = Form(None),
                   seed: int | None = Form(None), source: list[str] = Form([]),
                   image: list[UploadFile] = File([]), user: dict = Depends(need_user)):
    """Ein Auftrag je (Eingabebild x Anzahl). Mehrere Eingabebilder (hochgeladen und/oder
    eigene Ergebnisse) werden alle mit demselben Prompt einzeln bearbeitet."""
    entry = find_workflow(workflow)
    fields = entry["fields"]
    template = prompt.strip() or entry.get("default_prompt", "")     # Token aus Bild: Text optional
    if not template:
        raise HTTPException(400, "Bitte einen Prompt eingeben")
    if len(template) > MAX_PROMPT or len(variables) > MAX_VARIABLES:
        raise HTTPException(400, f"Prompt (max. {MAX_PROMPT}) oder Variablen (max. {MAX_VARIABLES} Zeichen) zu lang")
    if not 1 <= count <= MAX_BATCH:
        raise HTTPException(400, f"Anzahl 1-{MAX_BATCH}")
    files = [f for f in image if f is not None and f.filename]
    sources = [s for s in source if s]
    if "image" in fields:
        n_inputs = len(files) + len(sources)
        if not n_inputs:
            raise HTTPException(400, "Bitte ein Eingabebild wählen")
        if n_inputs > MAX_INPUTS:
            raise HTTPException(400, f"Höchstens {MAX_INPUTS} Eingabebilder auf einmal")
    else:
        files, sources, n_inputs = [], [], 1          # Erzeugen: Eingabebilder ignorieren
    total = n_inputs * count
    if total > MAX_BATCH:
        raise HTTPException(400, f"Zu viele Bilder auf einmal ({total}, höchstens {MAX_BATCH})")
    prompts, _ = expand_prompts(template, parse_variables(variables), total)
    if any(not p for p in prompts):
        raise HTTPException(400, "Nach dem Einsetzen der Variablen ist der Prompt leer")
    with closing(db()) as con:
        active = con.execute("SELECT COUNT(*) FROM jobs WHERE user_id = ? AND done = 0", (user["id"],)).fetchone()[0]
    if active + total > MAX_ACTIVE_JOBS:
        raise HTTPException(429, f"Zu viele Aufträge: {active} laufen noch, höchstens {MAX_ACTIVE_JOBS} gleichzeitig - bitte warten oder weniger Bilder")
    with open(os.path.join(WORKFLOWS, entry["file"] + ".json"), encoding="utf-8") as f:
        base = json.load(f)
    if "width" in fields and width and height:
        if not (256 <= width <= SIZE_LIMIT and 256 <= height <= SIZE_LIMIT):
            raise HTTPException(400, f"Größe 256-{SIZE_LIMIT} px")
        _set(base, fields["width"], width - width % 16)
        _set(base, fields["height"], height - height % 16)
    if "length" in fields:          # Video: Laenge in Sekunden -> Einzelbilder (Wan: 4n+1)
        secs = seconds or entry.get("default_duration") or 5
        if not 1 <= secs <= 10:
            raise HTTPException(400, "Videolänge 1-10 Sekunden")
        _set(base, fields["length"], round(secs * (entry.get("fps") or 16) / 4) * 4 + 1)
    chosen, mprefix, msuffix = {}, "", ""
    for opt, given, label in (("motion", motion, "Bewegung"), ("action", action, "Aktion")):
        if not entry.get(opt):
            continue
        levels = entry[opt]["levels"]
        if entry[opt].get("nsfw") and not user["nsfw"] and given and given != entry[opt]["default"]:
            raise HTTPException(403, f"{label}: nicht freigeschaltet")
        lvl = given or entry[opt]["default"]
        if not 1 <= lvl <= len(levels):
            raise HTTPException(400, f"{label} 1-{len(levels)}")
        chosen[opt] = lvl
        mprefix += levels[lvl - 1].get("prefix", "")
        msuffix += levels[lvl - 1].get("suffix", "")
        for node, key, value in levels[lvl - 1].get("set", []):
            base[node]["inputs"][key] = value
    est = entry["seconds"]          # Schaetzung gilt fuer Standardgroesse/-laenge; Video skaliert grob linear
    if "length" in fields:
        est *= secs / (entry.get("default_duration") or 5)
        dw, dh = entry.get("default_size") or (0, 0)
        if width and height and dw and dh:
            est *= (width * height) / (dw * dh)
    est = max(1, round(est))
    auto_size = bool(entry.get("auto_size")) and "width" in fields and not (width and height)

    # Eingabebilder: erst alles pruefen (Rechte, Inhalt), dann hochladen - damit bei einem
    # Fehler nichts halb hochgeladen liegen bleibt
    # (Wert fuer LoadImage, Upload-Verweis oder None, Bildmasse)
    inputs: list[tuple[str | None, str | None, tuple | None]] = []
    for src in sources:
        sub, name = _split_ref(src)
        if not name.lower().endswith(IMAGE_EXT):
            raise HTTPException(400, "Als Eingabe gehen nur Bilder, keine Videos")
        if not may_see(user, sub, name):
            raise HTTPException(403, "Kein Zugriff auf dieses Bild")
        dims = None
        if auto_size:
            try:
                with open(_safe_path("output", sub, name), "rb") as fh:
                    dims = image_dims(fh.read(256 * 1024))
            except OSError:
                pass
        inputs.append((f"{src} [output]", None, dims))  # ComfyUI laedt mit "[output]" aus dem Ausgabeordner
    blobs = [await _read_image(f) for f in files]
    uploads: list[str] = []
    try:
        for data, ext in blobs:
            ref = await _send_upload(data, ext)
            uploads.append(ref)
            inputs.append((ref, ref, image_dims(data) if auto_size else None))
    except Exception:
        for ref in uploads:
            _drop_upload(ref)
        raise
    if not inputs:
        inputs = [(None, None, None)]

    first_seed = seed if seed is not None and 0 <= seed < 2**53 - MAX_BATCH else None
    jobs, used, k = [], set(), 0
    for value, upload, dims in inputs:               # je Eingabebild 'count' Auftraege
        for _ in range(count):
            text = mprefix + prompts[k] + msuffix      # so geht er ans Modell und wird so gespeichert
            wf = json.loads(json.dumps(base))
            _set(wf, fields["prompt"], text)
            if value is not None:
                _set(wf, fields["image"], value)
            if auto_size and (wh := size_for(dims, entry.get("sizes") or [])):
                _set(wf, fields["width"], wh[0])         # Video so ausrichten wie das Startbild
                _set(wf, fields["height"], wh[1])
            s = first_seed + k if first_seed is not None else random.randrange(2**50)
            _set(wf, fields["seed"], s)
            k += 1
            # Eindeutige Dateinamen: ComfyUI zaehlt sonst nach dem Loeschen wieder ab _00001_, und
            # der Browser zeigt unter demselben Namen das alte, zwischengespeicherte Bild
            stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"
            for node in wf.values():
                if node.get("class_type") == "SaveImage":
                    node["inputs"]["filename_prefix"] = f"{node['inputs'].get('filename_prefix', 'Bild')}_{stamp}"
            r = await client.post(f"{COMFY}/prompt", json={"prompt": wf, "client_id": CLIENT_ID})
            if r.status_code != 200:
                try:
                    err = r.json()
                    msg = err.get("error", {}).get("message", "") + " " + json.dumps(err.get("node_errors", ""))[:400]
                except ValueError:
                    msg = r.text[:400]
                for ref in set(uploads) - used:      # nicht mehr gebrauchte Uploads weg
                    _drop_upload(ref)
                if jobs:        # ein Teil ist schon in der Warteschlange: den zurueckmelden
                    return {"id": jobs[0]["id"], "seed": jobs[0]["seed"], "jobs": jobs,
                            "warning": f"Nur {len(jobs)} von {total} gestartet: {msg}"}
                raise HTTPException(502, f"ComfyUI hat den Auftrag abgelehnt: {msg}")
            pid = r.json()["prompt_id"]
            with closing(db()) as con, con:
                con.execute("INSERT INTO jobs (prompt_id, user_id, workflow, title, prompt, seed, seconds, started, "
                            "upload, template, variables, motion, action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (pid, user["id"], entry["file"], entry["title"], text, s, est, time.time(),
                             upload, template, variables.strip(), chosen.get("motion"), chosen.get("action")))
            if upload:
                used.add(upload)
            jobs.append({"id": pid, "seed": s, "prompt": text})
    return {"id": jobs[0]["id"], "seed": jobs[0]["seed"], "jobs": jobs}


def _images(hist: dict) -> list[dict]:
    return [{"filename": i["filename"], "subfolder": i.get("subfolder", "")}
            for o in hist.get("outputs", {}).values() for i in o.get("images", [])
            if i.get("type", "output") == "output"]


def _finish(meta, state: int, imgs: list[dict] = (), error: str | None = None):
    """Auftrag abschliessen: Bilder dem Benutzer zuordnen (einmalig), Upload entfernen,
    sobald kein anderer Auftrag der Serie ihn noch braucht."""
    with closing(db()) as con, con:
        cur = con.execute("UPDATE jobs SET done = ?, error = ?, finished = ? WHERE prompt_id = ? AND done = 0",
                          (state, error, time.time(), meta["prompt_id"]))
        if not cur.rowcount:
            return          # schon abgeschlossen (paralleler Abruf)
        for i in imgs:
            con.execute("INSERT OR IGNORE INTO images (user_id, prompt_id, subfolder, filename, workflow, "
                        "prompt, seed, created, template, variables, motion, action) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (meta["user_id"], meta["prompt_id"], i["subfolder"], i["filename"], meta["title"],
                         meta["prompt"], meta["seed"], time.time(), meta["template"], meta["variables"],
                         meta["motion"], meta["action"]))
        still = meta["upload"] and con.execute("SELECT COUNT(*) FROM jobs WHERE upload = ? AND done = 0",
                                               (meta["upload"],)).fetchone()[0]
    if not still:
        _drop_upload(meta["upload"])


def _error_text(item: dict) -> str:
    msgs = [m[1] for m in item.get("status", {}).get("messages", []) if m[0] == "execution_error"]
    text = msgs[0].get("exception_message", "Fehler") if msgs else "Fehler in ComfyUI"
    return text.strip()[:500]


LOST_TEXT = "Der Auftrag ist verloren gegangen (ComfyUI wurde neu gestartet). Bitte nochmal starten."
STATES = {1: "done", 2: "error", 3: "lost", 4: "cancelled"}


def _job_row(pid: str):
    with closing(db()) as con:
        return con.execute("SELECT * FROM jobs WHERE prompt_id = ?", (pid,)).fetchone()


def _final_view(meta) -> dict:
    """Ergebnis eines abgeschlossenen Auftrags aus der eigenen Datenbank."""
    state = STATES[meta["done"]]
    if state == "done":
        with closing(db()) as con:
            imgs = [{"filename": r["filename"], "subfolder": r["subfolder"], "v": meta["prompt_id"][:8]}
                    for r in con.execute("SELECT subfolder, filename FROM images WHERE prompt_id = ? ORDER BY id",
                                         (meta["prompt_id"],))]
        return {"state": "done", "images": imgs, "title": meta["title"], "seed": meta["seed"],
                "prompt": meta["prompt"], "template": meta["template"], "variables": meta["variables"],
                "motion": meta["motion"], "action": meta["action"], "elapsed": round((meta["finished"] or time.time()) - meta["started"], 1)}
    if state == "lost":
        return {"state": "lost", "error": LOST_TEXT}
    if state == "cancelled":
        return {"state": "cancelled", "error": "Abgebrochen"}
    return {"state": "error", "error": meta["error"] or "Fehler in ComfyUI"}


async def _check_open(metas) -> dict[str, dict]:
    """Offene Auftraege mit ComfyUI abgleichen: eine Abfrage der Warteschlange fuer alle,
    den Verlauf nur fuer die, die nicht mehr in der Warteschlange stehen (= gerade fertig)."""
    out: dict[str, dict] = {}
    if not metas:
        return out
    q = (await client.get(f"{COMFY}/queue")).json()
    running = [x[1] for x in q.get("queue_running", [])]
    pending = [x[1] for x in q.get("queue_pending", [])]
    now = time.time()
    for meta in metas:
        pid = meta["prompt_id"]
        if pid in running:
            if not meta["run_started"]:     # Laufzeit ab dem echten Start, nicht ab dem Einreihen
                with closing(db()) as con, con:
                    con.execute("UPDATE jobs SET run_started = ? WHERE prompt_id = ? AND run_started IS NULL", (now, pid))
            out[pid] = {"state": "running", "elapsed": round(now - (meta["run_started"] or now), 1),
                        "seconds": meta["seconds"]}
            continue
        if pid in pending:
            out[pid] = {"state": "queued", "ahead": pending.index(pid) + len(running), "seconds": meta["seconds"]}
            continue
        item = (await client.get(f"{COMFY}/history/{pid}")).json().get(pid)
        if item:
            st = item.get("status", {})
            if st.get("status_str") == "error":
                _finish(meta, 2, error=_error_text(item))
            elif st.get("completed", True):
                _finish(meta, 1, _images(item))
            else:
                out[pid] = {"state": "running", "elapsed": 0, "seconds": meta["seconds"]}
                continue
        elif now - meta["started"] > LOST_AFTER:
            _finish(meta, 3)                # ComfyUI wurde neu gestartet: Auftrag ist weg
        else:
            out[pid] = {"state": "queued", "ahead": 0, "seconds": meta["seconds"]}
            continue
        out[pid] = _final_view(_job_row(pid))
    return out


async def reconcile_jobs():
    """Offene Auftraege abgleichen, auch wenn niemand mehr wartet (Seite geschlossen):
    sonst landeten fertige Bilder ohne Besitzer und fehlten in der Galerie."""
    with closing(db()) as con:
        open_jobs = con.execute("SELECT * FROM jobs WHERE done = 0").fetchall()
    try:
        await _check_open(open_jobs)
    except (httpx.HTTPError, ValueError):
        return                          # ComfyUI gerade nicht erreichbar: spaeter nochmal


def _own_jobs(user: dict, ids) -> list:
    if not isinstance(ids, list) or not ids or len(ids) > MAX_BATCH * 2:
        raise HTTPException(400, "Keine Aufträge angegeben")
    ids = [str(i) for i in ids]
    with closing(db()) as con:
        rows = con.execute(f"SELECT * FROM jobs WHERE prompt_id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return [r for r in rows if r["user_id"] == user["id"] or user["admin"]]


@app.get("/api/job/{pid}")
async def job(pid: str, user: dict = Depends(need_user)):
    meta = _job_row(pid)
    if not meta or (meta["user_id"] != user["id"] and not user["admin"]):
        raise HTTPException(404, "Auftrag unbekannt")
    if meta["done"]:
        return _final_view(meta)
    return (await _check_open([meta]))[pid]


@app.post("/api/jobs/status")
async def jobs_status(request: Request, user: dict = Depends(need_user)):
    """Status vieler Auftraege (Serie) mit einer Anfrage."""
    rows = _own_jobs(user, (await _json(request)).get("ids"))
    out = {r["prompt_id"]: _final_view(r) for r in rows if r["done"]}
    out.update(await _check_open([r for r in rows if not r["done"]]))
    return out


@app.post("/api/jobs/cancel")
async def jobs_cancel(request: Request, user: dict = Depends(need_user)):
    """Serie/Auftrag abbrechen: wartende aus der Warteschlange nehmen, laufenden gezielt
    unterbrechen (nur diesen, nicht fremde). Fertige Bilder bleiben."""
    rows = [r for r in _own_jobs(user, (await _json(request)).get("ids")) if not r["done"]]
    if not rows:
        return {"cancelled": 0}
    q = (await client.get(f"{COMFY}/queue")).json()
    running = {x[1] for x in q.get("queue_running", [])}
    ids = [r["prompt_id"] for r in rows]
    await client.post(f"{COMFY}/queue", json={"delete": [i for i in ids if i not in running]})
    for pid in ids:
        if pid in running:
            await client.post(f"{COMFY}/interrupt", json={"prompt_id": pid})
    for r in rows:
        _finish(r, 4, error="Abgebrochen")
    return {"cancelled": len(rows)}


@app.get("/api/jobs/active")
async def active_jobs(user: dict = Depends(need_user)):
    """Eigene laufende Auftraege - damit die Seite nach dem Neuladen weiter mitzaehlt."""
    with closing(db()) as con:
        rows = con.execute("SELECT prompt_id, workflow, title, started FROM jobs WHERE user_id = ? AND done = 0 "
                           "ORDER BY started", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/recent")
async def recent(limit: int = 24, offset: int = 0, user: dict = Depends(need_user)):
    if offset == 0:
        await reconcile_jobs()
    with closing(db()) as con:
        rows = con.execute("SELECT subfolder, filename, workflow, prompt, seed, created, template, variables, motion, action FROM images "
                           "WHERE user_id = ? ORDER BY created DESC LIMIT ? OFFSET ?",
                           (user["id"], max(1, min(limit, 100)), max(offset, 0))).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/image")
async def image(filename: str, subfolder: str = "", preview: int = 0, kind: str = "output", v: str = "",
                user: dict = Depends(need_user)):
    if ".." in filename + subfolder or "/" in filename or "\\" in filename + subfolder or kind not in FOLDERS:
        raise HTTPException(400, "Ungültig")
    # Eingabebilder (Uploads, ComfyUI-Importe) sieht nur der Admin
    if (kind == "input" and not user["admin"]) or (kind == "output" and not may_see(user, subfolder, filename)):
        raise HTTPException(404, "Bild nicht gefunden")
    # Nur mit Versionsangabe (v) lange zwischenspeichern; ohne immer beim Server nachfragen
    cache = "private, max-age=86400" if v else "private, no-cache"
    if preview and filename.lower().endswith(IMAGE_EXT):
        # kleine WebP-Vorschau rechnet ComfyUI
        r = await client.get(f"{COMFY}/view", params={"filename": filename, "subfolder": subfolder,
                                                     "type": kind, "preview": "webp;80"})
        if r.status_code != 200:
            raise HTTPException(404, "Bild nicht gefunden")
        return Response(r.content, media_type=r.headers.get("content-type", "image/webp"),
                        headers={"Cache-Control": cache})
    # Original direkt aus dem Ordner: unterstuetzt Range-Anfragen (Vorspulen in Videos)
    p = _safe_path(kind, subfolder, filename)
    if not os.path.isfile(p):
        raise HTTPException(404, "Bild nicht gefunden")
    return FileResponse(p, headers={"Cache-Control": cache})


# --------------------------------------------------------------------------- Alle Bilder (Admin)

def _scan(root_dir: str) -> list[dict]:
    out = []
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.lower().endswith(MEDIA_EXT):
                p = os.path.join(root, f)
                sub = os.path.relpath(root, root_dir).replace(os.sep, "/")
                try:
                    st = os.stat(p)
                except OSError:             # gerade geloescht
                    continue
                out.append({"subfolder": "" if sub == "." else sub, "filename": f,
                            "mtime": st.st_mtime, "size": st.st_size})
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def protected_inputs() -> set[tuple[str, str]]:
    """Eingabebilder, die die Workflows als Vorgabe im LoadImage-Knoten haben (nicht loeschen)."""
    keep = set()
    for name in os.listdir(WORKFLOWS):
        if name.endswith(".json") and name != "index.json":
            try:
                with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as f:
                    wf = json.load(f)
            except (OSError, ValueError):
                continue
            for node in wf.values() if isinstance(wf, dict) else []:
                if isinstance(node, dict) and node.get("class_type") == "LoadImage":
                    sub, _, fn = str(node["inputs"].get("image", "")).rpartition("/")
                    keep.add((sub, fn))
    return keep


def _listing(kind: str, owner: str) -> list[dict]:
    files = _scan(FOLDERS[kind])
    if kind == "input":
        keep = protected_inputs()
        for f in files:
            f.update(owner=None, owner_id=None, prompt=None, workflow=None,
                     protected=(f["subfolder"], f["filename"]) in keep)
        return files
    with closing(db()) as con:
        rows = con.execute("SELECT i.subfolder, i.filename, i.user_id, u.name, i.prompt, i.workflow "
                           "FROM images i LEFT JOIN users u ON u.id = i.user_id").fetchall()
    meta = {(r["subfolder"], r["filename"]): r for r in rows}
    items = []
    for f in files:
        m = meta.get((f["subfolder"], f["filename"]))
        f.update(owner=m["name"] if m and m["name"] else None, owner_id=m["user_id"] if m else None,
                 prompt=m["prompt"] if m else None, workflow=m["workflow"] if m else None, protected=False)
        if owner == "all" or (owner == "none" and f["owner_id"] is None) or str(f["owner_id"]) == owner:
            items.append(f)
    return items


@app.get("/api/admin/images")
async def admin_images(kind: str = "output", owner: str = "all", limit: int = 60, offset: int = 0,
                       admin: dict = Depends(need_admin)):
    if kind not in FOLDERS:
        raise HTTPException(400, "Ungültig")
    if offset == 0:
        await reconcile_jobs()
    items = await asyncio.to_thread(_listing, kind, owner)
    return {"total": len(items), "size": sum(i["size"] for i in items),
            "items": items[max(offset, 0):max(offset, 0) + max(1, min(limit, 200))]}


def _safe_path(kind: str, subfolder: str, filename: str) -> str:
    base = FOLDERS[kind]
    p = os.path.realpath(os.path.join(base, subfolder, filename))
    if not p.startswith(base + os.sep) or not p.lower().endswith(MEDIA_EXT):
        raise HTTPException(400, "Ungültiger Pfad")
    return p


async def _delete_files(kind: str, targets: set[tuple[str, str]]) -> int:
    """Dateien loeschen; bei Ausgaben auch Zuordnung und ComfyUI-Verlauf."""
    paths = [_safe_path(kind, sub, name) for sub, name in targets]   # erst alles pruefen
    deleted = 0
    for p in paths:
        try:
            os.remove(p)
            deleted += 1
        except FileNotFoundError:
            pass
    if kind == "output" and targets:
        with closing(db()) as con, con:
            con.executemany("DELETE FROM images WHERE subfolder = ? AND filename = ?", list(targets))
        try:
            h = (await client.get(f"{COMFY}/history", params={"max_items": 100000})).json()
            pids = [pid for pid, item in h.items()
                    if any((i["subfolder"], i["filename"]) in targets for i in _images(item))]
            if pids:
                await client.post(f"{COMFY}/history", json={"delete": pids})
        except (httpx.HTTPError, ValueError):
            pass
    return deleted


def _targets(items) -> set[tuple[str, str]]:
    if not isinstance(items, list) or not items or len(items) > 10000:
        raise HTTPException(400, "Nichts ausgewählt")
    try:
        return {(str(i.get("subfolder", "")), str(i["filename"])) for i in items}
    except (AttributeError, KeyError, TypeError):
        raise HTTPException(400, "Ungültige Auswahl")


def _own(user_id: int) -> set[tuple[str, str]]:
    with closing(db()) as con:
        return {(r["subfolder"], r["filename"]) for r in con.execute(
            "SELECT subfolder, filename FROM images WHERE user_id = ?", (user_id,))}


@app.post("/api/my/delete")
async def my_delete(request: Request, user: dict = Depends(need_user)):
    """Benutzer loescht eigene Bilder: Auswahl (items) oder alle (all + Bestaetigung)."""
    body = await _json(request)
    own = _own(user["id"])
    if body.get("all"):
        if body.get("confirm") != "LÖSCHEN":
            raise HTTPException(400, "Bestätigung fehlt")
        wanted = own
    else:
        wanted = _targets(body.get("items"))
        if wanted - own:
            raise HTTPException(403, "Nur eigene Bilder können gelöscht werden")
    return {"deleted": await _delete_files("output", wanted)}


@app.get("/api/my/summary")
async def my_summary(user: dict = Depends(need_user)):
    """Anzahl und Groesse der eigenen Bilder (fuer 'Alle herunterladen/loeschen')."""
    size = count = 0
    for sub, name in _own(user["id"]):
        try:
            size += os.path.getsize(_safe_path("output", sub, name))
            count += 1
        except (OSError, HTTPException):
            pass
    return {"count": count, "size": size}


# --------------------------------------------------------------------------- Herunterladen (ZIP)

def _build_zip(kind: str, targets: list[tuple[str, str]], path: str) -> int:
    """ZIP ohne Kompression (PNG/JPG sind schon komprimiert): schnell, wenig Rechenzeit."""
    n = 0
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for sub, name in sorted(targets):
            try:
                src = _safe_path(kind, sub, name)
                z.write(src, arcname=f"{sub}/{name}" if sub else name)
                n += 1
            except (OSError, HTTPException):
                continue                    # inzwischen geloescht: einfach weglassen
    return n


async def _zip_response(kind: str, targets, label: str) -> FileResponse:
    if not targets:
        raise HTTPException(404, "Keine Bilder zum Herunterladen")
    fd, path = tempfile.mkstemp(suffix=".zip", prefix="bilder-")
    os.close(fd)
    try:
        n = await asyncio.to_thread(_build_zip, kind, list(targets), path)
    except Exception:
        os.remove(path)
        raise
    if not n:
        os.remove(path)
        raise HTTPException(404, "Keine Bilder zum Herunterladen")
    name = f"bilder-{label}-{time.strftime('%Y-%m-%d_%H%M')}.zip"
    return FileResponse(path, media_type="application/zip", filename=name,
                        headers={"Cache-Control": "no-store", "X-Image-Count": str(n)},
                        background=BackgroundTask(os.remove, path))


@app.get("/api/my/zip")
async def my_zip_all(user: dict = Depends(need_user)):
    return await _zip_response("output", _own(user["id"]), user["name"])


@app.post("/api/my/zip")
async def my_zip_selection(request: Request, user: dict = Depends(need_user)):
    wanted = _targets((await _json(request)).get("items"))
    if wanted - _own(user["id"]):
        raise HTTPException(403, "Nur eigene Bilder")
    return await _zip_response("output", wanted, user["name"])


@app.get("/api/admin/zip")
async def admin_zip_all(kind: str = "output", owner: str = "all", admin: dict = Depends(need_admin)):
    if kind not in FOLDERS:
        raise HTTPException(400, "Ungültig")
    listing = await asyncio.to_thread(_listing, kind, owner)
    label = "eingabebilder" if kind == "input" else ("alle" if owner == "all" else "ohne-besitzer" if owner == "none"
                                                     else f"benutzer-{owner}")
    if kind == "output" and owner.isdigit():
        with closing(db()) as con:
            row = con.execute("SELECT name FROM users WHERE id = ?", (int(owner),)).fetchone()
        label = row["name"] if row else label
    return await _zip_response(kind, {(i["subfolder"], i["filename"]) for i in listing}, label)


@app.post("/api/admin/zip")
async def admin_zip_selection(request: Request, admin: dict = Depends(need_admin)):
    body = await _json(request)
    kind = body.get("kind", "output")
    if kind not in FOLDERS:
        raise HTTPException(400, "Ungültig")
    return await _zip_response(kind, _targets(body.get("items")), "auswahl")


@app.post("/api/admin/delete")
async def admin_delete(request: Request, admin: dict = Depends(need_admin)):
    """Loescht ausgewaehlte Bilder (items) oder alle zum Filter passenden (all + owner)."""
    body = await _json(request)
    kind = body.get("kind", "output")
    if kind not in FOLDERS:
        raise HTTPException(400, "Ungültig")
    if body.get("all"):
        if body.get("confirm") != "LÖSCHEN":
            raise HTTPException(400, "Bestätigung fehlt")
        listing = await asyncio.to_thread(_listing, kind, str(body.get("owner", "all")))
        targets = {(i["subfolder"], i["filename"]) for i in listing}
    else:
        targets = _targets(body.get("items"))
    skipped = 0
    if kind == "input":
        keep = protected_inputs()
        skipped = len(targets & keep)
        targets -= keep
    deleted = await _delete_files(kind, targets)
    return {"deleted": deleted, "skipped": skipped}


# --------------------------------------------------------------------------- Seiten

# --------------------------------------------------------------------------- Modul fuer den OllamaProxy
# Der OllamaProxy (https://github.com/Poldion/OllamaProxy) zeigt Module in seiner Speicheranzeige und gibt sie
# frei, wenn ein Sprachmodell Platz braucht. ImageGen meldet dafuer, was ComfyUI haelt (Custom-Node
# proxy_memory, sonst /system_stats). Optionaler Schutz: WEBAPP_MODULE_TOKEN (Bearer).
MODULE_TOKEN = os.environ.get("WEBAPP_MODULE_TOKEN", "")
COMFYUI_LINK_PORT = int(os.environ.get("WEBAPP_COMFYUI_LINK_PORT", "0") or 0)     # 0 = kein Link zu ComfyUI
MODULE_IDLE_FREE_MINUTES = float(os.environ.get("WEBAPP_IDLE_FREE_MINUTES", "2"))
GIB = 1024 ** 3
COMFY_MIN_HELD = 512 * 1024 ** 2          # darunter lohnt Freigeben nicht
# Ohne geladene Modelle ist der verbleibende RSS nur das Framework (PyTorch/ROCm) -> nicht freigebbar
COMFY_BASE_RSS = int(float(os.environ.get("WEBAPP_COMFYUI_BASE_RSS_GB", "4.0")) * GIB)


def need_module(request: Request):
    if MODULE_TOKEN and not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {MODULE_TOKEN}"):
        raise HTTPException(401, "Modul-Schlüssel fehlt")


@app.get("/proxy-module")
async def proxy_module(request: Request, _=Depends(need_module)):
    lang = i18n.request_lang(request.headers, LANG_HEADER)
    links = [{"label": i18n.tr("ImageGen öffnen", lang), "path": "/"}]
    if COMFYUI_LINK_PORT:
        links.append({"label": i18n.tr("ComfyUI öffnen", lang), "port": COMFYUI_LINK_PORT})
    return {"name": "ImageGen", "version": VERSION, "links": links, "capabilities": ["memory", "free"],
            "idle_free_minutes": MODULE_IDLE_FREE_MINUTES}


@app.get("/proxy-module/memory")
async def proxy_module_memory(request: Request, _=Depends(need_module)):
    lang = i18n.request_lang(request.headers, LANG_HEADER)
    out = {"rss": None, "gpu": 0, "held": 0, "reclaimable": False, "busy": False, "detail": None}
    try:
        r = await client.get(f"{COMFY}/proxy/memory", timeout=3)
        if r.status_code == 200:
            p = r.json()
            rss, gpu, n = p.get("rss") or 0, p.get("gpu") or 0, p.get("loaded_models")
            out.update(rss=rss, gpu=gpu, busy=bool(p.get("busy")),
                       reclaimable=(n or 0) > 0 or gpu >= COMFY_MIN_HELD or rss > COMFY_BASE_RSS)
            if n:
                out["detail"] = (i18n.tr("ComfyUI: {n} Modell im Cache", lang, n=n) if n == 1
                                 else i18n.tr("ComfyUI: {n} Modelle im Cache", lang, n=n))
            out["held"] = rss + gpu if out["reclaimable"] else 0
        else:
            d = (await client.get(f"{COMFY}/system_stats", timeout=3)).json()
            gpu = sum(dev.get("torch_vram_total", 0) for dev in d.get("devices", []))
            q = (await client.get(f"{COMFY}/queue", timeout=3)).json()
            out.update(gpu=gpu, held=gpu, reclaimable=gpu >= COMFY_MIN_HELD,
                       busy=bool(q.get("queue_running") or q.get("queue_pending")))
    except (httpx.HTTPError, ValueError):
        out["detail"] = i18n.tr("ComfyUI nicht erreichbar", lang)
    return out


@app.post("/proxy-module/free")
async def proxy_module_free(_=Depends(need_module)):
    r = await client.post(f"{COMFY}/proxy/free", timeout=65)
    if r.status_code == 404:
        r = await client.post(f"{COMFY}/free", json={"unload_models": True, "free_memory": True}, timeout=65)
    return JSONResponse({"ok": r.status_code == 200}, status_code=r.status_code)


# --------------------------------------------------------------------------- API fuer andere Programme
# Zugang per Schluessel (Authorization: Bearer ig_...), je Benutzer in der App erzeugt. Rechte wie der
# Benutzer selbst (eigene Bilder, NSFW-Freigabe), aber keine Verwaltung (Benutzer, Schluessel, Passwort).
# Es gelten dieselben Workflows samt Stilsaetzen, Optionen und "Ausformulieren" wie in der Oberflaeche.
MAX_API_KEYS = 20
API_WAIT_MAX = int(os.environ.get("WEBAPP_API_WAIT_MAX", "1800"))     # Sekunden, wie lange ein Aufruf wartet


@app.get("/api/me/keys")
async def list_keys(user: dict = Depends(need_session)):
    with closing(db()) as con:
        rows = con.execute("SELECT id, name, hint, created, last_used FROM api_keys WHERE user_id = ? ORDER BY created",
                           (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/me/keys")
async def create_key(request: Request, user: dict = Depends(need_session)):
    body = await _json(request)
    name = str(body.get("name", "")).strip()[:40] or "API"
    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    with closing(db()) as con, con:
        if con.execute("SELECT COUNT(*) FROM api_keys WHERE user_id = ?", (user["id"],)).fetchone()[0] >= MAX_API_KEYS:
            raise HTTPException(400, f"Höchstens {MAX_API_KEYS} Schlüssel")
        con.execute("INSERT INTO api_keys (user_id, hash, name, hint, created) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], _key_hash(key), name, key[:7] + "…" + key[-4:], time.time()))
    return {"key": key, "name": name}       # nur jetzt im Klartext, gespeichert ist nur der Hash


@app.delete("/api/me/keys/{kid}")
async def delete_key(kid: int, user: dict = Depends(need_session)):
    with closing(db()) as con, con:
        if not con.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (kid, user["id"])).rowcount:
            raise HTTPException(404, "Schlüssel unbekannt")
    return {"ok": True}


def _b64_image(value: str) -> UploadFile:
    """Bild als Base64 oder data:-URL -> wie ein Upload behandeln (gleiche Pruefungen)."""
    if not isinstance(value, str) or not value:
        raise HTTPException(400, "Bild fehlt oder ist kein Text (Base64)")
    if value.startswith("data:"):
        value = value.split(",", 1)[-1]
    if len(value) > MAX_UPLOAD * 4 // 3 + 16:
        raise HTTPException(400, "Bild zu groß (max. 30 MB)")
    try:
        data = base64.b64decode(value, validate=False)
    except ValueError:
        raise HTTPException(400, "Bild ist kein gültiges Base64")
    return UploadFile(file=io.BytesIO(data), filename="api" + (_image_type(data) or ".png"))


def _parse_size(size) -> tuple[int | None, int | None]:
    if not size or size == "auto":
        return None, None
    m = re.fullmatch(r"\s*(\d{3,4})\s*[x×]\s*(\d{3,4})\s*", str(size))
    if not m:
        raise HTTPException(400, "Größe als BREITExHÖHE, z.B. 1024x1024")
    return int(m.group(1)), int(m.group(2))


def _job_out(request: Request, view: dict) -> dict:
    """Job-Ansicht mit vollstaendigen Bild-Adressen (abrufbar mit demselben Schluessel)."""
    base = str(request.base_url).rstrip("/")
    out = dict(view)
    if view.get("images"):
        out["images"] = [dict(i, url=f"{base}/api/image?filename={quote(i['filename'])}"
                                     f"&subfolder={quote(i['subfolder'])}") for i in view["images"]]
    return out


async def _wait(pids: list[str], timeout: float) -> dict[str, dict]:
    """Auf Auftraege warten (ComfyUI-Abgleich wie in der Oberflaeche)."""
    end = time.time() + timeout
    while True:
        with closing(db()) as con:
            metas = con.execute(f"SELECT * FROM jobs WHERE prompt_id IN ({','.join('?' * len(pids))})", pids).fetchall()
        done = {m["prompt_id"]: _final_view(m) for m in metas if m["done"]}
        views = done | (await _check_open([m for m in metas if not m["done"]]))
        if all(v["state"] not in ("queued", "running") for v in views.values()) or time.time() > end:
            return views
        await asyncio.sleep(1.5)


async def _api_generate(user: dict, entry: dict, prompt: str, *, enhance: bool, images: list[UploadFile],
                        width=None, height=None, seconds=None, motion=None, action=None, seed=None, count=1,
                        variables: str = "") -> dict:
    """Wie die Oberflaeche: optional ausformulieren (mit erstem Eingabebild), dann erzeugen."""
    text, warning = prompt.strip(), None
    if enhance and text and LLM_URL:
        data = None
        if images and entry["mode"] == "edit":
            data, _ = await _read_image(images[0])
            await images[0].seek(0)
        try:
            text, warning = await enhance_text(entry, text, data)
        except HTTPException as e:              # Sprachmodell weg: mit dem eigenen Text weiter
            warning = f"Nicht ausformuliert: {e.detail}"
    r = await generate(workflow=entry["file"], prompt=text, variables=variables, count=count, width=width,
                       height=height, seconds=seconds, motion=motion, action=action, seed=seed, source=[],
                       image=images, user=user)
    if warning:
        r["warning"] = (r.get("warning") + " · " if r.get("warning") else "") + warning
    r["prompt_used"] = text
    return r


@app.get("/api/v1/workflows")
async def api_workflows(user: dict = Depends(need_user)):
    """Workflows mit allem, was ein Aufruf braucht (Optionen, Groessen, Laengen)."""
    return await workflows(user)


@app.post("/api/v1/generate")
async def api_generate(request: Request, user: dict = Depends(need_user)):
    """JSON: workflow, prompt, enhance (Standard ja), image/images (Base64), width, height, seconds,
    motion, action, seed, count, variables, wait (Sekunden warten; 0 = sofort Auftrags-IDs zurueck)."""
    body = await _json(request)
    entry = find_workflow(str(body.get("workflow", "")))
    imgs = body.get("images") or ([body["image"]] if body.get("image") else [])
    if not isinstance(imgs, list) or len(imgs) > MAX_INPUTS:
        raise HTTPException(400, "images: Liste mit Base64-Bildern")
    num = lambda k: body.get(k) if isinstance(body.get(k), int) and not isinstance(body.get(k), bool) else None
    r = await _api_generate(user, entry, str(body.get("prompt") or ""), enhance=body.get("enhance", True) is not False,
                            images=[_b64_image(i) for i in imgs], width=num("width"), height=num("height"),
                            seconds=num("seconds"), motion=num("motion"), action=num("action"), seed=num("seed"),
                            count=num("count") or 1, variables=str(body.get("variables") or ""))
    wait = body.get("wait", 0)
    if isinstance(wait, (int, float)) and not isinstance(wait, bool) and wait > 0:
        views = await _wait([j["id"] for j in r["jobs"]], min(float(wait), API_WAIT_MAX))
        r["results"] = [_job_out(request, views[j["id"]]) for j in r["jobs"]]
    return r


@app.get("/api/v1/jobs/{pid}")
async def api_job(pid: str, request: Request, user: dict = Depends(need_user)):
    return _job_out(request, await job(pid, user))


# OpenAI-kompatibel (z.B. Open WebUI: Bilder -> OpenAI, Basis-URL http://<host>:8190/v1, Schluessel, Modell =
# Workflow). Nur Bild-Workflows; Ausformulieren immer an (abschaltbar mit "enhance": false im JSON).
def _oai_entries(user: dict, mode: str | None = None) -> list[dict]:
    return [e for e in load_index() if e.get("kind") == "image" and (mode is None or e["mode"] == mode)]


def _oai_entry(user: dict, model: str, mode: str) -> dict:
    model = (model or "").strip()
    for e in _oai_entries(user, mode):
        if model in (e["file"], e["title"]):
            return e
    if not model or model.startswith(("dall-e", "gpt-image")):      # Standardnamen der Clients
        return _oai_entries(user, mode)[0]
    raise HTTPException(404, f"Workflow unbekannt oder passt nicht ({'Bearbeiten' if mode == 'edit' else 'Erzeugen'})")


@app.get("/v1/models")
async def oai_models(user: dict = Depends(need_user)):
    return {"object": "list", "data": [{"id": e["file"], "object": "model", "created": 0, "owned_by": "imagegen",
                                        "name": e["title"], "mode": e["mode"], "description": e["summary"]}
                                       for e in _oai_entries(user)]}


async def _oai_result(request: Request, user: dict, entry: dict, prompt: str, n, size, fmt: str,
                      enhance: bool, images: list[UploadFile]) -> dict:
    n = n if isinstance(n, int) and 1 <= n <= 10 else 1
    w, h = _parse_size(size) if "width" in entry["fields"] else (None, None)
    r = await _api_generate(user, entry, prompt, enhance=enhance, images=images, width=w, height=h, count=n)
    views = await _wait([j["id"] for j in r["jobs"]], API_WAIT_MAX)
    data = []
    for j in r["jobs"]:
        v = views[j["id"]]
        if v["state"] != "done":
            raise HTTPException(502, v.get("error") or "Bild nicht rechtzeitig fertig")
        for i in _job_out(request, v)["images"]:
            if fmt == "url":
                data.append({"url": i["url"], "revised_prompt": j["prompt"]})
            else:
                with open(_safe_path("output", i["subfolder"], i["filename"]), "rb") as fh:
                    data.append({"b64_json": base64.b64encode(fh.read()).decode(), "revised_prompt": j["prompt"]})
    return {"created": int(time.time()), "data": data}


@app.post("/v1/images/generations")
async def oai_generations(request: Request, user: dict = Depends(need_user)):
    body = await _json(request)
    entry = _oai_entry(user, str(body.get("model") or ""), "generate")
    if not str(body.get("prompt") or "").strip():
        raise HTTPException(400, "prompt fehlt")
    return await _oai_result(request, user, entry, str(body["prompt"]), body.get("n"), body.get("size"),
                             str(body.get("response_format") or "b64_json"), body.get("enhance", True) is not False, [])


@app.post("/v1/images/edits")
async def oai_edits(request: Request, user: dict = Depends(need_user)):
    form = await request.form()
    files = [f for k in ("image", "image[]") for f in form.getlist(k) if isinstance(f, StarletteUpload) and f.filename]
    if not files:
        raise HTTPException(400, "image fehlt")
    entry = _oai_entry(user, str(form.get("model") or ""), "edit")
    n = form.get("n")
    return await _oai_result(request, user, entry, str(form.get("prompt") or ""), int(n) if str(n or "").isdigit() else 1,
                             form.get("size"), str(form.get("response_format") or "b64_json"),
                             str(form.get("enhance", "true")).lower() not in ("false", "0", "no"), files[:1])


@app.get("/")
async def index(request: Request):
    if user_count() == 0:
        return page("setup.html")
    if not current_user(request):
        return page("login.html")
    return page("index.html")


@app.get("/admin")
async def admin_page(request: Request):
    u = current_user(request)
    if not u:
        return page("login.html")
    if not u["admin"]:
        raise HTTPException(403, "nur fuer Admins")
    return page("admin.html")


@app.get("/manifest.webmanifest")
async def manifest(request: Request):
    name = i18n.tr("Bilder", i18n.request_lang(request.headers, LANG_HEADER))
    return JSONResponse({"name": name, "short_name": name, "start_url": "/", "display": "standalone",
                         "background_color": "#111214", "theme_color": "#111214",
                         "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}]},
                        media_type="application/manifest+json")


@app.get("/icon.svg")
async def icon():
    return FileResponse(os.path.join(STATIC, "icon.svg"), media_type="image/svg+xml")


@app.get("/i18n.js")
async def i18n_js():
    return Response(i18n.script("imagegen.lang", "X-ImageGen-Lang"), media_type="text/javascript; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@app.get("/{name}.{ext}")
async def static_file(name: str, ext: str):
    # gemeinsame Dateien der Seiten (common.css, ui.js); no-cache = immer aktuell nach Updates
    allowed = {"common.css": "text/css", "ui.js": "text/javascript"}
    fn = f"{name}.{ext}"
    if fn not in allowed:
        raise HTTPException(404, "Nicht gefunden")
    return FileResponse(os.path.join(STATIC, fn), media_type=allowed[fn], headers={"Cache-Control": "no-cache"})


if __name__ == "__main__":
    # proxy_headers aus: Client-IP/HTTPS wertet client_ip()/_is_https() selbst aus,
    # nur fuer vertraute Proxys (uvicorn wuerde mit "*" jedem Header glauben)
    uvicorn.run(app, host=os.environ.get("WEBAPP_HOST", "0.0.0.0"), port=int(os.environ.get("WEBAPP_PORT", "8190")),
                proxy_headers=False, log_level="info")
