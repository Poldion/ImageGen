# Modelle

Modelle werden **nicht mitgeliefert**. Diese Liste sagt, welche Datei in welchen Ordner unter
`${IMAGEGEN_DATA}/models/` gehört und woher sie kommt. Du brauchst nur die Modelle der Workflows, die du nutzen
willst. Maßgeblich für die Lizenz ist immer die Modellkarte der Quelle; die Angaben hier sind eine Hilfe, keine
Rechtsberatung.

Nur `.safetensors` laden: `.ckpt`/`.pt` können beim Laden Code ausführen.

## Bilder

| Workflows | Datei | Ordner | Quelle | Lizenz |
|---|---|---|---|---|
| Realistisch, Gezeichnet - D&D (Illustration, Ort, Token, Token aus Bild), Bearbeiten, Charakter | `qwen_image_2.1_turbo_bf16.safetensors` (14,2 GB) | `diffusion_models/` | [Comfy-Org/Qwen-Image-2.1](https://huggingface.co/Comfy-Org/Qwen-Image-2.1) | Qwen Research License – **nicht-kommerziell** |
| | `qwen3vl_8b_bf16.safetensors` (17,5 GB) | `text_encoders/` | ebenda | ebenda |
| | `qwen_image_2.1_vae_bf16.safetensors` | `vae/` | ebenda | ebenda |
| Anime - Anima | `anima-turbo-v1.1.safetensors` | `diffusion_models/` | [circlestone-labs/Anima](https://huggingface.co/circlestone-labs/Anima) (`split_files/`) | siehe Modellkarte |
| D&D Battlemap, Ortskarte | `anima-aesthetic-v1.1.safetensors` | `diffusion_models/` | ebenda | siehe Modellkarte |
| Anima, Battlemap, Ortskarte | `qwen_3_06b_base.safetensors` | `text_encoders/` | ebenda | siehe Modellkarte |
| | `qwen_image_vae.safetensors` | `vae/` | ebenda | siehe Modellkarte |
| D&D Battlemap, Ortskarte | `mapcraft_anima_v1.safetensors` (LoRA) | `loras/` | [Civitai, Modell 799901](https://civitai.com/models/799901) (Version für Anima) | Bedingungen auf der Modellseite |
| Test - Flux 3 Klein | `flux3-image-klein-bf16.safetensors` (11,1 GB) | `diffusion_models/` | [black-forest-labs/flux-3-image-klein-comfy](https://huggingface.co/black-forest-labs/flux-3-image-klein-comfy) (Lizenz auf der Seite akzeptieren, dann mit Token laden) | FLUX Kommunity License |
| | `qwen3vl_4b_bf16.safetensors` | `text_encoders/` | ebenda | ebenda |
| | `flux2-vae.safetensors` | `vae/` | ebenda | ebenda |

## Videos (Wan 2.2)

Zusammen etwa 69 GB. Quelle: [Comfy-Org/Wan_2.2_ComfyUI_Repackaged](https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged)
(`split_files/`), Text-Encoder und VAE aus [Comfy-Org/Wan_2.1_ComfyUI_repackaged](https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged).
Lizenz: Apache-2.0; die Lightx2v-LoRAs siehe Modellkarte von lightx2v.

| Workflows | Datei | Ordner |
|---|---|---|
| Video | `wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors`, `wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors` | `diffusion_models/` |
| Video | `wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors`, `..._low_noise.safetensors` | `loras/` |
| Video aus Bild (beide) | `wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors`, `wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors` | `diffusion_models/` |
| Video aus Bild (schnell) | `wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors`, `..._low_noise.safetensors` | `loras/` |
| alle Videos | `umt5_xxl_fp8_e4m3fn_scaled.safetensors` | `text_encoders/` |
| alle Videos | `wan_2.1_vae.safetensors` | `vae/` |

## Eigene Erweiterungen

LoRAs, die eine Erweiterung in `extras/` einbindet (siehe [workflows/extras/README.md](workflows/extras/README.md)),
gehören ebenfalls nach `loras/`. Für sie gelten die Bedingungen ihrer Quelle.

## Laden

Mit dem Browser oder z. B.:

```bash
cd "${IMAGEGEN_DATA:-$HOME/imagegen}/models"
curl -L -o diffusion_models/qwen_image_2.1_turbo_bf16.safetensors \
  https://huggingface.co/Comfy-Org/Qwen-Image-2.1/resolve/main/diffusion_models/qwen_image_2.1_turbo_bf16.safetensors
```

Bei geschützten Modellen (FLUX 3) zusätzlich `-H "Authorization: Bearer <Hugging-Face-Token>"`.
In ComfyUI danach „R“ (Refresh) drücken oder die Seite neu laden.
