# Technik und Hintergründe

🇬🇧 [English](technik.en.md) · 🇩🇪 Deutsch

Ergänzung zur [README](../README.md): wie die Teile gebaut sind, warum, und was gemessen wurde. Messwerte stammen von
einem Ryzen AI Max+ 395 (iGPU Radeon 8060S, `gfx1151`, 128 GB Unified Memory, davon bis 108 GiB für die GPU).

## Aufbau

- `comfyui/Dockerfile`: AMDs `rocm/pytorch` (ROCm 7.2, PyTorch 2.10) + ComfyUI in fester Version. `torch` wird gegen
  Ersatz durch pip festgenagelt: normale PyTorch-Wheels haben keine gfx1151-Kernel und rechnen unbemerkt auf der CPU.
  `--disable-mmap`, weil mmap großer Modelle unter ROCm auf gfx1151 sehr langsam ist. Die eigenen Erweiterungen aus
  `comfyui/custom_nodes/` liegen im Image und werden beim Start in den `custom_nodes`-Ordner kopiert.
- `webapp/`: FastAPI + eine HTML-Seite je Ansicht, SQLite für Benutzer, Aufträge und Bild-Zuordnung. Beim Start baut
  die App ihre Workflows aus `build_workflows.py` (im Image) plus den Erweiterungen in `/extras` (`webapp/start.sh`)
  und legt die Token-Vorlagen in den Eingabeordner von ComfyUI.
- **Mit dem OllamaProxy** meldet sich die Web-App als Modul (`/proxy-module`): Der Proxy zeigt, was ComfyUI an RAM
  und GPU hält, und gibt es nach Leerlauf (`WEBAPP_IDLE_FREE_MINUTES`, Standard 2) frei, damit Sprachmodelle wieder
  Platz haben.

## Daten (`IMAGEGEN_DATA`)

```
models/        checkpoints/  diffusion_models/  loras/  vae/  text_encoders/ ...   (siehe MODELLE.md)
output/        fertige Bilder und Videos
input/         Eingabebilder (Uploads, Importe, Token-Vorlagen)
custom_nodes/  ComfyUI-Erweiterungen
extras/        eigene Workflow-Erweiterungen (*.py)
comfy-user/    ComfyUI-Einstellungen; default/workflows/ = Workflow-Menü
webapp-data/   webapp.db (Benutzer, Aufträge) – gehört ins Backup
cache/         MIOpen-/Triton-Kernel-Caches
```

## Workflows (`workflows/`)

`build_workflows.py` erzeugt alle Workflows aus einer Definition, in zwei Formaten: `<Titel>.json` (UI-Format, für das
Workflow-Menü von ComfyUI: nach `comfy-user/default/workflows/` kopieren) und `api/<name>.json` (API-Format für Open
WebUI / Skripte) plus `api/openwebui_mapping.json` (welcher Knoten für Prompt, Modell, Größe, Seed …) und
`api/index.json` (Liste für die Web-App). Jeder Workflow hat ein Textfeld (Knoten 10); Qualitäts- und Stilzusätze hängt
der Workflow selbst an (`StringConcatenate`), damit sie auch dann bleiben, wenn ein Client nur das Motiv einsetzt.

```bash
python3 workflows/build_workflows.py http://127.0.0.1:8188 workflows      # UI + API, prüft Knoten gegen ComfyUI
python3 workflows/build_workflows.py --api-only /tmp/wf                     # nur API, ohne ComfyUI
python3 workflows/run_workflow.py workflows/api/<name>.json "Prompt" [seed] [url] [--image datei.png]
```

**Erweiterungen:** jede `*.py` in `extras/` wird geladen, ihre Funktion `apply(b)` darf Workflows, Optionen und LoRAs
ergänzen ([workflows/extras/README.md](../workflows/extras/README.md)). So bleiben eigene oder heikle Zusätze
(z. B. LoRAs für Inhalte ab 18) außerhalb des Repositorys.

