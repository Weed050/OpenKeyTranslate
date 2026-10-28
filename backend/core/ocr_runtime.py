
# backend/core/ocr_runtime.py

"""
OCR runtime selection (CPU / GPU) from settings.json instead of hand-editing services/ocr_pipeline.py.

Before: switching between the CPU and GPU build meant commenting/uncommenting environment variables and changing
device='gpu' in the source (and a machine without a GPU crashed on import). Now:

  ocr_device       "auto" (default) | "cpu" | "gpu"      auto = GPU when this Paddle build + machine have CUDA, else CPU.
                                                         "gpu" on a machine without CUDA falls back to CPU with a warning
                                                         (health banner) instead of crashing the backend.
  ocr_cpu_threads  0 = library default, N = OMP_NUM_THREADS / cpu_threads
  ocr_mkldnn       false (default). oneDNN acceleration; on some CPU/Paddle combinations it fails inside the executor
                   ("ConvertPirAttribute2RuntimeAttribute not support ..."), so it is OFF unless you switch it on.
  ocr_env_flags    {} - extra environment variables applied BEFORE paddle is imported, e.g. {"FLAGS_enable_pir_api": "0"}.
                   An escape hatch for Paddle runtime problems that needs no code change.
  models_dir       "" = libraries' default caches (~/.cache/huggingface, ~/.paddlex). A folder = both caches live there
                   (portable, included by "Export all data").

apply_env() MUST run before `import paddle` / `import paddleocr`; services/ocr_pipeline.py calls it first.
"""

import os

from core.config import get_setting, APP_ROOT_DIR

# Filled by resolve_runtime(); shown by GET /system/health and in the startup log.
RUNTIME: dict = {"requested": None, "device": None, "reason": "", "mkldnn": False, "threads": 0}


def models_root() -> str | None:
    """Folder that holds the model caches, or None when the libraries' defaults are used."""
    configured = (get_setting("models_dir", "") or "").strip()
    if not configured:
        return None
    return configured if os.path.isabs(configured) else os.path.join(APP_ROOT_DIR, configured)


def cache_dirs() -> dict[str, str]:
    """{label: folder} of the model caches that are ACTIVE right now (used by export/import)."""
    root = models_root()
    if root:
        return {"huggingface": os.path.join(root, "huggingface"), "paddlex": os.path.join(root, "paddlex")}
    home = os.path.expanduser("~")
    return {
        "huggingface": os.environ.get("HF_HOME") or os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.join(home, ".cache")), "huggingface"),
        "paddlex": os.environ.get("PADDLE_PDX_CACHE_HOME") or os.path.join(home, ".paddlex"),
    }


def apply_env() -> None:
    """Set environment variables from settings. Idempotent. Already-set variables of the user's shell win, except ocr_env_flags."""
    root = models_root()
    if root:
        os.makedirs(root, exist_ok=True)
        os.environ.setdefault("HF_HOME", os.path.join(root, "huggingface"))
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", os.path.join(root, "paddlex"))

    mkldnn = bool(get_setting("ocr_mkldnn", False))
    os.environ.setdefault("FLAGS_use_mkldnn", "1" if mkldnn else "0")

    threads = int(get_setting("ocr_cpu_threads", 0) or 0)
    if threads > 0:
        os.environ.setdefault("OMP_NUM_THREADS", str(threads))

    if (get_setting("ocr_device", "auto") or "auto") == "cpu":
        # what the CPU build instructions asked to uncomment by hand
        os.environ.setdefault("FLAGS_allocator_strategy", "naive_best_fit")
        os.environ.setdefault("FLAGS_fraction_of_gpu_memory_to_use", "0")
        os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    for key, value in (get_setting("ocr_env_flags", {}) or {}).items():
        os.environ[str(key)] = str(value)       # explicit user flags always win


def _cuda_available() -> bool:
    try:
        import paddle
        return bool(paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
    except Exception:
        return False


def resolve_runtime() -> dict:
    """Decide the device and the PaddleOCR keyword arguments for it."""
    requested = (get_setting("ocr_device", "auto") or "auto").lower()
    if requested not in ("auto", "cpu", "gpu"):
        requested = "auto"

    if requested == "cpu":
        device, reason = "cpu", "forced by ocr_device=cpu"
    elif _cuda_available():
        device, reason = "gpu", "CUDA available" if requested == "auto" else "ocr_device=gpu"
    elif requested == "gpu":
        device, reason = "cpu", "ocr_device=gpu but this machine / Paddle build has no CUDA - using CPU"
    else:
        device, reason = "cpu", "no CUDA available"

    threads = int(get_setting("ocr_cpu_threads", 0) or 0)
    RUNTIME.update(requested=requested, device=device, reason=reason,
                   mkldnn=bool(get_setting("ocr_mkldnn", False)) and device == "cpu", threads=threads)
    return RUNTIME


def paddle_kwargs() -> dict:
    """Keyword arguments for PaddleOCR(...) derived from the resolved runtime."""
    rt = resolve_runtime()
    kwargs = dict(
        lang="en",
        device=rt["device"],
        use_textline_orientation=True,
        enable_mkldnn=rt["mkldnn"],        # never on the GPU build
        use_doc_unwarping=False,           # flat digital scans: the unwarping network is not needed
    )
    if rt["device"] == "cpu" and rt["threads"] > 0:
        kwargs["cpu_threads"] = rt["threads"]
    print(f"[OCR] device={rt['device']} ({rt['reason']}), mkldnn={rt['mkldnn']}, threads={rt['threads'] or 'default'}")
    return kwargs
