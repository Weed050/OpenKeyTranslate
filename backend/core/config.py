
# backend/core/config.py

import os
import json
import copy
import shutil
import tempfile

"""
Configuration module for OpenKeyTranslate.

Handles loading, migrating, and exposing application settings, 
system paths, and pipeline constants.
"""

# Define the project directory structure and configuration file path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
SETTINGS_FILE = os.path.join(os.path.dirname(ROOT_DIR), "settings.json")

# Set by scripts/build_docs.py. pdoc prints the VALUE of every module-level variable into the HTML, and the docs are
# public (GitHub Pages) - a docs build once published a Groq key this way. In this mode settings.json is never read.
_DOCS_BUILD = bool(os.environ.get("OKT_DOCS_BUILD"))

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
    # Share of translation runs (pages) that get the hint-free counterfactual pass when A/B logging is on.
    # 1.0 = every run (doubles requests for pages with a memory match), 0.3 = ~every third page - saves free-tier quota.
    "memory_ab_sample_rate": 1.0,
    # Store translations the user ACCEPTED unchanged as kind="approved" translation-memory entries (corrections stay
    # kind="corrected" and are ranked first). More material to retrieve from = memory fires far more often.
    "memory_store_approved": True,
    # Second, lexical gate for the embedding lane (content-word Jaccard overlap, 0-100): a candidate must clear BOTH
    # the cosine threshold and this - unless its cosine is >= memory_strong_similarity. Rejects "same topic,
    # different line" matches that MiniLM scores ~0.7 (measured: useful matches 30-75, noise 0-20). 0 = off.
    "memory_lexical_min": 25,
    "memory_strong_similarity": 0.90,

    # Terminology (glossary) - see services/terminology.py
    # After translation, check every glossary term was actually used (inflection-tolerant). Bubbles missing a term
    # get ONE extra post-editing call that inserts the term and changes nothing else (Moslem 2024: terminology-
    # constrained automatic post-editing nearly doubled term usage).
    "glossary_postedit": True,
    # Right after a correction is saved, look for recurring term-level fixes and offer them in the editor.
    "glossary_suggest_on_save": True,

    # Copy the source's casing: an ALL-CAPS source line gets an ALL-CAPS translation (deterministic, not left to the
    # model - ~30% of caps lines came back in mixed case).
    "translation_match_case": True,
    # Gemini 2.5 "thinking" tokens count against max_output_tokens and can truncate the JSON of a dense page.
    # 0 = thinking off (2.5 Flash), -1 = model default. Pro models cannot turn it off (minimum 128 is used).
    "gemini_thinking_budget": 0,

    # OCR post-filter: bubbles whose text matches any of these regexes
    # (case-insensitive, re.search) are dropped before translation - scanlation
    # watermarks, site URLs, credits. Editable on the Settings page. See
    # utils/ignore_filter.py.
    "ocr_ignore_patterns": [
        r"\b[\w-]+\.(?:com|net|org|gg|io)\b",
    ],

    # OCR runtime (see core/ocr_runtime.py): replaces hand-editing services/ocr_pipeline.py to switch CPU <-> GPU.
    "ocr_device": "auto",          # auto | cpu | gpu
    "ocr_cpu_threads": 0,          # 0 = library default
    "ocr_mkldnn": False,           # oneDNN acceleration (CPU only); off by default - it crashes on some Paddle/CPU combos
    "ocr_env_flags": {},           # extra env vars applied before paddle is imported, e.g. {"FLAGS_enable_pir_api": "0"}
    "models_dir": "",              # "" = library default caches; a folder = HuggingFace + PaddleX caches live inside it

    # Bubble geometry (utils/bubble_interior.py - one flood-fill detector for inpainting, typesetting and refine)
    "inpaint_mode": "glyph",       # "glyph" = erase whole letters + flat-fill white bubbles; "box" = the old rectangle + TELEA
    "inpaint_use_interior": True,  # also erase dark pixels OCR boxes missed, but only inside a verified WHITE bubble
    "ocr_scan_missed_bubbles": False,  # EXPERIMENTAL: look for white bubbles with text the page pass did not find at all
    "bubble_order": "ttb",         # order of bubbles sent to the LLM / listed in the editor: ttb | rtl | ltr (see services/bubble_edit.py)
    "ocr_refine": True,            # merge bubbles OCR cut in pieces; re-read bubbles that are only half detected

    # Post-OCR bubble handling (see utils/ocr_textfix.py). Both are applied live (no restart).
    # ocr_textfix: join line-wrap hyphenation ("SQUAD- RON") and fix L/U confusions (LNIT); raw text is kept in text_raw.
    "ocr_textfix": True,
    # Bubbles with only digits ("11", "9:55") / only symbols ("?!", "...") are listed in the editor but NOT translated
    # and NOT inpainted (the original pixels stay on the page).
    "ocr_skip_numeric": True,
    "ocr_skip_symbols": True,

    # Debug Flags
    "ocr_debug": True,
    "ocr_marker_debug": True,

    # Typesetting
    "typesetting_padding_ratio": 0.08,
    "typesetting_font": "",          # path to a .ttf/.otf with Polish letters; "" = Arial / DejaVu / Liberation if found
    "typesetting_uppercase": True,
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


_LIVE = {"stamp": None, "data": None}


def _invalidate_live_cache() -> None:
    _LIVE["stamp"] = None


def get_setting(key: str, default=None):
    """
    LIVE read of one setting (re-reads settings.json when it changed on disk).

    The module-level constants below are frozen at import time, so every tuning option needed an app restart
    (and the UI said "restart the application" with no way to do it). Code that should react to the Settings
    page immediately (memory threshold, translation provider/keys, ignore patterns, post-OCR options) reads
    through this function instead.
    """
    if _DOCS_BUILD:
        return settings.get(key, default)
    try:
        st = os.stat(SETTINGS_FILE)
        stamp = (st.st_mtime_ns, st.st_size)
    except OSError:
        return settings.get(key, default)
    if _LIVE["stamp"] != stamp:
        try:
            _LIVE["data"] = _read_json(SETTINGS_FILE)
            _LIVE["stamp"] = stamp
        except (json.JSONDecodeError, OSError):
            return (_LIVE["data"] or settings).get(key, default)   # keep last good copy while the file is mid-write
    return _LIVE["data"].get(key, default)


def get_active_model_name() -> str:
    provider = get_setting("active_provider", "groq")
    return (get_setting("providers", {}) or {}).get(provider, {}).get("model", provider)


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
    _invalidate_live_cache()


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
    - Docs build (OKT_DOCS_BUILD): defaults only, data folder in a throwaway temp directory, nothing read or written.
    """
    if _DOCS_BUILD:
        docs_settings = copy.deepcopy(DEFAULT_SETTINGS)
        docs_settings["app_root_dir"] = os.path.join(tempfile.gettempdir(), "okt_docs_build")
        return docs_settings

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
"""@private The whole settings.json incl. API keys - hidden from pdoc, which would print its value into docs/."""

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
"""@private Provider configs incl. API keys - hidden from pdoc (see `settings`)."""
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