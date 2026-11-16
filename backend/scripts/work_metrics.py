
# backend/scripts/work_metrics.py

"""
Thesis evaluation, part B: how much work does the tool leave to the translator?

Part A (scripts/replay_examples.py) asks whether examples from the correction memory improve the model. This script
asks a plainer question about the whole tool, from what the user actually did while reviewing - no API request, no
model, nothing re-run:

  translation   per reviewed bubble, the AI draft (`ai_translation`) against the user's final text (`translation`):
                - accepted unchanged / only case-punctuation-spacing changed / edited
                - character edit distance (Levenshtein) and its rate per character of the final text
                - word edit rate (Levenshtein on words, no shifts) and HTER (TER with shifts, sacrebleu - optional:
                  setup/requirements_research.txt; without it the HTER columns stay empty)
                - characters saved = length of the final text minus the edit distance: what the user did NOT have to
                  type compared with typing the translation from scratch (a keystroke model: one character edit = one
                  action; negative = the draft cost more to fix than to retype)
  manual work   per page, the user's manual operations recorded in the page JSON (`manual_ops`, page_export.
                record_manual_op): bubble fixes (merge / split / move lines / add / delete / skip / order), OCR text
                fixes (re-read a bubble or page, edit the English text), retranslations, clean-ups, lettering.

What the numbers mean (and do not):
  - `ai_translation` is the LAST AI draft the user saw: "Retranslate" on a bubble replaces it, a merge joins the drafts,
    a split divides them. Edits are measured against that draft, as the user met it.
  - Bubbles accepted with the whole page ("Accept page", `confirmed_via: "page"`) may never have been read one by one;
    they are counted separately ("one by one" = without them).
  - The `manual_ops` counter exists since 07.11.2026. A page counts for manual work only if work on it started after
    --ops-since (the first OCR or translation of the page; default 2026-11-08); older pages (all of Nihonkoku Shoukan)
    have no list, and no list there does NOT mean no work. Pages read by the OCR before and after the bubble-grouping
    change of 10.11.2026 need ~10x fewer manual merges after it: they are reported apart (--grouping-change).
  - Translation edits are NOT in manual_ops - they are the edit distances above. Time is not measured here (the plan:
    a separate timed comparison on a few pages).

Data safety: the database is opened READ-ONLY, page JSONs are only read, nothing of the app is written. The output
(default <app root>/research/work_<project>/) holds numbers only - lengths, distances, counts, page file names and
bubble ids, no text of the comic: pages.csv, bubbles.csv, summary.json.

Usage (from backend/, with the app's venv):
  python scripts/work_metrics.py --list                                     # projects and what they hold
  python scripts/work_metrics.py --project "Give My Regards to Black Jack"  # one project
  python scripts/work_metrics.py                                            # every project with reviewed bubbles
"""

import argparse
import csv
import json
import os
import sqlite3
import statistics
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timezone

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

# manual_ops op -> group (thesis tables). A new op name added to record_manual_op later lands in "other" until it is
# listed here - it is still counted.
OP_GROUPS = {
    "merge": "bubbles", "split": "bubbles", "move_lines": "bubbles", "add_bubble": "bubbles",
    "add_bubble_manual": "bubbles", "delete_bubble": "bubbles", "skip": "bubbles", "unskip": "bubbles",
    "reorder": "bubbles",
    "reocr_bubble": "ocr_text", "reocr_undo": "ocr_text", "edit_source": "ocr_text", "reocr_page": "ocr_text",
    "retranslate_bubble": "translation",
    "cleanup_add": "cleaning", "cleanup_undo": "cleaning", "cleanup_clear": "cleaning", "erase_off": "cleaning",
    "erase_on": "cleaning",
    "layout": "lettering", "layout_reset": "lettering", "layout_bulk": "lettering",
}
GROUPS = ["bubbles", "ocr_text", "translation", "cleaning", "lettering", "other"]
DEFAULT_OPS_SINCE = "2026-11-08"
DEFAULT_GROUPING_CHANGE = "2026-11-11"


# ---------------------------------------------------------------- reading (read-only)

