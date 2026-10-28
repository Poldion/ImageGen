# Third-party software and models

This repository contains original code under the [MIT License](LICENSE). The ComfyUI extensions under
`comfyui/custom_nodes/` are licensed under [GPL-3.0](comfyui/custom_nodes/LICENSE). The following third-party
components are used. None of them is included in the repository; they are fetched from their official sources when the
images are built.

## Runtime software

| Component | License | Note |
|---|---|---|
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | GPL-3.0 | Cloned when the image is built. That's why the own extensions are GPL-3.0 |
| [PyTorch](https://github.com/pytorch/pytorch) | BSD-3-Clause | Via the image `rocm/pytorch` |
| [ROCm](https://github.com/ROCm/ROCm) | mostly MIT/Apache-2.0, partly proprietary | Via the AMD image `rocm/pytorch` |
| [FastAPI](https://github.com/fastapi/fastapi) | MIT | web app |
| [Starlette](https://github.com/encode/starlette) | BSD-3-Clause | web app |
| [Uvicorn](https://github.com/encode/uvicorn) | BSD-3-Clause | web app |
| [HTTPX](https://github.com/encode/httpx) / httpcore | BSD-3-Clause | web app |
| [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 | web app |

## Models

**No models are included** (list with folders: [MODELLE.md](MODELLE.md), German). Whoever downloads a model must follow
its license. The model card of the source is authoritative; this table is a help, not legal advice.

| Model | Source | License (per source) | Note |
|---|---|---|---|
| Qwen-Image 2.1 (diffusion, text encoder, VAE) | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | Qwen Research License | **Non-commercial** |
| Wan 2.2 T2V/I2V 14B, Wan 2.1 VAE, UMT5-XXL | [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged) | Apache-2.0 | |
| Lightx2v 4-step LoRAs for Wan 2.2 | lightx2v (via Comfy-Org) | see model card | |
| Anima Turbo / Anima Aesthetic | [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima) | see model card | |
| FLUX 3 Image Klein | [black-forest-labs](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) | FLUX Kommunity License | check the terms for commercial use |
| Mapcraft Anima v1 (LoRA) | [Civitai, model 799901](https://civitai.com/models/799901) | author's Civitai license | terms on the model page |

## Images

`workflows/assets/token_rahmen.png` and `token_maske.png` (created with `make_token_assets.py`),
`workflows/platzhalter.png` and the screenshot `docs/bilder/imagegen.png` are original works and licensed under this
repository's MIT License.
