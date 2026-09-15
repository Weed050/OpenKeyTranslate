
# backend/core/config.py

import os
import json

"""
Configuration module for OpenKeyTranslate.

Handles loading, migrating, and exposing application settings, 
system paths, and pipeline constants.
"""

# Define the project directory structure and configuration file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
SETTINGS_FILE = os.path.join(os.path.dirname(ROOT_DIR), "settings.json")


def get_default_app_root():
    """
    Get the platform-specific default application data directory.
    Calculates root directory path (%AppData%...)
    """
    app_name = "OpenKeyTranslate"
    if os.name == 'nt':
        return os.path.join(os.environ.get('APPDATA'), app_name)
    return os.path.join(os.path.expanduser('~'), f".{app_name.lower()}")


# Default fallback settings and schema definition
DEFAULT_SETTINGS = {
    "app_root_dir": get_default_app_root(),
    "source_lang": "en",
    "target_lang": "pl",

    # OCR Pipeline Settings
    "ocr_white_space": True,
    "ocr_white_space_pad_width": 100,
    "ocr_text_threshold": 0.85,
    "ocr_soft_tolerance" : 0.15,
    "ocr_scale": 1.0,
    "ocr_slice_h_ratio": 0.1,
    "ocr_overlap_ratio": 0.15,
    "ocr_iou_thresh": 0.5,

    # Deduplication Settings
    "ocr_dedup_word_thresh": 0.1,
    "ocr_dedup_geo_thresh": 0.1,
    "ocr_dedup_min_overlap_px": 5,

    # Marker Settings (Thicker and larger so OCR doesn't lose it in empty spaces)
    "ocr_marker_font_scale": 0.9,
    "ocr_marker_thickness": 2,
    "ocr_marker_margin_x": 40,
    "ocr_marker_margin_y": 40,
    "ocr_marker_score_threshold": 0.4,

    # External APIs and Services (tranlation providers - see services/providers/)
    # active_provider selects which key below is used; add a new provider by
    # giving it its own entry here plus a class in services/providers/.
    "translation_on": True,
    "active_provider": "groq",
    "providers": {
        "groq": {
            "api_key": "",
            "model": "openai/gpt-oss-120b",
        },
        "gemini": {
            "api_key": "",
            "model": "gemini-2.5-flash",
        },
    },
    "translation_temperature": 0.4,
    "translation_max_tokens": 1024,

    # Correction Memory (thesis feature - see services/memory_service.py)
    # memory_similarity_threshold: minimum cosine similarity for a past
    #   correction to be injected as a hint. Treat as an open research
    #   parameter, not a fixed constant - similarity_score is logged for
    #   every match so this can be tuned after the fact against real data
    "memory_similarity_threshold": 0.80,

    # Local sentence-transformers model used to embed English source text.
    "memory_embedding_model": "all-MiniLM-L6-v2",

    # When true, matched bubbles are translated a second time without the
    # hint (hint-free "counterfactual"), purely for A/B logging. Turn off
    # once the experiment is done to save on API calls.
    "memory_ab_test_logging": True,

    # Debug Flags
    "ocr_debug": True,
    "ocr_marker_debug": True,

    # Typesetting
    "typesetting_padding_ratio": 0.08,
}


def load_settings():
    """
    Load settings from the JSON file.

    Creates the file with defaults if missing, and automatically migrates
    any new keys added in application updates.
    """

    # Create the config file with defaults if it doesn't exist
    if not os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
            json.dump(DEFAULT_SETTINGS, file, indent=4)
        return DEFAULT_SETTINGS

    # Load existing configuration
    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        current_settings = json.load(file)

    # Self-healing: Merge missing default keys (exmpl: after an app update)
    updated = False
    for key, value in DEFAULT_SETTINGS.items():
        if key not in current_settings:
            current_settings[key] = value
            updated = True

    # Save back to file if migration occurred
    if updated:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
            json.dump(current_settings, file, indent=4)

    return current_settings


# Initialize settings and ensure environment directories exist
settings = load_settings()

# Main system variables
APP_ROOT_DIR = settings.get("app_root_dir")
WORKSPACE_DIR = os.path.join(APP_ROOT_DIR, 'projects_workspace')
DATABASE_PATH = os.path.join(APP_ROOT_DIR, 'openkey_translate.db')

os.makedirs(WORKSPACE_DIR, exist_ok=True)

# --- Configuration Constants for Application Import ---
# This allows other files to just use: `from config import WHITE_SPACE`

# Core OCR Pipeline
WHITE_SPACE = settings.get("ocr_white_space")
WHITE_SPACE_PAD_WIDTH = settings.get("ocr_white_space_pad_width")
TEXT_THRESHOLD = settings.get("ocr_text_threshold")
SOFT_TOLERANCE = settings.get("ocr_soft_tolerance")
SCALE = settings.get("ocr_scale")
SLICE_H_RATIO = settings.get("ocr_slice_h_ratio")
OVERLAP_RATIO = settings.get("ocr_overlap_ratio")
IOU_THRESH = settings.get("ocr_iou_thresh")

# Deduplication
DEDUP_WORD_THRESH = settings.get("ocr_dedup_word_thresh")
DEDUP_GEO_THRESH = settings.get("ocr_dedup_geo_thresh")
DEDUP_MIN_OVERLAP_PX = settings.get("ocr_dedup_min_overlap_px")

# OCR Markers
MARKER_FONT_SCALE = settings.get("ocr_marker_font_scale")
MARKER_THICKNESS = settings.get("ocr_marker_thickness")
MARKER_MARGIN_X = settings.get("ocr_marker_margin_x")
MARKER_MARGIN_Y = settings.get("ocr_marker_margin_y")
MARKER_SCORE_THRESHOLD = settings.get("ocr_marker_score_threshold")
MARKER_COLOR = (0, 0, 0)  # Black RGB

# Translation Service / Providers
TRANSLATION_ON = settings.get("translation_on")
ACTIVE_PROVIDER = settings.get("active_provider", "groq")
PROVIDERS = settings.get("providers", {})
ACTIVE_MODEL_NAME = PROVIDERS.get(ACTIVE_PROVIDER, {}).get("model", ACTIVE_PROVIDER)
TRANSLATION_TEMPERATURE = settings.get("translation_temperature", 0.4)
TRANSLATION_MAX_TOKENS = settings.get("translation_max_tokens", 1024)

# Correction Memory
MEMORY_SIMILARITY_THRESHOLD = settings.get("memory_similarity_threshold", 0.80)
MEMORY_EMBEDDING_MODEL = settings.get("memory_embedding_model", "all-MiniLM-L6-v2")
MEMORY_AB_TEST_LOGGING = settings.get("memory_ab_test_logging", True)

# Environment Debugging
DEBUG = settings.get("ocr_debug")
MARKER_DEBUG = settings.get("ocr_marker_debug")

# Rendering / Typesetting
TYPESETTING_PADDING_RATIO = settings.get("typesetting_padding_ratio")