| Workflow | Modell | Zeit | Prompt |
|---|---|---|---|
| Anime - Anima | Anima Turbo v1.1 | ~13 s | Sätze oder Tags, `safe`/`nsfw`/`explicit` |
| Realistisch - Qwen 2.1 | Qwen-Image 2.1 Turbo | ~31 s (1024²) | Sätze, auch lang/mehrere Personen |
| Gezeichnet - D&D | Qwen-Image 2.1 Turbo + D&D-Stilsatz | ~31 s (1344×768) | Sätze |
| Bearbeiten - Qwen 2.1 | Qwen-Image 2.1 Turbo | ~70 s | Anweisung + Bild(er) |
| Charakter - Qwen 2.1 | Qwen-Image 2.1 Turbo | ~65 s | Referenzbild der Figur + neue Szene |
| D&D - Token aus Bild | Qwen-Image 2.1 Turbo + Rahmen | ~65 s | Bild der Figur, Text optional |
| Test - Flux 3 Klein | FLUX 3 Image Klein (destilliert, 16 Schritte) | ~30 s (1024²) | Sätze, Stil im Text |
| Video - Wan 2.2 | Wan 2.2 T2V 14B fp8 + Lightx2v (4 Schritte) | ~6 Min. (5 s) | Szene + Bewegung |
| Video aus Bild - Wan 2.2 | Wan 2.2 I2V 14B fp8 + Lightx2v | ~6 Min. (5 s) | Startbild + Bewegung |
| Video aus Bild (Dynamisch) - Wan 2.2 | Wan 2.2 I2V 14B fp8, 16 Schritte, CFG 3,5 | ~21 Min. (3 s) | Startbild + Körper-/Kamerabewegung |

Die Modelle filtern Inhalte nicht. Die Web-App blendet Hinweise und Optionen für Inhalte ab 18 nur für Benutzer mit
NSFW-Freigabe ein.

**Qwen-Image 2.1** (7B, Generieren und Bearbeiten in einem Modell, bis 10 Referenzbilder als `<image1>` …). Kette nach
der offiziellen ComfyUI-Vorlage „Qwen Image 2.1 Turbo Image Edit“ ohne Prompt-Enhancer: `TextEncodeQwenImage21`
(Bilder + VAE) → `QwenImage21Cache` → CFG 1, euler, 8 Schritte per `ManualSigmas`. Der „Load Image“-Knoten von
Bearbeiten/Charakter zeigt den Platzhalter `bild_hier_hochladen.png` (ComfyUI verlangt dort eine Datei).

**Bearbeiten war langsam (~180 s), jetzt ~70 s.** Sampling braucht ~35 s, der Rest steckt im Vision-Teil des
Qwen3-VL-Encoders, der absichtlich in fp32 rechnet (fp16/bf16 ergeben NaN). Zwei Bremsen:
- hipBLASLt nimmt auf gfx1151 für fp32-GEMM einen Notfall-Kernel (~1 TFLOPS). Abhilfe: `TORCH_BLAS_PREFER_HIPBLASLT=0`
  (rocBLAS; fp32 2,7×, bf16/fp16 ~30 % schneller).
- `Conv3d` im Patch-Embedding: MIOpen sucht bei jeder neuen Bildgröße ~125 s nach dem besten Kernel. Die Erweiterung
  `conv3d_patch_embed` rechnet es als gleichwertige Matrixmultiplikation (Kernel = Stride).

**Speicher auf Unified-Memory-Systemen** (`comfyui/custom_nodes/proxy_memory`): Mit `--disable-mmap` und dem
RAM-Pressure-Cache (ComfyUI ≥ 0.42) liegen entladene Modelle im Prozess-RAM (~22 GB), während `/system_stats` nur den
GPU-Teil meldet und `/free` den RAM nicht ans System zurückgibt. Die Erweiterung
- ergänzt `GET /proxy/memory` (RSS + GPU + geladene Modelle) und `POST /proxy/free` (wartet auf das Entladen und gibt
  den RAM per `gc.collect()`, `_host_emptyCache()` und `malloc_trim(0)` zurück),
- deckelt `get_free_memory` auf `min(GTT frei, MemAvailable)` und leert bei knappem RAM den Cache, damit Wan 2.2 (zwei
  14B-Experten) auch neben einem großen Sprachmodell nicht in einen HIP-OOM läuft,
