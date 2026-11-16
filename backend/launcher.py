
# backend/launcher.py

"""
One-click start: the entry point of the packaged Windows app (packaging/okt.spec). Works from the source too:
`python launcher.py` (or `python main.py` as before, without the browser and the port choice).

Why a separate entry and not main.py: main.py is the API itself; the packaged app needs a few things around it.
  - The backend serves the frontend (main.py mounts frontend/ at /app), so no second web server is needed: the
    browser opens http://127.0.0.1:<port>/app/ once the API answers (the first start downloads the OCR models first).
  - Port 8000 taken by OpenKeyTranslate already running: only the browser is opened, no second copy. Taken by another
    program: the next free port (pages served from /app call the API on their own origin, frontend/js/api.js). The
    browser keeps its saved preferences per address, so the app stays on 8000 whenever it can.
  - --ocr-selftest: scripts/ocr_selftest.py run by the exe (each step starts the same exe again - a packaged app has
    no python.exe to run a script with).
  - --smoke-test: start, check that the API and the pages answer, stop; exit code 0 = fine. The build workflow
    (.github/workflows/build-windows.yml) runs it on every build.
The app stays a console window on purpose: the log is visible and closing the window stops the app - nothing keeps
running hidden in the background.
"""

import argparse
import json
import multiprocessing
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
READY_TIMEOUT = 900             # seconds: the first start downloads the OCR models before the API answers


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
            return True
        except OSError:
            return False


def _get(url: str, timeout: float = 3.0, post: bool = False) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=b"" if post else None), timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:
        return 0, b""


def _okt_on(port: int) -> dict | None:
    """The /system/about answer when OpenKeyTranslate answers on this port, else None."""
    status, body = _get(f"http://{HOST}:{port}/system/about")
    if status != 200:
        return None
    try:
        info = json.loads(body)
    except ValueError:
        return None
    return info if info.get("app") == "OpenKeyTranslate" else None


def _wait_ready(port: int, server, timeout: float = READY_TIMEOUT) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if getattr(server, "started", False) and _okt_on(port):
            return True
        time.sleep(0.5)
    return False


def _smoke(port: int, server, result: dict) -> None:
    """Every check the build needs to trust the package: the API, the database, the pages, the bundled changelog."""
    try:
        if not _wait_ready(port, server):
            result["error"] = "the API did not answer in time"
            return
        base = f"http://{HOST}:{port}"
        checks = {
            "about": f"{base}/system/about",
            "projects (database)": f"{base}/projects/",
            "health": f"{base}/system/health",
            "changelog (bundled file)": f"{base}/system/changelog",
            "frontend index": f"{base}/app/index.html",
            "frontend module": f"{base}/app/js/api.js",
            "ocr runtime": f"{base}/system/ocr-runtime",
        }
        failed = []
        for name, url in checks.items():
            status, body = _get(url, timeout=30)
            ok = status == 200 and body
            print(f"[SMOKE] {'ok  ' if ok else 'FAIL'} {name} ({status})", flush=True)
            if not ok:
                failed.append(name)
        # the correction-memory model (torch + transformers - the part most likely to miss a file in a package):
        # loaded and asked whether a paraphrase is closer than an unrelated sentence (downloads it on a fresh machine)
        status, body = _get(f"{base}/system/embedder/test", timeout=600, post=True)
        try:
            emb = json.loads(body) if status == 200 else {}
        except ValueError:
            emb = {}
        print(f"[SMOKE] {'ok  ' if emb.get('ok') else 'FAIL'} memory model ({status}) "
              f"{emb.get('message') or ''}{'similar %s / unrelated %s' % (emb.get('similar'), emb.get('unrelated')) if emb.get('ok') else ''}",
              flush=True)
        if not emb.get("ok"):
            failed.append("memory model")
        result["error"] = f"failed: {', '.join(failed)}" if failed else None
    finally:
        server.should_exit = True


def _open_when_ready(port: int, server, url: str) -> None:
    if _wait_ready(port, server):
        print(f"\n  OpenKeyTranslate is ready: {url}\n  Close this window to stop the app.\n", flush=True)
        webbrowser.open(url)
    else:
        print(f"[LAUNCHER] the app did not answer within {READY_TIMEOUT // 60} minutes - see the messages above.")


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()           # no-op from the source; needed once frozen if anything spawns processes
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--ocr-selftest"]:
        from scripts import ocr_selftest
        ocr_selftest.main(argv[1:])
        return 0

    parser = argparse.ArgumentParser(
        description="Start OpenKeyTranslate and open it in the browser.",
        epilog="OCR self-test instead: --ocr-selftest as the FIRST argument, then its own options "
               "(--quick, --cpu, --image FILE, --threads N).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"preferred port (default {DEFAULT_PORT})")
    parser.add_argument("--no-browser", action="store_true", help="do not open the browser")
    parser.add_argument("--smoke-test", action="store_true", help="start, check the API and the pages, stop (build check)")
    args = parser.parse_args(argv)

    port = args.port
    if not _port_free(port):
        running = _okt_on(port)
        if running and not args.smoke_test:
            url = f"http://{HOST}:{port}/app/"
            print(f"[LAUNCHER] OpenKeyTranslate {running.get('version', '')} is already running - opening {url}")
            if not args.no_browser:
                webbrowser.open(url)
            return 0
        taken = port
        port = next((p for p in range(taken + 1, taken + 50) if _port_free(p)), 0)
        if not port:
            print(f"[LAUNCHER] ports {taken}-{taken + 49} are all taken - start with --port <a free port>.")
            return 1
        print(f"[LAUNCHER] port {taken} is used by another program - using {port}. (The browser keeps the app's "
              f"preferences per address: shortcuts and appearance set on port {taken} are not seen here.)")

    url = f"http://{HOST}:{port}/app/"
    print(f"[LAUNCHER] Starting OpenKeyTranslate on {url}\n"
          f"[LAUNCHER] The first start downloads the OCR and memory models - it can take a few minutes.", flush=True)

    import uvicorn
    from main import app                        # heavy: loads the OCR model (see main.py)

    server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="info"))
    result: dict = {}
    if args.smoke_test:
        threading.Thread(target=_smoke, args=(port, server, result), daemon=True).start()
    elif not args.no_browser:
        threading.Thread(target=_open_when_ready, args=(port, server, url), daemon=True).start()
    server.run()

    if args.smoke_test:
        if result.get("error", "not run"):
            print(f"[SMOKE] FAILED - {result.get('error', 'the checks did not run')}")
            return 1
        print("[SMOKE] all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
