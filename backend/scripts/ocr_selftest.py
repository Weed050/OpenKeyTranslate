# backend/scripts/ocr_selftest.py

"""
OCR self-test: finds out WHERE the OCR stack dies on a machine, step by step, without taking the backend down.

Why: a crash inside Paddle / OpenCV / OpenMP is native (Windows "exit code -1073741819" = 0xC0000005 access
violation). Python gets no exception and the backend just disappears, so "the OCR does not work on my laptop" says
nothing about the cause. Here every step runs in its OWN child process with faulthandler on. When a child dies, the
step and the native fault are reported and the next steps still run.

Run it with the backend STOPPED (the backend holds its own copy of the OCR model in memory):

    cd backend
    python scripts/ocr_selftest.py                  # all steps, 2 CPU threads (gentle on a laptop)
    python scripts/ocr_selftest.py --threads 0      # library default threads
    python scripts/ocr_selftest.py --image page.png # also read a real page
    python scripts/ocr_selftest.py --quick          # only versions, OpenCV and Paddle - no OCR model

Steps: versions -> OpenCV -> Paddle tensor maths -> torch + Paddle in one process (two OpenMP runtimes) -> the OCR
model on a small image (mobile, then server detection) -> a page-sized image. The report goes to the console and to
<app root>/logs/ocr_selftest.json; Settings > OCR engine shows the last one.
Nothing in the user's data or settings is changed.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from importlib import metadata

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

PACKAGES = ["numpy", "opencv-python", "opencv-contrib-python", "opencv-python-headless", "paddlepaddle",
            "paddlepaddle-gpu", "paddleocr", "paddlex", "torch", "sentence-transformers", "Pillow"]
STEP_TIMEOUT = 900          # seconds: the first OCR run may download the models


# ------------------------------------------------------------------ helpers

def _version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _cpu_name() -> str:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
                return winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        except OSError:
            pass
    return platform.processor() or platform.machine()


def _ram_gb() -> tuple[float | None, float | None]:
    try:
        import psutil
        vm = psutil.virtual_memory()
        return round(vm.total / 2**30, 1), round(vm.available / 2**30, 1)
    except Exception:
        return None, None


def _test_image(height: int):
    """A white page with black capitals in a few 'bubbles' - enough for detection + recognition to have work."""
    import numpy as np
    import cv2
    width = max(600, int(height * 0.7))
    img = np.full((height, width, 3), 255, np.uint8)
    lines = ["HELLO, THIS IS A TEST", "OF THE OCR ENGINE.", "TODAY'S ITINERARY", "STARTS AT 9:55"]
    y = 80
    while y < height - 60:
        for i, text in enumerate(lines):
            cv2.putText(img, text, (40, y + i * 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2, cv2.LINE_AA)
        y += 320
    return img


# ------------------------------------------------------------------ steps (each runs in a child process)

def step_opencv(args):
    import numpy as np
    import cv2
    img = _test_image(400)
    blur = cv2.GaussianBlur(img, (5, 5), 0)
    gray = cv2.cvtColor(blur, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    inpainted = cv2.inpaint(img, mask, 3, cv2.INPAINT_TELEA)
    return f"numpy {np.__version__}, cv2 {cv2.__version__} ({os.path.dirname(cv2.__file__)}), inpaint {inpainted.shape}"


def step_paddle(args):
    import paddle
    paddle.set_device("cpu")
    a = paddle.rand([256, 256])
    b = paddle.matmul(a, a)
    conv = paddle.nn.Conv2D(3, 8, 3)
    out = conv(paddle.rand([1, 3, 64, 64]))
    cuda = paddle.device.is_compiled_with_cuda()
    return f"paddle {paddle.__version__}, matmul {list(b.shape)}, conv {list(out.shape)}, compiled with CUDA: {cuda}"


def step_torch_paddle(args):
    import torch
    t = torch.rand(256, 256) @ torch.rand(256, 256)
    import paddle
    paddle.set_device("cpu")
    p = paddle.matmul(paddle.rand([256, 256]), paddle.rand([256, 256]))
    return f"torch {torch.__version__} {tuple(t.shape)} + paddle {list(p.shape)} in one process: OK"


def _ocr(args, det_model: str):
    from core import ocr_runtime
    ocr_runtime.apply_env()
    os.environ.setdefault("DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR
    kwargs = ocr_runtime.paddle_kwargs()
    kwargs["device"] = "cpu" if args.cpu else kwargs["device"]
    if det_model:
        kwargs["text_detection_model_name"] = det_model
    if args.threads > 0:
        kwargs["cpu_threads"] = args.threads
    t0 = time.time()
    engine = PaddleOCR(**kwargs)
    load_s = time.time() - t0
    return engine, load_s, kwargs


def _read(engine, image) -> tuple[list[str], float]:
    t0 = time.time()
    result = engine.predict(image, use_doc_orientation_classify=False)
    texts = []
    for r in result:
        texts += list(r.get("rec_texts", []) if hasattr(r, "get") else [])
    return texts, time.time() - t0


def step_ocr_mobile(args):
    engine, load_s, kw = _ocr(args, "PP-OCRv5_mobile_det")
    texts, s = _read(engine, _test_image(400))
    return f"device {kw['device']}, model load {load_s:.1f}s, read {s:.1f}s: {' | '.join(texts[:4])}"


def step_ocr_server(args):
    engine, load_s, kw = _ocr(args, "PP-OCRv5_server_det")
    texts, s = _read(engine, _test_image(400))
    return f"device {kw['device']}, model load {load_s:.1f}s, read {s:.1f}s: {' | '.join(texts[:4])}"


def step_ocr_page(args):
    import cv2
    engine, load_s, kw = _ocr(args, args.det)
    if args.image:
        image = cv2.imread(args.image)
        if image is None:
            raise RuntimeError(f"Can't read {args.image}")
    else:
        image = _test_image(1612)                     # the size of the first slice in the reported crash
    texts, s = _read(engine, image)
    return f"{kw['text_detection_model_name']}, image {image.shape[1]}x{image.shape[0]}, read {s:.1f}s, {len(texts)} lines"


STEPS = {
    "opencv": ("OpenCV + numpy", step_opencv),
    "paddle": ("Paddle tensor maths (CPU)", step_paddle),
    "torch_paddle": ("torch + Paddle in one process", step_torch_paddle),
    "ocr_mobile": ("OCR model, mobile detection, small image", step_ocr_mobile),
    "ocr_server": ("OCR model, server detection, small image", step_ocr_server),
    "ocr_page": ("OCR on a page-sized image, the model the app uses", step_ocr_page),
}


def _child(args):
    """Inside the child process: one step, faulthandler on, result as JSON on the last stdout line."""
    import faulthandler
    faulthandler.enable(all_threads=True)          # a native crash prints the Python stack to stderr
    if args.threads > 0:
        os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))
    _, fn = STEPS[args.step]
    try:
        detail = fn(args)
        print("@@RESULT " + json.dumps({"ok": True, "detail": detail}), flush=True)
    except Exception as e:                         # a Python error (not a crash): report it, the parent goes on
        print("@@RESULT " + json.dumps({"ok": False, "detail": f"{type(e).__name__}: {e}"}), flush=True)


def _run_step(name: str, args) -> dict:
    cmd = [sys.executable, os.path.abspath(__file__), "--step", name, "--threads", str(args.threads)]
    if args.det:
        cmd += ["--det", args.det]
    if args.image:
        cmd += ["--image", args.image]
    if args.cpu:
        cmd.append("--cpu")
    t0 = time.time()
    peak_mb = None
    timed_out = False
    # output goes to temp files, not pipes: Paddle prints a lot (model downloads) and a full pipe would block the child
    with tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as fout,             tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace") as ferr:
        env = dict(os.environ, **({"CUDA_VISIBLE_DEVICES": ""} if args.cpu else {}))   # --cpu: the app's own CPU path
        proc = subprocess.Popen(cmd, cwd=BACKEND_DIR, stdout=fout, stderr=ferr, env=env)
        try:
            import psutil
            ps = psutil.Process(proc.pid)
        except Exception:
            ps = None
        peak = 0
        while proc.poll() is None:
            if time.time() - t0 > STEP_TIMEOUT:
                proc.kill()
                timed_out = True
                break
            if ps is not None:
                try:            # a venv python.exe on Windows is a launcher: the real interpreter is its child
                    procs = [ps] + ps.children(recursive=True)
                    peak = max(peak, sum(c.memory_info().rss for c in procs))
                except Exception:
                    pass
            time.sleep(0.2)
        proc.wait()
        peak_mb = round(peak / 2**20) if peak else None
        fout.seek(0)
        ferr.seek(0)
        out, err = fout.read(), ferr.read()
    if timed_out:
        return {"step": name, "ok": False, "crash": False, "detail": f"timed out after {STEP_TIMEOUT}s",
                "seconds": round(time.time() - t0, 1), "peak_mb": peak_mb}
    result = next((json.loads(line[9:]) for line in reversed(out.splitlines()) if line.startswith("@@RESULT ")), None)
    entry = {"step": name, "seconds": round(time.time() - t0, 1), "peak_mb": peak_mb, "exit_code": proc.returncode}
    if result is None:                             # no result line: the process died in native code
        code = proc.returncode & 0xFFFFFFFF if proc.returncode is not None else None
        fault = [l for l in err.splitlines() if "fatal exception" in l.lower() or "fatal python error" in l.lower()]
        stack = [l.strip() for l in err.splitlines() if l.strip().startswith("File ")][:8]
        entry.update(ok=False, crash=True,
                     detail=f"process died, exit code {proc.returncode} (0x{code:08X})" if code is not None else "process died",
                     fault=fault[-1] if fault else None, stack=stack)
    else:
        entry.update(ok=result["ok"], crash=False, detail=result["detail"])
        if not result["ok"]:
            entry["stderr_tail"] = err.strip().splitlines()[-6:]
    return entry


def _advice(info: dict, steps: list[dict]) -> list[str]:
    tips = []
    v = info["packages"]
    if v.get("opencv-python") and v.get("opencv-contrib-python"):
        tips.append("Both opencv-python and opencv-contrib-python are installed - they overwrite the same cv2 module. "
                    "Keep ONE: pip uninstall -y opencv-python opencv-contrib-python && pip install opencv-contrib-python==4.10.0.84")
    if v.get("paddlepaddle") and v.get("paddlepaddle-gpu"):
        tips.append("Both paddlepaddle and paddlepaddle-gpu are installed - uninstall both and install the one for this machine.")
    by = {s["step"]: s for s in steps}
    failed = [s for s in steps if not s["ok"]]
    if not failed:
        tips.append("Every step passed. If the app still crashes, run with --image on the page that crashed.")
        return tips
    first = failed[0]["step"]
    if first == "opencv":
        tips.append("OpenCV / numpy itself fails: reinstall them (one opencv package, numpy from setup/requirements_cpu_only.txt).")
    elif first == "paddle":
        tips.append("Paddle fails before any OCR model: this Paddle build does not run on this CPU / Python. Install the pinned "
                    "version from setup/requirements_cpu_only.txt (the one tested with this project).")
    elif first == "torch_paddle":
        tips.append("torch and Paddle crash in ONE process (two OpenMP runtimes). Set ocr_device to \"cpu\" in Settings > OCR "
                    "engine (it sets KMP_DUPLICATE_LIB_OK) or install the CPU builds from setup/requirements_cpu_only.txt.")
    elif first in ("ocr_mobile", "ocr_server", "ocr_page"):
        if by.get("ocr_mobile", {}).get("ok") and not by.get("ocr_server", {}).get("ok", True):
            tips.append("The mobile detection model works and the server one does not: Settings > OCR engine > Detection model: mobile.")
        else:
            tips.append("The OCR model crashes while basic Paddle works. Try the pinned Paddle version from "
                        "setup/requirements_cpu_only.txt, fewer threads (--threads 1) and oneDNN off.")
        peak = max((s.get("peak_mb") or 0) for s in steps)
        total = info.get("ram_total_gb")
        if total and peak and peak / 1024 > total * 0.7:
            tips.append(f"The OCR used {peak} MB of {total} GB RAM - close the browser tabs / other programs, use the mobile model.")
    return tips


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--step", choices=list(STEPS), help=argparse.SUPPRESS)
    p.add_argument("--threads", type=int, default=2, help="CPU threads for Paddle (0 = library default). Default 2.")
    p.add_argument("--image", help="also OCR this image file (e.g. the page that crashed)")
    p.add_argument("--det", default=None, help="detection model for the page step (default: what the app uses, see ocr_det_model)")
    p.add_argument("--cpu", action="store_true", help="force CPU even when CUDA is available")
    p.add_argument("--quick", action="store_true", help="only versions, OpenCV and Paddle (no OCR model)")
    args = p.parse_args()
    if args.step:
        return _child(args)

    total, avail = _ram_gb()
    info = {
        "time": datetime.now().isoformat(timespec="seconds"),
        "python": sys.version.split()[0], "platform": platform.platform(), "cpu": _cpu_name(),
        "cores": os.cpu_count(), "ram_total_gb": total, "ram_free_gb": avail,
        "packages": {name: _version(name) for name in PACKAGES},
        "threads": args.threads,
    }
    print(f"OCR self-test  {info['time']}")
    print(f"  Python {info['python']} | {info['platform']}")
    print(f"  CPU {info['cpu']} ({info['cores']} threads) | RAM {total} GB, {avail} GB free")
    print("  " + ", ".join(f"{k} {v}" for k, v in info["packages"].items() if v))
    print()

    names = ["opencv", "paddle"] if args.quick else ["opencv", "paddle", "torch_paddle", "ocr_mobile", "ocr_server", "ocr_page"]
    steps = []
    for name in names:
        label = STEPS[name][0]
        print(f"- {label} ...", end=" ", flush=True)
        entry = _run_step(name, args)
        steps.append(entry)
        mem = f", peak {entry['peak_mb']} MB" if entry.get("peak_mb") else ""
        print(("OK" if entry["ok"] else "CRASH" if entry["crash"] else "FAILED") + f" ({entry['seconds']}s{mem})")
        print(f"    {entry['detail']}")
        if entry.get("fault"):
            print(f"    {entry['fault']}")
        for line in entry.get("stack") or entry.get("stderr_tail") or []:
            print(f"    {line}")

    tips = _advice(info, steps)
    print("\nWhat to do:")
    for t in tips:
        print(f"  * {t}")

    try:
        from core.config import APP_ROOT_DIR
        out_dir = os.path.join(APP_ROOT_DIR, "logs")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "ocr_selftest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({**info, "steps": steps, "advice": tips}, f, ensure_ascii=False, indent=2)
        print(f"\nReport: {path}")
    except Exception as e:
        print(f"\n(report not saved: {e})")


if __name__ == "__main__":
    main()
