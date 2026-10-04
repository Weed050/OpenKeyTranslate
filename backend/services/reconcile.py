
# backend/services/reconcile.py

"""
DB <-> disk reconcile. Runs at startup (main.py lifespan) AND on demand (POST /projects/{id}/rescan, the
"Rescan folders" button in the page list) - before it ran only at startup and everything it found was printed to
a log file the user never opens.

  raw/<folder> exists, no DB chapter   -> chapter + pages are registered
                                          (page is "processed" if its *_ocr.json exists)
  DB chapter, raw/<folder> missing     -> the DB row (and its page rows) is removed, BUT ONLY when
                                          the project's raw/ still has other chapters (so an unplugged
                                          drive / wrong workspace path never wipes the DB) and
                                          processed/<folder> is gone too. If processed/ output still
                                          exists the chapter is kept and a warning is returned.
  image file in raw/<chapter>/ with no page row -> NEW: registered as a page (appended; order = last + 1)
  page row, file missing in raw/       -> only reported, never deleted.
  page status vs processed/ output     -> NEW: "processed" without json/png -> pending;
                                          "pending"/"failed" WITH json + png -> processed.

Translation logs / corrections / glossary are never touched (logs of removed pages just stop showing
up in the Logs page, which joins through Page).

The returned report (added / removed / warnings / fixed) is stored in LAST_REPORT so GET /system/health can show a
banner, and is returned by the rescan endpoint so the UI can show exactly what changed.
"""

import os

from sqlalchemy.orm import Session

from models.models import Chapter, Page, Project
from utils.chapter_labels import chapter_sort_key, list_images

LAST_REPORT: dict = {"added": [], "removed": [], "warnings": [], "fixed": []}


def _outputs_exist(processed_dir: str, label: str, file_name: str) -> bool:
    base = os.path.splitext(file_name)[0]
    d = os.path.join(processed_dir, label)
    return os.path.exists(os.path.join(d, f"{base}_ocr.json")) and os.path.exists(os.path.join(d, f"ocr_{base}_inpainted.png"))


def reconcile_chapters(db: Session, project_id: int | None = None) -> dict:
    report = {"added": [], "removed": [], "warnings": [], "fixed": []}

    query = db.query(Project)
    if project_id is not None:
        query = query.filter(Project.id == project_id)

    for project in query.all():
        raw_root = os.path.join(project.workspace_path, "raw")
        processed_root = os.path.join(project.workspace_path, "processed")
        if not os.path.isdir(raw_root):
            report["warnings"].append(f"{project.name}: raw/ folder not found ({raw_root}) - drive offline / moved?")
            continue

        disk_dirs = {d for d in os.listdir(raw_root) if os.path.isdir(os.path.join(raw_root, d))}
        known = {c.number: c for c in project.chapters}

        # 1. folders on disk without a DB chapter -> register them
        for label in sorted(disk_dirs - set(known), key=chapter_sort_key):
            images = list_images(os.path.join(raw_root, label))
            if not images:
                continue
            chapter = Chapter(
                project_id=project.id, number=label, title=label.replace("_", " "),
                raw_path=os.path.join(raw_root, label), processed_path=os.path.join(processed_root, label),
            )
            db.add(chapter)
            db.flush()
            for order, name in enumerate(images, start=1):
                db.add(Page(chapter_id=chapter.id, file_name=name, order=order,
                            status="processed" if _outputs_exist(processed_root, label, name) else "pending"))
            report["added"].append(f"{project.name}/{label} ({len(images)} pages)")

        for number, chapter in known.items():
            if number in disk_dirs:
                chapter_dir = os.path.join(raw_root, number)
                disk_images = list_images(chapter_dir)
                db_names = {p.file_name for p in chapter.pages}

                # 2. image files added by hand to an existing chapter folder -> new pages (appended)
                new_files = [n for n in disk_images if n not in db_names]
                next_order = max([p.order or 0 for p in chapter.pages] + [0]) + 1
                for name in new_files:
                    db.add(Page(chapter_id=chapter.id, file_name=name, order=next_order,
                                status="processed" if _outputs_exist(processed_root, number, name) else "pending"))
                    next_order += 1
                if new_files:
                    report["added"].append(f"{project.name}/{number}: {len(new_files)} new page file(s) ({new_files[0]}...)")

                # 3. page files missing inside an existing chapter: report only
                missing = [p.file_name for p in chapter.pages
                           if p.status != "excluded" and p.file_name not in disk_images]
                if missing:
                    report["warnings"].append(f"{project.name}/{number}: {len(missing)} page file(s) missing in raw/ (e.g. {missing[0]})")

                # 4. page status vs. real outputs
                for p in chapter.pages:
                    if p.status == "excluded" or p.file_name in missing:
                        continue
                    has = _outputs_exist(processed_root, number, p.file_name)
                    if p.status == "processed" and not has:
                        p.status = "pending"
                        report["fixed"].append(f"{project.name}/{number}/{p.file_name}: marked processed but output files are gone -> pending")
                    elif p.status in ("pending", "failed") and has:
                        p.status = "processed"
                        report["fixed"].append(f"{project.name}/{number}/{p.file_name}: output exists -> processed")
                continue

            # DB chapter whose raw folder vanished
            if not disk_dirs:
                continue  # raw/ is empty: could be anything, don't wipe
            if os.path.isdir(os.path.join(processed_root, number)):
                report["warnings"].append(
                    f"{project.name}/{number}: raw/ folder missing but processed/ still exists - DB row kept")
                continue
            report["removed"].append(f"{project.name}/{number} ({len(chapter.pages)} pages)")
            db.delete(chapter)  # cascades to pages

    db.commit()
    for key, label in (("added", "registered"), ("removed", "removed (folder gone)"), ("fixed", "status fixed"), ("warnings", "WARNING")):
        for line in report[key]:
            print(f"[RECONCILE] chapter {label}: {line}")
    if project_id is None:
        LAST_REPORT.update(report)
    return report
