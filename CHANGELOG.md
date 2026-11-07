# Changelog

All notable changes to OpenKeyTranslate. Versions follow `APP_VERSION` in `backend/core/config.py`.

## [0.4.0] - 2026-11-07

### Research data - read before comparing with older logs
- `injection_json.temperature` on every log row (shown and A/B counterfactual alike; a new key, nothing old changes
  meaning): the sampling temperature of the call. The 446 older rows (2026-10-28 .. 11-05) were filled in afterwards
  with 0.4 (the setting they ran at, per the user) by `scripts/backfill_log_temperature.py` and carry
  `temperature_backfilled: "2026-11-07"` - filled in, not logged by the call.
- History: **Temp.** column (sortable; `0.4*` = filled in afterwards), also in the request details.
- Page JSON: `manual_ops: [{op, at, bubble?, ...}]` = what the user fixed by hand on the page (merge, split,
  move_lines, reocr_bubble, add_bubble, delete_bubble, edit_source, skip / unskip, reorder, retranslate_bubble,
  cleanup_add / undo / clear, layout, layout_reset, layout_bulk, reocr_page), kept through Re-OCR. Lettering calls on
  the same bubble within 60 s count as one adjustment (`n` = requests), one click that styles many bubbles as one
  `layout_bulk`. Translation edits are not listed (they are measured as edit distance), nor page review or a whole-page
  retranslation. Pages processed earlier have no list - a missing list is "not recorded", not zero.

### Added
- **`scripts/replay_examples.py`** (thesis, part A): translates reviewed pages again at temperature 0 in variants
  that differ only in the memory examples (A0 none, A0b none again = noise floor, A1 3 random, A2 3 nearest without
  threshold, A3 3 biggest corrections), each page seeing only corrections made before its first translation; `score`
  compares with the final translations (chrF++ / TER, paired bootstrap CI, Wilcoxon, split by nearest similarity).
  Reads the database read-only, writes to `<app root>/research/` (full texts - never commit), resumable, waits out
  rate limits. Scoring needs `setup/requirements_research.txt` (sacrebleu).
- **Settings > Correction memory > Test memory model** (`POST /system/embedder/test`, state: `GET /system/embedder`):
  loads the model if needed (= retries a failed load) and checks that similar lines score above unrelated ones. The
  "memory model failed to load" banner links to it (opening Settings at `#memory` runs the test) and a passing test
  removes the banner row.

### Fixed
- "Correction memory model failed to load (RuntimeError: Cannot send a request, as the client has been closed.)" with a
  flaky or missing connection, although the model was on disk: it is now loaded from disk first (`local_files_only`,
  no network at all - offline the load no longer waits ~30 s and then fails); huggingface.co is only asked when it
  isn't there yet. Cause: a
  bug in huggingface_hub 1.x - after a connection error it closes its shared HTTP client and retries on the closed one,
  so the offline fallback to the cache never ran. Offline without the model the message now says so plainly.

## [0.3.1] - 2026-11-05

### Research data - read before comparing with older logs
- `injection_json.memory_probe` on **every** translated bubble (a new key; nothing old changes meaning): `size` =
  memory entries of the project at that moment, `threshold` / `lexical_min` / `strong`, `lane`, `candidates`, `best` =
  the highest cosine also **below** the threshold, `best_overlap`, `hit`. About 170 bytes per bubble, never sent to the
  model. History > Research view: hit rate by memory size and "if the threshold were 0.50 ... 0.90" (older logs:
  memory size estimated from the entries' `created_at`).
- Memory: entries know their bubble (`Correction.page_id` / `bubble_id`, added by the automatic migration). Saving the
  **same bubble again** updates its own entry while memory has not used it yet (`reuse_count` 0, `created_at` = last
  save); going back to the AI text removes it. Before, every changed save added a row (one bubble -> WIWERNY and
  WYWERNY). Old duplicates stay in the database.
- A bubble can carry `bulk_edits: [{at, find, replace}]` = translation changed by the project-wide Find & replace, not
  in the editor. `ai_translation` is untouched, so such a bubble counts as "edited".

### Added
- **Find & replace in translations** of the whole project (editor More menu, pages list): preview with a diff and a
  checkbox per bubble, whole words or "word + endings", a warning for bubbles not reviewed yet, optionally the same in
  memory.
- **Project preferences** (`<project>/project_prefs.json`, `GET/PATCH /projects/{id}/prefs`): the bubble order and the
  Lettering style are kept for pages processed later (also by the background prefetch).
- Lettering **"Use in whole project"**: font / B / I / letters / outline for every bubble without its own style, also in
  the preview, export and new pages; "Project style: ... remove". Replaces "Style carries over" (localStorage).
- Lettering **font search**: type a part of the name, arrows + Enter; system fonts shown in their own face.
- **White letters by themselves** on a dark area (`auto_color`); "Black" overrides it.
- Editor **Original** button (shortcut O) = the raw page instead of the cleaned one; More > "Show original file"
  (`GET /pages/{id}/original`).
- **Undo clean (n) ▾**: a list of the clean-ups, hover shows the area, a click undoes only that one
  (`DELETE /pages/{id}/cleanups?index=n`).
- **Split**: select lines by dragging a rectangle; the translation can be split automatically, **cut by hand** (click
  the word that starts the second part) or translated again; bubbles being translated show a pulsing "Translating...".
- History: the Hints column sorts (memory / glossary first), counts in the filter ("With memory - 8").

### Fixed
- "Right to left" + "Use this order for the whole project" was lost on pages OCR'd later (the prefetch used the global
  setting).
