#!/usr/bin/env python3
"""
Erzeugt die Bild-Workflows fuer ComfyUI in zwei Formaten:
  api/<name>.json   API-Format  -> fuer Open WebUI / Skripte (/prompt)
  <Titel>.json      UI-Format   -> fuer das Workflow-Menue in ComfyUI

Jeder Workflow hat EIN Textfeld "Prompt - dein Motiv" (Knoten 10). Qualitaets-Tags
bzw. Stil-Satz haengt der Workflow selbst an (StringConcatenate), damit sie auch dann
erhalten bleiben, wenn Open WebUI nur das Motiv einsetzt.

Die Eingabereihenfolge und Ausgabetypen der Knoten kommen aus /object_info der
laufenden ComfyUI-Instanz, damit die UI-Dateien zur installierten Version passen.

Nutzung:  python3 build_workflows.py [http://127.0.0.1:8188] [zielordner] [--extras ORDNER]
          python3 build_workflows.py --api-only [--extras ORDNER] ZIELORDNER   (ohne ComfyUI, nur api/)

Erweiterungen: jede Datei *.py im Ordner --extras (Standard: $IMAGEGEN_EXTRAS oder workflows/extras/)
wird geladen und ihre Funktion apply(b) mit diesem Modul aufgerufen. So lassen sich eigene Workflows,
Optionen ("motion", "action") oder LoRAs ergaenzen, ohne diese Datei zu aendern (Beispiel:
extras/README.md). Die Web-App baut ihre Workflows beim Start genauso (Ordner /extras).
"""
import importlib.util
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
_args = sys.argv[1:] if __name__ == "__main__" else []
API_ONLY = "--api-only" in _args
EXTRAS = os.environ.get("IMAGEGEN_EXTRAS") or os.path.join(HERE, "extras")
if "--extras" in _args:
    EXTRAS = _args[_args.index("--extras") + 1]
    del _args[_args.index("--extras"):_args.index("--extras") + 2]
_args = [a for a in _args if a != "--api-only"]
if API_ONLY:
    COMFY, OUT = "", (_args[0] if _args else HERE)
else:
    COMFY = (_args[0] if _args else "http://127.0.0.1:8188").rstrip("/")
    OUT = _args[1] if len(_args) > 1 else HERE

# ---------------------------------------------------------------------------
# Die Workflows (API-Format). Knoten-IDs sind fest, damit die Zuordnung in
# Open WebUI stabil bleibt: 10 = Prompt, 1 = Modell, 20 = Bildgroesse, 30 = Sampler.
# ---------------------------------------------------------------------------

NOOB_QUALITY = "masterpiece, best quality, newest, absurdres, highres"
NOOB_NEGATIVE = ("worst quality, old, early, low quality, lowres, signature, username, logo, "
                 "bad hands, mutated hands, mammal, anthro, furry, ambiguous form, feral, semi-anthro")
# Qwen-Image 2.1 Turbo: 8 Schritte, Sigmas aus der offiziellen ComfyUI-Vorlage
QWEN21_TURBO_SIGMAS = "1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0"
# "oil painting" + "warm lighting" ergab gold-braune, fast einfarbige Bilder -> bunte Palette
# und gemischtes Licht ausdruecklich verlangen (getestet 2026-10-21)
DND_STYLE = ("Official Dungeons & Dragons 5th edition rulebook art: a painterly digital fantasy illustration "
             "with crisp detailed rendering, visible brushwork, a vibrant colorful palette of contrasting jewel "
             "tones, mixed warm and cool light sources, expressive faces and rich costume details. "
             "Painted artwork, not a photograph.")
# D&D-Unterarten: gleicher Malstil, anderer Bildaufbau
DND_LOCATION_STYLE = ("Wide establishing shot of the location, environment art without a central character, "
                      "clear sense of scale and depth. " + DND_STYLE)
DND_TOKEN_STYLE = ("Head-and-shoulders character portrait for a round tabletop token: the face centered in the "
                   "middle of the square image, filling about half of it, facing the viewer, simple plain "
                   "background. " + DND_STYLE)
# Token aus Bild: Figur aus <image1> als Brustbild neu malen (Rahmen/Ausschnitt wie beim Token)
TOKEN_FROM_IMAGE_PREFIX = ("Create a head-and-shoulders portrait of the same character shown in <image1> for a round "
                           "tabletop token. Keep the character's identity exactly the same: face, hair, skin, any "
                           "distinctive features it has (such as ears, horns, scars or tattoos), outfit and colors. "
                           "The face is centered in the middle of the square image, filling about half of it, facing "
                           "the viewer, on a simple plain background; do not keep the background of <image1>.")
# Karten (Battlemap, Ortskarte): Anima Aesthetic + Mapcraft. Vergleich 2026-10-26 (Beispiele des Nutzers im Stil
# Dungeondraft/Inkarnate): Qwen 2.1 malt schoen, aber schraeg/isometrisch -> fuer Karten unbrauchbar; NoobAI +
# Mapcraft unruhig; Anima Turbo zu bunt/anime; Anima Aesthetic (30 Schritte, CFG 4,5 -> Negativ-Prompt wirkt)
# trifft gedeckte Farben, Tusche-Konturen und echte Draufsicht. Licht und Raster macht die VTT-Software.
MAP_PREFIX = "masterpiece, best quality, mapcraft, battle map, top-down view, from above, no humans"
BATTLEMAP_STYLE = ("hand-drawn digital tabletop battle map like Dungeondraft with Forgotten Adventures assets, "
                   "orthographic top-down view, muted desaturated earthy colors, dark ink outlines around every "
                   "object, painted textures, flat even lighting without light effects or glow, no grid")
TOWN_MAP_STYLE = ("hand-drawn top-down fantasy settlement map like Inkarnate and Dungeondraft town maps, seen from "
                  "directly above, every building drawn as its roof with ridge lines and colored roof tiles, thick "
                  "black ink outlines around all buildings and trees, soft watercolor-textured muted colors, light "
                  "green grass washes, round dark green tree canopies, light grey cobblestone roads, flat even "
                  "lighting, no grid")
MAP_NEGATIVE = ("worst quality, low quality, blurry, anime, vibrant saturated colors, glossy, 3d render, photo, "
                "perspective, isometric, side view, people, characters, text, labels, watermark, grid, light glow, "
                "lens flare")
ANIMA_MAP_MAPPING = {"prompt": ("10", "value"), "model": ("1", "unet_name"),
                     "width": ("20", "width"), "height": ("20", "height"), "seed": ("30", "seed")}


def anima_map(style: str, example: str, width: int, height: int, prefix: str) -> dict:
    """Karte in Draufsicht: Anima Aesthetic v1.1 + Mapcraft-LoRA (Trigger + Stil vor dem Prompt)."""
    return {
        "1": ("UNETLoader", {"unet_name": "anima-aesthetic-v1.1.safetensors", "weight_dtype": "default"}),
        "2": ("CLIPLoader", {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion",
                             "device": "default"}),
        "3": ("VAELoader", {"vae_name": "qwen_image_vae.safetensors"}),
        "5": ("LoraLoaderModelOnly", {"model": ["1", 0], "lora_name": "mapcraft_anima_v1.safetensors",
                                      "strength_model": 1.0}),
        "10": ("PrimitiveStringMultiline", {"value": example}),
        "11": ("StringConcatenate", {"string_a": f"{MAP_PREFIX}, {style}", "string_b": ["10", 0], "delimiter": ". "}),
        "12": ("CLIPTextEncode", {"text": ["11", 0], "clip": ["2", 0]}),
        "13": ("CLIPTextEncode", {"text": MAP_NEGATIVE, "clip": ["2", 0]}),
        "20": ("EmptyLatentImage", {"width": width, "height": height, "batch_size": 1}),
        "30": ("KSampler", {"model": ["5", 0], "seed": 0, "steps": 30, "cfg": 4.5,
                            "sampler_name": "er_sde", "scheduler": "simple",
                            "positive": ["12", 0], "negative": ["13", 0],
                            "latent_image": ["20", 0], "denoise": 1.0}),
        "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
        "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": prefix}),
    }