def _utc(value) -> datetime | None:
    """ISO text with or without offset (page JSON: offset; SQLite via SQLAlchemy: naive UTC) -> naive UTC."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _day(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise SystemExit(f"Not a date: {value!r} (expected YYYY-MM-DD)")


def _natural(value):
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def connect_readonly(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise SystemExit(f"Database not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_projects(con, wanted: list[str] | None) -> list[dict]:
    rows = [dict(r) for r in con.execute("SELECT id, name FROM projects ORDER BY id")]
    if not wanted:
        return rows
    out = []
    for w in wanted:
        hit = next((r for r in rows if r["name"] == w or str(r["id"]) == w), None)
        if hit is None:
            raise SystemExit(f"Project '{w}' not found. Projects: {', '.join(r['name'] for r in rows)}")
        out.append(hit)
    return out


def load_pages(con, project_id: int) -> list[dict]:
    rows = con.execute("""SELECT p.id, p.file_name, p."order" AS ord, p.status, c.number AS chapter
                          FROM pages p JOIN chapters c ON c.id = p.chapter_id WHERE c.project_id = ?""",
                       (project_id,)).fetchall()
    pages = [dict(r) for r in rows]
    pages.sort(key=lambda p: (_natural(p["chapter"]), p["ord"] or 0, p["file_name"]))
    return pages


def first_translation_times(con, project_id: int) -> dict[int, datetime]:
    rows = con.execute("""SELECT l.page_id, MIN(l.created_at) AS t FROM translation_logs l
                          JOIN pages p ON p.id = l.page_id JOIN chapters c ON c.id = p.chapter_id
                          WHERE c.project_id = ? GROUP BY l.page_id""", (project_id,))
    return {r["page_id"]: _utc(r["t"]) for r in rows if r["t"]}


def page_json_path(workspace_dir: str, project_name: str, page: dict) -> str:
    stem = os.path.splitext(page["file_name"])[0]
    return os.path.join(workspace_dir, project_name, "processed", str(page["chapter"]), f"{stem}_ocr.json")


# ---------------------------------------------------------------- measures

def _bare(text: str) -> str:
    """Text without case, punctuation and spacing - equal bare texts = only those changed."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", text).casefold()
                   if not unicodedata.category(ch).startswith(("P", "Z", "S")) and not ch.isspace())


def bubble_reviewed(b: dict) -> bool:
    """Same rule as services/page_export.bubble_reviewed (copied: importing it would load the app's config)."""
    text = (b.get("translation") or "").strip()
    if not text:
        return False
    return text != (b.get("ai_translation") or "").strip() or b.get("confirmed_translation") == b.get("translation")


def measure_bubble(b: dict, ter=None) -> dict:
    from rapidfuzz.distance import Levenshtein
    ai, final = (b.get("ai_translation") or "").strip(), (b.get("translation") or "").strip()
    row = {"bubble_id": b["bubble_id"], "via_page": b.get("confirmed_via") == "page",
           "bulk_edited": bool(b.get("bulk_edits")), "source_words": len((b.get("text") or "").split()),
           "ai_chars": len(ai), "final_chars": len(final), "ai_words": len(ai.split()), "final_words": len(final.split())}
    if not ai:                          # no AI draft (failed translation, typed by the user): nothing to compare
        row.update(status="no_draft", char_dist=None, word_dist=None, chars_saved=None, ter=None)
        return row
    char_dist = Levenshtein.distance(ai, final)
    row.update(
        status="unchanged" if ai == final else ("minor" if _bare(ai) == _bare(final) else "edited"),
        char_dist=char_dist, word_dist=Levenshtein.distance(ai.split(), final.split()),
        chars_saved=len(final) - char_dist,
        ter=round(ter.sentence_score(ai, [final]).score / 100, 4) if ter is not None else None)
    return row


def ops_of(data: dict) -> list[dict]:
    return [o for o in data.get("manual_ops") or [] if isinstance(o, dict) and o.get("op")]


# ---------------------------------------------------------------- one project

