
# backend/scripts/replay_examples.py

"""
Offline replay for the thesis experiment, part A: which examples from the user's corrections help the model?
cm
Why a separate script: the app's live A/B only fires when memory has a hit (1.8% of bubbles in the logs), so it can
not answer "would examples given MORE often still help?". This script translates already REVIEWED pages again in
several variants that differ ONLY in the examples sent with each bubble, and compares every output with the user's
final translation. Everything else is the app's own code and format: the page batch (all bubbles of a page in one
call, each other's context), the glossary field, services/providers/prompts.SYSTEM_PROMPT, the example format of
translation_service._example and the candidate rules of memory_service (one entry per source line, no short-phrase
entries, no two examples with the same final text, corrected entries win near-ties).

Variants (one request per page each):
  A0   no examples                                   - baseline
  A0b  no examples, run again                        - noise floor of the model itself (A0b - A0)
  A1   3 random earlier corrections (fixed seed)     - do examples help just by being there?
  A2   3 nearest earlier corrections, NO threshold   - H1 / H2; results split by similarity replace threshold variants
  A3   3 biggest earlier corrections (optional)      - H3: style examples instead of similarity
Examples go to bubbles of the fuzzy lane only (more than memory_short_phrase_max_words words), as in the app.

No leak: a page only sees corrections created BEFORE its first translation (earliest TranslationLog row of the page)
and never one made on the page itself. Temperature 0 (the app stays at its own setting): the variants should differ by
their examples, not by sampling. The terminology post-edit pass is not run - like the app's A/B log, the comparison is
on the model output (case-matched for ALL-CAPS lines exactly as the app does).

Data safety: the database is opened READ-ONLY and nothing is written to the app's logs, memory or page files. The
output folder (default <app root>/research/replay_<project>/, outside the repository - refused inside it) holds full
texts of the manga: never commit or publish it. `score` writes summary.json / summary.csv with numbers only.

Usage (from backend/, with the app's venv):
  python scripts/replay_examples.py run --project "Nihonkoku Shoukan" --dry-run            # payload stats, no API
  python scripts/replay_examples.py run --project "Nihonkoku Shoukan" --limit 5 --variants A0,A0b,A1,A2   # pilot
  python scripts/replay_examples.py run --project "Nihonkoku Shoukan"                       # all reviewed pages
  python scripts/replay_examples.py score --project "Nihonkoku Shoukan"                     # needs sacrebleu
A run can be stopped and started again: finished (page, variant) requests are skipped. Rate limits wait and retry.
"""

import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

VARIANTS = {
    "A0": "no examples",
    "A0b": "no examples, second run (noise floor)",
    "A1": "3 random earlier corrections",
    "A2": "3 nearest earlier corrections, no threshold",
    "A3": "3 biggest earlier corrections",
}
DEFAULT_VARIANTS = "A0,A0b,A1,A2"
K = 3
SIM_BINS = [(0.0, 0.3, "<0.30"), (0.3, 0.5, "0.30-0.50"), (0.5, 0.67, "0.50-0.67"), (0.67, 1.01, ">=0.67")]
RATE_LIMIT_WAIT = 65            # seconds - when the pool cannot tell how long its keys rest
MAX_RETRIES = 6
MAX_WAIT = 300                  # a key resting longer than this (a spent daily quota rests an hour): stop, don't sleep


# ---------------------------------------------------------------- reading the app's data (read-only)

