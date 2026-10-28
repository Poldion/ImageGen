# Erweiterungen für die Workflows

Jede Datei `*.py` in diesem Ordner (oder im Ordner aus `--extras` / `IMAGEGEN_EXTRAS`, in Docker
`${IMAGEGEN_DATA}/extras`) wird von `build_workflows.py` geladen. Ihre Funktion `apply(b)` bekommt das
Modul `build_workflows` und kann alles daran ändern, bevor die Dateien geschrieben werden:

- `b.WORKFLOWS` – alle Workflows (Titel → Knoten, Zuordnung, Videoeinstellungen)
- `b.WEBAPP`, `b.ENHANCE_HINT`, `b.SUMMARY_NSFW` – Texte und Dauer für die Web-App
- `b.add_model_loras(nodes, {Zielknoten: (neue ID, LoRA-Datei)})` – LoRA-Platz in den Modell-Pfad einfügen

Dateien hier außer dieser README stehen in `.gitignore`. So bleiben eigene oder private Erweiterungen
(z. B. LoRAs für Inhalte ab 18) aus dem Repository heraus.

## Beispiel: Auswahl „Stil“ mit einem LoRA für „Video aus Bild“

```python
# extras/wan_stil.py
STIL = {"default": 1, "levels": [
    {"label": "Ohne", "set": []},
    {"label": "Aquarell", "prefix": "watercolor style, ",
     "set": [["60", "lora_name", "mein_aquarell_high.safetensors"], ["60", "strength_model", 0.8],
             ["61", "lora_name", "mein_aquarell_low.safetensors"], ["61", "strength_model", 0.8]]},
]}


def apply(b):
    wf = b.WORKFLOWS["Video aus Bild - Wan 2.2"]
    b.add_model_loras(wf["nodes"], {"8": ("60", "mein_aquarell_high.safetensors"),
                                    "9": ("61", "mein_aquarell_low.safetensors")})
    wf["video"]["action"] = STIL
```

Die Web-App zeigt Optionen unter den Schlüsseln `motion` („Bewegung“) und `action` („Aktion“) als
Auswahlknöpfe. Mit `"nsfw": True` in der Option sehen sie nur Benutzer, denen der Admin die NSFW-Freigabe
gegeben hat; der Server lehnt andere Stufen für alle übrigen ab.
