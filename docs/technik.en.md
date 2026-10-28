# Technical details and background

🇬🇧 English · 🇩🇪 [Deutsch](technik.md)

Supplement to the [README](../README.md): how the parts are built, why, and what was measured. Measurements come from a
Ryzen AI Max+ 395 (iGPU Radeon 8060S, `gfx1151`, 128 GB unified memory, of which up to 108 GiB for the GPU).

The workflow titles below are the internal (German) names; the English interface shows them translated, e.g.
“Realistisch - Qwen 2.1” as “Realistic - Qwen 2.1” or “Video aus Bild - Wan 2.2” as “Image to video - Wan 2.2”.

## Structure

- `comfyui/Dockerfile`: AMD's `rocm/pytorch` (ROCm 7.2, PyTorch 2.10) + ComfyUI in a fixed version. `torch` is pinned
  against replacement by pip: normal PyTorch wheels have no gfx1151 kernels and silently compute on the CPU.
  `--disable-mmap`, because mmap of large models is very slow under ROCm on gfx1151. The own extensions from
  `comfyui/custom_nodes/` are part of the image and copied into the `custom_nodes` folder at start.
- `webapp/`: FastAPI + one HTML page per view, SQLite for users, jobs and image ownership. At start the app builds its
  workflows from `build_workflows.py` (in the image) plus the extensions in `/extras` (`webapp/start.sh`) and puts the
  token templates into ComfyUI's input folder.
- **With the OllamaProxy** the web app registers as a module (`/proxy-module`): the proxy shows how much RAM and GPU
  ComfyUI holds and frees it after idle time (`WEBAPP_IDLE_FREE_MINUTES`, default 2) so that language models have room
  again.

## Data (`IMAGEGEN_DATA`)

```
models/        checkpoints/  diffusion_models/  loras/  vae/  text_encoders/ ...   (see MODELS.md)
output/        finished images and videos
input/         input images (uploads, imports, token templates)
custom_nodes/  ComfyUI extensions
extras/        your own workflow extensions (*.py)
comfy-user/    ComfyUI settings; default/workflows/ = workflow menu
webapp-data/   webapp.db (users, jobs) – belongs in the backup
cache/         MIOpen/Triton kernel caches
```

## Workflows (`workflows/`)

`build_workflows.py` creates all workflows from one definition, in two formats: `<title>.json` (UI format, for
ComfyUI's workflow menu: copy to `comfy-user/default/workflows/`) and `api/<name>.json` (API format for Open WebUI /
scripts) plus `api/openwebui_mapping.json` (which node for prompt, model, size, seed …) and `api/index.json` (list for
the web app). Every workflow has one text field (node 10); quality and style additions are appended by the workflow
itself (`StringConcatenate`) so that they stay even if a client only inserts the subject.

```bash
python3 workflows/build_workflows.py http://127.0.0.1:8188 workflows      # UI + API, checks nodes against ComfyUI
python3 workflows/build_workflows.py --api-only /tmp/wf                     # API only, without ComfyUI
python3 workflows/run_workflow.py workflows/api/<name>.json "Prompt" [seed] [url] [--image file.png]
```

**Extensions:** every `*.py` in `extras/` is loaded; its function `apply(b)` may add workflows, options and LoRAs
([workflows/extras/README.en.md](../workflows/extras/README.en.md)). That way your own or delicate additions (e.g. LoRAs
for adult content) stay outside the repository.

