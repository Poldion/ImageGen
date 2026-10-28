"""Token-Vorlagen (1024x1024): Rahmen-Ring und runde Ausschnittmaske, je mit Alpha. 4x ueberabgetastet.
Aufruf (im ComfyUI-Container, braucht numpy + Pillow): python make_token_assets.py /opt/ComfyUI/input"""
import math
import sys

import numpy as np
from PIL import Image

OUT = sys.argv[1]
S, K = 1024, 4
N = S * K
y, x = np.mgrid[0:N, 0:N].astype(np.float32)
cx = cy = (N - 1) / 2
r = np.hypot(x - cx, y - cy) / K                    # Radius in Zielpixeln
ang = np.arctan2(y - cy, x - cx)

R_OUT, WIDTH = 506.0, 30.0                          # Aussenradius, Ringbreite
R_IN = R_OUT - WIDTH

# Ausschnitt: alles innerhalb des Rings sichtbar
cut = (r <= R_OUT).astype(np.float32)

# Ring: Metall (Gold/Bronze) mit Woelbung quer zum Ring und Lichtkante oben links
ring = ((r >= R_IN) & (r <= R_OUT)).astype(np.float32)
t = np.clip((r - R_IN) / WIDTH, 0, 1)               # 0 innen .. 1 aussen
bevel = np.clip(np.sin(t * math.pi), 0, 1) ** 0.6     # Wulst
light = 0.55 + 0.45 * np.cos(ang + math.pi * 0.75)  # Licht von oben links
shade = np.clip(0.25 + 0.75 * bevel * (0.55 + 0.45 * light), 0, 1.15)
edge = ((np.abs(r - R_IN) < 1.6) | (np.abs(r - R_OUT) < 1.6)).astype(np.float32)   # dunkle Kanten
gold = np.array([212, 166, 74], np.float32)
rgb = np.clip(gold[None, None, :] * shade[..., None], 0, 255)
rgb = rgb * (1 - 0.6 * edge[..., None])

frame = np.dstack([rgb, ring[..., None] * 255]).astype(np.uint8)
Image.fromarray(frame, "RGBA").resize((S, S), Image.LANCZOS).save(f"{OUT}/token_rahmen.png")
mask = np.dstack([np.full((N, N, 3), 255, np.float32), cut[..., None] * 255]).astype(np.uint8)
Image.fromarray(mask, "RGBA").resize((S, S), Image.LANCZOS).save(f"{OUT}/token_maske.png")
print("ok")