def collect(con, project: dict, workspace_dir: str, ops_since: datetime, grouping_change: datetime, ter) -> dict:
    pages = load_pages(con, project["id"])
    first_tr = first_translation_times(con, project["id"])
    page_rows, bubble_rows = [], []
    texts = {}                          # (page_id, bubble_id) -> (AI draft, final): HTER only, never written out
    missing_json = 0
    for p in pages:
        path = page_json_path(workspace_dir, project["name"], p)
        if p["status"] == "excluded":
            continue
        if not os.path.exists(path):
            missing_json += p["status"] != "pending"
            continue
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        todo = [b for b in data.get("bubbles", []) if not b.get("skip")]
        reviewed = [b for b in todo if bubble_reviewed(b)]
        ocr_at = _utc((data.get("ocr_debug") or {}).get("at"))
        starts = [t for t in (ocr_at, first_tr.get(p["id"])) if t]
        work_start = min(starts) if starts else None
        tracked = work_start is not None and work_start >= ops_since
        layout = bool(data.get("layout_checked_at"))
        # as page_export.page_stage: a page with nothing to translate (no text, only skipped numbers) is text-reviewed
        stage = ("checked" if layout else "reviewed") if len(reviewed) == len(todo) else \
                ("ocr" if not any((b.get("translation") or "").strip() for b in todo) else "in_review")
        ops = ops_of(data)
        retranslated = {o.get("bubble") for o in ops if o["op"] == "retranslate_bubble"}

        measured = []
        for b in reviewed:
            m = measure_bubble(b, ter)
            m["retranslated"] = (b["bubble_id"] in retranslated) if tracked else None
            measured.append(m)
            bubble_rows.append({"page_id": p["id"], "chapter": p["chapter"], "file": p["file_name"], **m})
            if m["status"] != "no_draft":
                texts[(p["id"], b["bubble_id"])] = ((b.get("ai_translation") or "").strip(),
                                                    (b.get("translation") or "").strip())

        by_op = Counter(o["op"] for o in ops)
        by_group = Counter()
        for op, n in by_op.items():
            by_group[OP_GROUPS.get(op, "other")] += n
        drafted = [m for m in measured if m["status"] != "no_draft"]
        page_rows.append({
            "page_id": p["id"], "chapter": p["chapter"], "file": p["file_name"], "stage": stage,
            "layout_checked": layout, "ocr_date": ocr_at.date().isoformat() if ocr_at else "",
            "grouping": ("" if ocr_at is None else "new" if ocr_at >= grouping_change else "old"),
            "bubbles": len(todo), "reviewed": len(reviewed),
            "page_accepted": sum(1 for m in measured if m["via_page"]),
            "unchanged": sum(1 for m in measured if m["status"] == "unchanged"),
            "minor": sum(1 for m in measured if m["status"] == "minor"),
            "edited": sum(1 for m in measured if m["status"] == "edited"),
            "no_draft": sum(1 for m in measured if m["status"] == "no_draft"),
            "final_chars": sum(m["final_chars"] for m in drafted),
            "char_dist": sum(m["char_dist"] for m in drafted),
            "final_words": sum(m["final_words"] for m in drafted),
            "word_dist": sum(m["word_dist"] for m in drafted),
            "ops_tracked": tracked,
            **{f"ops_{g}": (by_group.get(g, 0) if tracked else None) for g in GROUPS},
            "ops_total": sum(by_op.values()) if tracked else None,
            "ops_detail": ";".join(f"{op}:{n}" for op, n in sorted(by_op.items())) if tracked else "",
        })
    return {"pages": page_rows, "bubbles": bubble_rows, "texts": texts, "missing_json": missing_json}


def _share(part, whole):
    return round(100.0 * part / whole, 1) if whole else None


def translation_summary(bubbles: list[dict], hyps: list[str], refs: list[str], ter) -> dict:
    n = len(bubbles)
    drafted = [b for b in bubbles if b["status"] != "no_draft"]
    status = Counter(b["status"] for b in bubbles)
    final_chars = sum(b["final_chars"] for b in drafted)
    char_dist = sum(b["char_dist"] for b in drafted)
    final_words = sum(b["final_words"] for b in drafted)
    word_dist = sum(b["word_dist"] for b in drafted)
    saved = sum(b["chars_saved"] for b in drafted)
    out = {
        "bubbles": n,
        "unchanged_pct": _share(status["unchanged"], n), "minor_only_pct": _share(status["minor"], n),
        "edited_pct": _share(status["edited"], n), "no_draft": status["no_draft"],
        "char_edit_rate_pct": _share(char_dist, final_chars),          # corpus: all edits / all final characters
        "char_edit_rate_mean_pct": round(100 * statistics.fmean(b["char_dist"] / max(1, b["final_chars"])
                                                                for b in drafted), 1) if drafted else None,
        "word_edit_rate_pct": _share(word_dist, final_words),
        "chars_final": final_chars, "chars_edited": char_dist,
        "chars_saved": saved, "chars_saved_pct": _share(saved, final_chars),
        "bubbles_draft_worse_than_typing": sum(1 for b in drafted if b["chars_saved"] < 0),
        "bulk_edited": sum(1 for b in bubbles if b["bulk_edited"]),
        "hter_pct": None,
    }
    if ter is not None and hyps:
        out["hter_pct"] = round(ter.corpus_score(hyps, [refs]).score, 1)
    edited = [b for b in drafted if b["status"] != "unchanged"]
    out["edited_only_char_edit_rate_pct"] = _share(sum(b["char_dist"] for b in edited),
                                                   sum(b["final_chars"] for b in edited))
    return out


