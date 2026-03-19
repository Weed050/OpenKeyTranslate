from fastapi import APIRouter, HTTPException
import os
import json
import shutil
from schemas import SettingsSchema
from routers.config import load_settings, APP_ROOT_DIR, SETTINGS_FILE

router = APIRouter(prefix="/settings", tags=["Settings"])


def get_dir_size_mb(path):
    """Oblicza rozmiar folderu w MB."""
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total / (1024 * 1024)


@router.post("/")
async def update_settings(new_settings: SettingsSchema):
    old_path = APP_ROOT_DIR
    new_path = new_settings.app_root_dir
    settings_data = new_settings.model_dump()
    migration_msg = "Ustawienia zapisane."

    if new_settings.migrate_data and old_path != new_path:
        if not os.path.exists(old_path):
            raise HTTPException(status_code=404, detail="Folder źródłowy nie istnieje.")

        size_mb = get_dir_size_mb(old_path)
        if size_mb > 500:  # np. limit 500MB
            return {"message": f"Folder jest za duży ({size_mb:.2f} MB) na automatyczną migrację. Przenieś go ręcznie."}

        try:
            print(f"Migracja: {old_path} -> {new_path}")
            shutil.copytree(old_path, new_path, dirs_exist_ok=True)

            # Próba usunięcia starego folderu
            try:
                shutil.rmtree(old_path)
                migration_msg = "Dane przeniesione pomyślnie."
            except PermissionError:
                migration_msg = "Dane skopiowane. Zamknij aplikację, aby ręcznie usunąć stary folder (baza w użyciu)."

        except Exception as e:
            if os.path.exists(new_path):
                shutil.rmtree(new_path)
            raise HTTPException(status_code=500, detail=f"Błąd migracji: {str(e)}")

    settings_data.pop("migrate_data", None)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as file:
        json.dump(settings_data, file, indent=4, ensure_ascii=False)

    return {"message": f"{migration_msg} Zrestartuj aplikację, aby zastosować zmiany."}