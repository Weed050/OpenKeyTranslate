
# backend/app_window.py

"""
The app's own window: the frontend in a desktop window (pywebview -> Microsoft Edge WebView2) instead of a browser tab.

Why WebView2 and not Electron: it is the Edge engine Windows already has (Windows 11; Windows 10 gets it with Edge
updates), so the package grows by a few MB instead of carrying its own ~150 MB Chromium. The pages stay the static
frontend served by the backend at /app/ - in this window, in a browser (launcher.py --browser) or from Live Server.

A window is not a browser tab; the differences are handled here, in one place, so the frontend does not need to know
where it runs:
  - links to other sites (Google Translate, key pages, fonts, GitHub) open in the system browser: inside the window
    they would replace the app, with no address bar and no back button. A target=_blank link / window.open is a
    new-window request, a plain link a navigation - both are caught (`_patch_edge`);
  - the app's own pages opened "in a new tab" (a Memory entry, Find & replace, View export) open in a second app window,
    which closes together with the main one;
  - downloads (the JSON exports of History, Memory, Glossary, shortcuts) ask where to save;
  - the browser keys and the right-click menu stay on (F5, Ctrl+F, Ctrl+wheel zoom, copy / paste, spell check of the
    translation fields) - pywebview turns them off outside its debug mode;
  - the window keeps its own browser profile (localStorage = shortcuts, appearance, editor preferences) in
    %LOCALAPPDATA%\\OpenKeyTranslate\\WebView2 - separate from every browser's, as a second browser would be (Settings >
    Backup carries the preferences over);
  - pywebview / pythonnet / the WebView2 runtime missing, or WebView2 not starting: launcher.py falls back to the
    browser and says why (`unavailable_reason`, run() -> "failed").
Windows only: pywebview has other engines elsewhere, but the patches below are written for WebView2.
"""

import ctypes
import html
import os
import sys
import threading
import webbrowser

TITLE = "OpenKeyTranslate"
LOAD_TIMEOUT = 60               # seconds for WebView2 to show the start page before the browser is used instead
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

_origin = ""                    # http://127.0.0.1:<port> of the backend the windows show (set by run())
_state = {"master": None}


def data_dir() -> str:
    """The window's browser profile (localStorage, cache). Same path as core.config.WEBVIEW_DATA_DIR - not imported
    from there: importing core.config loads the settings, and the window starts before the backend."""
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    return os.path.join(base, "OpenKeyTranslate", "WebView2")


def unavailable_reason() -> str | None:
    """None = the window can open here; else why not (shown before falling back to the browser)."""
    if sys.platform != "win32":
        return "the app window is made for Windows - use the browser"
    try:
        import webview  # noqa: F401
    except Exception as e:
        return f"pywebview is not installed ({type(e).__name__}: {e}) - pip install pywebview"
    try:
        from webview.platforms import winforms
    except Exception as e:
        return f"the window library could not start ({type(e).__name__}: {e})"
    if not getattr(winforms, "is_chromium", False):
        # pywebview would fall back to Internet Explorer, which cannot run the app's pages (ES modules)
        return f"the Microsoft Edge WebView2 Runtime is not installed - free download: {WEBVIEW2_URL}"
    return None


def _app_windows() -> list[int]:
    """Visible top-level app windows: the title AND a WinForms window class - an Explorer window showing a folder
    named OpenKeyTranslate has the same title."""
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        title = ctypes.create_unicode_buffer(64)
        user32.GetWindowTextW(hwnd, title, 64)
        if title.value == TITLE and user32.IsWindowVisible(hwnd):
            cls = ctypes.create_unicode_buffer(128)
            user32.GetClassNameW(hwnd, cls, 128)
            if cls.value.startswith("WindowsForms10."):
                found.append(hwnd)
        return True
    user32.EnumWindows(visit, 0)
    return found


def focus_running() -> bool:
    """A second start: bring the open app window to the front. False = no app window is open (e.g. the backend runs
    from the source or in browser mode) - the caller opens a window of its own on that backend."""
    if sys.platform != "win32":
        return False
    windows = _app_windows()
    if not windows:
        return False
    user32 = ctypes.windll.user32
    if user32.IsIconic(windows[0]):
        user32.ShowWindow(windows[0], 9)        # SW_RESTORE
    user32.SetForegroundWindow(windows[0])
    return True