- setzt `mallopt(M_MMAP_THRESHOLD, 128 KB)`: Sonst landen Tensoren in glibc-Arenen, deren Seiten ROCm/`amdkfd` nach
  `malloc_trim` (nur `MADV_DONTNEED`) per `userptr`-Lock sofort wieder in den RAM holt; mit eigenem `mmap()` je Tensor
  gibt `free()` sie per `munmap()` echt frei.

**Videos (Wan 2.2)**: zwei Experten (hohe Rauschstufen = grobe Bewegung, niedrige = Details), je mit
Lightx2v-4-Schritt-LoRA, CFG 1, euler/simple, Shift 5, 16 fps, `SaveVideo` (MP4/H.264). Länge = Sekunden × 16 + 1
(Wan ist auf ~5 s trainiert). Bei „Video aus Bild“ wählt „Wie Bild“ je Startbild Quer/Hoch/Quadrat (Maße aus dem
Dateikopf), sonst würde das Bild beschnitten. Gemessen (5 s, 832×480): ~6 Min.; je Sampling-Schritt ~78 s reine
Rechnung, VAE-Decode ~37 s. Kein Nachladen zwischen den Experten. Die Web-App skaliert ihre Schätzung linear mit Länge
und Pixelzahl.

**Bewegung** (5 Stufen, `WAN_MOTION` / `WAN_MOTION_HQ`): Wan 2.2 hat keinen Regler wie `motion_bucket_id` (SVD). Je
Stufe ein Zusatz am Prompt + Shift 4/4,5/5/6,5/8 (schnell) bzw. 5/6,5/8/9,5/11 (dynamisch). Mit Lightx2v ist Bild-zu-
Video stark an das erste Bild gebunden und neigt zu Mund-/Kieferzappeln; dafür gibt es **„Video aus Bild
(Dynamisch)“**: natives Dual-Modell ohne 4-Schritt-LoRA, 16 Schritte mit CFG 3,5 und sgm_uniform, Mundbewegungen im
Negativ-Prompt unterdrückt. CFG bedeutet doppelte Rechnung je Schritt (gemessen 1229–1297 s für 3 s). Die Stufen
setzen neben Shift auch den Expertenwechsel passend zu sigma 0,9 (Schritt 6/7/8/9/9).

**Ausformulieren** (Knopf am Prompt): Ein Sprachmodell (am besten über den OllamaProxy, damit Kontext und
`keep_alive` fest sind) macht aus einer kurzen Idee einen ausführlichen englischen Prompt (~3 s); Platzhalter `[..]`
bleiben erhalten. Regeln je Art (Bild, Bearbeiten, Video, Video aus Bild mit Startbild) plus `ENHANCE_HINT` je
Workflow. Beispiel mit gleichem Seed: „A female tiefling bard in a tavern“ → ohne: eine normale Frau mit Stab;
ausformuliert: eine Tieflingin mit Hörnern, Schwanz und Laute. Ohne D&D-Regel erfand das Modell Kerzenlicht → dunkle,
braune Bilder; daher verlangt die Regel helles, buntes Licht. Bei Videos: keine Verneinungen, Bewegung konkret.

**D&D-Arten** (eigener Tab, Unterauswahl „Art“ über `VARIANTS` → `group`/`variant`/`tab` in `index.json`):
- Illustration: Qwen 2.1 + Stilsatz `DND_STYLE` (bunte, gemalte Illustration wie die 5e-Bücher). „oil painting“ +
  „warm lighting“ gab gold-braune, fast einfarbige Bilder → bunte Palette und gemischtes Licht ausdrücklich verlangen.
  Deutsch versteht das Modell nur halb → Englisch (bzw. Ausformulieren).
- Ort: `DND_LOCATION_STYLE` (weite Totale ohne Hauptfigur).
- Token: Brustbild, dann Ring darüber (`token_rahmen.png`, `ImageCompositeMasked` mit invertierter Maske) und rund
  ausgeschnitten (`token_maske.png`, `JoinImageWithAlpha`) → PNG mit Alpha, fest 1024×1024. „Token aus Bild“ malt die
  Figur aus einem eigenen Bild neu (gleiche Identität, D&D-Stil).