| Workflow | Model | Time | Prompt |
|---|---|---|---|
| Anime - Anima | Anima Turbo v1.1 | ~13 s | sentences or tags, `safe`/`nsfw`/`explicit` |
| Realistisch - Qwen 2.1 | Qwen-Image 2.1 Turbo | ~31 s (1024²) | sentences, also long/several people |
| Gezeichnet - D&D | Qwen-Image 2.1 Turbo + D&D style sentence | ~31 s (1344×768) | sentences |
| Bearbeiten - Qwen 2.1 (edit) | Qwen-Image 2.1 Turbo | ~70 s | instruction + image(s) |
| Charakter - Qwen 2.1 | Qwen-Image 2.1 Turbo | ~65 s | reference image of the character + new scene |
| D&D - Token aus Bild (token from image) | Qwen-Image 2.1 Turbo + frame | ~65 s | image of the character, text optional |
| Test - Flux 3 Klein | FLUX 3 Image Klein (distilled, 16 steps) | ~30 s (1024²) | sentences, style in the text |
| Video - Wan 2.2 | Wan 2.2 T2V 14B fp8 + Lightx2v (4 steps) | ~6 min (5 s) | scene + motion |
| Video aus Bild - Wan 2.2 (image to video) | Wan 2.2 I2V 14B fp8 + Lightx2v | ~6 min (5 s) | start image + motion |
| Video aus Bild (Dynamisch) - Wan 2.2 | Wan 2.2 I2V 14B fp8, 16 steps, CFG 3.5 | ~21 min (3 s) | start image + body/camera motion |

The models do not filter content. The web app only shows hints and options for adult content to users with NSFW
access.

**Qwen-Image 2.1** (7B, generating and editing in one model, up to 10 reference images as `<image1>` …). Chain after
the official ComfyUI template “Qwen Image 2.1 Turbo Image Edit” without the prompt enhancer: `TextEncodeQwenImage21`
(images + VAE) → `QwenImage21Cache` → CFG 1, euler, 8 steps via `ManualSigmas`. The “Load Image” node of
Bearbeiten/Charakter shows the placeholder `bild_hier_hochladen.png` (ComfyUI requires a file there).

**Editing was slow (~180 s), now ~70 s.** Sampling takes ~35 s; the rest is in the vision part of the Qwen3-VL encoder,
which deliberately computes in fp32 (fp16/bf16 give NaN). Two brakes:
- hipBLASLt uses a fallback kernel (~1 TFLOPS) for fp32 GEMM on gfx1151. Remedy: `TORCH_BLAS_PREFER_HIPBLASLT=0`
  (rocBLAS; fp32 2.7×, bf16/fp16 ~30 % faster).
- `Conv3d` in the patch embedding: MIOpen searches ~125 s for the best kernel for every new image size. The extension
  `conv3d_patch_embed` computes it as an equivalent matrix multiplication (kernel = stride).

**Memory on unified-memory systems** (`comfyui/custom_nodes/proxy_memory`): with `--disable-mmap` and the RAM pressure
cache (ComfyUI ≥ 0.42), unloaded models stay in process RAM (~22 GB), while `/system_stats` only reports the GPU part
and `/free` does not return the RAM to the system. The extension
- adds `GET /proxy/memory` (RSS + GPU + loaded models) and `POST /proxy/free` (waits for unloading and returns the RAM
  via `gc.collect()`, `_host_emptyCache()` and `malloc_trim(0)`),
- caps `get_free_memory` at `min(GTT free, MemAvailable)` and empties the cache when RAM is short, so that Wan 2.2 (two
  14B experts) does not run into a HIP OOM even next to a large language model,
- sets `mallopt(M_MMAP_THRESHOLD, 128 KB)`: otherwise tensors end up in glibc arenas whose pages ROCm/`amdkfd` pulls
  back into RAM via a `userptr` lock right after `malloc_trim` (only `MADV_DONTNEED`); with its own `mmap()` per tensor,
  `free()` really releases them via `munmap()`.

**Videos (Wan 2.2)**: two experts (high noise levels = coarse motion, low = details), each with a Lightx2v 4-step LoRA,
CFG 1, euler/simple, shift 5, 16 fps, `SaveVideo` (MP4/H.264). Length = seconds × 16 + 1 (Wan is trained on ~5 s). For
image to video, “like the image” picks landscape/portrait/square per start image (dimensions from the file header),
otherwise the image would be cropped. Measured (5 s, 832×480): ~6 min; ~78 s of pure computation per sampling step,
VAE decode ~37 s. No reloading between the experts. The web app scales its estimate linearly with length and pixel
count.

