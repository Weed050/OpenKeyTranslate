
# backend/core/config.py

import os
import json
import copy
import shutil

"""
Configuration module for OpenKeyTranslate.

Handles loading, migrating, and exposing application settings, 
system paths, and pipeline constants.
"""

# Define the project directory structure and configuration file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
SETTINGS_FILE = os.path.join(os.path.dirname(ROOT_DIR), "settings.json")

# Bump manually on release. Stamped into every log file header (see
# core/app_logging.py) so a bug report's log always says which version produced it.
APP_VERSION = "0.1.1"


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

    # External APIs and Services (translation providers - see services/providers/)
    # active_provider selects which entry below is used. Each provider holds
    # a *pool* of keys (see _migrate_provider_keys below for the one-time
    # upgrade from the old single-key shape) - GroqProvider / GeminiProvider
    # rotate through them automatically on a rate limit, so quota from
    # multiple accounts/keys can be pooled without manual key-swapping.
    # Add a new provider by giving it its own entry here plus a class in
    # services/providers/.
    "translation_on": True,
    "active_provider": "groq",
    "providers": {
        "groq": {
            "model": "openai/gpt-oss-120b",
            "keys": [{"label": "default", "api_key": ""}],
        },
        "gemini": {
            "model": "gemini-2.5-flash",
            "keys": [{"label": "default", "api_key": ""}],
        },
    },
    "translation_temperature": 0.4,
    "translation_max_tokens": 4096,

    # Correction Memory (thesis feature - see services/memory_service.py)
    # memory_similarity_threshold: minimum cosine similarity for a past
    #   correction to be injected as a hint. Treat as an open research
    #   parameter, not a fixed constant - similarity_score is logged for
    #   every match so this can be tuned after the fact against real data.
    "memory_similarity_threshold": 0.80,
    # How many similar past corrections are sent to the LLM as style hints per bubble.
    "memory_top_k": 3,
    # Local sentence-transformers model used to embed English source text.
    "memory_embedding_model": "all-MiniLM-L6-v2",
    # Minimum word count in the final translation before it's persisted as a
    # Correction. Below this, short interjections ("Tak", "Nie", "Aaa!")
    # embed poorly and produce noisy, over-eager similarity matches.
    "memory_min_words": 3,
    # Sources with this many words or fewer (interjections, sound effects) are
    # matched by EXACT normalized text instead of embeddings - see
    # services/memory_service.is_short_phrase. 0 disables that lane.
    "memory_short_phrase_max_words": 2,
    # When True, matched bubbles are translated a second time without the
    # hint (hint-free "counterfactual"), purely for A/B logging. Turn off
    # once the experiment is done to save on API calls.
    "memory_ab_test_logging": True,

    # OCR post-filter: bubbles whose text matches any of these regexes
    # (case-insensitive, re.search) are dropped before translation - scanlation
    # watermarks, site URLs, credits. Editable on the Settings page. See
    # utils/ignore_filter.py.
    "ocr_ignore_patterns": [
        r"\b[\w-]+\.(?:com|net|org|gg|io)\b",
    ],

    # Debug Flags
    "ocr_debug": True,
    "ocr_marker_debug": True,

    # Typesetting
    "typesetting_padding_ratio": 0.08,
}


def _migrate_provider_keys(settings_dict: dict) -> bool:
    """
    Upgrade providers.<name> from the old single-key shape
    ({"api_key": ..., "model": ...}) to the new key-pool shape
    ({"model": ..., "keys": [{"label": ..., "api_key": ...}]}).

    Runs on every load_settings() call; a no-op once a provider is already
    migrated. Lets an existing settings.json (with a single api_key from
    before the multi-key change) keep working without the user having to
    hand-edit the file.

    :return: True if any provider entry was modified (caller re-saves the file).
    """
    changed = False
    for provider_cfg in settings_dict.get("providers", {}).values():
        if "keys" not in provider_cfg and "api_key" in provider_cfg:
            provider_cfg["keys"] = [{"label": "default", "api_key": provider_cfg.pop("api_key")}]
            changed = True
    return changed


def _deep_fill(current: dict, defaults: dict) -> bool:
    """
    Add keys that exist in `defaults` but not in `current`, recursively through dicts.
    Never overwrites a value the user already has, never touches lists (e.g. provider key pools).
    :return: True if anything was added.
    """
    changed = False
    for key, value in defaults.items():
        if key not in current:
            current[key] = copy.deepcopy(value)
            changed = True
        elif isinstance(value, dict) and isinstance(current[key], dict):
            if _deep_fill(current[key], value):
                changed = True
    return changed


def save_settings(data: dict) -> None:
    """
    Atomic write: temp file -> fsync -> os.replace, after copying the previous file to settings.json.bak.
    A crash mid-write can no longer leave a half-written settings.json (= lost API keys).
    """
    tmp_path = SETTINGS_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    if os.path.exists(SETTINGS_FILE):
        shutil.copy2(SETTINGS_FILE, SETTINGS_FILE + ".bak")
    os.replace(tmp_path, SETTINGS_FILE)


def _read_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def load_settings():
    """
    Load settings from the JSON file.

    - Missing file: created from DEFAULT_SETTINGS.
    - Existing file: NEVER overwritten with defaults. Keys added in newer app versions are filled in
      (recursively - new nested options too) and the old provider single-key shape is upgraded; the
      previous file is kept as settings.json.bak whenever that rewrites it.
    - Unreadable / corrupt file: falls back to settings.json.bak, otherwise stops with a clear error
      (it does NOT silently replace your keys with an empty template).
    """
    if not os.path.exists(SETTINGS_FILE):
        save_settings(copy.deepcopy(DEFAULT_SETTINGS))
        return copy.deepcopy(DEFAULT_SETTINGS)

    try:
        current_settings = _read_json(SETTINGS_FILE)
    except (json.JSONDecodeError, OSError) as e:
        backup = SETTINGS_FILE + ".bak"
        if not os.path.exists(backup):
            raise RuntimeError(f"settings.json is unreadable ({e}) and there is no settings.json.bak. "
                               f"Fix or delete {SETTINGS_FILE}.") from e
        print(f"[CONFIG] settings.json unreadable ({e}) - restoring settings.json.bak")
        current_settings = _read_json(backup)
        save_settings(current_settings)

    updated = _deep_fill(current_settings, DEFAULT_SETTINGS)
    if _migrate_provider_keys(current_settings):
        updated = True

    if updated:
        save_settings(current_settings)

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

# OCR ignore-list (see utils/ignore_filter.py)
OCR_IGNORE_PATTERNS = settings.get("ocr_ignore_patterns", [])

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
TRANSLATION_MAX_TOKENS = settings.get("translation_max_tokens", 4096)

# Correction Memory
MEMORY_SIMILARITY_THRESHOLD = settings.get("memory_similarity_threshold", 0.80)
MEMORY_TOP_K = settings.get("memory_top_k", 3)
MEMORY_EMBEDDING_MODEL = settings.get("memory_embedding_model", "all-MiniLM-L6-v2")
MEMORY_MIN_WORDS = settings.get("memory_min_words", 3)
MEMORY_SHORT_PHRASE_MAX_WORDS = settings.get("memory_short_phrase_max_words", 2)
MEMORY_AB_TEST_LOGGING = settings.get("memory_ab_test_logging", True)

# Environment Debugging
DEBUG = settings.get("ocr_debug")
MARKER_DEBUG = settings.get("ocr_marker_debug")

# Rendering / Typesetting
TYPESETTING_PADDING_RATIO = settings.get("typesetting_padding_ratio")