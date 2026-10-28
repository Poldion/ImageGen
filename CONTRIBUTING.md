# Contributing

Thanks for wanting to help! Bug reports, ideas and pull requests are welcome.

## Development setup

```bash
git clone https://github.com/Poldion/ImageGen.git && cd ImageGen
cp imagegen.env.example ~/imagegen/imagegen.env      # adjust
docker compose --env-file ~/imagegen/imagegen.env up -d --build
```

- `webapp/` – FastAPI app (`app.py`) and static pages (`static/`)
- `workflows/build_workflows.py` – the single source of all workflows. Change workflows there and regenerate the JSON
  files (`python3 workflows/build_workflows.py http://127.0.0.1:8188 workflows`); never edit the JSON by hand.
- `comfyui/` – ComfyUI image and own ComfyUI extensions (GPL-3.0)

## Tests

```bash
webapp/run_tests.sh   # needs only Docker
```

ComfyUI is simulated by `webapp/tests/fake_comfy.py`, the workflows are built with the test extension
`webapp/tests/extras/`. Please add a test in `webapp/tests/` for new behaviour.

## Guidelines

- Small, self-contained changes – rather several pull requests than one big one.
- Keep the style of the existing code: short comments that explain *why*. The UI and most comments are in German;
  English is fine for new comments.
- Commit messages: one short line saying what changed, details in the body if needed.
- No private data in code, tests or issues: no API keys, private images, IP addresses or host names. Personal
  additions (your own LoRAs, workflows) belong in an extension (`extras/`), not in the repository.
- Line endings LF (enforced by `.gitattributes`).

By contributing you agree that your contribution is licensed under the license of the respective part
([MIT](LICENSE), or [GPL-3.0](comfyui/custom_nodes/LICENSE) for `comfyui/custom_nodes/`).

## Deutsch

Beiträge auf Deutsch sind willkommen – Issues und Pull Requests dürfen gern deutsch sein. Die ausführliche Doku
(`MODELLE.md`, `docs/`) gibt es bisher nur auf Deutsch.