**Motion** (5 levels, `WAN_MOTION` / `WAN_MOTION_HQ`): Wan 2.2 has no control like `motion_bucket_id` (SVD). Each level
adds a phrase to the prompt + shift 4/4.5/5/6.5/8 (fast) or 5/6.5/8/9.5/11 (dynamic). With Lightx2v, image to video is
strongly bound to the first image and tends to mouth/jaw twitching; that is what **“Video aus Bild (Dynamisch)”** is
for: native dual model without the 4-step LoRA, 16 steps with CFG 3.5 and sgm_uniform, mouth movements suppressed in
the negative prompt. CFG means double computation per step (measured 1229–1297 s for 3 s). Besides the shift, the
levels also set the expert switch to match sigma 0.9 (step 6/7/8/9/9).

**Prompt expansion** (button at the prompt): a language model (best via the OllamaProxy, so that context and
`keep_alive` are fixed) turns a short idea into a detailed English prompt (~3 s); placeholders `[..]` are kept. Rules per
type (image, edit, video, image to video with start image) plus `ENHANCE_HINT` per workflow. Example with the same
seed: “A female tiefling bard in a tavern” → without: an ordinary woman with a staff; expanded: a tiefling woman with
horns, tail and lute. Without the D&D rule the model invented candlelight → dark, brown images; so the rule asks for
bright, colourful light. For videos: no negations, describe motion concretely.

**D&D types** (own tab, sub-choice “type” via `VARIANTS` → `group`/`variant`/`tab` in `index.json`):
- Illustration: Qwen 2.1 + style sentence `DND_STYLE` (colourful, painted illustration like the 5e books). “oil
  painting” + “warm lighting” gave gold-brown, almost monochrome images → explicitly ask for a colourful palette and
  mixed light. The model only half understands German → English (or prompt expansion).
- Location: `DND_LOCATION_STYLE` (wide shot without a main character).
- Token: bust, then a ring on top (`token_rahmen.png`, `ImageCompositeMasked` with inverted mask) and cut out round
  (`token_maske.png`, `JoinImageWithAlpha`) → PNG with alpha, fixed 1024×1024. “Token from image” repaints the character
  from your own image (same identity, D&D style).
- Battle map and location map: Anima Aesthetic v1.1 (30 steps, CFG 4.5, er_sde, negative prompt) + Mapcraft LoRA, style
  like Dungeondraft/Forgotten Adventures or Inkarnate: muted colours, ink outlines, flat light without lighting effects
  and without a grid (the VTT software does both). Qwen 2.1 paints places nicely but at an angle/isometric; Anima Turbo
  was too colourful.

**Character**: reference image as `<image1>`, prefix `CHARACTER_PREFIX` (deliberately general – a fixed “horns” gave
characters without horns some) asks for the same identity but a new setting. Objects from the reference come along →
“her hands are empty”.

**Collapsed encoders**: in the UI the text field of an encoder stays visible even if its text is connected; typing there
breaks the connection (style sentence gone). `build_workflows.py` therefore collapses such nodes.

Pure diffusion models (Anima, Qwen, Flux, Wan) load with “Load Diffusion Model” (`UNETLoader`, `diffusion_models/`),
text encoder and VAE separately.

## Web app

**Interface:** tabs Generate / Image as input / D&D, below them workflow, prompt, size.
Save the result, keep editing it right away, repeat with a new seed or delete; below that “My images” (loads more while
scrolling; large view with workflow, date, prompt and “use prompt”).

**Editing several images:** upload several and/or select them in “My images” → “Edit (n)”. Each input image is
edited separately with the same prompt. First all images are checked (permissions, content), then uploaded – on an
error nothing is left half done. At most 100 input images per job.

**Variables & series:**
- In the prompt `[red hair|blue hair]` → one option per image. Named variables, one per line:
  `hair = red hair | blue hair`, in the prompt `[hair]`; the same value everywhere in the same image. Values may contain
  variables themselves (any depth; circular references give an error message).
- Count without a fixed limit, each with its own seed. Rolled evenly: every option comes up before one repeats. Rolling
  happens in the app; ComfyUI only gets the finished prompt.
- **Cancel:** waiting jobs leave the queue, the running one is interrupted specifically; finished images stay.