- Switching to Lettering at the last bubble: the typed text is saved first and the open bubble counts as accepted.
- OCR: a level line turned 180° by the text-line orientation classifier ("I'M SORRY" -> "W,1 SORRY") is read again
  without it; refine no longer merges blocks more than 3.5 line heights apart; a lone "?" / "!!" in the soft
  tolerance zone of the threshold is kept.
- "Find terms with AI" (Memory / Glossary) shows one readable sentence instead of the provider's raw JSON (rate limit /
  no connection / rejected key per key); the raw text goes to the log.
- "View export" for a page not exported yet: a message with "Export now" instead of a blank tab.
- Lettering: the number of the selected box sits above it (it covered the text and the corner handle); the drag hint
  moved to the panel (the mode bar jumped when switching modes).
- The Save / arrows bar stays at the bottom of a short detail panel; the other buttons scroll under it.
- Sort selects (project page, pages list): the arrow sits right after the text (`field-sizing`, Chrome / Edge).
- Log noise: asyncio `ConnectionResetError [WinError 10054]` on Windows is no longer printed.

## [0.3.0] - 2026-11-04

### Research data - read before comparing with older logs
- The A/B counterfactual (same payload without memory) is now sent **pinned to the key - and so the model - of the
  shown translation**. Before, a rate limit could move it to another key or provider. A busy pinned key skips the pair
  (`injection_json.ab = "failed"`). `key_label` keeps its meaning; with several providers `model_used` tells them apart.
- A bubble can carry `confirmed_via: "page"` = accepted together with the whole page ("Accept page" / the
  "Next page: accept ..." mode), possibly without being read on its own. Without the field = accepted one by one as
  before. `confirmed_at` / `confirmed_translation` are unchanged.

### Added
- **Key pool** (Settings > Translation & API keys): one ordered list of keys across Groq, Gemini and
  **OpenAI-compatible** servers (beta: OpenRouter, Mistral, DeepSeek, OpenAI, local Ollama / LM Studio - no key needed
  there). Per key: on / off, order, provider, model (suggestions + "Models" asks the server), **Test** with a result box
  (works / rejected / rate limit / no connection + a hint), and "Test the pool". Rotation: rate limit -> 65 s pause,
  rejected key -> 10 min, server unreachable -> keys on other hosts. Old settings keep working unchanged.
- **OCR debug view** in the editor (switch in Settings > Editor): over the page, what OCR kept (by confidence), what the
  second reading rescued, readings under the threshold, what later steps removed and why (duplicate, noise, ignore
  pattern, line cut at a slice edge), and the slice cuts of tall strips; layer panel + "Not used" list. Full data for
  pages OCR'd from this version on (`ocr_debug` in the page JSON, `GET /pages/{id}/ocr-debug`).
- **Settings > Storage & uninstall**: every folder of the app with its size and an Open button; deletes only what comes
  back by itself or is a spare copy (model caches, logs, old settings copies, import leftovers - never projects, the
  database or settings); uninstall steps with the folders to remove.
- **Page review**: "Accept page", "Layout checked", a review mode ("Next page: ...") in the mode bar with Undo on the
  next page, and » next to the page arrows = the next page still to review.
