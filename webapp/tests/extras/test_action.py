"""Test-Erweiterung: eine "Aktion" mit NSFW-Kennung fuer Video aus Bild (Platzhalter-LoRAs)."""
LORA = "test_action_{}.safetensors"


def _set(strength):
    return [["60", "lora_name", LORA.format("high")], ["60", "strength_model", strength],
            ["61", "lora_name", LORA.format("low")], ["61", "strength_model", strength]]


ACTION = {"default": 1, "nsfw": True, "levels": [
    {"label": "Ohne", "hint": "Kein Zusatz-LoRA", "prefix": "", "set": []},
    {"label": "Stufe A", "hint": "allgemein", "prefix": "", "set": _set(0.9)},
    {"label": "Stufe B", "hint": "mit Trigger", "prefix": "tr1gg3r, the person turns around. ", "set": _set(1.0)},
]}


def apply(b):
    for title in ("Video aus Bild - Wan 2.2", "Video aus Bild (Dynamisch) - Wan 2.2"):
        wf = b.WORKFLOWS[title]
        b.add_model_loras(wf["nodes"], {"8": ("60", LORA.format("high")), "9": ("61", LORA.format("low"))})
        wf["video"]["action"] = ACTION
    b.SUMMARY_NSFW["anime_anima"] = "Anime, normale Sätze; safe/nsfw/explicit steuert den Inhalt"
