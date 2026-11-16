
# backend/launcher.py

"""
One-click start: the entry point of the packaged Windows app (packaging/okt.spec). Works from the source too:
`python launcher.py` (or `python main.py` as before: the API only, no window, no port choice).

Why a separate entry and not main.py: main.py is the API itself; the packaged app needs a few things around it.
  - The app opens in its own window (app_window.py: pywebview -> the Edge WebView2 that Windows has), showing the
    frontend the backend serves at http://127.0.0.1:<port>/app/ - no second web server. The window appears at once with
    a start page and switches to the app when the API answers (the first start downloads the OCR models first).
    Closing the window stops the app. No WebView2 / pywebview: the browser opens instead, as with --browser.
  - The black console window is hidden when it belongs to the app alone (a double-click), so only the app window is
    on the screen; the log goes to <app root>/logs as always. Started from a terminal, or with --console, it stays.
  - Port 8000 taken by OpenKeyTranslate already running: its window is brought to the front (or, when that one runs
    without a window, a window is opened on it) - no second copy. Taken by another program: the next free port (pages
    served from /app call the API on their own origin, frontend/js/api.js). Saved preferences (localStorage) belong to
    the address, so the app stays on 8000 whenever it can.
  - --ocr-selftest: scripts/ocr_selftest.py run by the exe (each step starts the same exe again - a packaged app has
    no python.exe to run a script with).
  - --smoke-test: start, check that the API, the pages and the app window answer, stop; exit code 0 = fine. The build
    workflow (.github/workflows/build-windows.yml) runs it on every build.
The backend runs in a worker thread: the window's message loop needs the main thread.
"""

import argparse
import json
import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser

HOST = "127.0.0.1"
DEFAULT_PORT = 8000
READY_TIMEOUT = 900             # seconds: the first start downloads the OCR models before the API answers
STOP_TIMEOUT = 10               # seconds for the API to stop after the window closed
PROGRAM_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
"""@private Folder of the program files (as core.config.PROGRAM_DIR, not imported: that loads the settings) - a path on
this machine."""
ICON = os.path.join(PROGRAM_DIR, "frontend", "img", "okt.ico")
"""@private The window icon - a path on this machine."""


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


class Backend:
    """The API (main.py under uvicorn) in a worker thread. Imported there too: main.py is heavy (loads the OCR model)
    and the window should be on the screen meanwhile."""

    def __init__(self, port: int):
        self.port = port
        self.server = None
        self.error: str | None = None
        self.done = False
        self.thread = threading.Thread(target=self._run, name="okt-backend", daemon=True)

    def _run(self) -> None:
        try:
            import uvicorn
            from main import app
            self.server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=self.port, log_level="info"))
            self.server.run()
        except BaseException as e:                       # noqa: BLE001 - reported on the window's start page
            self.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        finally:
            self.done = True

    def start(self) -> "Backend":
        self.thread.start()
        return self

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end and not self.done:
            if getattr(self.server, "started", False) and _okt_on(self.port):
                return True
            time.sleep(0.5)
        return False

    def why_not_ready(self) -> str:
        if self.error:
            return self.error
        if self.done:
            return "The backend stopped while starting - the log says why."
        return f"No answer within {READY_TIMEOUT // 60} minutes."

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        if self.server is not None:
            self.server.should_exit = True
        self.thread.join(timeout)

    def wait_until_stopped(self) -> None:
        """Browser mode: run until the console is closed / Ctrl+C."""
        try:
            while self.thread.is_alive():
                self.thread.join(0.5)
        except KeyboardInterrupt:
            self.stop()


def _smoke(backend: Backend) -> list[str]:
    """Every check the build needs to trust the package: the API, the database, the pages, the bundled changelog,
    the memory model. -> names of the failed checks."""
    if not backend.wait_ready():
        return [f"the API did not start ({backend.why_not_ready()})"]
    base = f"http://{HOST}:{backend.port}"
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
    return failed


def _smoke_window(url: str, origin: str) -> str | None:
    """The app window of the build (pywebview + pythonnet + WebView2 in the package): opened minimised, the projects
    page must load and fill its list from the API. -> None = fine, else what failed. No WebView2 runtime on the
    machine = skipped (not the package's fault); pywebview missing from the package = failed."""
    import app_window
    reason = app_window.unavailable_reason()
    if reason:
        if "WebView2 Runtime" in reason:
            print(f"[SMOKE] skip app window: {reason}", flush=True)
            return None
        return reason
    seen = {}

    def check(window):
        end = time.monotonic() + 60
        while time.monotonic() < end:
            seen["list"] = window.evaluate_js(
                "(() => { const l = document.getElementById('projectList');"
                " return l && l.children.length ? l.textContent.replace(/\\s+/g, ' ').trim().slice(0, 100) : ''; })()")
            if seen["list"]:
                return
            time.sleep(0.5)

    if app_window.run(url, origin, check=check) == "failed":
        return "WebView2 did not show the window"
    text = seen.get("list") or ""
    if not text or text.startswith("Couldn't load"):
        return f"the projects page did not load its list ({text or 'empty'})"
    print(f"[SMOKE] ok   app window (projects page: {text[:60]})", flush=True)
    return None


