# backend/scripts/build_docs.py

"""
Build the pdoc HTML documentation into docs/ WITHOUT leaking secrets.

pdoc imports every module and prints the VALUE of every module-level variable. Run plainly it once put the whole
settings.json - Groq key included - into docs/core/config.html and docs/search.js, which are public (GitHub repo +
GitHub Pages). This script:
  1. sets OKT_DOCS_BUILD=1, so core/config.py uses DEFAULT_SETTINGS (empty keys, temp data folder) and never reads
     settings.json;
  2. builds into a temporary folder;
  3. scans every generated file for API-key patterns AND for the real key values from settings.json / .env (so a
     key of an unknown format is caught too) and STOPS without touching docs/ if anything is found;
  4. only then copies the result into docs/ (hand-written files such as docs/ARCHITECTURE_API.md are kept).

Usage (from backend/):
    python scripts/build_docs.py                 # default module list (same as the published docs)
    python scripts/build_docs.py core models     # only these modules
Requires: pip install pdoc
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROJECT_DIR = os.path.dirname(BACKEND_DIR)
DOCS_DIR = os.path.join(PROJECT_DIR, "docs")
DEFAULT_MODULES = ["main", "test_main", "core", "models", "routers", "services", "utils"]

# Same patterns as .githooks/pre-commit
KEY_PATTERN = re.compile(
    r"gsk_[A-Za-z0-9]{20,}"                       # Groq
    r"|AIza[0-9A-Za-z_-]{30,}"                    # Google / Gemini
    r"|sk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"      # OpenAI / Anthropic
    r"|hf_[A-Za-z0-9]{20,}"                       # Hugging Face
    r"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


def _mask(value: str) -> str:
    return value[:6] + "..." + value[-3:] if len(value) > 12 else "***"


def _settings_candidates() -> list[str]:
    """Where settings.json can be (core/config.py SETTINGS_FILE; before 2026-10-28 it was in the project folder)."""
    app_root = (os.path.join(os.environ.get("APPDATA", ""), "OpenKeyTranslate") if os.name == "nt"
                else os.path.join(os.path.expanduser("~"), ".openkeytranslate"))
    paths = [os.environ.get("OKT_SETTINGS_FILE", ""), os.path.join(app_root, "settings.json"),
             os.path.join(app_root, "legacy_settings.json.bak"), os.path.join(PROJECT_DIR, "settings.json")]
    return [p for p in paths if p and os.path.exists(p)]


def _real_secret_values() -> list[str]:
    """API keys from settings.json and secret-looking entries of backend/.env - only to search for them, never printed."""
    values = []
    for settings_path in _settings_candidates():
        try:
            with open(settings_path, encoding="utf-8") as f:
                data = json.load(f)
            for provider in (data.get("providers") or {}).values():
                values += [k.get("api_key", "") for k in provider.get("keys", [])]
                values.append(provider.get("api_key", ""))
        except (OSError, json.JSONDecodeError):
            pass
    env_path = os.path.join(BACKEND_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                name, _, value = line.partition("=")
                if re.search(r"KEY|TOKEN|SECRET|PASSWORD", name, re.I):
                    values.append(value.strip().strip("\"'"))
    return [v for v in values if isinstance(v, str) and len(v) >= 8]


def scan(folder: str) -> list[str]:
    """Return 'file: masked match' for every secret found in the generated files."""
    secrets = _real_secret_values()
    hits = []
    for root, _, files in os.walk(folder):
        for name in files:
            path = os.path.join(root, name)
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            rel = os.path.relpath(path, folder)
            hits += [f"{rel}: {_mask(m.group(0))}" for m in KEY_PATTERN.finditer(text)]
            hits += [f"{rel}: value from settings.json/.env ({_mask(s)})" for s in secrets if s in text]
    return hits


def main() -> int:
    modules = sys.argv[1:] or DEFAULT_MODULES
    env = dict(os.environ, OKT_DOCS_BUILD="1")
    with tempfile.TemporaryDirectory(prefix="okt_docs_") as out:
        print(f"[DOCS] pdoc {' '.join(modules)} -> temp folder (settings.json is NOT read)")
        subprocess.run([sys.executable, "-m", "pdoc", "-o", out, *modules], cwd=BACKEND_DIR, env=env, check=True)
        hits = scan(out)
        if hits:
            print("[DOCS] STOP - secrets found in the generated docs, docs/ was NOT changed:")
            for h in hits:
                print("   ", h)
            return 1
        shutil.copytree(out, DOCS_DIR, dirs_exist_ok=True)
    print(f"[DOCS] OK - no secrets found, written to {DOCS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