def ops_summary(pages: list[dict]) -> dict:
    if not pages:
        return {"pages": 0}
    totals = [p["ops_total"] for p in pages]
    bubbles = sum(p["bubbles"] for p in pages)
    out = {"pages": len(pages), "bubbles": bubbles,
           "ops_per_page_mean": round(statistics.fmean(totals), 2), "ops_per_page_median": statistics.median(totals),
           "pages_without_ops_pct": _share(sum(1 for t in totals if t == 0), len(pages)),
           "ops_per_10_bubbles": round(10 * sum(totals) / bubbles, 2) if bubbles else None, "groups": {}}
    for g in GROUPS:
        vals = [p[f"ops_{g}"] for p in pages]
        out["groups"][g] = {"total": sum(vals), "per_page_mean": round(statistics.fmean(vals), 2),
                            "pages_with_pct": _share(sum(1 for v in vals if v), len(pages))}
    by_op = Counter()
    for p in pages:
        for part in filter(None, p["ops_detail"].split(";")):
            op, n = part.rsplit(":", 1)
            by_op[op] += int(n)
    out["by_op"] = dict(by_op.most_common())
    return out


def summarize(project: dict, got: dict, ter, args) -> dict:
    pages, bubbles = got["pages"], got["bubbles"]

    def tr(sel):
        pairs = [got["texts"][(b["page_id"], b["bubble_id"])] for b in sel if b["status"] != "no_draft"]
        return translation_summary(sel, [a for a, _ in pairs], [f for _, f in pairs], ter)

    one_by_one = [b for b in bubbles if not b["via_page"]]
    done = [p for p in pages if p["stage"] in ("reviewed", "checked") and p["ops_tracked"]]
    summary = {
        "project": project["name"], "made_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ops_since": args.ops_since, "grouping_change": args.grouping_change, "hter": ter is not None,
        "pages": {"with_json": len(pages), "text_reviewed": sum(1 for p in pages if p["stage"] in ("reviewed", "checked")),
                  "layout_checked": sum(1 for p in pages if p["stage"] == "checked"),
                  "ops_tracked": sum(1 for p in pages if p["ops_tracked"]),
                  "processed_without_json": got["missing_json"]},
        "translation": {"all_reviewed": tr(bubbles), "one_by_one": tr(one_by_one)},
        # text_reviewed = every bubble reviewed (the usual end of work - "Layout checked" is optional in the editor);
        # old / new grouping = read by the OCR before / after --grouping-change (merges needed dropped ~10x)
        "manual_ops": {
            "text_reviewed_pages": ops_summary(done),
            "text_reviewed_old_grouping": ops_summary([p for p in done if p["grouping"] == "old"]),
            "text_reviewed_new_grouping": ops_summary([p for p in done if p["grouping"] == "new"]),
            "layout_checked_pages": ops_summary([p for p in done if p["stage"] == "checked"]),
        },
        "ocr_dates": dict(sorted(Counter(p["ocr_date"] or "unknown" for p in pages).items())),
    }
    return summary


# ---------------------------------------------------------------- output

def _write_csv(path: str, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})


