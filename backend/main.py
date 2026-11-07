
# backend/main.py

"""
Main application entry point for the FastAPI backend.

This module initializes the core web framework, sets up asynchronous lifecycle hooks
(such as database initialization), configures Cross-Origin Resource Sharing (CORS)
middleware, and mounts application routers (including pages.router, whose
import pre-warms the shared PaddleOCR model - see the NOTE near the bottom
of this file).
"""

from dotenv import load_dotenv

# Load environment variables from .env file before importing config elements
load_dotenv()

from core.app_logging import setup_logging
setup_logging()  # capture stdout/stderr to a rotating log file before anything else prints

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routers import projects, corrections, pages, logs, settings, system, glossary, chapters, fonts
from services.reconcile import reconcile_projects, reconcile_chapters
import uvicorn
from core.database import init_db, SessionLocal
from services.memory_service import warm_up_embedder_async
from core.startup_checks import log_startup_report


def _quiet_connection_resets() -> None:
    """
    Windows only noise: when the browser drops a connection (tab closed, an image request cancelled by the next page
    load, "View export" tab closed), asyncio's Proactor loop logs
        Exception in callback _ProactorBasePipeTransport._call_connection_lost ... ConnectionResetError [WinError 10054]
    as an ERROR with a traceback, although nothing failed (CPython issue 83413). Only that exact case is dropped;
    every other loop exception goes to the default handler as before.
    """
    import asyncio
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handler(loop_, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) and "_call_connection_lost" in repr(context.get("handle") or context.get("message", "")):
            return
        if previous is not None:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Asynchronous context manager governing the application lifecycle.

    Handles pre-startup procedures (e.g., establishing database schemas)
    and post-shutdown cleanup operations cleanly.
    """
    print("[LIFECYCLE] Initializing database...")
    init_db()
    print("[LIFECYCLE] Database is ready and operational.")

    log_startup_report()  # missing API key / unreachable API / bad settings -> visible in app.log + /system/health

    db = SessionLocal()
    try:
        project_report = reconcile_projects(db)
        reconcile_chapters(db)  # chapter folders added/removed on disk by hand -> DB follows (see services/reconcile.py)
        from services.reconcile import LAST_REPORT
        LAST_REPORT["warnings"] = project_report["warnings"] + LAST_REPORT.get("warnings", [])   # shown by /system/health
        pages.reset_stale_statuses(db)  # queued/processing left over from a crash -> pending (else the UI is stuck)
    finally:
        db.close()

    warm_up_embedder_async()   # background thread: correction memory is not needed until the first translation / save
    print("[LIFECYCLE] Embedding model loading in the background; the app is ready.")
    _quiet_connection_resets()

    yield

    print("[LIFECYCLE] Closing active application sessions and cleaning up...")

app = FastAPI(lifespan=lifespan)

# CORS (Cross-Origin Resource Sharing) Middleware Configuration.
# Enables communication between the frontend client and this backend API.
#
# NOTE: This application is architected primarily as a local tool (running on localhost),
# making the wildcard ["*"] perfectly safe for its intended use case.
# If a future developer decides to deploy this backend as a public-facing web service,
# they MUST explicitly restrict 'allow_origins' to the exact frontend domain for security.
# Only pages served from this machine (Live Server :5500, the future desktop shell, ...) may call the API.
# "*" let ANY website the user opens read responses from this server (e.g. API keys, see routers/settings.py)
# and call DELETE /projects/{id}, POST /settings/ (migrate = rmtree) ... A local server is NOT safe by default.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes Mount Points
app.include_router(projects.router)
app.include_router(chapters.router)
app.include_router(corrections.router)
app.include_router(pages.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(system.router)
app.include_router(glossary.router)
app.include_router(fonts.router)

# NOTE: PaddleOCR is no longer instantiated here directly. Importing
# routers.pages (above) already pulls in services/ocr_pipeline.py, whose
# module-level `ocr = PaddleOCR(...)` instance loads and pre-warms the GPU
# model as a side effect of that import - the same effect this file used to
# achieve by instantiating a second, separate PaddleOCR instance here.
# Keeping both meant loading the model twice for no benefit.

if __name__ == '__main__':
    # Local development server entry point
    uvicorn.run(app, host="127.0.0.1", port=8000)