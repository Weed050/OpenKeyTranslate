
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
from routers import projects, corrections, pages, logs, settings, system, glossary
from routers.projects import reconcile_projects_from_disk
import uvicorn
from core.database import init_db, SessionLocal


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

    db = SessionLocal()
    try:
        reconcile_projects_from_disk(db)
    finally:
        db.close()

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# API Routes Mount Points
app.include_router(projects.router)
app.include_router(corrections.router)
app.include_router(pages.router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(system.router)
app.include_router(glossary.router)

# NOTE: PaddleOCR is no longer instantiated here directly. Importing
# routers.pages (above) already pulls in services/ocr_pipeline.py, whose
# module-level `ocr = PaddleOCR(...)` instance loads and pre-warms the GPU
# model as a side effect of that import - the same effect this file used to
# achieve by instantiating a second, separate PaddleOCR instance here.
# Keeping both meant loading the model twice for no benefit.

if __name__ == '__main__':
    # Local development server entry point
    uvicorn.run(app, host="127.0.0.1", port=8000)