def _parse_time(value) -> datetime | None:
    """SQLite text from SQLAlchemy ('2026-10-28 20:21:01.696757', UTC without offset) -> naive UTC datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _natural(value: str):
    return (0, int(value)) if str(value).isdigit() else (1, str(value))


def connect_readonly(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def load_project(con, project: str):
    row = con.execute("SELECT id, name FROM projects WHERE name = ? OR CAST(id AS TEXT) = ?", (project, project)).fetchone()
    if row is None:
        names = [r["name"] for r in con.execute("SELECT name FROM projects")]
        raise SystemExit(f"Project '{project}' not found. Projects: {', '.join(names)}")
    return row["id"], row["name"]


def load_pages(con, project_id: int) -> list[dict]:
    rows = con.execute("""SELECT p.id, p.file_name, p."order" AS ord, p.status, c.number AS chapter
                          FROM pages p JOIN chapters c ON c.id = p.chapter_id WHERE c.project_id = ?""",
                       (project_id,)).fetchall()
    pages = [dict(r) for r in rows]
    pages.sort(key=lambda p: (_natural(p["chapter"]), p["ord"] or 0, p["file_name"]))
    return pages


def first_translation_times(con, page_ids: list[int]) -> dict[int, datetime]:
    out = {}
    for pid in page_ids:
        row = con.execute("SELECT MIN(created_at) AS t FROM translation_logs WHERE page_id = ?", (pid,)).fetchone()
        if row and row["t"]:
            out[pid] = _parse_time(row["t"])
    return out


def load_corrections(con, project_id: int):
    import numpy as np
    out = []
    for r in con.execute("""SELECT id, source_text, ai_translation, final_translation, kind, embedding, created_at,
                                   page_id FROM corrections WHERE project_id = ?""", (project_id,)):
        if not r["embedding"] or not (r["final_translation"] or "").strip():
            continue
        out.append(SimpleNamespace(id=r["id"], source_text=r["source_text"], ai_translation=r["ai_translation"],
                                   final_translation=r["final_translation"], kind=r["kind"], page_id=r["page_id"],
                                   created=_parse_time(r["created_at"]),
                                   vector=np.frombuffer(r["embedding"], dtype=np.float32)))
    return out


def load_glossary(con, project_id: int):
    return [SimpleNamespace(source_term=r["source_term"], target_term=r["target_term"])
            for r in con.execute("SELECT source_term, target_term FROM glossary_terms WHERE project_id = ?", (project_id,))]


def page_json_path(workspace_dir: str, project_name: str, page: dict) -> str:
    stem = os.path.splitext(page["file_name"])[0]
    return os.path.join(workspace_dir, project_name, "processed", str(page["chapter"]), f"{stem}_ocr.json")


# ---------------------------------------------------------------- example selection

def _unique_finals(ranked):
    """(correction, sim) best first -> at most K, never two with the same final text (memory_service rule)."""
    out, seen = [], set()
    for c, sim in ranked:
        key = c.final_translation.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append((c, sim))
        if len(out) >= K:
            break
    return out


def correction_size(c) -> int:
    """Characters the user had to change: edit distance AI text -> final (0 for approved entries)."""
    from rapidfuzz.distance import Levenshtein
    if (c.kind or "corrected") != "corrected":
        return 0
    return Levenshtein.distance((c.ai_translation or "").strip(), c.final_translation.strip())


def plan_page(page: dict, bubbles: list[dict], corrections, glossary, t_first: datetime, seed: int, variants) -> dict:
    """
    Everything a page needs for every variant, decided BEFORE any request: per bubble the examples of each variant,
    the glossary field, the nearest similarity and the size of the memory the page may see.
    """
    import numpy as np
    from services.memory_service import _latest_per_source, _CORRECTED_BONUS, embed_text, is_short_phrase, memory_text
    from services.glossary_service import match_glossary
    from services.translation_service import _example

    eligible = [c for c in corrections
                if c.created and c.created < t_first and (c.page_id is None or c.page_id != page["id"])]
    candidates = [c for c in _latest_per_source(eligible) if not is_short_phrase(c.source_text)]
    biggest = sorted((c for c in candidates if correction_size(c) > 0),
                     key=lambda c: (correction_size(c), c.id), reverse=True)
    matrix = np.stack([c.vector for c in candidates]) if candidates else None

    plan = {"eligible": len(eligible), "candidates": len(candidates), "bubbles": []}
    for b in bubbles:
        text = b["text"]
        short = is_short_phrase(text)
        entry = {"bubble": b, "short": short, "glossary": match_glossary(text, glossary), "nearest": None,
                 "examples": {v: [] for v in variants}}
        if not short and candidates:
            sims = matrix @ embed_text(memory_text(text))
            sim_of = {c.id: float(s) for c, s in zip(candidates, sims)}
            entry["nearest"] = round(float(sims.max()), 4)
            by_sim = sorted(candidates, key=lambda c: sim_of[c.id] + (_CORRECTED_BONUS if (c.kind or "corrected") == "corrected" else 0),
                            reverse=True)
            chosen = {}
            if "A2" in variants:
                chosen["A2"] = _unique_finals((c, sim_of[c.id]) for c in by_sim)
            if "A1" in variants:
                rng = random.Random(f"{seed}|{page['id']}|{b['bubble_id']}")
                shuffled = candidates[:]
                rng.shuffle(shuffled)
                picked = _unique_finals((c, sim_of[c.id]) for c in shuffled)
                chosen["A1"] = sorted(picked, key=lambda t: t[1], reverse=True)
            if "A3" in variants:
                chosen["A3"] = _unique_finals((c, sim_of[c.id]) for c in biggest)
            for v, pairs in chosen.items():
                entry["examples"][v] = [(c.id, round(sim, 4), _example(c, sim)) for c, sim in pairs]
        plan["bubbles"].append(entry)
    return plan


def payload_for(plan: dict, variant: str) -> list[dict]:
    items = []
    for e in plan["bubbles"]:
        item = {"id": e["bubble"]["bubble_id"], "text": e["bubble"]["text"]}
        ex = e["examples"].get(variant) or []
        if ex:
            item["examples"] = [x[2] for x in ex]
        if e["glossary"]:
            item["glossary"] = e["glossary"]
        items.append(item)
    return items


# ---------------------------------------------------------------- run

def _jsonl_rows(path: str):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _append(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def output_dir(args, app_root: str, project_name: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in project_name).strip("_").lower()
    out = os.path.abspath(args.out or os.path.join(app_root, "research", f"replay_{slug}"))
    repo = os.path.abspath(REPO_DIR)
    try:
        inside = os.path.commonpath([os.path.normcase(out), os.path.normcase(repo)]) == os.path.normcase(repo)
    except ValueError:                         # another drive (C: vs D:) - certainly not inside the repository
        inside = False
    if inside:
        raise SystemExit(f"Refusing to write into the repository ({out}): the output holds the manga's full text.")
    return out


def build_pool(model: str, keys: str | None, temperature: float):
    """A key pool of the app's own clients with ONLY the chosen keys (all on the same model) at this temperature."""
    from core.config import get_setting
    from services.providers import pool_entries, unusable_reason, _client, host_of
    from services.providers.pool import KeyPool

    usable = [e for e in pool_entries(include_disabled=True) if not unusable_reason(e)]
    if keys:                                   # named keys: also ones switched off in Settings (the user asked for them)
        wanted = [k.strip() for k in keys.split(",") if k.strip()]
        chosen = [e for e in usable if e["id"] in wanted or e["label"] in wanted]
    else:                                      # default: the switched-on keys of this model, in the Settings order
        chosen = [e for e in usable if e["enabled"] and e["model"] == model]
    if not chosen:
        raise SystemExit(f"No usable key with model '{model}'" + (f" among {keys}" if keys else "") +
                         ". Keys: " + ", ".join(f"{e['id']} ({e['model']})" for e in usable))
    models = {e["model"] for e in chosen}
    if len(models) != 1:
        raise SystemExit(f"The chosen keys use different models {sorted(models)} - the variants would compare models.")
    pool = KeyPool(chosen, {name: _client(name) for name in {e["provider"] for e in chosen}}, host_of,
                   temperature, get_setting("translation_max_tokens", 4096))
    return pool, chosen[0]["model"], [e["label"] for e in chosen]