- Battlemap und Ortskarte: Anima Aesthetic v1.1 (30 Schritte, CFG 4,5, er_sde, Negativ-Prompt) + Mapcraft-LoRA, Stil
  wie Dungeondraft/Forgotten Adventures bzw. Inkarnate: gedeckte Farben, Tusche-Konturen, flaches Licht ohne
  Lichteffekte und ohne Raster (beides macht die VTT-Software). Qwen 2.1 malt Orte schön, aber schräg/isometrisch;
  Anima Turbo war zu bunt.

**Charakter**: Referenzbild als `<image1>`, Präfix `CHARACTER_PREFIX` (bewusst allgemein – ein festes „horns“ gab
Figuren ohne Hörner welche) verlangt gleiche Identität, aber neue Umgebung. Gegenstände aus der Referenz wandern mit
→ „her hands are empty“.

**Zugeklappte Encoder**: In der UI bleibt das Textfeld eines Encoders sichtbar, auch wenn sein Text verbunden ist;
wer dort tippt, trennt die Verbindung (Stilsatz weg). `build_workflows.py` klappt solche Knoten deshalb zu.

Reine Diffusionsmodelle (Anima, Qwen, Flux, Wan) laden mit „Load Diffusion Model“ (`UNETLoader`,
`diffusion_models/`), Text-Encoder und VAE separat.

## Web-App

**Oberfläche:** Tabs Erzeugen / Bild als Eingabe / D&D, darunter Workflow, Prompt, Größe. Ergebnis speichern, direkt
weiterbearbeiten, mit neuem Seed wiederholen oder löschen; darunter „Meine Bilder“ (lädt beim Scrollen nach; Großansicht
mit Workflow, Datum, Prompt und „Prompt übernehmen“).

**Mehrere Bilder bearbeiten:** mehrere hochladen und/oder in „Meine Bilder“ auswählen → „Bearbeiten (n)“. Jedes
Eingabebild wird einzeln mit demselben Prompt bearbeitet. Erst werden alle Bilder geprüft (Rechte, Inhalt), dann
hochgeladen – bei einem Fehler bleibt nichts halb liegen. Höchstens 100 Eingabebilder je Auftrag.

**Variablen & Serien:**
- Im Prompt `[red hair|blue hair]` → pro Bild eine Möglichkeit. Benannte Variablen, eine pro Zeile:
  `haar = red hair | blue hair`, im Prompt `[haar]`; im selben Bild überall derselbe Wert. Werte dürfen selbst
  Variablen enthalten (beliebig tief, Kreisverweise ergeben eine Fehlermeldung).
- Anzahl ohne feste Grenze, je eigener Seed. Gleichmäßig gewürfelt: jede Möglichkeit kommt dran, bevor sich eine
  wiederholt. Gewürfelt wird in der App, ComfyUI bekommt nur den fertigen Prompt.
- **Abbrechen:** wartende Aufträge verlassen die Warteschlange, der laufende wird gezielt unterbrochen; fertige Bilder
  bleiben.

**Benutzer:** Einrichtungsassistent legt den Admin an. Der Admin legt Benutzer an, setzt Passwörter, vergibt Admin-
Rechte und die **NSFW-Freigabe** (ohne sie fehlen Optionen mit `"nsfw": true`, der Server lehnt andere Stufen ab).
Passwörter PBKDF2-SHA256 (600k Runden), Sitzung 30 Tage (Cookie httponly, SameSite=Lax, Secure hinter HTTPS).
Admin-Passwort vergessen: `webapp.db` wegschieben → Assistent erscheint wieder (Bild-Zuordnung geht dabei verloren).

**Bilder und Aufträge:** Jeder sieht und löscht nur seine eigenen Bilder. ZIP-Download (eigene bzw. als Admin alle).
Dateinamen bekommen einen Zeitstempel, Bild-Adressen eine Version, damit nach dem Löschen kein altes Bild aus dem
Browser-Cache erscheint. Auftragsstatus: offen → fertig / Fehler / verloren (ComfyUI neu gestartet) / abgebrochen. Der
Server gleicht offene Aufträge alle 20 s selbst ab.

