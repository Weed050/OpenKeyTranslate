
# backend/main.py

"""
Main application entry point for the FastAPI backend.

This module initializes the core web framework, sets up asynchronous lifecycle hooks
(such as database initialization), configures Cross-Origin Resource Sharing (CORS)
middleware, and mounts application routers. It also instantiates shared system models
like PaddleOCR.
"""

from dotenv import load_dotenv

# Load environment variables from .env file before importing config elements
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from routers import projects
import uvicorn
from core.database import init_db
from paddleocr import PaddleOCR


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
# app.include_router(settings.router)

# Pre-warm and instantiate the PaddleOCR global engine instance.
# Enabling use_textline_orientation=True helps automatically fix slightly rotated/tilted text boxes.
ocr_model = PaddleOCR(use_textline_orientation=True, lang='en')


if __name__ == '__main__':
    # Local development server entry point
    uvicorn.run(app, host="127.0.0.1", port=8000)