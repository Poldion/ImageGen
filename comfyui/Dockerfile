# ComfyUI fuer AMD-GPUs mit ROCm (getestet: Strix Halo, gfx1151), nur aus offiziellen Quellen:
#   - AMDs rocm/pytorch (ROCm + PyTorch fuer AMD-GPUs)
#   - ComfyUI von GitHub (Comfy-Org), feste Version
#
# torch/torchvision/torchaudio kommen aus dem Basis-Image und werden NICHT durch pip
# ersetzt -- normale PyTorch-Pakete enthalten keine gfx1151-Kernel und rechnen dann
# unbemerkt auf der CPU.
ARG BASE_IMAGE=rocm/pytorch:rocm7.2.4_ubuntu24.04_py3.12_pytorch_release_2.10.0
FROM ${BASE_IMAGE}
LABEL org.opencontainers.image.title="ImageGen ComfyUI" \
      org.opencontainers.image.source="https://github.com/Poldion/ImageGen" \
      org.opencontainers.image.licenses="GPL-3.0-only"

ARG COMFYUI_VERSION=v0.42.1

RUN git clone --depth 1 -b "${COMFYUI_VERSION}" https://github.com/Comfy-Org/ComfyUI.git /opt/ComfyUI

# ComfyUI-Abhaengigkeiten ohne torch*, und die vorhandenen torch-Versionen festnageln
RUN cd /opt/ComfyUI \
 && pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' > /tmp/torch-pins.txt \
 && grep -viE '^(torch|torchvision|torchaudio)\s*([<>=~!].*)?$' requirements.txt > /tmp/requirements.txt \
 && pip install --no-cache-dir -r /tmp/requirements.txt -c /tmp/torch-pins.txt \
 && pip freeze | grep -iE '^(torch|torchvision|torchaudio)==' | diff - /tmp/torch-pins.txt \
 && rm -f /tmp/requirements.txt

# Laeuft als Host-User (compose: user: PUID:PGID); Caches (MIOpen, Triton) unter $HOME
RUN chmod -R a+rwX /opt/ComfyUI && mkdir -p /cache && chmod 777 /cache
ENV HOME=/cache \
    PYTHONUNBUFFERED=1

WORKDIR /opt/ComfyUI
EXPOSE 8188

# --disable-mmap: Memory-Mapping grosser Modelle ist unter ROCm auf gfx1151 sehr langsam
# Eigene Erweiterungen (proxy_memory, conv3d_patch_embed) ins Image; beim Start in den gemounteten
# custom_nodes-Ordner kopieren (aktualisiert sie bei jedem neuen Image, andere Erweiterungen bleiben)
COPY custom_nodes /opt/builtin_nodes
CMD ["sh", "-c", "for d in /opt/builtin_nodes/*/; do cp -r \"$d\" /opt/ComfyUI/custom_nodes/ 2>/dev/null || true; done; exec python main.py --listen 0.0.0.0 --port 8188 --disable-mmap"]