def _print_summary(s: dict) -> None:
    pg = s["pages"]
    print(f"\n[B] {s['project']}: {pg['text_reviewed']} page(s) with every bubble reviewed, {pg['layout_checked']} "
          f"fully reviewed (layout checked), manual ops tracked on {pg['ops_tracked']}")
    for name, t in s["translation"].items():
        if not t["bubbles"] or (name == "one_by_one" and t["bubbles"] == s["translation"]["all_reviewed"]["bubbles"]):
            continue                                   # no bubble accepted with "Accept page": the same numbers again
        hter = f", HTER {t['hter_pct']}%" if t["hter_pct"] is not None else ""
        print(f"  translation, {name.replace('_', ' ')} ({t['bubbles']} bubbles): unchanged {t['unchanged_pct']}%, "
              f"only case/punctuation {t['minor_only_pct']}%, edited {t['edited_pct']}%; character edit rate "
              f"{t['char_edit_rate_pct']}% (edited bubbles only {t['edited_only_char_edit_rate_pct']}%), word edit rate "
              f"{t['word_edit_rate_pct']}%{hter}; characters saved {t['chars_saved_pct']}% "
              f"({t['chars_saved']} of {t['chars_final']})")
    for name, o in s["manual_ops"].items():
        if not o.get("pages"):
            continue
        groups = ", ".join(f"{g} {v['per_page_mean']}" for g, v in o["groups"].items() if v["total"])
        print(f"  manual ops, {name.replace('_', ' ')} ({o['pages']} pages, {o['bubbles']} bubbles): "
              f"{o['ops_per_page_mean']} per page (median {o['ops_per_page_median']}), {o['ops_per_10_bubbles']} per 10 "
              f"bubbles, pages with none {o['pages_without_ops_pct']}%" + (f"; per page: {groups}" if groups else ""))
    if not s["manual_ops"]["text_reviewed_pages"].get("pages"):
        print(f"  manual ops: no reviewed page whose work started on/after {s['ops_since']} - the counter did not exist "
              f"before (no list is not zero work)")
    print(f"  OCR dates of the pages: " + ", ".join(f"{d} {n}" for d, n in s["ocr_dates"].items())
          + f" (new bubble grouping from {s['grouping_change']})")


def output_dir(args, app_root: str, project_name: str, several: bool) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in project_name).strip("_").lower()
    if args.out:
        return os.path.join(args.out, slug) if several else args.out
    return os.path.join(app_root, "research", f"work_{slug}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Part B: how much work the tool leaves (edits of AI drafts, manual ops).")
    parser.add_argument("--project", action="append", help="project name or id (repeat for several; default: all)")
    parser.add_argument("--list", action="store_true", help="list projects with page / bubble counts and stop")
    parser.add_argument("--ops-since", default=DEFAULT_OPS_SINCE,
                        help=f"manual ops count only on pages whose work started on/after this day (default {DEFAULT_OPS_SINCE})")
    parser.add_argument("--grouping-change", default=DEFAULT_GROUPING_CHANGE,
                        help=f"pages read by the OCR from this day on use the new bubble grouping (default {DEFAULT_GROUPING_CHANGE})")
    parser.add_argument("--out", default=None, help="output folder (default <app root>/research/work_<project>)")
    parser.add_argument("--db", default=None, help="database file (default: the app's)")
    parser.add_argument("--workspace", default=None, help="projects folder (default: the app's)")
    args = parser.parse_args()

    if args.db and args.workspace:
        db_path, workspace, app_root = args.db, args.workspace, os.path.dirname(os.path.abspath(args.db))
    else:
        from core.config import APP_ROOT_DIR, DATABASE_PATH, WORKSPACE_DIR
        db_path, workspace, app_root = args.db or DATABASE_PATH, args.workspace or WORKSPACE_DIR, APP_ROOT_DIR
    ops_since, grouping_change = _day(args.ops_since), _day(args.grouping_change)
    try:
        from sacrebleu.metrics import TER
        ter = TER()
    except ImportError:
        ter = None
        print("[B] sacrebleu not installed - HTER left out (pip install -r ../setup/requirements_research.txt); "
              "the character and word edit rates are computed anyway.")

    con = connect_readonly(db_path)
    projects = load_projects(con, args.project)
    results = []
    for project in projects:
        got = collect(con, project, workspace, ops_since, grouping_change, ter)
        if args.list:
            pages = got["pages"]
            print(f"  {project['id']:>3}  {project['name']:<50} pages with JSON {len(pages):>4}  reviewed bubbles "
                  f"{len(got['bubbles']):>5}  layout checked {sum(1 for p in pages if p['stage'] == 'checked'):>4}  "
                  f"ops tracked {sum(1 for p in pages if p['ops_tracked']):>4}")
            continue
        if not got["bubbles"] and not args.project:
            continue                                   # nothing reviewed: not worth a folder
        results.append((project, got))
    con.close()
    if args.list:
        return

    several = len(results) > 1
    for project, got in results:
        summary = summarize(project, got, ter, args)
        out = output_dir(args, app_root, project["name"], several)
        os.makedirs(out, exist_ok=True)
        _write_csv(os.path.join(out, "pages.csv"), got["pages"])
        _write_csv(os.path.join(out, "bubbles.csv"), got["bubbles"])
        with open(os.path.join(out, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        _print_summary(summary)
        print(f"  -> {out} (pages.csv, bubbles.csv, summary.json - numbers only)")
    if not results:
        print("[B] no project with reviewed bubbles.")


if __name__ == "__main__":
    main()
