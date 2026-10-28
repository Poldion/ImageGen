# Bilder-Web-App (schlanke Oberflaeche vor ComfyUI). Gebaut ueber imagegen/docker-compose.yml,
# Build-Kontext ist der Ordner imagegen/ (die Workflows baut die App beim Start selbst, start.sh).
FROM python:3.12-slim
LABEL org.opencontainers.image.title="ImageGen" \
      org.opencontainers.image.source="https://github.com/Poldion/ImageGen" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY webapp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY workflows/build_workflows.py ./build_workflows.py
COPY workflows/assets/*.png ./assets/
COPY workflows/platzhalter.png ./assets/bild_hier_hochladen.png
COPY webapp/app.py webapp/version.py webapp/start.sh ./
COPY webapp/static ./static

ENV COMFYUI_URL=http://comfyui:8188 \
    OUTPUT_DIR=/output \
    INPUT_DIR=/input \
    WEBAPP_DB=/data/webapp.db \
    WEBAPP_PORT=8190 \
    PYTHONUNBUFFERED=1
EXPOSE 8190

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8190/icon.svg', timeout=4)"]

CMD ["sh", "/app/start.sh"]