**API für andere Programme:** Schlüssel je Benutzer über „API“ in der App (gespeichert wird nur der SHA-256-Hash).
Header `Authorization: Bearer ig_...`; Rechte wie das Konto, aber keine Verwaltung, keine Schlüssel, kein Passwort.
Es gelten dieselben Workflows samt Stilsätzen, Optionen und Ausformulieren (`enhance`, Standard an; ist das
Sprachmodell weg, geht der eigene Text mit Hinweis durch).
- `GET /api/v1/workflows` – Liste wie in der App (Optionen, Größen, Längen)
- `POST /api/v1/generate` (JSON): `workflow`, `prompt`, `enhance`, `image`/`images` (Base64 oder data:-URL), `width`,
  `height`, `seconds`, `motion`, `action`, `seed`, `count`, `variables`, `wait` (Sekunden; dann mit `results` samt
  Bild-`url`, höchstens `WEBAPP_API_WAIT_MAX` = 1800)
- `GET /api/v1/jobs/<id>`; Bilder über die `url` mit demselben Schlüssel
- OpenAI-kompatibel: `GET /v1/models` (nur Bild-Workflows), `POST /v1/images/generations` (`model` = Workflow-Datei
  oder -Titel, `dall-e*`/`gpt-image*` = erster Workflow, `n` bis 10, `size`, `response_format` b64_json/url) und
  `POST /v1/images/edits` (multipart `image`). Antwort mit `revised_prompt`. Open WebUI: Bilder → OpenAI,
  Basis-URL `http://<host>:8190/v1`.

**Modul für den OllamaProxy:** `GET /proxy-module`, `GET /proxy-module/memory`, `POST /proxy-module/free`, optional
geschützt mit `WEBAPP_MODULE_TOKEN`.

**Sicherheit:** für das eigene Netz gedacht; nach außen nur hinter einem Reverse-Proxy mit HTTPS (der
`X-Forwarded-For` setzt) oder per VPN. ComfyUI (:8188) bleibt im LAN, die App spricht es intern an.
- Login-Sperre: 10 Fehlversuche pro IP und 20 pro Name in 10 Min., 1 s Wartezeit je Fehlversuch, unbekannte Namen
  kosten gleich viel Zeit.
- `X-Forwarded-*` wird nur von vertrauten Proxys geglaubt (`WEBAPP_TRUSTED_PROXIES`) und von rechts gelesen.
- Ändernde Anfragen nur von der eigenen Seite (Origin-Prüfung), Schutz gegen Einbetten, `nosniff`.
- Uploads: höchstens 30 MB und nur echte PNG/JPG/WebP (Inhalt geprüft); hochgeladene Eingabebilder werden nach dem
  Auftrag entfernt.

**Tests:** `webapp/run_tests.sh` (braucht nur Docker) baut das Image, erzeugt die Workflows mit der Test-Erweiterung
`webapp/tests/extras/` und lässt `webapp/tests/test_app.py` gegen ein nachgebautes ComfyUI (`tests/fake_comfy.py`)
laufen. Deckt Einrichtung, Login-Schutz, Rechte, Erzeugen, Uploads, Aufträge, Variablen, Serien, Abbrechen, ZIP,
Videos, Ausformulieren, Optionen, D&D-Arten, API und Modul-Schnittstelle ab (69 Tests).

## Betrieb

```bash
docker compose --env-file ~/imagegen/imagegen.env up -d --build   # starten/aktualisieren
docker compose --env-file ~/imagegen/imagegen.env logs -f comfyui
docker compose --env-file ~/imagegen/imagegen.env down            # stoppen
```

ComfyUI aktualisieren: `COMFYUI_VERSION` in `imagegen.env` anheben und neu bauen. Eigene ComfyUI-Erweiterungen in
`custom_nodes/` sind ausführbarer Code – nur vertrauenswürdige, und keine Pakete, die `torch` mitziehen.

## Gemessen

- PyTorch im Container: `gfx1151`, 108 GiB für die GPU, Matmul fp16/bf16 ~26 TFLOPS
- SDXL Base 1.0, 1024×1024, 25 Schritte: 21,6 s pro Bild
- parallel zu einem 26B-Sprachmodell mit 128k Kontext problemlos