def _console_is_ours(pids: list[int]) -> bool:
    """Only this process on the console - or this process and the python.exe of a venv, which is a small launcher
    that starts the real interpreter as its child (`python launcher.py` double-clicked from the source)."""
    others = [p for p in pids if p != os.getpid()]
    if not others:
        return True
    if others != [os.getppid()]:
        return False
    try:
        import psutil
        return psutil.Process(others[0]).name().lower() in ("python.exe", "pythonw.exe", "py.exe")
    except Exception:
        return False


def hide_own_console() -> int:
    """
    Hide the black console window when it belongs to the app alone (the exe started with a double-click) - never a
    terminal someone typed the command in (the shell shares it). Hidden, not freed: the log still has a valid output
    and programs started by the app inherit the hidden console instead of flashing their own.
    -> the console's handle (show_console() puts it back if the window fails), 0 = left as it was.
    """
    if sys.platform != "win32":
        return 0
    kernel32 = ctypes.windll.kernel32
    hwnd = kernel32.GetConsoleWindow()
    if not hwnd:
        return 0
    buf = (ctypes.c_ulong * 8)()
    count = kernel32.GetConsoleProcessList(buf, 8)
    if not 0 < count <= 8 or not _console_is_ours(list(buf[:count])):
        return 0
    ctypes.windll.user32.ShowWindow(hwnd, 0)    # SW_HIDE
    return hwnd


def show_console(hwnd: int) -> None:
    if hwnd:
        ctypes.windll.user32.ShowWindow(hwnd, 5)    # SW_SHOW


def _route(uri: str) -> str:
    """"app" = one of the app's own pages / files, "external" = another site (system browser), "ignore" = the rest
    (about:blank, the start page)."""
    if _origin and (uri == _origin or uri.startswith((_origin + "/", "blob:" + _origin))):
        return "app"
    if uri.lower().startswith(("http://", "https://", "mailto:")):
        return "external"
    return "ignore"


def _open_app_window(url: str) -> None:
    """A second window for an app page opened "in a new tab" (from a worker thread: pywebview creates windows that
    way once the GUI runs)."""
    import webview

    def create():
        webview.create_window(TITLE, url, width=1280, height=860, min_size=(700, 500), text_select=True,
                              zoomable=True, background_color=_background())
    threading.Thread(target=create, daemon=True).start()


def _patch_edge() -> None:
    """Links, keys and the context menu (module doc). Patched on the class, before any window exists: pywebview binds
    these handlers when a window's WebView2 is ready."""
    from webview.platforms import edgechromium as edge
    if getattr(edge.EdgeChrome, "_okt_patched", False):
        return
    original_ready = edge.EdgeChrome.on_webview_ready

    def on_navigation_starting(sender, args):
        uri = str(args.Uri)
        if _route(uri) == "external":
            args.Cancel = True
            webbrowser.open(uri)

    def on_webview_ready(self, sender, args):
        original_ready(self, sender, args)
        if not args.IsSuccess:
            return
        settings = sender.CoreWebView2.Settings
        settings.AreBrowserAcceleratorKeysEnabled = True
        settings.AreDefaultContextMenusEnabled = True
        sender.CoreWebView2.NavigationStarting += on_navigation_starting

    def on_new_window_request(self, sender, args):
        args.set_Handled(True)
        uri = str(args.get_Uri())
        where = _route(uri)
        if where == "app":
            _open_app_window(uri)
        elif where == "external":
            webbrowser.open(uri)

    edge.EdgeChrome.on_webview_ready = on_webview_ready
    edge.EdgeChrome.on_new_window_request = on_new_window_request
    edge.EdgeChrome._okt_patched = True


def _dark_system() -> bool:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def _background() -> str:
    """The colour shown before a page paints (no white flash on a dark system)."""
    return "#1d1f23" if _dark_system() else "#f6f6f4"


