# backend/services/lama_service.py

"""
Optional "Redraw (AI)" for the editor's clean tools: big-LaMa (Suvorov et al., "Resolution-robust Large Mask
Inpainting with Fourier Convolutions", WACV 2022; Apache-2.0) redraws the artwork under a user-drawn rectangle.

Why optional: the model is a 196 MB download (205,669,692 bytes - the UI shows binary MB like Windows Explorer) and
only helps with text over artwork / gradients. Tested on real pages (2026-11-08): over artwork it clearly beats the
flat fill / TELEA (the strokes and the gradient are redrawn), but in white boxes and on plain paper it invents texture
and ghosts of nearby text - so it is never used automatically, only on a rectangle the user marks (page JSON cleanups,
mode "redraw"). Nothing is downloaded until the user presses Install in Settings > Add-ons; Remove there (or Settings >
Storage & uninstall) deletes it again.

The download is read in small chunks so the progress moves even on a slow line, can be cancelled, and gives up when
no data arrives for STALL_SECONDS (a connection that hangs used to look like "0 MB" forever).

The model file is the TorchScript export used by IOPaint (lama-cleaner); its MD5 is checked after the download and a
file that does not match is deleted. Runs on the CPU (about 1-4 s for a rectangle with its surroundings); torch is
already installed for the correction memory.
"""

import hashlib
import os
import threading
import time
import urllib.request

import numpy as np

from core.config import APP_ROOT_DIR

MODEL_URL = "https://github.com/Sanster/models/releases/download/add_big_lama/big-lama.pt"
MODEL_MD5 = "e3aa4aaa15225a33ec84f9f4bc47e500"
MODEL_BYTES = 205_669_692
CONTEXT_SHARE = 0.5          # surroundings given to the model on every side, as a share of the rectangle's longer side ...
CONTEXT_MIN_PX = 48          # ... but at least this many pixels
CROP_MAX_SIDE = 1600         # bigger crops are scaled down for the model (CPU time grows with the area)
CHUNK_BYTES = 64 * 1024      # progress step of the download
STALL_SECONDS = 30           # no data for this long = the download failed (socket timeout per read)


class _Cancelled(Exception):
    """The user pressed Cancel while the model was downloading."""


_STATE = {"downloading": False, "done": 0, "total": MODEL_BYTES, "error": None, "cancel": False, "started": 0.0}
_LOCK = threading.Lock()     # download state + model load
_RUN_LOCK = threading.Lock()  # one forward pass at a time (memory)
_MODEL = None


def model_dir() -> str:
    """<app root>/models/lama - next to the database and the projects, listed in Settings > Storage & uninstall."""
    return os.path.join(APP_ROOT_DIR, "models", "lama")


def model_path() -> str:
    return os.path.join(model_dir(), "big-lama.pt")


def installed() -> bool:
    """The model file is there with the expected size (the MD5 was checked when it was downloaded)."""
    try:
        return os.path.getsize(model_path()) == MODEL_BYTES
    except OSError:
        return False


def status() -> dict:
    with _LOCK:
        return {"installed": installed(), "downloading": _STATE["downloading"], "done": _STATE["done"],
                "total": _STATE["total"], "error": _STATE["error"], "bytes": MODEL_BYTES,
                "cancelling": _STATE["downloading"] and _STATE["cancel"],
                "seconds": round(time.time() - _STATE["started"], 1) if _STATE["downloading"] else None}


def start_install() -> dict:
    """Start the download in the background (no-op when installed or already downloading). -> status()."""
    with _LOCK:
        if not installed() and not _STATE["downloading"]:
            _STATE.update(downloading=True, done=0, total=MODEL_BYTES, error=None, cancel=False, started=time.time())
            threading.Thread(target=_download, name="lama-download", daemon=True).start()
    return status()


def cancel_install() -> dict:
    """Ask a running download to stop (the thread checks between chunks and deletes the partial file). -> status()."""
    with _LOCK:
        if _STATE["downloading"]:
            _STATE["cancel"] = True
    return status()


