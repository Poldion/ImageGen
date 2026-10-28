"""
Speicher-Schnittstelle fuer den Ollama-Proxy (Strix Halo, geteilter RAM).

ComfyUI meldet ueber /system_stats nur, was torch auf der GPU haelt. Mit --disable-mmap
und dem RAM-Pressure-Cache (ComfyUI >= 0.42) liegen die Modelle aber zusaetzlich im
normalen RAM des Prozesses -- gemessen 22 GB, waehrend /system_stats 0,2 GB meldete.
Und das eingebaute /free leert zwar den Cache, gibt den Speicher aber nicht ans System
zurueck (glibc behaelt freigegebene Bloecke, torch den Pinned-Host-Cache).

Routen:
  GET  /proxy/memory  {"rss", "gpu", "pinned", "loaded_models", "busy"}
  POST /proxy/free    Modelle entladen + Cache leeren (wie /free), danach gc,
                      torch-Host-Cache leeren und malloc_trim -> {"rss_before", "rss_after"}

Keine Knoten. Liegt im Repo unter imagegen/comfyui/custom_nodes/ und muss nach
~/imagegen/custom_nodes/ kopiert werden (Volume), danach ComfyUI neu starten.
"""
import asyncio
import ctypes
import gc
import logging
import time

import torch
from aiohttp import web

import comfy.memory_management as cmem
import comfy.model_management as mm
import comfy.system_memory as sm
from server import PromptServer

FLAG_TIMEOUT = 60          # so lange auf den Worker warten (Entladen von ~30 GB dauert)
TRIM_ROUNDS = 20           # Aufraeumrunden im Sekundentakt, bis Worker-GC + malloc_trim durch sind
TRIM_MIN_GAIN = 256 * 2**20
# Auf dem Strix Halo kommen GTT (GPU) und Prozess-RAM aus demselben physischen Pool.
# Vor dem Laden eines Modells auf die GPU muss im echten System-RAM (MemAvailable)
# mindestens Modellgroesse + dieser Puffer frei sein -- sonst erst alte GPU-Modelle
# und RAM-Cache-Eintraege abwerfen.
UMA_SAFETY_HEADROOM = 6 * 2**30
# glibc hebt M_MMAP_THRESHOLD dynamisch bis 32 MB (in Threads 64-MB-Subheaps) an.
# Wenn ROCm hipMemcpy Tensoren > 1 MB aus einem solchen 64-MB-glibc-Arena-Subheap auf
# die GPU kopiert, pinnt rocclr die Quelladresse per KFD-userptr (hsa_amd_memory_lock),
# bis die VMA per munmap() verschwindet. In einer glibc-Arena ruft free() aber kein
# munmap() auf und malloc_trim(0) macht nur madvise(MADV_DONTNEED) -> der amdkfd-Kernel-
# Worker (restore_process_worker) holt alle freigegebenen Seiten binnen 0,6 s per
# get_user_pages zurueck in den RAM!
# Mit mallopt(M_MMAP_THRESHOLD, 128 KB) deaktivieren wir die dynamische Anhebung: jeder
# Tensor >= 128 KB bekommt ein eigenes mmap() und wird bei free() sofort per munmap()
# samt KFD-userptr-Lock echt an den Kernel zurueckgegeben.
M_MMAP_THRESHOLD = -3
MMAP_THRESHOLD_BYTES = 128 * 1024

