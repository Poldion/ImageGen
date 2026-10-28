"""
Patch-Embedding der Qwen-VL-Vision-Encoder ohne Conv3d (ROCm/gfx1151).

Kernel = Stride, also ist die Faltung nichts anderes als eine Matrixmultiplikation
ueber die flachgelegten Patches. Unter ROCm sucht MIOpen fuer Conv3d bei jeder neuen
Eingabegroesse den besten Kernel (~125 s), auch mit MIOPEN_FIND_MODE=FAST. Das traf
jedes "Bild bearbeiten" mit Qwen-Image 2.1 (Text-Encoder Qwen3-VL). Gleiches Ergebnis
(Abweichung ~fp16-Rundung), die Matrixmultiplikation dauert ~0,15 s.

Keine Knoten, nur der Patch beim Laden. Abschalten: Ordner aus custom_nodes entfernen.
"""
import logging

import torch.nn.functional as F


def _patch(cls):
    def forward(self, x):
        w = self.proj.weight
        b = self.proj.bias
        w = w.to(device=x.device, dtype=x.dtype).reshape(w.shape[0], -1)
        if b is not None:
            b = b.to(device=x.device, dtype=x.dtype)
        x = x.reshape(-1, w.shape[1])
        return F.linear(x, w, b).view(-1, self.embed_dim)
    cls.forward = forward


patched = []
try:
    from comfy.text_encoders.qwen_vl import VisionPatchEmbed
    _patch(VisionPatchEmbed)
    patched.append("qwen_vl.VisionPatchEmbed")
except Exception as e:  # andere ComfyUI-Version -> unveraendert lassen
    logging.warning("conv3d_patch_embed: qwen_vl nicht gepatcht: %s", e)
try:
    from comfy.text_encoders.qwen35 import Qwen35VisionPatchEmbed
    _patch(Qwen35VisionPatchEmbed)
    patched.append("qwen35.Qwen35VisionPatchEmbed")
except Exception as e:
    logging.warning("conv3d_patch_embed: qwen35 nicht gepatcht: %s", e)
logging.info("conv3d_patch_embed: Conv3d -> Matmul fuer %s", ", ".join(patched) or "nichts")

NODE_CLASS_MAPPINGS = {}
