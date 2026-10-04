
# backend/services/chapter_import.py

"""
Chapter import core - shared by the first import (routers/projects.py
import_folder) and the "add chapters" flow (routers/chapters.py).

Rules (all of them exist because the old counter-based import broke them):
  1. Chapter label comes from the SOURCE FOLDER NAME (utils.chapter_labels),
     never from a running counter. 13.5 stays 13.5, "Epilog" stays "Epilog".
  2. A folder whose content fingerprint already exists in the project is a
     DUPLICATE and is not selected by default (re-importing a parent folder
     no longer clones everything).
  3. Labels must be unique inside a project; conflicts are reported, never
     silently renumbered.
  4. All-or-nothing: everything is validated before the first file is copied,
     and a failure mid-way removes the folders this call created.
Pages are numbered page_001.. in (file mtime, natural name) order - the order the old
"add chapters" import used, and the one existing raw/ folders follow.
"""

import os
import shutil

from sqlalchemy.orm import Session

from models.models import Chapter, Page, Project
from utils.chapter_labels import (
    chapter_fingerprint, derive_chapter_label, list_images, sanitize_label,
    scan_source, unique_label,
)


class ChapterImportError(Exception):
    """Validation failed before anything was copied. `.errors` = list of human-readable strings."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _raw_dir(project: Project, label: str) -> str:
    return os.path.join(project.workspace_path, "raw", label)


def _processed_dir(project: Project, label: str) -> str:
    return os.path.join(project.workspace_path, "processed", label)


def existing_fingerprints(project: Project) -> dict:
    """{label: fingerprint or None} for chapters already in the project (read from their raw/ folder)."""
    return {ch.number: chapter_fingerprint(_raw_dir(project, ch.number)) for ch in project.chapters}


def build_preview(project: Project, root: str, order_mode: str = "auto") -> dict:
    """
    Scan `root` and classify every candidate chapter folder against the project:
      new             - free label, unseen content            (selected by default)
      duplicate       - same content as chapter `conflict_with` (not selected)
      label_conflict  - label already used by `conflict_with`   (not selected; rename to import)
    """
    candidates, skipped = scan_source(root, order_mode)
    existing = existing_fingerprints(project)
    taken = set(existing)
    fp_to_label = {fp: label for label, fp in existing.items() if fp}

    rows = []
    for c in candidates:
        fp = chapter_fingerprint(c["folder"])
        label = derive_chapter_label(c["name"])
        status, conflict_with = "new", None
        if fp in fp_to_label:
            status, conflict_with = "duplicate", fp_to_label[fp]
        elif label.lower() in {t.lower() for t in taken}:
            status, conflict_with = "label_conflict", label
        else:
            taken.add(label)
            fp_to_label[fp] = label  # later identical folders in this same batch = duplicates
        rows.append({
            "folder": c["folder"], "rel": c["rel"], "source_name": c["name"],
            "label": label, "pages": len(c["images"]),
            "order_method": c["order_method"], "order_disagree": c["order_disagree"],  # UI: warn when > 0
            "first_pages": c["images"][:3], "ignored_files": c["ignored_files"],        # UI: what page 1 is / files NOT imported
            "status": status, "conflict_with": conflict_with,
            "selected": status == "new",
        })
    return {"chapters": rows, "skipped_folders": skipped}


def auto_selections(root: str) -> list[dict]:
    """First-import helper: every folder, label from name, collisions get ' (2)' suffixes."""
    candidates, _ = scan_source(root)
    taken: set = set()
    selections = []
    for c in candidates:
        label = unique_label(derive_chapter_label(c["name"]), taken)
        taken.add(label)
        selections.append({"folder": c["folder"], "label": label})
    return selections


def import_selected(db: Session, project: Project, selections: list[dict], allow_duplicates: bool = False,
                    order_mode: str = "auto") -> dict:
    """
    Import [{"folder": abs path, "label": str}, ...] into `project`.
    Raises ChapterImportError (nothing touched) if validation fails.
    """
    existing = existing_fingerprints(project)
    taken = {t.lower() for t in existing}
    fp_seen = {fp: label for label, fp in existing.items() if fp}

    errors, plan = [], []
    for sel in selections:
        folder = sel.get("folder") or ""
        label = sanitize_label(sel.get("label") or derive_chapter_label(os.path.basename(os.path.normpath(folder))))
        images = list_images(folder, by_mtime=True, mode=order_mode)  # same page order as scan_source
        if not os.path.isdir(folder) or not images:
            errors.append(f"'{folder}': folder missing or has no supported images")
            continue
        if label.lower() in taken:
            errors.append(f"label '{label}' already used - rename it or skip this folder")
            continue
        if os.path.exists(_raw_dir(project, label)):
            errors.append(f"label '{label}': folder already exists on disk (orphan?) - rename it")
            continue
        fp = chapter_fingerprint(folder)
        if not allow_duplicates and fp in fp_seen:
            errors.append(f"'{label}' has the same content as {fp_seen[fp]} - skip it (or import with allow_duplicates)")
            continue
        taken.add(label.lower())
        fp_seen.setdefault(fp, label)
        plan.append((folder, label, images))

    if errors:
        raise ChapterImportError(errors)

    created_dirs, added_labels, added_pages = [], [], 0
    try:
        for folder, label, images in plan:
            raw_dir, processed_dir = _raw_dir(project, label), _processed_dir(project, label)
            os.makedirs(raw_dir)
            created_dirs.append(raw_dir)
            if not os.path.isdir(processed_dir):
                os.makedirs(processed_dir)
                created_dirs.append(processed_dir)

            chapter = Chapter(
                project_id=project.id, number=label, title=label.replace("_", " "),
                raw_path=raw_dir, processed_path=processed_dir,
            )
            db.add(chapter)
            db.flush()

            for i, name in enumerate(images, start=1):
                ext = os.path.splitext(name)[1].lower()
                std_name = f"page_{i:03d}{ext}"
                shutil.copy2(os.path.join(folder, name), os.path.join(raw_dir, std_name))
                db.add(Page(chapter_id=chapter.id, file_name=std_name, order=i))
                added_pages += 1
            added_labels.append(label)
        db.commit()
    except Exception:
        db.rollback()
        for d in reversed(created_dirs):
            shutil.rmtree(d, ignore_errors=True)
        raise

    return {"chapters_added": len(added_labels), "pages_added": added_pages, "labels": added_labels}