try:
    _LIBC = ctypes.CDLL("libc.so.6")
    _LIBC.mallopt(M_MMAP_THRESHOLD, MMAP_THRESHOLD_BYTES)
    logging.info("proxy_memory: glibc M_MMAP_THRESHOLD fest auf %d KB gesetzt (ROCm userptr-Fix)",
                 MMAP_THRESHOLD_BYTES // 1024)
except Exception as _e:
    _LIBC = None
    logging.debug("proxy_memory: mallopt fehlgeschlagen: %s", _e)


def _rss() -> int | None:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return None


def _gpu() -> int:
    try:
        return int(torch.cuda.memory_reserved()) if torch.cuda.is_available() else 0
    except Exception:
        return 0


def _busy(q) -> bool:
    try:
        running, pending = q.get_current_queue_volatile()
    except Exception:
        try:
            running, pending = q.get_current_queue()
        except Exception:
            return False
    return bool(running or pending)


def _trim():
    """Was nach dem Entladen noch im Prozess haengt, ans System zurueckgeben."""
    gc.collect()
    for name in ("_host_emptyCache", "_cuda_hostEmptyCache"):
        fn = getattr(torch._C, name, None)
        if fn:
            try:
                fn()
            except Exception as e:
                logging.debug("proxy_memory: %s: %s", name, e)
    try:
        mm.soft_empty_cache()
    except Exception:
        pass
    try:
        if _LIBC is not None:
            _LIBC.malloc_trim(0)
    except Exception as e:
        logging.debug("proxy_memory: malloc_trim: %s", e)


# ---- Unified-Memory-Schutz (Strix Halo: GTT + Prozess-RAM teilen sich 121 GB) ----
#
# Problem: torch.cuda.mem_get_info() meldet (gtt_total - gtt_used), weiss aber nichts von
# CPU-Prozess-RAM (z.B. 28 GB ComfyUI-Host-Cache mit --disable-mmap + 4 GB llama-server).
# Wenn qwen3-coder-next (55 GB) geladen ist und Wan 2.2 nach dem 1. Experten (high_noise,
# 14 GB GPU + 14 GB RAM) den 2. Experten (low_noise, 14 GB) laedt, meldet mem_get_info()
# noch 25 GB frei, obwohl MemAvailable nur noch ~14 GB betraegt -> ComfyUI behaelt den
# 1. Experten auf der GPU UND im RAM-Cache und laeuft in einen HIP-OOM.
_orig_get_free_memory = mm.get_free_memory
_orig_free_memory = mm.free_memory


def _uma_get_free_memory(dev=None, torch_free_too=False):
    res = _orig_get_free_memory(dev, torch_free_too=torch_free_too)
    try:
        ram_avail = sm.virtual_memory_available()
    except Exception:
        return res
    if torch_free_too:
        total, torch_free = res
        return (min(total, ram_avail + torch_free), torch_free)
    return min(res, ram_avail)


def _uma_free_memory(memory_required, device, keep_loaded=[], for_dynamic=False,
                     pins_required=0, ram_required=0):
    res = _orig_free_memory(memory_required, device, keep_loaded=keep_loaded,
                            for_dynamic=for_dynamic, pins_required=pins_required,
                            ram_required=ram_required)
    try:
        target = int(memory_required) + UMA_SAFETY_HEADROOM
        if sm.virtual_memory_available() < target:
            cmem.extra_ram_release(target, free_active=True)
            _trim()
    except Exception as e:
        logging.debug("proxy_memory: _uma_free_memory: %s", e)
    return res


mm.get_free_memory = _uma_get_free_memory
mm.free_memory = _uma_free_memory
logging.info("proxy_memory: Unified-Memory-Patch aktiv (get_free_memory gedeckelt auf MemAvailable)")


routes = PromptServer.instance.routes


@routes.get("/proxy/memory")
async def proxy_memory(request):
    q = PromptServer.instance.prompt_queue
    return web.json_response({
        "rss": _rss(),
        "gpu": _gpu(),
        "pinned": getattr(mm, "TOTAL_PINNED_MEMORY", None),
        "loaded_models": len(getattr(mm, "current_loaded_models", []) or []),
        "busy": _busy(q),
    })


@routes.post("/proxy/free")
async def proxy_free(request):
    q = PromptServer.instance.prompt_queue
    before = _rss()
    if _busy(q):
        return web.json_response({"error": "busy", "rss_before": before}, status=409)
    # wie das eingebaute /free: der Worker-Thread entlaedt und leert den Cache
    q.set_flag("unload_models", True)
    q.set_flag("free_memory", True)
    # Der Worker entlaedt zuerst: die Modelle wandern dabei von der GPU in den RAM (RSS
    # steigt kurz um ihre volle Groesse!), dann leert e.reset() den Node-Cache, und erst
    # nach gc_collect_interval (10 s) sammelt der Worker die Objekte ein. Solange der RSS
    # noch steigt oder noch nicht merklich gefallen ist, duerfen wir nicht abbrechen.
    deadline = time.monotonic() + FLAG_TIMEOUT
    while time.monotonic() < deadline:
        with q.mutex:
            pending = bool(q.flags)
        if not pending and not getattr(mm, "current_loaded_models", None):
            break
        await asyncio.sleep(0.25)
    loop = asyncio.get_running_loop()
    peak = before or 0
    prev = peak
    stable = 0
    for i in range(TRIM_ROUNDS):
        await asyncio.sleep(1)
        await loop.run_in_executor(None, _trim)
        now = _rss() or 0
        if now > peak:
            peak = now
            stable = 0
        elif peak - now >= TRIM_MIN_GAIN and abs(prev - now) < TRIM_MIN_GAIN:
            stable += 1
            if stable >= 2:
                break
        elif i >= 12 and abs(prev - now) < TRIM_MIN_GAIN:
            break
        prev = now
    after = _rss()
    logging.info("proxy_memory: freigegeben, RAM %.1f -> %.1f GB",
                 (before or 0) / 2**30, (after or 0) / 2**30)
    return web.json_response({"rss_before": before, "rss_after": after})


NODE_CLASS_MAPPINGS = {}