def _download() -> None:
    os.makedirs(model_dir(), exist_ok=True)
    part = model_path() + ".part"
    md5 = hashlib.md5()
    print(f"[LAMA] downloading {MODEL_URL}")
    try:
        req = urllib.request.Request(MODEL_URL, headers={"User-Agent": "OpenKeyTranslate"})
        with urllib.request.urlopen(req, timeout=STALL_SECONDS) as resp, open(part, "wb") as out:
            total = int(resp.headers.get("Content-Length") or MODEL_BYTES)
            with _LOCK:
                _STATE["total"] = total
            while True:
                with _LOCK:
                    if _STATE["cancel"]:
                        raise _Cancelled()
                chunk = resp.read(CHUNK_BYTES)
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                with _LOCK:
                    _STATE["done"] += len(chunk)
        if md5.hexdigest() != MODEL_MD5:
            raise ValueError("the downloaded file is damaged or not the expected model (checksum mismatch) - deleted, try again")
        os.replace(part, model_path())
        print(f"[LAMA] model installed: {model_path()}")
        with _LOCK:
            _STATE["error"] = None
    except _Cancelled:
        print("[LAMA] download cancelled")
        with _LOCK:
            _STATE["error"] = None
        _remove_quietly(part)
    except Exception as e:                      # noqa: BLE001 - any failure is shown in Settings, nothing half-installed stays
        print(f"[LAMA] download failed: {e}")
        if isinstance(e, TimeoutError) or "timed out" in str(e):
            message = f"no data from the server for {STALL_SECONDS} s (connection lost or blocked) - try again"
        elif isinstance(e, ValueError):
            message = str(e)
        else:
            message = f"{type(e).__name__}: {e}"
        with _LOCK:
            _STATE["error"] = message
        _remove_quietly(part)
    finally:
        with _LOCK:
            _STATE["downloading"] = False
            _STATE["cancel"] = False


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def unload() -> None:
    """Forget the loaded model (Storage & uninstall deleted the file)."""
    global _MODEL
    with _LOCK:
        _MODEL = None


def _model():
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            if not installed():
                raise RuntimeError("The AI redraw model is not installed (Settings > Add-ons).")
            import torch                        # imported here: the app starts without it being needed
            t0 = time.time()
            _MODEL = torch.jit.load(model_path(), map_location="cpu").eval()
            print(f"[LAMA] model loaded in {time.time() - t0:.1f} s")
        return _MODEL


def _run(crop_bgr: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """big-LaMa on one crop: RGB 0..1, binary mask, both padded to a multiple of 8 (as IOPaint does)."""
    import cv2
    import torch
    h, w = mask.shape
    ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
    rgb = np.pad(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB), ((0, ph), (0, pw), (0, 0)), mode="symmetric")
    m = np.pad((mask > 0).astype(np.float32), ((0, ph), (0, pw)), mode="symmetric")
    x = torch.from_numpy(rgb.transpose(2, 0, 1).astype(np.float32) / 255.0).unsqueeze(0)
    mt = torch.from_numpy(m).unsqueeze(0).unsqueeze(0)
    model = _model()
    with _RUN_LOCK, torch.no_grad():
        y = model(x, mt)[0].permute(1, 2, 0).numpy()
    y = np.clip(y * 255, 0, 255).astype(np.uint8)[:h, :w]
    return cv2.cvtColor(y, cv2.COLOR_RGB2BGR)


def redraw(image: np.ndarray, box, shape: np.ndarray | None = None) -> np.ndarray:
    """
    Redraw the rectangle `box` (x1, y1, x2, y2) of `image` (BGR, changed in place and returned) from its
    surroundings. The model sees the rectangle plus CONTEXT_SHARE of margin; only the rectangle is replaced - or only
    the True pixels of `shape` (a bool mask of the rectangle, e.g. the ellipse of utils.inpainting._shape_mask).
    """
    import cv2
    ih, iw = image.shape[:2]
    x1, y1, x2, y2 = (int(round(v)) for v in box)
    x1, x2 = sorted((max(0, x1), min(iw, x2)))
    y1, y2 = sorted((max(0, y1), min(ih, y2)))
    if x2 - x1 < 2 or y2 - y1 < 2:
        return image
    pad = max(CONTEXT_MIN_PX, int(CONTEXT_SHARE * max(x2 - x1, y2 - y1)))
    cx1, cy1, cx2, cy2 = max(0, x1 - pad), max(0, y1 - pad), min(iw, x2 + pad), min(ih, y2 + pad)
    crop = image[cy1:cy2, cx1:cx2]
    mask = np.zeros(crop.shape[:2], np.uint8)
    if shape is not None and shape.shape == (y2 - y1, x2 - x1):
        mask[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1][shape] = 255
    else:
        mask[y1 - cy1:y2 - cy1, x1 - cx1:x2 - cx1] = 255
    scale = min(1.0, CROP_MAX_SIDE / max(crop.shape[:2]))
    if scale < 1.0:
        small = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        filled = cv2.resize(_run(small, small_mask), (crop.shape[1], crop.shape[0]), interpolation=cv2.INTER_CUBIC)
    else:
        filled = _run(crop, mask)
    region = mask > 0
    crop[region] = filled[region]
    return image
