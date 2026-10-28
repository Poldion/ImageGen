# Changelog

All notable changes to this project are documented in this file. The format is based on
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to [Semantic Versioning](https://semver.org/).

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
