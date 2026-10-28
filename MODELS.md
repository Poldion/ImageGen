# Models

🇬🇧 English · 🇩🇪 [Deutsch](MODELLE.md)

Models are **not included**. This list says which file belongs in which folder under `${IMAGEGEN_DATA}/models/` and
where it comes from. You only need the models of the workflows you want to use. The model card of the source is always
authoritative for the license; the information here is a help, not legal advice.

Only download `.safetensors`: `.ckpt`/`.pt` can execute code when loaded.

Workflow names as shown in the English interface.

## Images

| Workflows | File | Folder | Source | License |
|---|---|---|---|---|
| Realistic, Painted - D&D (illustration, location, token, token from image), Edit, Character | `qwen_image_2.1_turbo_bf16.safetensors` (14.2 GB) | `diffusion_models/` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | Qwen Research License – **non-commercial** |
| | `qwen3vl_8b_bf16.safetensors` (17.5 GB) | `text_encoders/` | same | same |
| | `qwen_image_2.1_vae_bf16.safetensors` | `vae/` | same | same |
| Anime - Anima | `anima-turbo-v1.1.safetensors` | `diffusion_models/` | [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima) (`split_files/`) | see model card |
| D&D battle map, location map | `anima-aesthetic-v1.1.safetensors` | `diffusion_models/` | same | see model card |
| Anima, battle map, location map | `qwen_3_06b_base.safetensors` | `text_encoders/` | same | see model card |
| | `qwen_image_vae.safetensors` | `vae/` | same | see model card |
| D&D battle map, location map | `mapcraft_anima_v1.safetensors` (LoRA) | `loras/` | [Civitai, model 799901](https://civitai.com/models/799901) (version for Anima) | terms on the model page |
| Test - Flux 3 Klein | `flux3-image-klein-bf16.safetensors` (11.1 GB) | `diffusion_models/` | [black-forest-labs/flux-3-image-klein-comfy](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) (accept the license on the page, then download with a token) | FLUX Community License |
| | `qwen3vl_4b_bf16.safetensors` | `text_encoders/` | same | same |
| | `flux2-vae.safetensors` | `vae/` | same | same |

## Videos (Wan 2.2)

About 69 GB in total. Source: [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged)
(`split_files/`), text encoder and VAE from [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged).
License: Apache-2.0; for the Lightx2v LoRAs see the model card of lightx2v.

| Workflows | File | Folder |
|---|---|---|
| Video | `wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors`, `wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors` | `diffusion_models/` |
| Video | `wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors`, `..._low_noise.safetensors` | `loras/` |
| Image to video (both) | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`, `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `diffusion_models/` |
| Image to video (fast) | `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors`, `..._low_noise.safetensors` | `loras/` |
| all videos | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders/` |
| all videos | `wan_2.1_vae.safetensors` | `vae/` |

## Your own extensions

LoRAs that an extension in `extras/` adds (see [workflows/extras/README.en.md](workflows/extras/README.en.md)) also go
into `loras/`. The terms of their source apply to them.

## Downloading

With the browser or e.g.:

```bash
cd "${IMAGEGEN_DATA:-$HOME/imagegen}/models"
curl -L -o diffusion_models/qwen_image_2.1_turbo_bf16.safetensors \
  https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors
```

For gated models (FLUX 3) add `-H "Authorization: Bearer <Hugging Face token>"`.
Then press “R” (refresh) in ComfyUI or reload the page.