_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>OpenKeyTranslate</title><style>
:root {{ color-scheme: light dark; --bg: #f6f6f4; --fg: #22252a; --muted: #6b7078; --err: #b3261e; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #1d1f23; --fg: #e6e6e3; --muted: #9da2aa; --err: #f2b8b5; }} }}
html, body {{ height: 100%; margin: 0; }}
body {{ background: var(--bg); color: var(--fg); font: 15px/1.5 "Segoe UI", system-ui, sans-serif; display: grid;
       place-items: center; cursor: default; }}
main {{ max-width: 560px; padding: 24px; text-align: center; }}
h1 {{ font-size: 22px; font-weight: 600; margin: 0 0 8px; }}
p {{ margin: 6px 0; color: var(--muted); }}
pre {{ text-align: left; white-space: pre-wrap; color: var(--err); font: 13px/1.4 Consolas, monospace; }}
.dots::after {{ content: ""; animation: d 1.5s steps(4) infinite; }}
@keyframes d {{ 0% {{ content: ""; }} 25% {{ content: "."; }} 50% {{ content: ".."; }} 75% {{ content: "..."; }} }}
</style></head><body><main>{body}</main></body></html>"""


def _start_page() -> str:
    return _PAGE.format(body=(
        '<h1>Starting OpenKeyTranslate<span class="dots"></span></h1>'
        '<p>Usually a few seconds. The first start downloads the OCR and memory models: a few minutes, '
        'internet needed once.</p><p id="t"></p>'
        '<script>const s = Date.now(); setInterval(() => { const n = Math.round((Date.now() - s) / 1000); '
        'if (n >= 10) document.getElementById("t").textContent = n + " s"; }, 1000);</script>'))


def _error_page(error: str) -> str:
    return _PAGE.format(body=(
        "<h1>OpenKeyTranslate could not start</h1>"
        f"<pre>{html.escape(error or 'unknown error')}</pre>"
        "<p>Details: the newest app.log / errors.log in %APPDATA%\\OpenKeyTranslate\\logs (and crash.log there).</p>"
        "<p>Close this window to quit. Starting OpenKeyTranslate.exe from a terminal shows the whole log.</p>"))


def run(target, origin: str, *, devtools: bool = False, icon: str | None = None, check=None) -> str:
    """
    Show the app window; blocks (on the main thread) until every app window is closed.
      target - the app's URL, or a function that waits for the backend: -> (url, None) or (None, error message);
               meanwhile the window shows a start page;
      origin - http://127.0.0.1:<port>: pages there stay in the app, any other http(s) goes to the system browser;
      check  - smoke test: called with the window once the app page loaded (in a worker thread), then the window
               closes; the window starts minimised.
    -> "closed" (the user closed it) or "failed" (WebView2 showed nothing within LOAD_TIMEOUT: use the browser).
    """
    global _origin
    import webview

    _origin = origin.rstrip("/")
    _patch_edge()
    webview.settings["ALLOW_DOWNLOADS"] = True
    url = target if isinstance(target, str) else None
    window = webview.create_window(
        TITLE, url=url, html=None if url else _start_page(), width=1366, height=860, min_size=(900, 600),
        maximized=check is None, minimized=check is not None, text_select=True, zoomable=True,
        background_color=_background())
    _state["master"] = window
    outcome = {"failed": False}

    def close_the_rest():
        for w in list(webview.windows):
            if w is not window:
                try:
                    w.destroy()
                except Exception:
                    pass
    window.events.closed += close_the_rest

    def drive():
        if not window.events.loaded.wait(LOAD_TIMEOUT):
            outcome["failed"] = True
            window.destroy()
            return
        if url is None:
            found, error = target()
            if found:
                window.load_url(found)
            else:
                window.load_html(_error_page(error))
                return
        if check is not None:
            try:
                window.events.loaded.wait(LOAD_TIMEOUT)
                check(window)
            finally:
                window.destroy()

    if sys.platform == "win32":
        try:        # the taskbar groups the window under the app, not under python.exe (running from the source)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OpenKeyTranslate.App")
        except Exception:
            pass
    os.makedirs(data_dir(), exist_ok=True)
    webview.start(drive, private_mode=False, storage_path=data_dir(), debug=devtools,
                  icon=icon if icon and os.path.isfile(icon) else None)
    return "failed" if outcome["failed"] else "closed"
