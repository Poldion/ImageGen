# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.2.0] – 2026-10-28

### Added

- English user interface for all pages (generate, D&D, image as input, admin, login, setup): language switch (DE/EN),
  defaults to the browser language and is remembered in the browser; workflow names, options and sizes translated too
- API and error messages in the language of the request (`X-ImageGen-Lang` from the UI, otherwise `Accept-Language`)
- Module interface answers in the requested language (OllamaProxy sends its UI language)
- Translation check `webapp/scripts/check_i18n.py` (tests and CI): reports missing translations and wrong placeholders
- English versions of the docs (`MODELS.md`, `docs/technik.en.md`, `workflows/extras/README.en.md`), linked from the
  English README

## [0.1.0] – 2026-10-28

### Added

- Initial public release.
- ComfyUI image for AMD GPUs (ROCm) with own extensions `proxy_memory` and `conv3d_patch_embed`
- Generated workflows: anime, realistic, D&D illustration/location/token/battle map/town map, editing, character, FLUX 3 Klein, Wan 2.2 video
- Web app with users, NSFW permission per user, history, variables and series, multiple input images, ZIP download
- Prompt expansion via a language model (e.g. OllamaProxy) with rules per workflow
- API with per-user keys, including OpenAI-compatible image endpoints
- Workflow extensions (`extras/*.py`) for own workflows, options and LoRAs
- Module interface for OllamaProxy (memory view, freeing ComfyUI after idle time)
- Test suite with a simulated ComfyUI