def cmd_run(args) -> None:
    from core.config import APP_ROOT_DIR, APP_VERSION, DATABASE_PATH, WORKSPACE_DIR, get_setting
    from services.page_export import bubble_reviewed, load_page_json
    from services.providers.prompts import SYSTEM_PROMPT
    from services.translation_service import match_case

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown:
        raise SystemExit(f"Unknown variant(s) {unknown}; known: {', '.join(VARIANTS)}")

    con = connect_readonly(DATABASE_PATH)
    project_id, project_name = load_project(con, args.project)
    pages = load_pages(con, project_id)
    times = first_translation_times(con, [p["id"] for p in pages])
    corrections = load_corrections(con, project_id)
    glossary = load_glossary(con, project_id)
    con.close()

    # pages with reviewed bubbles and a known first translation, in the order they were first translated
    work = []
    for p in pages:
        path = page_json_path(WORKSPACE_DIR, project_name, p)
        if p["status"] == "excluded" or p["id"] not in times or not os.path.exists(path):
            continue
        data = load_page_json(path)
        bubbles = [b for b in data.get("bubbles", []) if not b.get("skip") and (b.get("text") or "").strip()]
        if not any(bubble_reviewed(b) for b in bubbles):
            continue
        work.append((p, bubbles))
    work.sort(key=lambda pb: times[pb[0]["id"]])
    if args.limit and args.limit < len(work):
        # evenly spread over the translation history, so a pilot sees small AND large memories
        n = len(work)
        idx = sorted({round(i * (n - 1) / (args.limit - 1)) for i in range(args.limit)}) if args.limit > 1 else [n - 1]
        work = [work[i] for i in idx]

    print(f"[REPLAY] {project_name}: {len(work)} page(s), {len(corrections)} memory entries, {len(glossary)} glossary "
          f"terms, variants {','.join(variants)}")
    plans = []
    for p, bubbles in work:
        plan = plan_page(p, bubbles, corrections, glossary, times[p["id"]], args.seed, variants)
        plans.append((p, plan))
        fuzzy = [e for e in plan["bubbles"] if not e["short"]]
        reviewed = sum(1 for e in plan["bubbles"] if bubble_reviewed(e["bubble"]))
        print(f"  ch {p['chapter']:>3} {p['file_name']:<24} first {times[p['id']]:%Y-%m-%d %H:%M}  bubbles {len(bubbles):>2} "
              f"(reviewed {reviewed}, fuzzy {len(fuzzy)})  memory seen {plan['eligible']:>3} (candidates "
              f"{plan['candidates']:>3})  " + "  ".join(f"{v}:{len(json.dumps(payload_for(plan, v), ensure_ascii=False))}ch"
                                                       for v in variants))
    if args.dry_run:
        print("[REPLAY] dry run - no request sent, nothing written.")
        return

    out = output_dir(args, APP_ROOT_DIR, project_name)
    os.makedirs(out, exist_ok=True)
    pool, model, key_labels = build_pool(args.model, args.keys, args.temperature)
    info_path = os.path.join(out, "run_info.json")
    info = {"project": project_name, "model": model, "temperature": args.temperature, "seed": args.seed, "k": K,
            "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest(),
            "short_phrase_max_words": get_setting("memory_short_phrase_max_words", 2),
            "a3_size": "Levenshtein distance ai_translation -> final_translation, corrected entries only",
            "postedit": False, "match_case": True}
    if os.path.exists(info_path):
        old = json.load(open(info_path, encoding="utf-8"))
        differ = {k: (old.get(k), v) for k, v in info.items() if old.get(k) != v}
        if differ and not args.force:
            raise SystemExit(f"{info_path} was made with other settings {differ} - mixing them breaks the comparison. "
                             f"Use another --out (or --force if you really mean it).")
    json.dump({**info, "app_version": APP_VERSION, "keys": key_labels,
               "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "glossary_terms": len(glossary), "variants": {v: VARIANTS[v] for v in variants}},
              open(info_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    req_path, bub_path = os.path.join(out, "requests.jsonl"), os.path.join(out, "bubbles.jsonl")
    done = {(r["page_id"], r["variant"]) for r in _jsonl_rows(req_path) if r.get("ok")}
    todo = [(p, plan, v) for p, plan in plans for v in variants if (p["id"], v) not in done]
    print(f"[REPLAY] {len(todo)} request(s) to send ({len(done)} done earlier) -> {out}")

    try:
        _send_all(todo, pool, model, args, req_path, bub_path)
    except KeyboardInterrupt:
        print("\n[REPLAY] stopped (Ctrl+C). Everything finished so far is saved - run the same command to continue.")


def _rate_limit_wait(pool, error: str) -> int | None:
    """
    Seconds to wait before the next try when every key is limited, or None = stop for today. Gemini tells which quota
    was hit and how long to wait (services/providers/gemini_provider.rate_limit_from); the pool keeps each key resting
    exactly that long, so the script sleeps until the FIRST key is usable again instead of a blind 65 s.
    """
    if "daily quota of every" in error:
        return None
    wait = pool._seconds_until_any_key() or RATE_LIMIT_WAIT
    return None if wait > MAX_WAIT else max(3, wait)


def _send_all(todo, pool, model, args, req_path, bub_path):
    sent = 0
    for p, plan, v in todo:
        payload = payload_for(plan, v)
        if v not in ("A0", "A0b") and payload == payload_for(plan, "A0"):
            # no earlier correction to pick from (the first pages): the request would be A0 again. Not sent and no
            # bubble rows - the page simply drops out of this variant's comparison instead of adding a fake 0.
            _append(req_path, {"page_id": p["id"], "variant": v, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "ok": True, "skipped": "same payload as A0 (no examples)", "items": len(payload)})
            print(f"  skip ch {p['chapter']} {p['file_name']} {v}: no examples to give (same payload as A0)")
            continue
        for attempt in range(MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                result = pool.translate(payload)
                error = None
                break
            except Exception as e:                      # keys are scrubbed from these messages by the pool
                error = f"{type(e).__name__}: {e}"
                limited = any(s in error.lower() for s in ("rate", "cooling", "quota", "429", "no translation key"))
                wait = _rate_limit_wait(pool, error) if limited else None
                if limited and wait is not None and attempt < MAX_RETRIES:
                    print(f"  every key is at its per-minute limit - waiting {wait}s ({attempt + 1}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                result = None
                break
        seconds = round(time.monotonic() - start, 2)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if result is None:
            _append(req_path, {"page_id": p["id"], "variant": v, "at": now, "ok": False, "error": error[:500],
                               "model": model, "temperature": args.temperature})
            print(f"  FAILED ch {p['chapter']} {p['file_name']} {v}: {error[:200]}")
            if "limit" in error.lower() or "quota" in error.lower():
                daily = "daily quota" in error or (pool._seconds_until_any_key() or 0) > MAX_WAIT
                print(f"[REPLAY] stopping after {sent} request(s) - "
                      + ("the DAILY quota of the keys is used up: run the same command tomorrow after 09:00 (midnight "
                         "Pacific time)." if daily else "keys still limited: run the same command in a few minutes.")
                      + " Finished requests are kept, it continues where it stopped.")
                return
            continue
        for e in plan["bubbles"]:
            b = e["bubble"]
            raw = result.get(b["bubble_id"], "")
            _append(bub_path, {
                "page_id": p["id"], "chapter": p["chapter"], "file": p["file_name"], "variant": v,
                "bubble_id": b["bubble_id"], "short": e["short"], "source": b["text"],
                "final": b.get("translation", "") if bubble_reviewed(b) else None,
                "ai_first": b.get("ai_translation", ""), "confirmed_via": b.get("confirmed_via"),
                "output_raw": raw, "output": match_case(b["text"], raw),
                "examples": [{"id": cid, "sim": sim, "kind": ex["kind"]} for cid, sim, ex in e["examples"].get(v) or []],
                "nearest": e["nearest"], "memory_seen": plan["eligible"], "candidates": plan["candidates"],
                "glossary": len(e["glossary"]),
            })
        _append(req_path, {"page_id": p["id"], "variant": v, "at": now, "ok": True, "model": pool.current_model,
                           "key_label": pool.current_key_label, "temperature": args.temperature, "seconds": seconds,
                           "items": len(payload), "payload_chars": len(json.dumps(payload, ensure_ascii=False)),
                           "examples": sum(len(i.get("examples", [])) for i in payload),
                           "missing": sum(1 for i in payload if not result.get(i["id"]))})
        sent += 1
        print(f"  ok ch {p['chapter']} {p['file_name']} {v} ({seconds}s, key {pool.current_key_label})")
    print(f"[REPLAY] done: {sent} request(s) sent this time.")


# ---------------------------------------------------------------- score

def _bootstrap_ci(diffs, rounds=2000, seed=0):
    import numpy as np
    arr = np.asarray(diffs, dtype=float)
    if len(arr) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = arr[rng.integers(0, len(arr), size=(rounds, len(arr)))].mean(axis=1)
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def _wilcoxon(diffs):
    try:
        from scipy.stats import wilcoxon
        if len(diffs) < 6 or all(d == 0 for d in diffs):
            return None
        return float(wilcoxon(diffs, zero_method="zsplit").pvalue)
    except Exception:
        return None


def cmd_score(args) -> None:
    try:
        from sacrebleu.metrics import CHRF, TER
    except ImportError:
        raise SystemExit("score needs sacrebleu: backend\\.venv\\Scripts\\python.exe -m pip install sacrebleu")
    from core.config import APP_ROOT_DIR, DATABASE_PATH

    con = connect_readonly(DATABASE_PATH)
    _, project_name = load_project(con, args.project)
    con.close()
    out = output_dir(args, APP_ROOT_DIR, project_name)
    rows = _jsonl_rows(os.path.join(out, "bubbles.jsonl"))
    if not rows:
        raise SystemExit(f"No results in {out} - run first.")

    latest = {}
    for r in rows:                                      # a re-run of a (page, variant) replaces the older rows
        latest[(r["page_id"], r["bubble_id"], r["variant"])] = r
    chrf, ter = CHRF(word_order=2), TER()
    table: dict[tuple, dict] = {}
    for (pid, bid, v), r in latest.items():
        if r["final"] is None or not r["final"].strip():
            continue
        if r.get("confirmed_via") == "page" and not args.include_page_accepted:
            continue
        hyp, ref = r["output"] or "", r["final"]
        table.setdefault((pid, bid), {"meta": r})[v] = {
            "chrf": chrf.sentence_score(hyp, [ref]).score, "ter": ter.sentence_score(hyp, [ref]).score}

    variants = [v for v in VARIANTS if any(v in t for t in table.values())]
    subsets = {
        "fuzzy": lambda m: not m["short"],
        "fuzzy_edited": lambda m: not m["short"] and m["final"].strip() != (m["ai_first"] or "").strip(),
        "all": lambda m: True,
    }
    for lo, hi, name in SIM_BINS:
        subsets[f"nearest {name}"] = (lambda lo, hi: lambda m: not m["short"] and m["nearest"] is not None
                                      and lo <= m["nearest"] < hi)(lo, hi)
    pairs = [("A0b", "A0"), ("A1", "A0"), ("A2", "A0"), ("A3", "A0"), ("A2", "A1"), ("A3", "A2")]

    summary = {"project": project_name, "scored_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "include_page_accepted": bool(args.include_page_accepted), "means": [], "pairs": []}
    csv_rows = [["subset", "kind", "a", "b", "metric", "n", "mean", "ci_low", "ci_high", "p_wilcoxon"]]
    for sname, keep in subsets.items():
        sel = [t for t in table.values() if keep(t["meta"])]
        for v in variants:
            vals = [t[v]["chrf"] for t in sel if v in t]
            if vals:
                m = sum(vals) / len(vals)
                summary["means"].append({"subset": sname, "variant": v, "metric": "chrF++", "n": len(vals), "mean": m})
                csv_rows.append([sname, "mean", v, "", "chrF++", len(vals), round(m, 3), "", "", ""])
        for a, b in pairs:
            for metric in ("chrf", "ter"):
                diffs = [t[a][metric] - t[b][metric] for t in sel if a in t and b in t]
                if not diffs:
                    continue
                mean = sum(diffs) / len(diffs)
                lo, hi = _bootstrap_ci(diffs)
                p = _wilcoxon(diffs)
                name = "chrF++" if metric == "chrf" else "TER"
                summary["pairs"].append({"subset": sname, "a": a, "b": b, "metric": name, "n": len(diffs), "mean": mean,
                                         "ci95": [lo, hi], "p_wilcoxon": p})
                csv_rows.append([sname, "diff", a, b, name, len(diffs), round(mean, 3), round(lo, 3), round(hi, 3),
                                 "" if p is None else round(p, 4)])

    reqs = [r for r in _jsonl_rows(os.path.join(out, "requests.jsonl")) if r.get("ok") and not r.get("skipped")]
    summary["requests"] = {v: {"n": len([r for r in reqs if r["variant"] == v]),
                               "mean_payload_chars": round(sum(r["payload_chars"] for r in reqs if r["variant"] == v) /
                                                           max(1, len([r for r in reqs if r["variant"] == v])))}
                           for v in variants}
    json.dump(summary, open(os.path.join(out, "summary.json"), "w", encoding="utf-8"), indent=2)
    with open(os.path.join(out, "summary.csv"), "w", encoding="utf-8") as f:
        f.write("\n".join(",".join(str(c) for c in row) for row in csv_rows) + "\n")

    print(f"[SCORE] {project_name}: {len(table)} reviewed bubble(s) scored -> {out}\\summary.csv (numbers only)")
    for sname in ("fuzzy", "fuzzy_edited"):
        print(f"  {sname}: " + "  ".join(f"{m['variant']} {m['mean']:.1f} (n={m['n']})" for m in summary["means"]
                                         if m["subset"] == sname))
    print("  chrF++ differences (95% CI, Wilcoxon p):")
    for row in summary["pairs"]:
        main_subset = row["subset"] in ("fuzzy", "fuzzy_edited")
        by_similarity = row["subset"].startswith("nearest") and row["b"] == "A0"
        if row["metric"] == "chrF++" and (main_subset or by_similarity):
            p = "-" if row["p_wilcoxon"] is None else f"{row['p_wilcoxon']:.3f}"
            print(f"    {row['subset']:<18} {row['a']}-{row['b']}: {row['mean']:+6.2f} "
                  f"[{row['ci95'][0]:+.2f}, {row['ci95'][1]:+.2f}] n={row['n']} p={p}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline replay of reviewed pages with different memory examples.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="translate reviewed pages again in the chosen variants")
    run.add_argument("--project", required=True, help="project name or id")
    run.add_argument("--variants", default=DEFAULT_VARIANTS, help=f"comma list of {', '.join(VARIANTS)}")
    run.add_argument("--limit", type=int, default=0, help="only N pages, spread evenly over the history (pilot)")
    run.add_argument("--model", default="gemini-2.5-flash", help="use the keys of this model (default gemini-2.5-flash)")
    run.add_argument("--keys", default=None, help="comma list of key ids (provider/label) or labels; same model")
    run.add_argument("--temperature", type=float, default=0.0)
    run.add_argument("--seed", type=int, default=20261107, help="seed of the random examples (A1)")
    run.add_argument("--out", default=None, help="output folder (default <app root>/research/replay_<project>)")
    run.add_argument("--dry-run", action="store_true", help="show pages, memory sizes and payload sizes; send nothing")
    run.add_argument("--force", action="store_true", help="allow other settings in an existing output folder")
    score = sub.add_parser("score", help="chrF++ / TER against the user's final translations, paired differences")
    score.add_argument("--project", required=True)
    score.add_argument("--out", default=None)
    score.add_argument("--include-page-accepted", action="store_true",
                       help="also bubbles accepted only with the whole page (confirmed_via = page)")
    args = parser.parse_args()
    cmd_run(args) if args.cmd == "run" else cmd_score(args)


if __name__ == "__main__":
    main()
