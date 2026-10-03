
# backend/services/reconcile.py

"""
Chapter-level DB <-> disk reconcile, run at startup (main.py lifespan) after
routers.projects.reconcile_projects_from_disk (which only handles WHOLE projects).

  raw/<folder> exists, no DB chapter   -> chapter + pages are registered
                                          (page is "processed" if its *_ocr.json exists)
  DB chapter, raw/<folder> missing     -> the DB row (and its page rows) is removed, BUT ONLY when
                                          the project's raw/ still has other chapters (so an unplugged
                                          drive / wrong workspace path never wipes the DB) and
                                          processed/<folder> is gone too. If processed/ output still
                                          exists the chapter is kept and a warning is logged.
  page row, file missing in raw/       -> only reported, never deleted.

Translation logs / corrections / glossary are never touched (logs of removed pages just stop showing
up in the Logs page, which joins through Page).
"""

import os

from sqlalchemy.orm import Session

from models.models import Chapter, Page, Project
from utils.chapter_labels import chapter_sort_key, list_images


def reconcile_chapters(db: Session) -> dict:
    report = {"added": [], "removed": [], "warnings": []}

    for project in db.query(Project).all():
        raw_root = os.path.join(project.workspace_path, "raw")
        processed_root = os.path.join(project.workspace_path, "processed")
        if not os.path.isdir(raw_root):
            continue  # project folder gone / drive offline - not a chapter-level problem

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
                base = os.path.splitext(name)[0]
                done = os.path.exists(os.path.join(processed_root, label, f"{base}_ocr.json"))
                db.add(Page(chapter_id=chapter.id, file_name=name, order=order, status="processed" if done else "pending"))
            report["added"].append(f"{project.name}/{label} ({len(images)} pages)")

        # 2. DB chapters whose raw folder vanished
        for number, chapter in known.items():
            if number in disk_dirs:
                # 3. page files missing inside an existing chapter: report only
                missing = [p.file_name for p in chapter.pages
                           if p.status != "excluded" and not os.path.exists(os.path.join(raw_root, number, p.file_name))]
                if missing:
                    report["warnings"].append(f"{project.name}/{number}: {len(missing)} page file(s) missing in raw/ (e.g. {missing[0]})")
                continue
            if not disk_dirs:
                continue  # raw/ is empty: could be anything, don't wipe
            if os.path.isdir(os.path.join(processed_root, number)):
                report["warnings"].append(
                    f"{project.name}/{number}: raw/ folder missing but processed/ still exists - DB row kept")
                continue
            report["removed"].append(f"{project.name}/{number} ({len(chapter.pages)} pages)")
            db.delete(chapter)  # cascades to pages

    db.commit()
    for key, label in (("added", "registered"), ("removed", "removed (folder gone)"), ("warnings", "WARNING")):
        for line in report[key]:
            print(f"[RECONCILE] chapter {label}: {line}")
    return report