
# backend/utils/chapter_labels.py

"""
Chapter naming + folder fingerprinting helpers (pure stdlib, no app imports,
so scripts/ and routers/ can both use them).

WHY THIS EXISTS
Chapter.number is a free-form STRING ("Chapter_13.5", "Epilog", ...). It used
to be a blind counter (Chapter_0, Chapter_1, ...) that ignored what the source
folders were actually called, so 13.5 / epilogues / re-imports got renumbered
and duplicated. Now the label is DERIVED from the source folder name, and a
cheap content fingerprint lets us spot "this folder is already imported".
"""

import hashlib
import os
import re

IMG_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp', '.jfif', '.bmp', '.tif', '.tiff')
_IGNORABLE_EXTENSIONS = {'.db', '.ini', '.txt', '.json', '.nfo', '.url', '.xml', '.md'}

# "chapter_13_5", "Series_Chapter_10", "ch 12.5", "Rozdział 7" -> number token.
# (?<![a-z]) stops "ch" matching inside words such as "March 3".
_KEYWORD_NUM = re.compile(
    r'(?<![a-z])(?:chapter|chap|ch|episode|ep|rozdzia[łl])[\s._\-]*(\d+(?:[._]\d{1,2})?)(?!\d)',
    re.IGNORECASE,
)
_ONLY_NUM = re.compile(r'#?\s*(\d+(?:[._]\d{1,2})?)')
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_LABEL_NUM = re.compile(r'^Chapter_(\d+)(?:\.(\d+))?$')


def natural_sort_key(s: str):
    """'2_a.jpg' sorts before '10_a.jpg'."""
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', s)]


def sanitize_label(name: str) -> str:
    """Make a string safe as a Windows/Linux folder name (also the DB label)."""
    cleaned = _BAD_CHARS.sub('_', name or '').strip().rstrip('. ')
    return cleaned[:80] or 'Chapter'


def _number_label(raw: str) -> str:
    whole, _, frac = raw.replace('_', '.').partition('.')
    whole = str(int(whole))
    return f"Chapter_{whole}.{frac}" if frac else f"Chapter_{whole}"


def derive_chapter_label(folder_name: str) -> str:
    """
    Source folder name -> chapter label. Keeps the ORIGINAL numbering.

      chapter_13_5                          -> Chapter_13.5
      Eternally_Regressing_Knight_Chapter_10 -> Chapter_10
      013                                   -> Chapter_13
      Epilog / Extra 2                      -> Epilog / Extra 2   (kept as-is)
    """
    name = (folder_name or '').strip()
    m = _KEYWORD_NUM.search(name)
    if m:
        return _number_label(m.group(1))
    m = _ONLY_NUM.fullmatch(name)
    if m:
        return _number_label(m.group(1))
    return sanitize_label(name)


def unique_label(label: str, taken: set) -> str:
    """label, or 'label (2)', 'label (3)'... if already taken (case-insensitive)."""
    lowered = {t.lower() for t in taken}
    if label.lower() not in lowered:
        return label
    n = 2
    while f"{label} ({n})".lower() in lowered:
        n += 1
    return f"{label} ({n})"


def chapter_sort_key(label: str):
    """Numeric labels by value (13 < 13.5 < 14), everything else after them, alphabetically."""
    m = _LABEL_NUM.match(label or '')
    if m:
        frac = float(f"0.{m.group(2)}") if m.group(2) else 0.0
        return (0, int(m.group(1)), frac, '')
    return (1, 0, 0.0, (label or '').lower())


def sha1_of_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def page_order_key(folder: str):
    """
    Reading order of SOURCE pages: file modification time first, natural file name second.
    This is the order the old "add chapters" import used, and the one the already-imported raw/ folders
    follow (many chapters were NOT in plain filename order - e.g. scraped files whose names don't sort).
    """
    def key(name: str):
        try:
            mtime = os.path.getmtime(os.path.join(folder, name))
        except OSError:
            mtime = 0.0
        return (mtime, natural_sort_key(name))
    return key


def list_images(folder: str, by_mtime: bool = False) -> list[str]:
    """Image file names directly inside `folder`: natural-sorted, or (mtime, natural) when by_mtime."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    imgs = [n for n in names
            if n.lower().endswith(IMG_EXTENSIONS) and os.path.isfile(os.path.join(folder, n))]
    return sorted(imgs, key=page_order_key(folder) if by_mtime else natural_sort_key)


def chapter_fingerprint(folder: str):
    """
    (page_count, sha1 over ALL page hashes) - "same chapter?" test that does NOT depend on page order or
    file names (raw/ pages are renamed page_001.. and may be ordered differently than the source names).
    None if the folder has no images.
    """
    imgs = list_images(folder)
    if not imgs:
        return None
    digests = sorted(sha1_of_file(os.path.join(folder, n)) for n in imgs)
    return (len(imgs), hashlib.sha1("\n".join(digests).encode()).hexdigest())


def scan_source(root: str):
    """
    Walk `root` (natural folder order) and return (chapters, skipped).
    chapters: [{"folder": abs path, "name": folder name, "rel": path relative to root, "images": [names]}]
    skipped:  [{"folder": rel path, "extensions_found": [...]}] - folders whose files are all unsupported.
    A folder that itself contains images is a chapter (works for a single chapter folder AND a parent of many).
    """
    chapters, skipped = [], []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort(key=natural_sort_key)
        images = sorted((f for f in files if f.lower().endswith(IMG_EXTENSIONS)), key=page_order_key(dirpath))
        if images:
            chapters.append({
                "folder": dirpath,
                "name": os.path.basename(os.path.normpath(dirpath)),
                "rel": os.path.relpath(dirpath, root),
                "images": images,
            })
            continue
        exts = sorted({os.path.splitext(f)[1].lower() for f in files if os.path.splitext(f)[1]} - _IGNORABLE_EXTENSIONS)
        if exts:
            skipped.append({"folder": os.path.relpath(dirpath, root), "extensions_found": exts})
    return chapters, skipped