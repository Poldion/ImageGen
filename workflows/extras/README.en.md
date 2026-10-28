# Extensions for the workflows

🇬🇧 English · 🇩🇪 [Deutsch](README.md)

Every `*.py` file in this folder (or in the folder from `--extras` / `IMAGEGEN_EXTRAS`, in Docker
`${IMAGEGEN_DATA}/extras`) is loaded by `build_workflows.py`. Its function `apply(b)` receives the module
`build_workflows` and can change anything in it before the files are written:

- `b.WORKFLOWS` – all workflows (title → nodes, mapping, video settings)
- `b.WEBAPP`, `b.ENHANCE_HINT`, `b.SUMMARY_NSFW` – texts and duration for the web app
- `b.add_model_loras(nodes, {target node: (new ID, LoRA file)})` – insert a LoRA slot into the model path

All files here except the two READMEs are in `.gitignore`. That way your own or private extensions (e.g. LoRAs for
adult content) stay out of the repository.

## Example: a “style” choice with a LoRA for “Image to video”

```python
# extras/wan_style.py
STYLE = {"default": 1, "levels": [
    {"label": "None", "set": []},
    {"label": "Watercolour", "prefix": "watercolor style, ",
     "set": [["60", "lora_name", "my_watercolour_high.safetensors"], ["60", "strength_model", 0.8],
             ["61", "lora_name", "my_watercolour_low.safetensors"], ["61", "strength_model", 0.8]]},
]}


def apply(b):
    wf = b.WORKFLOWS["Video aus Bild - Wan 2.2"]   # internal title, shown as “Image to video - Wan 2.2”
    b.add_model_loras(wf["nodes"], {"8": ("60", "my_watercolour_high.safetensors"),
                                    "9": ("61", "my_watercolour_low.safetensors")})
    wf["video"]["action"] = STYLE
```

The web app shows options under the keys `motion` (“Motion”) and `action` (“Action”) as choice buttons. With
`"nsfw": True` in the option, only users who were given NSFW access by the admin can see them; the server rejects other
levels for everyone else.