**Users:** a setup wizard creates the admin. The admin creates users, sets passwords, grants admin rights and **NSFW
access** (without it, options with `"nsfw": true` are missing and the server rejects other levels). Passwords
PBKDF2-SHA256 (600k rounds), session 30 days (cookie httponly, SameSite=Lax, Secure behind HTTPS). Forgot the admin
password: move `webapp.db` away → the wizard appears again (image ownership is lost).

**Images and jobs:** everyone only sees and deletes their own images. ZIP download (own images, or all as admin). File
names get a time stamp, image addresses a version, so that no old image appears from the browser cache after deleting.
Job status: open → done / error / lost (ComfyUI restarted) / cancelled. The server reconciles open jobs itself every
20 s.

**API for other programs:** key per user via “API” in the app (only the SHA-256 hash is stored).
Header `Authorization: Bearer ig_...`; rights like the account, but no administration, no keys, no password. The same
workflows apply including style sentences, options and prompt expansion (`enhance`, on by default; if the language model
is gone, your own text passes through with a hint).
- `GET /api/v1/workflows` – list as in the app (options, sizes, lengths)
- `POST /api/v1/generate` (JSON): `workflow`, `prompt`, `enhance`, `image`/`images` (Base64 or data: URL), `width`,
  `height`, `seconds`, `motion`, `action`, `seed`, `count`, `variables`, `wait` (seconds; then with `results` including
  image `url`, at most `WEBAPP_API_WAIT_MAX` = 1800)
- `GET /api/v1/jobs/<id>`; images via the `url` with the same key
- OpenAI-compatible: `GET /v1/models` (image workflows only), `POST /v1/images/generations` (`model` = workflow file or
  title, `dall-e*`/`gpt-image*` = first workflow, `n` up to 10, `size`, `response_format` b64_json/url) and
  `POST /v1/images/edits` (multipart `image`). Response with `revised_prompt`. Open WebUI: Images → OpenAI, base URL
  `http://<host>:8190/v1`.

**Module for the OllamaProxy:** `GET /proxy-module`, `GET /proxy-module/memory`, `POST /proxy-module/free`, optionally
protected with `WEBAPP_MODULE_TOKEN`.

**Security:** meant for your own network; to the outside only behind a reverse proxy with HTTPS (that sets
`X-Forwarded-For`) or via VPN. ComfyUI (:8188) stays in the LAN; the app talks to it internally.
- Login lockout: 10 failed attempts per IP and 20 per name within 10 min, 1 s waiting time per failed attempt; unknown
  names cost the same time.
- `X-Forwarded-*` is only trusted from trusted proxies (`WEBAPP_TRUSTED_PROXIES`) and read from the right.
- Changing requests only from the app's own page (origin check), protection against embedding, `nosniff`.
- Uploads: at most 30 MB and only real PNG/JPG/WebP (content checked); uploaded input images are removed after the job.

**Tests:** `webapp/run_tests.sh` (only needs Docker) builds the image, creates the workflows with the test extension
`webapp/tests/extras/` and runs `webapp/tests/test_app.py` against a simulated ComfyUI (`tests/fake_comfy.py`). Covers
setup, login protection, permissions, generating, uploads, jobs, variables, series, cancelling, ZIP, videos, prompt
expansion, options, D&D types, API and the module interface (69 tests).

## Operation

```bash
docker compose --env-file ~/imagegen/imagegen.env up -d --build   # start/update
docker compose --env-file ~/imagegen/imagegen.env logs -f comfyui
docker compose --env-file ~/imagegen/imagegen.env down            # stop
```

Updating ComfyUI: raise `COMFYUI_VERSION` in `imagegen.env` and rebuild. Your own ComfyUI extensions in `custom_nodes/`
are executable code – only trustworthy ones, and no packages that pull in `torch`.

## Measured

- PyTorch in the container: `gfx1151`, 108 GiB for the GPU, matmul fp16/bf16 ~26 TFLOPS
- SDXL Base 1.0, 1024×1024, 25 steps: 21.6 s per image
- runs fine in parallel to a 26B language model with 128k context