- **Page stages** everywhere from one rule (`services/page_progress.py`): Not started -> OCR done -> partly translated
  -> Translated (AI) -> Translation reviewed -> Fully reviewed -> exported; exports to another folder or a ZIP are
  remembered too (`<project>/export_log.json`). Fixed stage colours per light / dark theme.
- Pages list: the legend is a quick filter, a **Status** filter (hides tiles only, bars count everything, "Open first
  matching page"), sort field + direction; tiles are built when a chapter is opened. Project cards: stage bar and sort.
- Bulk export skips pages not fully reviewed by default (exporting AI drafts is a conscious choice, with a warning).
- **History**: sort by clicking a column name (Time / Where / Source / In the bubble now), rows grouped by day or
  chapter (fold a group, "Fold all"), rows drawn in chunks (50 / 100 / 250 / all - filters and the summary still use
  everything), the filter bar and the column names stick to the top, "accepted with the page" status.
- **Memory / Glossary**: sort by column names, long lists in chunks ("Show more"); glossary **Used** = translated
  bubbles where the term was given to the AI, with "Show where it fired".
- Logo and window icon (`frontend/img/`, `backend/scripts/make_icon.py`).
- Help: key pool, OpenAI-compatible, A/B and keys, Storage & uninstall, OCR debug, page review, sorting.

### Fixed
- OCR self-test: on a PC with `paddlepaddle-gpu` a CPU-only run no longer advises installing the CPU Paddle (it would
  replace the GPU build).
- **OCR on a computer without a GPU**: the default detection model on the CPU is now "mobile" (`ocr_det_model`:
  auto = server on the GPU, mobile on the CPU). Measured on a 1612 px page: server 979 s with Paddle 3.3 CPU and an
  access violation with Paddle 3.0 CPU; mobile 19 s with both. The CPU flags of `ocr_runtime` are applied for every
  CPU run (before: only when `ocr_device` was forced to "cpu").
- **Lettering switch** uses the same rule as opening a page: not processed / not every bubble reviewed = it stays in
  Translate and says why (before: the view switched and the bubbles disappeared).
- **Outline**: grows with the text size (2 / 4 px only on small text); the panel explains that a white outline is
  invisible in a white bubble.
- Setup: one OpenCV package in the requirement files (both overwrote `cv2`), UTF-8 files, a new
  `setup/pip_install_instruction.txt` (no more hand edits of `ocr_pipeline.py`).

### Added
- `backend/scripts/ocr_selftest.py`: OpenCV, Paddle, torch + Paddle and the OCR model step by step, each in its own
  process - names the step that crashes and what to change; report in `logs/ocr_selftest.json`.
- Native crashes are written to `logs/crash.log` (faulthandler); the next start shows a health warning.
- Settings > **OCR engine**: device, detection model, CPU threads, oneDNN, what the running OCR uses, the last
  self-test report.
- Lettering: **shape fit** for oval bubbles (each line as wide as the bubble at its height; used only when the text
  gets larger; `typesetting_shape_fit`, on by default) - 61 of 164 bubbles of chapter 1 got a larger size, none a
  smaller one. **White letters** (black outline). **Style carries over** to the next bubbles (font, B / I, letters,
  outline) and "Use on whole page".
- Pages list: the **stage** of each page (no translation / AI draft / reviewed / exported, "export old") and a
  stage bar per chapter; "← Projects"; **Exported files** (also in the project card menu); the export message opens
  the folder / selects the file; "View export" in the editor.
- **Translation history** (was "Logs"): date, chapter / page / bubble with an "Open" link into the editor, what the
  bubble says now (diff against the AI text), filters (changed by you, not reviewed, chapter, search). The A/B columns
  and the experiment summary are under "Research view". Logged fields unchanged (display only).

### Changed
- Light themes: their own tint factors (strong tints the panels too; before it was barely visible).

## [0.2.1] - 2026-11-03

### Added
- **Project list redesign**: one card per project with a three-colour progress bar (reviewed by you / processed by
  the system only / not processed; `GET /projects/{id}/progress`), Resume / Open / Export buttons and the rare actions
  (copy path, open folder, delete) in a "..." menu.
- **Fonts**: Settings > Text & fonts lists the user's fonts (`<app root>/fonts`, upload / remove) and the system fonts,
  marks fonts without Polish letters, and picks the default font by family name. Lettering mode can pick a font per
  bubble (recently used first) with **bold** / *italic* (imitated when the family has no such file).
- **Lettering**: vertical alignment (top / middle / bottom), "Fit to bubble", Alt + corner resizes evenly from the
  centre.
- **Move lines to another bubble** from the Split dialog, with their part of the translation (no new request).
- **Bubble order**: picking an order sorts the page at once (Undo in the message), can be applied to the whole
  chapter / project, is stored per page and kept by Re-OCR.
- Settings > Translation & API keys has a quick start (where to get a free key); the "no API key" banner links there.
- Help: separate Glossary chapter; arrows (previous / next page) on pages that are not processed yet.

### Changed
- Split: a one-sentence translation is cut at a comma or conjunction that matches the next source piece
  (BUT -> ALE) instead of staying whole in the first part.
- Bubble panel: every button keeps its place (disabled where it does not apply, e.g. on a skipped bubble); the
  arrows sit at the bottom of the panel also when no bubble is open.
- Editor toolbar: the main tools first, view and page tools in a "More" menu; the toolbar wraps instead of hiding
  buttons off-screen.
- Memory and Glossary pages use the same centred column as Settings.

## [0.2.0] - 2026-11-02

### Added
- **Lettering mode** in the editor (`Translate | Lettering` switch under the image): drag, resize and rotate the text
  box of each bubble, and set font size, alignment, angle and outline per bubble. Layouts are kept by Re-OCR and
  dropped on merge/split. Typesetting was refactored into plan / draw steps, so bubbles without a custom layout render
  pixel-identically to before.
- **Merge any bubbles**: Shift+click on the page or in the list, a preview dialog with order arrows (default order
  follows the `bubble_order` setting). A merged bubble loses its "accepted" stamp.
- **Split a bubble without a new translation request**: choose where the lines break, preview, and the existing
  translation is divided accordingly (by sentences, else proportionally; otherwise the bubble is flagged
  "check translation").
- **Manual image clean-up**: "Clean area" / "Undo clean" in the editor; kept by Rebuild image and Re-OCR.
- **Bulk page export** on the project page: translated / clean / both, PNG or JPG, optional ZIP and target folder.
- **OCR rescue** (`utils/ocr_rescue.py`): a second reading for lines that were detected but rejected - stacked vertical
  letters, boxes cut too tight, and tilted lines (e.g. map labels). Uses the same acceptance test as normal OCR.
- **Keyboard shortcuts** with a Settings card for custom keybindings (collision-checked) and an in-app **Help**
  dialog (sidebar "Help" or `?`).
- **Bubble markers** in the list: edited / accepted / unsaved, with a legend and header counters; warning before
  leaving the page with unsaved text.
- **Appearance settings**: light/dark scheme, 12 colour palettes per scheme, surface tint (off / subtle / strong).
  The Settings page got a wider layout with a sticky table of contents.

### Changed
- Page rotation detection (PaddleOCR document orientation) is **off by default** (`ocr_detect_page_rotation`); it
  wrongly rotated about one slice in five on upright pages. It can be enabled per page in the Re-OCR dialog.
- Slicing: pages up to `ocr_slice_max_h` are read in one pass; taller pages are cut along blank bands.
- Re-OCR keeps the "accepted" stamp (`confirmed_*`) of bubbles whose text did not change.
- "Used from memory" matches logs by the bubble's current text and marks stale entries.
- The glossary suggestion popup has a "not now" button (remembered per project).
- Memory page: find & replace is a toolbar button with its own panel.
- Bubbles that share an outline now get their own text areas.

### Fixed
- Text lines touching the bubble outline were split off into separate bubbles.
- Duplicate bubbles created by re-reading text of connected bubbles.
- Tilted text was merged with level lines into one bubble; tilted lines are now lettered along their slant.
- Inpainting left ink and pale traces: glyph detection scales with line height, dot chains and letter holes are
  handled, light "ghost" pixels near erased text are filled with the bubble colour, text on plain paper gets a flat
  fill (screentone is not flattened).

### Notes
- Only OCR, image and UI behaviour changed. Logged A/B fields (`hints_json`, `injection_json`, `memory_lane`) keep
  their meaning, so earlier study data stays comparable.
- Existing pages need **Re-OCR** or **Rebuild image** to benefit from the OCR and inpainting fixes.
- Not yet covered: other manga styles (the pipeline assumes white bubbles on B/W pages); generated `docs/` were not
  rebuilt for this release.

## [0.1.1]

Previous state of the repository: glyph inpainting, settings moved to the app data folder, find & replace in
memory, manual bubble edits, terminology post-edit, light mode, backup service.