# Unterarten in der Web-App: Workflow -> (Gruppe, Art); die Gruppe erscheint einmal im Dropdown
DND_GROUP = "Gezeichnet - D&D"
VARIANTS = {"gezeichnet_dnd": (DND_GROUP, "Illustration"), "dnd_ort": (DND_GROUP, "Ort"),
            "dnd_ortskarte": (DND_GROUP, "Ortskarte"), "dnd_battlemap": (DND_GROUP, "Battlemap"),
            "dnd_token": (DND_GROUP, "Token"), "dnd_token_bild": (DND_GROUP, "Token aus Bild")}
# Platzhalter im "Load Image"-Knoten (ComfyUI verlangt dort eine Datei): wird beim Benutzen
# durch das eigene Bild ersetzt. Liegt in ~/imagegen/input/, Quelle: workflows/platzhalter.png
PLACEHOLDER_IMAGE = "bild_hier_hochladen.png"
# Allgemein gehalten (Mensch, Tiefling, Tier, Foto oder Gemaelde): keine Merkmale nennen,
# die nicht jede Figur hat - "horns" erzeugte sonst Hoerner bei Figuren ohne
CHARACTER_PREFIX = ("Create a new image of the same character shown in <image1>. Keep the character's identity "
                    "exactly the same: face, hair, skin, any distinctive features it has (such as ears, horns, "
                    "scars or tattoos), body type, outfit, and the same art style or medium as <image1> "
                    "(photo, painting, anime ...) - except for anything the description below explicitly asks "
                    "to change. Do not keep the background of <image1>: create a completely new, fully "
                    "detailed environment, pose and camera angle as described. New scene:")
PHOTO_STYLE ="Photorealistic photograph, natural skin texture, realistic lighting, sharp focus, high detail."
QWEN21_T2I_MAPPING = {"prompt": ("10", "value"), "model": ("1", "unet_name"),
                      "width": ("20", "width"), "height": ("20", "height"), "seed": ("17", "noise_seed")}


def qwen21_t2i(example: str, style: str, width: int, height: int, prefix: str) -> dict:
    """Text-zu-Bild mit Qwen-Image 2.1 Turbo (Kette wie die offizielle Vorlage, ohne Bild)."""
    return {
        "1": ("UNETLoader", {"unet_name": "qwen_image_2.1_turbo_bf16.safetensors", "weight_dtype": "default"}),
        "2": ("CLIPLoader", {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image",
                             "device": "default"}),
        "3": ("VAELoader", {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}),
        "4": ("QwenImage21Cache", {"model": ["1", 0], "device": "auto", "dtype": "default"}),
        "10": ("PrimitiveStringMultiline", {"value": example}),
        "11": ("StringConcatenate", {"string_a": ["10", 0], "string_b": style, "delimiter": " "}),
        "12": ("TextEncodeQwenImage21", {"clip": ["2", 0], "prompt": ["11", 0], "negative_prompt": "",
                                         "resolution": 1024, "vae": ["3", 0]}),
        "14": ("CFGGuider", {"model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1], "cfg": 1.0}),
        "15": ("KSamplerSelect", {"sampler_name": "euler"}),
        "16": ("ManualSigmas", {"sigmas": QWEN21_TURBO_SIGMAS}),
        "17": ("RandomNoise", {"noise_seed": 0}),
        "20": ("EmptyLatentImage", {"width": width, "height": height, "batch_size": 1}),
        "30": ("SamplerCustomAdvanced", {"noise": ["17", 0], "guider": ["14", 0], "sampler": ["15", 0],
                                         "sigmas": ["16", 0], "latent_image": ["20", 0]}),
        "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
        "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": prefix}),
    }


# Bewegungsstufen fuer die Videos (Web-App: "Bewegung"). Wan 2.2 hat keinen Bewegungsregler wie
# motion_bucket_id bei SVD. Gemessen 2026-10-23 (gleicher Seed, 3 s, mittlere Aenderung Bild zu Bild):
# Text-zu-Video Prompt-Zusatz "Ruhig" 3,3 / ohne 8,1 / "Sehr viel" 9,3; Shift 3 -> 7,6, Shift 8 -> 9,0.
# LoRA high 0,6 aenderte vor allem den Bildaufbau (weite Totale), nicht die Bewegung -> nicht genutzt.
# Bild-zu-Video: die im Prompt beschriebene Handlung bestimmt die Bewegung fast allein; die Stufen
# aendern dort v.a. Tempo/Dynamik. Je Stufe: Zusatz am Prompt + "set" = [Knoten, Eingang, Wert].
def _shift(v):
    return [["8", "shift", v], ["9", "shift", v]]


WAN_MOTION = {"default": 3, "levels": [
    {"label": "Ruhig", "hint": "Kaum Bewegung, Kamera steht",
     "suffix": " Very subtle, minimal movement, almost still, locked-off static camera.", "set": _shift(4.0)},
    {"label": "Wenig", "hint": "Sanfte, langsame Bewegung, ruhige Kamera",
     "suffix": " Gentle, slow, calm movement, steady camera.", "set": _shift(4.5)},
    {"label": "Normal", "hint": "Wie im Prompt beschrieben", "suffix": "", "set": _shift(5.0)},
    {"label": "Viel", "hint": "Lebhafte Bewegung, bewegte Kamera",
     "suffix": " Lively, energetic movement, dynamic camera motion.", "set": _shift(6.5)},
    {"label": "Sehr viel", "hint": "Schnelle, kraeftige Action, kann unruhiger werden",
     "suffix": " Fast, intense, highly dynamic motion, energetic action, quick camera movement.", "set": _shift(8.0)},
]}

def _shift_hq(v, steps=16, boundary=0.9):
    """Shift + passender Expertenwechsel. Wan 2.2 I2V ist auf den Wechsel High->Low-Noise bei
    sigma 0,9 trainiert. Mit fester Grenze (Schritt 8) bekaeme der Detail-Experte bei Shift 9,5/11
    noch zu verrauschte Latents (sigma 0,905/0,917), bei Shift 5 macht der Grob-Experte zu viel."""
    split = next(i for i in range(steps + 1) if v * (1 - i / steps) / (1 + (v - 1) * (1 - i / steps)) < boundary)
    return _shift(v) + [["30", "end_at_step", split], ["31", "start_at_step", split]]


WAN_MOTION_HQ = {"default": 3, "levels": [
    {"label": "Ruhig", "hint": "Kaum Bewegung, Kamera steht",
     "suffix": " Very subtle, minimal movement, almost still, locked-off static camera.", "set": _shift_hq(5.0)},
    {"label": "Wenig", "hint": "Sanfte, langsame Bewegung, ruhige Kamera",
     "suffix": " Gentle, slow, calm movement, steady camera.", "set": _shift_hq(6.5)},
    {"label": "Normal", "hint": "Natürliche Bewegung wie im Prompt beschrieben", "suffix": "", "set": _shift_hq(8.0)},
    {"label": "Viel", "hint": "Lebhafte Bewegung, bewegte Kamera",
     "suffix": " Lively, energetic movement, dynamic camera motion.", "set": _shift_hq(9.5)},
    {"label": "Sehr viel", "hint": "Schnelle, kräftige Action",
     "suffix": " Fast, intense, highly dynamic motion, energetic action, quick camera movement.", "set": _shift_hq(11.0)},
]}