def _open_browser_when_ready(backend: Backend, url: str) -> None:
    if backend.wait_ready():
        print(f"\n  OpenKeyTranslate is ready: {url}\n  Close this window to stop the app.\n", flush=True)
        webbrowser.open(url)
    else:
        print(f"[LAUNCHER] the app did not start: {backend.why_not_ready()} See the messages above.", flush=True)


def _exit_now(code: int) -> None:
    """After the window closed: leave even if a worker thread (a page run, the model warm-up) is still busy - closing
    the console window did the same before."""
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        logging.shutdown()
    finally:
        os._exit(code)


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()           # no-op from the source; needed once frozen if anything spawns processes
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--ocr-selftest"]:
        from scripts import ocr_selftest
        ocr_selftest.main(argv[1:])
        return 0

    parser = argparse.ArgumentParser(
        description="Start OpenKeyTranslate in its own window (or the browser).",
        epilog="OCR self-test instead: --ocr-selftest as the FIRST argument, then its own options "
               "(--quick, --cpu, --image FILE, --threads N).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"preferred port (default {DEFAULT_PORT})")
    parser.add_argument("--browser", action="store_true", help="open the app in the default browser, not in its own window")
    parser.add_argument("--no-browser", action="store_true", help="only start the backend (no window, no browser)")
    parser.add_argument("--console", action="store_true", help="keep the console window with the log visible")
    parser.add_argument("--devtools", action="store_true", help="the app window with the browser developer tools")
    parser.add_argument("--smoke-test", action="store_true",
                        help="start, check the API, the pages and the app window, stop (build check)")
    args = parser.parse_args(argv)

    window_reason = None
    want_window = not (args.browser or args.no_browser or args.smoke_test)
    if want_window:
        import app_window
        window_reason = app_window.unavailable_reason()
        if window_reason:
            print(f"[LAUNCHER] the app window is not available here ({window_reason}) - using the browser.", flush=True)
    use_window = want_window and not window_reason

    port = args.port
    if not _port_free(port):
        running = _okt_on(port)
        if running and not args.smoke_test:
            url = f"http://{HOST}:{port}/app/"
            print(f"[LAUNCHER] OpenKeyTranslate {running.get('version', '')} is already running on {url}", flush=True)
            if use_window:
                if app_window.focus_running():
                    return 0
                if app_window.run(url, f"http://{HOST}:{port}", devtools=args.devtools, icon=ICON) == "failed":
                    webbrowser.open(url)
            elif not args.no_browser:
                webbrowser.open(url)
            return 0
        taken = port
        port = next((p for p in range(taken + 1, taken + 50) if _port_free(p)), 0)
        if not port:
            print(f"[LAUNCHER] ports {taken}-{taken + 49} are all taken - start with --port <a free port>.")
            return 1
        print(f"[LAUNCHER] port {taken} is used by another program - using {port}. (Preferences such as shortcuts "
              f"and appearance are kept per address: the ones set on port {taken} are not seen here.)")

    origin = f"http://{HOST}:{port}"
    url = f"{origin}/app/"
    print(f"[LAUNCHER] Starting OpenKeyTranslate on {url}\n"
          f"[LAUNCHER] The first start downloads the OCR and memory models - it can take a few minutes.", flush=True)

    if args.smoke_test:
        backend = Backend(port).start()
        failed = _smoke(backend)
        if not failed:
            problem = _smoke_window(url, origin)
            if problem:
                print(f"[SMOKE] FAIL app window: {problem}", flush=True)
                failed.append("app window")
        backend.stop(timeout=60)
        if failed:
            print(f"[SMOKE] FAILED - failed: {', '.join(failed)}")
            return 1
        print("[SMOKE] all checks passed")
        return 0

    if not use_window:
        backend = Backend(port).start()
        if not args.no_browser:
            threading.Thread(target=_open_browser_when_ready, args=(backend, url), daemon=True).start()
        backend.wait_until_stopped()
        return 0

    console = 0 if args.console else app_window.hide_own_console()
    backend = Backend(port).start()

    def wait_for_app():
        return (url, None) if backend.wait_ready() else (None, backend.why_not_ready())

    if app_window.run(wait_for_app, origin, devtools=args.devtools, icon=ICON) == "failed":
        app_window.show_console(console)
        print("[LAUNCHER] the app window did not start (Microsoft Edge WebView2) - opening the browser instead.\n"
              "[LAUNCHER] Start with --browser to skip the window next time.", flush=True)
        _open_browser_when_ready(backend, url)
        backend.wait_until_stopped()
        return 0
    print("[LAUNCHER] the window was closed - stopping OpenKeyTranslate.", flush=True)
    backend.stop()
    _exit_now(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
