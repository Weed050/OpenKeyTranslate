import os
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)
SETTINGS_FILE = os.path.join(ROOT_DIR, "settings.json")


def get_default_app_root():
    """ Calculates root directory path (%AppData%...)"""
    app_name = "OpenKeyTranslate"
    if os.name == 'nt':
        return os.path.join(os.environ.get('APPDATA'), app_name)
    return os.path.join(os.path.expanduser('~'), f".{app_name.lower()}")


def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        default = {
            "app_root_dir": get_default_app_root(),
            "source_lang": "en",
            "target_lang": "pl"
        }
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
            json.dump(default, file, indent=4)
        return default

    with open(SETTINGS_FILE, "r", encoding="utf-8") as file:
        return json.load(file)

settings = load_settings()
APP_ROOT_DIR = settings.get("app_root_dir")
WORKSPACE_DIR = os.path.join(APP_ROOT_DIR, 'projects_workspace')
DATABASE_PATH = os.path.join(APP_ROOT_DIR, 'openkey_translate.db')

os.makedirs(WORKSPACE_DIR, exist_ok=True)