WORKFLOWS = {
    # ---- Anime mit freiem Text: Anima Turbo v1.1 (Tags UND natuerliche Sprache) ----
    "Anime - Anima": {
        "file": "anime_anima",
        "note": (
            "ANIME, FREI BESCHRIEBEN (Anima Turbo v1.1)\n\n"
            "Im Feld 'Prompt - dein Motiv' das Bild in normalen Saetzen beschreiben\n"
            "(Englisch am besten) - Danbooru-Tags gehen auch, ebenso gemischt.\n"
            "Inhaltsstufe per Wort: safe / sensitive / nsfw / explicit. Ohne Angabe\n"
            "neigt Anima zu anzueglichen Posen -> fuer brave Bilder 'safe' dazuschreiben.\n"
            "Kuenstlerstil mit @ davor, z.B. '@artistname'.\n"
            "'masterpiece, best quality' wird automatisch vorangestellt.\n\n"
            "Einstellungen (Model-Card, Turbo): 10 Schritte, CFG 1, euler + simple,\n"
            "1024x1024 (512-1536 moeglich) -> ~13 s pro Bild. Bei CFG 1 wirkt der\n"
            "Negativ-Prompt nicht. Etwas mehr Qualitaet/Vielfalt (~68 s): im Loader\n"
            "anima-aesthetic-v1.1 waehlen, im KSampler 30 Schritte, CFG 4,5, er_sde.\n"
            "Gewichtung braucht hoehere Werte als SDXL, z.B. (chibi:2).\n"
            "Lizenz: CircleStone Non-Commercial (privat frei, Bilder frei nutzbar)."),
        "nodes": {
            "1": ("UNETLoader", {"unet_name": "anima-turbo-v1.1.safetensors", "weight_dtype": "default"}),
            "2": ("CLIPLoader", {"clip_name": "qwen_3_06b_base.safetensors", "type": "stable_diffusion",
                                 "device": "default"}),
            "3": ("VAELoader", {"vae_name": "qwen_image_vae.safetensors"}),
            "10": ("PrimitiveStringMultiline", {"value": "A girl with long silver hair and red eyes wearing a "
                                                         "witch hat sits on a rooftop under a starry night sky, "
                                                         "smiling at the viewer."}),
            "11": ("StringConcatenate", {"string_a": "masterpiece, best quality", "string_b": ["10", 0],
                                         "delimiter": ", "}),
            "12": ("CLIPTextEncode", {"text": ["11", 0], "clip": ["2", 0]}),
            "13": ("CLIPTextEncode", {"text": "worst quality, low quality, artist name, blurry, "
                                              "jpeg artifacts, chromatic aberration", "clip": ["2", 0]}),
            "20": ("EmptyLatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
            "30": ("KSampler", {"model": ["1", 0], "seed": 0, "steps": 10, "cfg": 1.0,
                                "sampler_name": "euler", "scheduler": "simple",
                                "positive": ["12", 0], "negative": ["13", 0],
                                "latent_image": ["20", 0], "denoise": 1.0}),
            "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
            "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": "Anime/Anima"}),
        },
        "titles": {"10": "Prompt - dein Motiv", "13": "Negativ-Prompt"},
        "openwebui": {"prompt": ("10", "value"), "model": ("1", "unet_name"),
                      "width": ("20", "width"), "height": ("20", "height"),
                      "steps": ("30", "steps"), "seed": ("30", "seed")},
    },
    # ---- Gezeichnet: Qwen-Image 2.1 Turbo mit D&D-Stilsatz (Stil wie die 5e-Buecher) ----
    "Gezeichnet - D&D": {
        "file": "gezeichnet_dnd",
        "note": (
            "GEZEICHNET IM D&D-STIL (Qwen-Image 2.1 Turbo)\n\n"
            "Im Feld 'Prompt - dein Motiv' das Bild in ganzen Saetzen beschreiben\n"
            "- AUF ENGLISCH: Deutsch versteht es nur halb ('Tieflinge' -> keine Hoerner).\n"
            "Figuren, Kreaturen, Orte, Landschaften, Innenraeume - ruhig ausfuehrlich, das\n"
            "Modell setzt auch lange Beschreibungen mit vielen Figuren gut um.\n"
            "Tiefling, Elf, Dwarf, Orc kennt es; seltenere Voelker kurz beschreiben, z.B.\n"
            "'a dragonborn, a dragon-headed humanoid with red scales and a snout'.\n"
            "Den Knoten 'Text Encode' NICHT aufklappen und dort tippen - sonst faellt\n"
            "der Stilsatz weg und es wird fotorealistisch.\n"
            "Der Stil (Oelmalerei wie in den D&D-5e-Buechern) wird automatisch angehaengt.\n"
            "Nacktheit/NSFW einfach im Satz beschreiben.\n\n"
            "Bildgroesse 1344x768 (Querformat); hochkant 768x1344, quadratisch 1024x1024.\n"
            "Einstellungen (Turbo): 8 Schritte (ManualSigmas), CFG 1, euler -> ~38 s.\n"
            "Lizenz: Qwen Research (privat/nicht-kommerziell)."),
        "nodes": qwen21_t2i("Inside a merchant tent lit by lanterns, a group of tiefling adventurers with "
                            "horns and tails rests around a campfire, among shelves with bottles, scrolls, "
                            "rugs and pillows.", DND_STYLE, 1344, 768, "Gezeichnet/DnD"),
        "titles": {"10": "Prompt - dein Motiv"},
        "openwebui": QWEN21_T2I_MAPPING,
    },
    # ---- D&D-Unterarten (Web-App: Auswahl "Art" beim D&D-Workflow) ----
    "D&D - Ort": {
        "file": "dnd_ort",
        "note": (
            "D&D - ORT / LANDSCHAFT (Qwen-Image 2.1 Turbo)\n\n"
            "Wie 'Gezeichnet - D&D', aber fuer Orte: Landschaften, Staedte, Ruinen, Innenraeume\n"
            "als weite Totale ohne Hauptfigur. Im Feld 'Prompt - dein Motiv' den Ort beschreiben\n"
            "(Englisch). Stil und Bildaufbau werden automatisch angehaengt. 1344x768, ~38 s."),
        "nodes": qwen21_t2i("A fortified dwarven city carved into a mountainside, with stone bridges over a deep "
                            "chasm, glowing forges and banners, seen from a high cliff.",
                            DND_LOCATION_STYLE, 1344, 768, "Gezeichnet/DnD_Ort"),
        "titles": {"10": "Prompt - dein Motiv"},
        "openwebui": QWEN21_T2I_MAPPING,
    },
    "D&D - Token": {
        "file": "dnd_token",
        "note": (
            "D&D - TOKEN (Qwen-Image 2.1 Turbo + runder Rahmen)\n\n"
            "Erzeugt ein Brustbild im D&D-Stil und schneidet es rund aus: goldener Ring, aussen\n"
            "transparent (PNG) - fertig fuer Roll20/Foundry/Owlbear. Im Feld 'Prompt - dein Motiv'\n"
            "die Figur beschreiben (Volk, Klasse, Aussehen). Rahmen und Maske: token_rahmen.png /\n"
            "token_maske.png im Input-Ordner (Quelle workflows/assets/). 1024x1024, ~38 s."),
        "nodes": qwen21_t2i("A grizzled dwarf cleric with a braided grey beard, a steel helmet and a glowing holy "
                            "symbol on his chest.", DND_TOKEN_STYLE, 1024, 1024, "Gezeichnet/DnD_Token"),
        "titles": {"10": "Prompt - dein Motiv", "70": "Token-Rahmen", "73": "Token-Maske"},
        "openwebui": {"prompt": ("10", "value"), "model": ("1", "unet_name"), "seed": ("17", "noise_seed")},
    },
    "D&D - Battlemap": {
        "file": "dnd_battlemap",
        "note": (
            "D&D - BATTLEMAP (Anima Aesthetic v1.1 + Mapcraft-LoRA)\n\n"
            "Karten in Draufsicht fuer virtuelle Spieltische im Stil von Dungeondraft/Forgotten Adventures:\n"
            "gedeckte Farben, Tusche-Konturen, flaches Licht (Licht macht die VTT-Software), ohne Raster.\n"
            "Im Feld 'Prompt - dein Motiv' den Ort und was darin ist beschreiben (Englisch).\n"
            "Stil und 'mapcraft, battle map, top-down view' werden automatisch vorangestellt.\n"
            "30 Schritte, CFG 4,5, er_sde -> ~75 s. LoRA: Mapcraft Anima v1 (civitai 799901)."),
        "nodes": anima_map(BATTLEMAP_STYLE, "A long winding natural cave tunnel with a cobblestone floor, large "
                           "boulders, crates and barrels in one corner; everything outside the cave walls is pure "
                           "black.", 1344, 768, "Gezeichnet/DnD_Battlemap"),
        "titles": {"10": "Prompt - dein Motiv", "5": "Mapcraft-LoRA", "13": "Negativ-Prompt"},
        "openwebui": ANIMA_MAP_MAPPING,
    },
    "D&D - Ortskarte": {
        "file": "dnd_ortskarte",
        "note": (
            "D&D - ORTSKARTE (Anima Aesthetic v1.1 + Mapcraft-LoRA)\n\n"
            "Doerfer, Staedte und Grossstaedte von oben wie Inkarnate-/Dungeondraft-Ortskarten: Daecher mit\n"
            "Firsten, schwarze Tusche-Konturen, Aquarell-Flaechen. Im Feld 'Prompt - dein Motiv' die Siedlung\n"
            "beschreiben (Groesse, Mauern, Fluss, Plaetze, besondere Gebaeude). Fuer grosse Staedte das\n"
            "groessere Format nehmen. 1280x960 ~90 s, 1536x1152 ~135 s."),
        "nodes": anima_map(TOWN_MAP_STYLE, "A village of about forty houses with red, brown and grey roofs along "
                           "winding cobblestone roads, a market square with a well, a temple, a river with a wooden "
                           "bridge, a wooden palisade, dense forest around it.", 1280, 960, "Gezeichnet/DnD_Ortskarte"),
        "titles": {"10": "Prompt - dein Motiv", "5": "Mapcraft-LoRA", "13": "Negativ-Prompt"},
        "openwebui": ANIMA_MAP_MAPPING,
        "sizes": [["Quer 4:3", 1280, 960], ["Groß 4:3", 1536, 1152], ["Quadrat", 1024, 1024],
                  ["Groß quadratisch", 1536, 1536], ["Hoch 3:4", 960, 1280]],
    },
    # ---- Test: FLUX 3 Image Klein (BFL, 15.10.2026), destillierte bf16-Fassung, Werte der offiziellen Vorlage ----
    "Test - Flux 3 Klein": {
        "file": "flux3_klein",
        "note": (
            "FLUX 3 IMAGE KLEIN (Black Forest Labs, 5,6 Mrd. Parameter, destilliert)\n\n"
            "Im Feld 'Prompt' das Bild in ganzen Saetzen beschreiben (Englisch), kein Stilsatz -\n"
            "Foto, Gemaelde, Anime: alles im Text sagen. 16 Schritte, CFG 1, euler + simple\n"
            "(offizielle Vorlage); Negativ-Prompt wirkt bei CFG 1 nicht.\n"
            "Den Prompt-Upsampler der Vorlage (Qwen 3.5 9B) gibt es hier nicht - dafuer in der\n"
            "Web-App 'Ausformulieren'. Lizenz: FLUX Kommunity License."),
        "nodes": {
            "1": ("UNETLoader", {"unet_name": "flux3-image-klein-bf16.safetensors", "weight_dtype": "default"}),
            "2": ("CLIPLoader", {"clip_name": "qwen3vl_4b_bf16.safetensors", "type": "flux3", "device": "default"}),
            "3": ("VAELoader", {"vae_name": "flux2-vae.safetensors"}),
            "10": ("PrimitiveStringMultiline", {"value": "A close-up photograph of a red fox standing in fresh snow "
                                                         "at sunrise, alert ears, amber eyes, soft golden light."}),
            "12": ("CLIPTextEncode", {"text": ["10", 0], "clip": ["2", 0]}),
            "13": ("CLIPTextEncode", {"text": "", "clip": ["2", 0]}),
            "20": ("EmptyFlux2LatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
            "30": ("KSampler", {"model": ["1", 0], "seed": 0, "steps": 16, "cfg": 1.0,
                                "sampler_name": "euler", "scheduler": "simple",
                                "positive": ["12", 0], "negative": ["13", 0],
                                "latent_image": ["20", 0], "denoise": 1.0}),
            "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
            "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": "Test/Flux3Klein"}),
        },
        "titles": {"10": "Prompt"},
        "openwebui": {"prompt": ("10", "value"), "model": ("1", "unet_name"),
                      "width": ("20", "width"), "height": ("20", "height"),
                      "steps": ("30", "steps"), "seed": ("30", "seed")},
    },
    # ---- Realistisch mit Qwen-Image 2.1 Turbo ----
    "Realistisch - Qwen 2.1": {
        "file": "realistisch_qwen21",
        "note": (
            "REALISTISCH (Qwen-Image 2.1 Turbo)\n\n"
            "Im Feld 'Prompt - dein Motiv' das Bild in ganzen Saetzen beschreiben\n"
            "(Englisch am besten). Ein kurzer Foto-Stilsatz wird automatisch angehaengt.\n"
            "Setzt lange, genaue Beschreibungen (mehrere Personen, Text im Bild) gut um.\n"
            "Nacktheit/NSFW einfach im Satz beschreiben.\n\n"
            "Bildgroesse 1024x1024 (bis 2048 moeglich, dauert dann ~4x so lange).\n"
            "Einstellungen (Turbo): 8 Schritte (ManualSigmas), CFG 1, euler -> ~37 s.\n"
            "Lizenz: Qwen Research (privat/nicht-kommerziell)."),
        "nodes": qwen21_t2i("A woman with long red hair stands in a misty pine forest at dawn, wearing a "
                            "green wool coat.", PHOTO_STYLE, 1024, 1024, "Realistisch/Qwen21"),
        "titles": {"10": "Prompt - dein Motiv"},
        "openwebui": QWEN21_T2I_MAPPING,
    },
    # ---- Bearbeiten: Qwen-Image 2.1 Turbo (Generieren + Bearbeiten in einem Modell) ----
    "Bearbeiten - Qwen 2.1": {
        "file": "bearbeiten_qwen21",
        "note": (
            "BILD BEARBEITEN (Qwen-Image 2.1 Turbo) - aehnlich wie Nano Banana\n\n"
            "1. Im Knoten 'Bild 1 - wird bearbeitet' ein Bild hochladen (oder aus\n"
            "   ComfyUI-Ausgaben hineinziehen).\n"
            "2. Im Feld 'Anweisung' sagen, was sich aendern soll, in ganzen Saetzen\n"
            "   (Englisch am besten), z.B. 'Change the background to a snowy mountain\n"
            "   village at night. Keep the person, pose and clothing unchanged.'\n"
            "   Gut: Hintergrund/Kleidung/Licht/Tageszeit tauschen, Dinge entfernen oder\n"
            "   hinzufuegen, Stil umwandeln, Text im Bild aendern.\n"
            "3. Weitere Referenzbilder (bis 10): neuen 'Load Image'-Knoten an den\n"
            "   naechsten freien images-Eingang haengen und im Text als <image2>, <image3>\n"
            "   ansprechen, z.B. 'Put the sword from <image2> into her hand in <image1>'.\n\n"
            "Ausgabe hat die Groesse von Bild 1 (auf ~1 MP skaliert, 'resolution' 1024;\n"
            "bis 2048 moeglich, dauert dann ~4x so lange).\n"
            "Einstellungen (offizielle Vorlage, Turbo): 8 Schritte (ManualSigmas), CFG 1,\n"
            "euler. Lizenz: Qwen Research (privat/nicht-kommerziell)."),
        "nodes": {
            "1": ("UNETLoader", {"unet_name": "qwen_image_2.1_turbo_bf16.safetensors", "weight_dtype": "default"}),
            "2": ("CLIPLoader", {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image",
                                 "device": "default"}),
            "3": ("VAELoader", {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}),
            "4": ("QwenImage21Cache", {"model": ["1", 0], "device": "auto", "dtype": "default"}),
            "6": ("LoadImage", {"image": PLACEHOLDER_IMAGE}),
            "10": ("PrimitiveStringMultiline", {"value": "Change the time of day to a moonlit night with a starry "
                                                         "sky. Keep the composition, the buildings and the "
                                                         "painting style unchanged."}),
            "12": ("TextEncodeQwenImage21", {"clip": ["2", 0], "prompt": ["10", 0], "negative_prompt": "",
                                             "resolution": 1024, "images.image_1": ["6", 0], "vae": ["3", 0]}),
            "14": ("CFGGuider", {"model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1], "cfg": 1.0}),
            "15": ("KSamplerSelect", {"sampler_name": "euler"}),
            "16": ("ManualSigmas", {"sigmas": QWEN21_TURBO_SIGMAS}),
            "17": ("RandomNoise", {"noise_seed": 0}),
            "30": ("SamplerCustomAdvanced", {"noise": ["17", 0], "guider": ["14", 0], "sampler": ["15", 0],
                                             "sigmas": ["16", 0], "latent_image": ["12", 2]}),
            "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
            "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": "Bearbeitet/Qwen21"}),
        },
        "titles": {"10": "Anweisung - was soll sich aendern", "6": "Bild 1 - wird bearbeitet"},
        "openwebui": {"prompt": ("10", "value"), "image": ("6", "image"), "model": ("1", "unet_name"),
                      "seed": ("17", "noise_seed")},
    },
    # ---- Charakter: gleiche Figur in neuen Szenen (Qwen-Image 2.1, Referenzbild als Identitaet) ----
    "Charakter - Qwen 2.1": {
        "file": "charakter_qwen21",
        "note": (
            "GLEICHER CHARAKTER, NEUE SZENE (Qwen-Image 2.1 Turbo)\n\n"
            "1. Im Knoten 'Charakter-Referenz' ein Bild der Figur laden, am besten\n"
            "   ganzer Koerper, gut erkennbar (z.B. vorher mit Gezeichnet - D&D erzeugt).\n"
            "2. Im Feld 'Neue Szene' beschreiben, was die Figur tut und wo (Englisch am\n"
            "   besten), z.B. 'She rides a horse through a snowy mountain pass.'\n"
            "   Gesicht, Haare, Haut, Kleidung und Malstil kommen aus der Referenz.\n"
            "   Andere Kleidung: einfach dazuschreiben ('wearing a black ball gown').\n"
            "   Gegenstaende aus der Referenz wandern gern mit -> 'her hands are empty'.\n"
            "3. Mehr Sicherheit: weitere Bilder derselben Figur (andere Ansicht) an den\n"
            "   naechsten freien images-Eingang haengen; im Text '<image1>' bleibt.\n"
            "   Zwei Figuren: zweites Bild anhaengen und z.B. 'the elf from <image2>'\n"
            "   im Szenentext erwaehnen.\n\n"
            "Bildgroesse frei im Knoten 'Bildgroesse' (1344x768 quer, 768x1344 hoch).\n"
            "~65 s pro Bild. NSFW einfach im Text beschreiben.\n"
            "Lizenz: Qwen Research (privat/nicht-kommerziell)."),
        "nodes": {
            "1": ("UNETLoader", {"unet_name": "qwen_image_2.1_turbo_bf16.safetensors", "weight_dtype": "default"}),
            "2": ("CLIPLoader", {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image",
                                 "device": "default"}),
            "3": ("VAELoader", {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}),
            "4": ("QwenImage21Cache", {"model": ["1", 0], "device": "auto", "dtype": "default"}),
            "6": ("LoadImage", {"image": PLACEHOLDER_IMAGE}),
            "10": ("PrimitiveStringMultiline", {"value": "She sits at a tavern table at night, laughing and "
                                                         "raising a mug of ale, lantern light."}),
            "11": ("StringConcatenate", {"string_a": CHARACTER_PREFIX, "string_b": ["10", 0], "delimiter": " "}),
            "12": ("TextEncodeQwenImage21", {"clip": ["2", 0], "prompt": ["11", 0], "negative_prompt": "",
                                             "resolution": 1024, "images.image_1": ["6", 0], "vae": ["3", 0]}),
            "14": ("CFGGuider", {"model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1], "cfg": 1.0}),
            "15": ("KSamplerSelect", {"sampler_name": "euler"}),
            "16": ("ManualSigmas", {"sigmas": QWEN21_TURBO_SIGMAS}),
            "17": ("RandomNoise", {"noise_seed": 0}),
            "20": ("EmptyLatentImage", {"width": 1344, "height": 768, "batch_size": 1}),
            "30": ("SamplerCustomAdvanced", {"noise": ["17", 0], "guider": ["14", 0], "sampler": ["15", 0],
                                             "sigmas": ["16", 0], "latent_image": ["20", 0]}),
            "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
            "50": ("SaveImage", {"images": ["40", 0], "filename_prefix": "Charakter/Qwen21"}),
        },
        "titles": {"10": "Neue Szene - was tut die Figur, wo", "6": "Charakter-Referenz",
                   "20": "Bildgroesse"},
        "openwebui": {"prompt": ("10", "value"), "image": ("6", "image"), "model": ("1", "unet_name"),
                      "width": ("20", "width"), "height": ("20", "height"), "seed": ("17", "noise_seed")},
    },
    # ---- Token aus Bild (Web-App: Bearbeiten -> Gezeichnet - D&D, Art Token) ----
    "D&D - Token aus Bild": {
        "file": "dnd_token_bild",
        "note": (
            "D&D - TOKEN AUS BILD (Qwen-Image 2.1 Turbo + runder Rahmen)\n\n"
            "1. Im Knoten 'Figur' ein Bild der Figur laden (Foto, Zeichnung, eigenes D&D-Bild).\n"
            "2. Im Feld 'Wuensche' optional Aenderungen (z.B. 'angry expression, wearing a hood').\n"
            "Die Figur wird als Brustbild im D&D-Stil neu gemalt, rund ausgeschnitten, goldener Ring,\n"
            "aussen transparent (PNG). Rahmen und Maske wie beim Token. 1024x1024, ~65 s."),
        "nodes": {
            "1": ("UNETLoader", {"unet_name": "qwen_image_2.1_turbo_bf16.safetensors", "weight_dtype": "default"}),
            "2": ("CLIPLoader", {"clip_name": "qwen3vl_8b_bf16.safetensors", "type": "qwen_image",
                                 "device": "default"}),
            "3": ("VAELoader", {"vae_name": "qwen_image_2.1_vae_bf16.safetensors"}),
            "4": ("QwenImage21Cache", {"model": ["1", 0], "device": "auto", "dtype": "default"}),
            "6": ("LoadImage", {"image": PLACEHOLDER_IMAGE}),
            "10": ("PrimitiveStringMultiline", {"value": "Same character, friendly expression."}),
            "11": ("StringConcatenate", {"string_a": TOKEN_FROM_IMAGE_PREFIX, "string_b": ["10", 0],
                                         "delimiter": " "}),
            "13": ("StringConcatenate", {"string_a": ["11", 0], "string_b": DND_STYLE, "delimiter": " "}),
            "12": ("TextEncodeQwenImage21", {"clip": ["2", 0], "prompt": ["13", 0], "negative_prompt": "",
                                             "resolution": 1024, "images.image_1": ["6", 0], "vae": ["3", 0]}),
            "14": ("CFGGuider", {"model": ["4", 0], "positive": ["12", 0], "negative": ["12", 1], "cfg": 1.0}),
            "15": ("KSamplerSelect", {"sampler_name": "euler"}),
            "16": ("ManualSigmas", {"sigmas": QWEN21_TURBO_SIGMAS}),
            "17": ("RandomNoise", {"noise_seed": 0}),
            "20": ("EmptyLatentImage", {"width": 1024, "height": 1024, "batch_size": 1}),
            "30": ("SamplerCustomAdvanced", {"noise": ["17", 0], "guider": ["14", 0], "sampler": ["15", 0],
                                             "sigmas": ["16", 0], "latent_image": ["20", 0]}),
            "40": ("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
        },
        "titles": {"10": "Wuensche (optional)", "6": "Figur", "70": "Token-Rahmen", "73": "Token-Maske"},
        "openwebui": {"prompt": ("10", "value"), "image": ("6", "image"), "model": ("1", "unet_name"),
                      "seed": ("17", "noise_seed")},
        "default_prompt": "Same character.",
    },
    # ---- Video aus Text: Wan 2.2 14B (zwei Experten) + Lightx2v-4-Schritt-LoRAs ----
    "Video - Wan 2.2": {
        "file": "video_wan22_t2v",
        "note": (
            "VIDEO AUS TEXT (Wan 2.2 14B, 4 Schritte mit Lightx2v)\n\n"
            "Im Feld 'Prompt' Szene UND Bewegung in ganzen Saetzen beschreiben (Englisch am\n"
            "besten): wer/was, wo, was passiert, Kamerabewegung, Licht. Z.B. 'A woman walks\n"
            "along a beach at sunset, her dress moving in the wind, the camera slowly follows.'\n"
            "NSFW einfach im Text beschreiben.\n\n"
            "Laenge: Bildzahl im Knoten 'Groesse und Laenge' = Sekunden x 16 + 1 (81 = 5 s).\n"
            "Wan ist auf ~5 s trainiert; laenger wird schnell unruhig.\n"
            "Groesse: 832x480 (quer) oder 480x832 (hoch) ist der schnelle Standard,\n"
            "1280x720 sieht besser aus, dauert aber ~3x so lange.\n"
            "Ablauf wie die offizielle Vorlage: Experte fuer grobe Bewegung (Schritt 0-2),\n"
            "dann Experte fuer Details (2-4), CFG 1, euler/simple, Shift 5, 16 fps."),
        "nodes": None,          # unten per wan22() gesetzt
        "titles": {"10": "Prompt - Szene und Bewegung", "20": "Groesse und Laenge"},
        "openwebui": {"prompt": ("10", "value"), "model": ("1", "unet_name"), "seed": ("30", "noise_seed"),
                      "width": ("20", "width"), "height": ("20", "height"), "length": ("20", "length")},
        "video": {"durations": [3, 5, 7], "default_duration": 5, "motion": WAN_MOTION,
                  "sizes": [["Quer", 832, 480], ["Hoch", 480, 832], ["Quadrat", 640, 640], ["Quer HD", 1280, 720]]},
    },
    # ---- Video aus Bild: Wan 2.2 14B I2V + Lightx2v (Schnell) ----
    "Video aus Bild - Wan 2.2": {
        "file": "video_wan22_i2v",
        "note": (
            "VIDEO AUS BILD - SCHNELL (Wan 2.2 14B I2V, 4 Schritte mit Lightx2v)\n\n"
            "1. Im Knoten 'Startbild' ein Bild laden - es wird das erste Bild des Videos.\n"
            "2. Im Feld 'Prompt' beschreiben, was passieren soll (Bewegung, Kamera), z.B.\n"
            "   'She turns her head and smiles, her hair moves in the wind, slow zoom in.'\n"
            "   NSFW einfach im Text beschreiben.\n\n"
            "Groesse im Knoten 'Groesse und Laenge' passend zum Bild waehlen (quer 832x480,\n"
            "hoch 480x832) - sonst wird das Bild beschnitten. Die Web-App waehlt das\n"
            "automatisch. Laenge = Sekunden x 16 + 1 (81 = 5 s)."),
        "nodes": None,
        "titles": {"10": "Prompt - was passieren soll", "6": "Startbild", "21": "Groesse und Laenge"},
        "openwebui": {"prompt": ("10", "value"), "image": ("6", "image"), "model": ("1", "unet_name"),
                      "seed": ("30", "noise_seed"), "width": ("21", "width"), "height": ("21", "height"),
                      "length": ("21", "length")},
        "video": {"durations": [3, 5, 7], "default_duration": 5, "auto_size": True, "motion": WAN_MOTION,
                  "sizes": [["Quer", 832, 480], ["Hoch", 480, 832], ["Quadrat", 640, 640], ["Quer HD", 1280, 720]]},
    },
    # ---- Video aus Bild: Wan 2.2 14B I2V Dynamisch (16 Schritte, MoE ohne LoRA) ----
    "Video aus Bild (Dynamisch) - Wan 2.2": {
        "file": "video_wan22_i2v_hq",
        "note": (
            "VIDEO AUS BILD - DYNAMISCHE BEWEGUNG (Wan 2.2 14B I2V, 16 Schritte)\n\n"
            "Nutzt das native Wan 2.2 14B Dual-Modell (High-Noise + Low-Noise) ohne die starre\n"
            "4-Schritt-LoRA. Erlaubt echte, ausladende Körperbewegungen, Gestik und Action.\n"
            "16 Schritte mit CFG 3.5 und sgm_uniform-Scheduler.\n\n"
            "Dauert deutlich länger als der 4-Schritt-Schnellmodus: 16 Schritte mit CFG (doppelte\n"
            "Rechnung je Schritt), gemessen ~21 Min. für 3 s auf Strix Halo. Für Tests 3 s wählen!\n\n"
            "1. Im Knoten 'Startbild' ein Bild laden.\n"
            "2. Im Feld 'Prompt' die gewünschte Handlung und Bewegung beschreiben.\n"
            "3. Bei 'Bewegung' kann die Dynamik feingesteuert werden."),
        "nodes": None,
        "titles": {"10": "Prompt - was passieren soll", "6": "Startbild", "21": "Groesse und Laenge"},
        "openwebui": {"prompt": ("10", "value"), "image": ("6", "image"), "model": ("1", "unet_name"),
                      "seed": ("30", "noise_seed"), "width": ("21", "width"), "height": ("21", "height"),
                      "length": ("21", "length")},
        "video": {"durations": [3, 5, 7], "default_duration": 3, "auto_size": True, "motion": WAN_MOTION_HQ,
                  "sizes": [["Quer", 832, 480], ["Hoch", 480, 832], ["Quadrat", 640, 640], ["Quer HD", 1280, 720]]},
    },
}

# Wan-Standard-Negativ; wirkt bei CFG > 1 stark gegen Einfrieren und ungewollte Mundbewegungen
WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，"
                "JPEG压缩残留，丑陋的，残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，"
                "手指融合，静止不动的画面，杂乱的背景，三条腿，背景人很多，倒着走, "
                "static, motionless, talking, mouth moving, chewing, open mouth, blurry, worst quality, low quality")


def wan22(kind: str, hq: bool = False) -> dict:
    """Wan 2.2 14B: Entweder mit Lightx2v (4 Schritte, schnell) oder HQ (16 Schritte, natives MoE,
    volle Bewegung). Experte fuer hohe Rauschstufen, dann fuer niedrige (Details)."""
    n = {
        "1": ("UNETLoader", {"unet_name": f"wan2.2_{kind}_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}),
        "2": ("UNETLoader", {"unet_name": f"wan2.2_{kind}_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}),
        "3": ("CLIPLoader", {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}),
        "4": ("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
    }
    if not hq:
        lora = {"t2v": "wan2.2_t2v_lightx2v_4steps_lora_v1.1_{}_noise.safetensors",
                "i2v": "wan2.2_i2v_lightx2v_4steps_lora_v1_{}_noise.safetensors"}[kind]
        n["5"] = ("LoraLoaderModelOnly", {"model": ["1", 0], "lora_name": lora.format("high"), "strength_model": 1.0})
        n["7"] = ("LoraLoaderModelOnly", {"model": ["2", 0], "lora_name": lora.format("low"), "strength_model": 1.0})
        m_high, m_low = ["5", 0], ["7", 0]
        shift, steps, split, cfg, scheduler = 5.0, 4, 2, 1.0, "simple"
    else:
        m_high, m_low = ["1", 0], ["2", 0]
        shift, steps, split, cfg, scheduler = 8.0, 16, 8, 3.5, "sgm_uniform"

    n["8"] = ("ModelSamplingSD3", {"model": m_high, "shift": shift})
    n["9"] = ("ModelSamplingSD3", {"model": m_low, "shift": shift})
    n["10"] = ("PrimitiveStringMultiline", {"value": "A red fox runs through fresh snow in a quiet winter forest, "
                                                     "snow dust flying up, the camera tracks alongside it."
                                                     if kind == "t2v" else
                                                     "The subject turns around dynamically, expressive body movement, "
                                                     "gentle wind moves hair and clothing, cinematic camera tracking shot."
                                                     if hq else
                                                     "The subject slowly turns and looks into the camera, gentle "
                                                     "wind moves the hair, soft natural light, slow zoom in."})
    n["12"] = ("CLIPTextEncode", {"text": ["10", 0], "clip": ["3", 0]})
    n["13"] = ("CLIPTextEncode", {"text": WAN_NEGATIVE, "clip": ["3", 0]})

    if kind == "t2v":
        n["20"] = ("EmptyHunyuanLatentVideo", {"width": 832, "height": 480, "length": 81, "batch_size": 1})
        pos, neg, lat = ["12", 0], ["13", 0], ["20", 0]
    else:
        n["6"] = ("LoadImage", {"image": PLACEHOLDER_IMAGE})
        n["21"] = ("WanImageToVideo", {"positive": ["12", 0], "negative": ["13", 0], "vae": ["4", 0], "start_image": ["6", 0],
                                       "width": 832, "height": 480, "length": 49 if hq else 81, "batch_size": 1})
        pos, neg, lat = ["21", 0], ["21", 1], ["21", 2]
    common = {"steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": scheduler, "positive": pos, "negative": neg}
    n["30"] = ("KSamplerAdvanced", {"model": ["8", 0], "add_noise": "enable", "noise_seed": 0, **common,
                                    "latent_image": lat, "start_at_step": 0, "end_at_step": split,
                                    "return_with_leftover_noise": "enable"})
    n["31"] = ("KSamplerAdvanced", {"model": ["9", 0], "add_noise": "disable", "noise_seed": 0, **common,
                                    "latent_image": ["30", 0], "start_at_step": split, "end_at_step": 10000,
                                    "return_with_leftover_noise": "disable"})
    n["40"] = ("VAEDecode", {"samples": ["31", 0], "vae": ["4", 0]})
    n["45"] = ("CreateVideo", {"images": ["40", 0], "fps": 16.0})
    suffix = f"Wan22_{kind}_hq" if hq else f"Wan22_{kind}"
    n["50"] = ("SaveVideo", {"video": ["45", 0], "filename_prefix": f"Video/{suffix}", "format": "auto",
                             "format.codec": "auto"})
    return n


WORKFLOWS["Video - Wan 2.2"]["nodes"] = wan22("t2v")
WORKFLOWS["Video aus Bild - Wan 2.2"]["nodes"] = wan22("i2v")
WORKFLOWS["Video aus Bild (Dynamisch) - Wan 2.2"]["nodes"] = wan22("i2v", hq=True)

# Token: Ring drueberlegen (Maske = Alpha des Rings) und rund ausschneiden. ComfyUI-Masken sind
# "1 - Alpha" (LoadImage); InvertMask macht daraus die Ring-Deckung, JoinImageWithAlpha rechnet
# selbst wieder 1 - Maske -> Alpha = Kreis.
for _title in ("D&D - Token", "D&D - Token aus Bild"):
    WORKFLOWS[_title]["nodes"].update({
        "70": ("LoadImage", {"image": "token_rahmen.png"}),
        "71": ("InvertMask", {"mask": ["70", 1]}),
        "72": ("ImageCompositeMasked", {"destination": ["40", 0], "source": ["70", 0], "x": 0, "y": 0,
                                        "resize_source": False, "mask": ["71", 0]}),
        "73": ("LoadImage", {"image": "token_maske.png"}),
        "74": ("JoinImageWithAlpha", {"image": ["72", 0], "alpha": ["73", 1]}),
        "50": ("SaveImage", {"images": ["74", 0], "filename_prefix": "Gezeichnet/DnD_Token"}),
    })

WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"}


def object_info() -> dict:
    with urllib.request.urlopen(f"{COMFY}/object_info", timeout=30) as r:
        return json.loads(r.read())


def api_format(wf: dict) -> dict:
    return {nid: {"class_type": cls, "inputs": inputs,
                  "_meta": {"title": wf.get("titles", {}).get(nid, cls)}}
            for nid, (cls, inputs) in wf["nodes"].items()}


def ui_format(wf: dict, info: dict) -> dict:
    """API-Format -> UI-Format (Knoten, Links, Widgets in der Reihenfolge von ComfyUI)."""
    nodes_api = wf["nodes"]
    ids = {nid: i + 1 for i, nid in enumerate(nodes_api)}
    links, nodes = [], []

    # Spalte = Tiefe im Graphen (Abstand von den Quellen), fuer ein lesbares Layout
    depth = {}

    def d(nid):
        if nid not in depth:
            srcs = [v[0] for v in nodes_api[nid][1].values() if isinstance(v, list)]
            depth[nid] = 1 + max((d(s) for s in srcs), default=-1)
        return depth[nid]
    for nid in nodes_api:
        d(nid)
    rows = {}

    out_links = {nid: {} for nid in nodes_api}            # nid -> slot -> [link ids]
    for nid, (cls, inputs) in nodes_api.items():
        ci = info[cls]
        order = (ci.get("input_order", {}).get("required", list(ci["input"].get("required", {})))
                 + ci.get("input_order", {}).get("optional", list(ci["input"].get("optional", {}))))
        specs = {**ci["input"].get("required", {}), **ci["input"].get("optional", {})}
        ui_inputs, widgets = [], []
        text_linked = False
        for name in order:
            spec = specs[name]
            typ = spec[0] if not isinstance(spec[0], list) else "COMBO"
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if typ == "COMFY_AUTOGROW_V3":                 # wachsende Eingaenge "images.image_1" ...
                tpl = spec[1]["template"]
                sub_type = next(iter(tpl["input"]["required"].values()))[0]
                used = [n for n in tpl["names"] if f"{name}.{n}" in inputs]
                free = [n for n in tpl["names"] if n not in used][:1 if used else 0]
                for n in used + free:
                    v = inputs.get(f"{name}.{n}")
                    entry = {"name": f"{name}.{n}", "type": sub_type, "link": None}
                    if v:
                        lid = len(links) + 1
                        links.append([lid, ids[v[0]], v[1], ids[nid], len(ui_inputs),
                                      info[nodes_api[v[0]][0]]["output"][v[1]]])
                        out_links[v[0]].setdefault(v[1], []).append(lid)
                        entry["link"] = lid
                    ui_inputs.append(entry)
                continue
            if typ == "COMFY_DYNAMICCOMBO_V3":             # z.B. SaveVideo format -> codec (verschachtelt)
                def dyn(key, spec_):
                    choices = spec_[1]["options"]
                    val_ = inputs.get(key, choices[0]["key"])
                    widgets.append(val_)
                    sel = next((c for c in choices if c["key"] == val_), choices[0])
                    for sub, sub_spec in sel["inputs"].get("required", {}).items():
                        if sub_spec[0] == "COMFY_DYNAMICCOMBO_V3":
                            dyn(f"{key}.{sub}", sub_spec)
                        else:
                            widgets.append(inputs.get(f"{key}.{sub}", sub_spec[1].get("default")))
                dyn(name, spec)
                continue
            val = inputs.get(name)
            is_widget = typ in WIDGET_TYPES
            if isinstance(val, list):                      # Verbindung
                lid = len(links) + 1
                src, slot = val
                links.append([lid, ids[src], slot, ids[nid], len(ui_inputs),
                              info[nodes_api[src][0]]["output"][slot]])
                out_links[src].setdefault(slot, []).append(lid)
                entry = {"name": name, "type": typ, "link": lid}
                if is_widget:
                    entry["widget"] = {"name": name}
                    widgets.append("")
                    text_linked = text_linked or typ == "STRING"
                ui_inputs.append(entry)
            elif is_widget:
                widgets.append(val if val is not None else opts.get("default"))
                if opts.get("control_after_generate"):
                    widgets.append("randomize")
                if opts.get("image_upload"):               # Upload-Knopf von LoadImage
                    widgets.append("image")
            elif val is None:
                ui_inputs.append({"name": name, "type": typ, "link": None})
        col = depth[nid]
        if nid == "10":                                    # Prompt-Feld oben, neben der Anleitung
            pos, size = [780, -260], [700, 230]
        else:
            row = rows.get(col, 0)
            rows[col] = row + 1
            n_lines = max(len(ui_inputs), len(ci["output"])) + len(widgets)
            pos, size = [40 + col * 380, 60 + row * 240], [340, 90 + 28 * n_lines]
        nodes.append({
            "id": ids[nid], "type": cls, "pos": pos,
            "size": size,
            # Encoder mit verbundenem Text zuklappen: sein Textfeld bleibt sonst sichtbar, und
            # wer dort tippt, trennt die Verbindung -> Stil-/Qualitaetszusatz faellt weg
            "flags": {"collapsed": True} if text_linked else {}, "order": 0, "mode": 0,
            "inputs": ui_inputs,
            "outputs": [{"name": ci["output_name"][i], "type": t, "slot_index": i,
                         "links": out_links[nid].get(i, [])} for i, t in enumerate(ci["output"])],
            "properties": {"Node name for S&R": cls},
            "widgets_values": widgets,
            **({"title": wf["titles"][nid]} if nid in wf.get("titles", {}) else {}),
        })
    # Ausgaben-Links nachtragen (wurden waehrend der Schleife ergaenzt)
    for n in nodes:
        nid = next(k for k, v in ids.items() if v == n["id"])
        for o in n["outputs"]:
            o["links"] = out_links[nid].get(o["slot_index"], [])
    order_map = {nid: i for i, nid in enumerate(sorted(nodes_api, key=lambda k: depth[k]))}
    for n in nodes:
        nid = next(k for k, v in ids.items() if v == n["id"])
        n["order"] = order_map[nid]
    note_id = len(nodes) + 1
    nodes.append({"id": note_id, "type": "Note", "pos": [40, -260], "size": [700, 230],
                  "flags": {}, "order": len(nodes), "mode": 0, "inputs": [], "outputs": [],
                  "properties": {}, "widgets_values": [wf["note"]], "title": "So geht's"})
    return {"last_node_id": note_id, "last_link_id": len(links), "nodes": nodes, "links": links,
            "groups": [], "config": {}, "extra": {"ds": {"scale": 0.75, "offset": [0, 300]}},
            "version": 0.4}


# Fuer die Web-App (imagegen/webapp): Kurzbeschreibung und typische Dauer pro Workflow.
# Modus ergibt sich aus der Zuordnung: mit Eingabebild ("image") = bearbeiten.
WEBAPP = {
    "gezeichnet_dnd": ("Gemalt im Stil der D&D-5e-Bücher", 32),
    "realistisch_qwen21": ("Fotorealistisch, versteht lange Beschreibungen", 32),
    "anime_anima": ("Anime, normale Sätze", 13),
    "bearbeiten_qwen21": ("Bild nach Anweisung ändern; Größe folgt dem Bild", 70),
    "charakter_qwen21": ("Gleiche Figur in neuer Szene, Pose, Kleidung", 65),
    "dnd_ort": ("Orte und Landschaften als weite Totale im D&D-Stil", 40),
    "dnd_token": ("Rundes Token mit Goldrand, transparent (PNG) für virtuelle Spieltische", 40),
    "dnd_token_bild": ("Figur aus deinem Bild als rundes D&D-Token (PNG); Text optional", 65),
    "dnd_battlemap": ("Kampfkarte von oben für den virtuellen Spieltisch, ohne Licht und Raster", 75),
    "dnd_ortskarte": ("Dorf oder Stadt von oben, Tusche + Aquarell; große Städte im großen Format", 90),
    "flux3_klein": ("Test: FLUX 3 Klein (neu), alles im Text beschreiben", 30),  # gemessen 30-34 s (1024²)
    "video_wan22_t2v": ("Video aus Text; Bewegung im Text beschreiben", 360),
    "video_wan22_i2v": ("Startbild wird lebendig (schnell, 4 Schritte); Bewegung im Text beschreiben", 360),
    "video_wan22_i2v_hq": ("Startbild mit voller Körperbewegung (dynamisch, 16 Schritte)", 1260),  # gemessen 1229-1297 s
}
# Kurzbeschreibung fuer Benutzer mit NSFW-Freigabe (sonst die neutrale aus WEBAPP); fuellen Erweiterungen
SUMMARY_NSFW: dict[str, str] = {}


# Zusatzregel fuer "Ausformulieren" (Sprachmodell in der Web-App) je Workflow
ENHANCE_HINT = {
    # Test 2026-10-26: ohne diese Regel erfand das Sprachmodell "dimly lit ... candlelight" -> dunkle,
    # braune Bilder statt des hellen, bunten Looks der 5e-Buecher
    "gezeichnet_dnd": ("Lighting and colors like official D&D book art: bright, clearly lit, colorful, mixed warm and "
                       "cool light, saturated jewel tones. Avoid dim, dark, murky or candle-lit-only scenes unless asked."),
    "dnd_ort": ("A location without a main character: describe landscape or architecture, landmarks, weather, time "
                "of day and atmosphere; bright, colorful D&D book-art lighting unless asked otherwise."),
    "dnd_token": ("One character only, head and shoulders, facing the viewer: describe race traits, face, hair, "
                  "expression, armor/clothing and one signature item; plain background, no scene."),
    "dnd_token_bild": ("The character comes from the start image; only describe changes the user asks for "
                       "(expression, headwear, item), short, no scene, no background."),
    "dnd_battlemap": ("A top-down tabletop battle map: describe the layout from above (rooms, walls, doors, paths, "
                      "water, furniture, terrain features and where they are), no people, no perspective, no "
                      "lighting effects (the virtual tabletop adds light); 40-90 words."),
    "dnd_ortskarte": ("A top-down settlement map: describe size (hamlet, village, town, big city), layout from above "
                      "(walls, gates, districts, main roads, squares, river, bridges, harbor, castle, temples, "
                      "farmland, forest) and where things are; no people, no perspective, no text; 50-100 words."),
    "realistisch_qwen21": "It is a photograph: you may name the framing and natural light, like a real photo caption.",
    "anime_anima": "Anime illustration: describe character design details (hair color and style, eye color, outfit).",
    "charakter_qwen21": ("The character from the image is kept automatically: describe only the new scene, pose, action, "
                         "expression and any requested clothing changes; do not re-describe face or identity."),
}


def webapp_entry(wf: dict) -> dict:
    summary, seconds = WEBAPP.get(wf["file"], ("", 60))
    fields = {k: {"node": n, "key": key} for k, (n, key) in wf["openwebui"].items()}
    size = None
    if "width" in fields:
        size = [wf["nodes"][fields["width"]["node"]][1]["width"],
                wf["nodes"][fields["height"]["node"]][1]["height"]]
    entry = {"file": wf["file"], "mode": "edit" if "image" in fields else "generate",
             "summary": summary, "seconds": seconds, "default_size": size, "fields": fields, "kind": "image"}
    if wf["file"] in SUMMARY_NSFW:
        entry["summary_nsfw"] = SUMMARY_NSFW[wf["file"]]
    if "default_prompt" in wf:          # Prompt darf leer bleiben (z.B. Token aus Bild)
        entry["default_prompt"] = wf["default_prompt"]
    if "sizes" in wf:                   # eigene Groessen (z.B. Ortskarte: gross fuer Staedte)
        entry["sizes"] = wf["sizes"]
    if wf["file"] in VARIANTS:          # Unterart: erscheint in der Web-App unter ihrer Gruppe
        entry["group"], entry["variant"] = VARIANTS[wf["file"]]
        if entry["group"] == DND_GROUP:             # eigener Tab "D&D" (Erzeugen und aus Bild gemischt)
            entry["tab"] = "dnd"
    if wf["file"] in ENHANCE_HINT:
        entry["enhance_hint"] = ENHANCE_HINT[wf["file"]]
    if "video" in wf:       # Videos: eigene Groessen, Laenge in Sekunden (16 fps), ggf. Groesse wie Startbild
        entry.update(kind="video", fps=16, **wf["video"])
    return entry


def add_model_loras(nodes: dict, slots: dict):
    """LoRA-Knoten in den Modell-Pfad einfuegen: slots = {Zielknoten: (neue ID, LoRA-Datei)}. Der neue
    Knoten nimmt das bisherige Modell des Zielknotens, der Zielknoten danach den neuen (Staerke 0 =
    ComfyUI ueberspringt ihn; eine Option der Web-App setzt Datei und Staerke)."""
    for target, (nid, lora) in slots.items():
        cls, inputs = nodes[target]
        nodes[nid] = ("LoraLoaderModelOnly", {"model": inputs["model"], "lora_name": lora, "strength_model": 0.0})
        inputs["model"] = [nid, 0]


def load_extras(folder: str) -> list[str]:
    """Erweiterungen laden (siehe Kopf der Datei); liefert die Namen der geladenen Dateien."""
    if not os.path.isdir(folder):
        return []
    done = []
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py") or name.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location(f"imagegen_extra_{name[:-3]}", os.path.join(folder, name))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "apply"):
            mod.apply(sys.modules[__name__])
            done.append(name)
    return done


def main():
    extras = load_extras(EXTRAS)
    if extras:
        print("Erweiterungen:", ", ".join(extras))
    info = None if API_ONLY else object_info()
    os.makedirs(os.path.join(OUT, "api"), exist_ok=True)
    mapping = {}
    index = []
    for title, wf in WORKFLOWS.items():
        if info is not None:
            missing = [cls for cls, _ in wf["nodes"].values() if cls not in info]
            if missing:
                sys.exit(f"{title}: Knoten fehlen in ComfyUI: {missing}")
        with open(os.path.join(OUT, "api", wf["file"] + ".json"), "w", encoding="utf-8") as f:
            json.dump(api_format(wf), f, indent=1, ensure_ascii=False)
        if info is not None:
            with open(os.path.join(OUT, title + ".json"), "w", encoding="utf-8") as f:
                json.dump(ui_format(wf, info), f, indent=1, ensure_ascii=False)
        mapping[wf["file"]] = {k: {"node": n, "key": key} for k, (n, key) in wf["openwebui"].items()}
        index.append({"title": title, **webapp_entry(wf)})
        print("geschrieben:", title)
    with open(os.path.join(OUT, "api", "openwebui_mapping.json"), "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=1)
    # Reihenfolge in der Web-App = Reihenfolge in WEBAPP (meistgenutzte zuerst)
    order = list(WEBAPP)
    index.sort(key=lambda e: order.index(e["file"]) if e["file"] in order else len(order))
    with open(os.path.join(OUT, "api", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    sys.modules.setdefault("build_workflows", sys.modules[__name__])
    main()
