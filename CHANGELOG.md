# Changelog

All notable changes to OpenKeyTranslate. Versions follow `APP_VERSION` in `backend/core/config.py`.

## [0.5.0] - 2026-11-11

### Added
- **Lettering: your own line breaks.** New **Lines** field in the Lettering panel: the text as it is drawn, line by
  line. Enter = a line break of your own, also inside a word (a sound effect lettered one letter per line). With an
  automatic font size your lines stay whole and the size shrinks to fit them. Only the breaks can change there - the
  words are edited in Translate, so the translation, its "edited" marker and the correction memory are not touched.
  **Auto** goes back to the automatic wrap; breaks that no longer match an edited translation are ignored.
- **Add bubble where the OCR reads nothing** (drawn sound effects, hand lettering): instead of "No text was found"
  the editor asks for the text and adds the bubble without OCR. Nothing is erased for such a bubble - remove the
  original lettering with Fill with background / Redraw (AI).
- **Glossary: where a term came from.** The Source column shows it for every term: **typed** (by you), **scan**
  (repeated fix found in your corrections), **AI** (Find terms with AI), **import**; a pencil marks a suggestion
  whose Polish term you changed before accepting (the proposal is in the tooltip). Terms added before this version:
  **scan / AI** or **typed / import** - the app did not note which of the two.
- **Clean tools: Erase text.** Draw a rectangle over text and only its LETTERS are erased (with their light
  outline): the OCR lines in the area (also of a skipped bubble), or the area is read when there are none. Letters
  glued to a fine screentone are found too. The holes are filled by what is around them: one plain colour - painted;
  a regular screentone - the dots are continued (Black Jack, Chapter 2 page 1: the raster sky stays a raster sky,
  in a fraction of a second; Redraw (AI) over the whole rectangle made it a smooth grey patch); a drawing - redrawn
  by the optional AI model, without it filled from around. Where the letters cross the edge of a black (or white)
  area, that part is painted with its colour and the edge is carried on straight under the letters (Black Jack,
  Chapter 3 page 2: "The" over the edge of a dark room); in a screentone each pixel takes the repeat that matches its
  surroundings, so the lines of a drawing in it go on instead of breaking into dashes. The message says what was
  used (background colour / screentone continued / AI model / filled from around). What the automatic erase changed
  inside the area is replaced too. Nothing outside the area changes.
- **Clean tools: Erase text (AI)** (only with the optional model): the same letters, all redrawn by the model - for
  when Erase text left a visible pattern. Erase text itself already uses the model where nothing better fits.
- **Clean tools menu**: the shape (rectangle / ellipse) is at the top of the list and the button always shows it
  (▭ / ◯); Erase text comes first.
- **Export page into another folder**: the arrow next to Export page (editor) - the system folder dialog, the page
  goes into <folder>/<project>/<chapter>/ as with Export pages; the last folder is offered again. View export finds it
  there too.
- **Settings: how much of each key is used** - under a key: requests made today, its daily limit and about how many
  are left, with a bar. The limit is the server's own number (Groq sends it with every answer, Gemini names it when
  its quota is used up) or, without one, the count where the quota ran out last time. Only this app's requests are
  counted (the same key used elsewhere is not seen); Google's day ends at midnight Pacific time (09:00 in Poland).
- **Lettering: sides of a text box** can be dragged (a small square in the middle of each side, or anywhere along
  it) - only that side moves; before, only the corners resized. **Lines: Vertical** = one letter per line, an empty
  line between words; the "Auto" button of the Lines field is now called **Reset**. **Box to the text** next to
  "The text does not fit" makes the box as big as the text needs at its size.
- **Editor: zoom and pan** - Ctrl+scroll zooms at the mouse pointer (the buttons at the middle); holding the middle
  mouse button and dragging moves the page.
- **Lettering: Preview text can be switched off** - the clean page under the text boxes (before, Lettering always
  showed the text). Kept for the next pages until switched on.
- **Bubble panel: Erase: auto / off.** Off = the bubble stays translated and lettered, but its original pixels come
  back (the automatic erase smeared the drawing) - then clean that spot yourself, e.g. with Erase text. Before, the
  only way to get the original pixels back was Skip, which also dropped the translation from the page.
- **Bubble panel: Google ↗** next to Edit opens the English text in Google Translate in the browser - a second
  opinion to compare (no key; the text goes to Google only on that click, nothing comes back into the app).

### Fixed
- **Zoomed in, the left part of the page could not be reached** - the view was centred so that a page wider than it
  overflowed to the left, out of the scroll range (also with the middle-button scroll). Now the whole page scrolls.
- **The view jumped to the bubble** when a page had just been OCR'd / translated (and on far jumps): it glides there now,
  without the browser's smooth scroll that a redraw used to stop half-way.
- **Re-OCR bubble took text from the next bubble and erased part of a drawing**: the bubble's inside can leak through
  a light drawing (a white face) into the next bubble; the read then got half of its text ("THERE'S NO MEAI THE REASON
  ..."), the bubble's box spanned the face and the cleaning erased half of it (Black Jack, Chapter 3 page 7). The
  text of other bubbles is now blanked before reading, and only lines joined to the bubble's own text are kept (a line
  the box missed sits next to the others). A rectangle you draw (Set area & re-OCR) is still read as it is.
- **Backend console flooded** with the same "[BUBBLE GUARD]" / "[BUBBLE OVERFLOW]" lines on every Lettering change:
  each one is printed once (again after 10 minutes if it stays).
- **Skip / Unskip**: the button of a skipped bubble ("Translate this") is now called **Unskip**.
- **Erase text over a black area next to a screentone** carried the black on into the tone in streaks and bit into
  the black edge (Black Jack, official, Chapter 3 page 2) - see Erase text above.
- **Exports**: an export whose file was deleted (or whose ZIP / folder is gone) no longer counts as exported. A page
  exported before its review (AI text as it was) keeps its stage, but with "Exported" shown its tile says
  "· exported" and the project card counts "n exported before the review" - before, such an export was nowhere.
- **Retranslate reported "Bubble retranslated" while the bubble came back empty** (and wiped the text it had). The
  model sometimes answers without an entry for the bubble; it is now asked once more, alone, through the same key.
  If it still returns nothing, the old text stays and the editor says so. Not a content filter: a blocked answer is
  an error, and the same sentence had been translated in the page batch a minute before.
- **The view did not go to the bubble**: a page opened zoomed in stayed at its top-left corner although the first
  bubble was selected (also after OCR + translation finished on an open page); the view now moves to the selected
  bubble - or, with none selected, to the first one to translate - as soon as the image is there. A far jump is
  instant (a smooth scroll across a page at 700% stopped half-way). **Zooming keeps the middle of the view** in
  place instead of the top-left corner.
- **Split of two bubbles standing side by side**: the new parts follow the page's reading order (right-to-left: the
  right one first) - before, the part whose first line sat a few pixels higher came first.
- **Glossary suggestions**: the Polish term field grows with its text (a long term scrolled inside 180 px).
- **OCR: a bubble cut into pieces when its lettering touches the outline.** Large lettering (Black Jack) reaches the
  outline on both sides; the letters then wall the bubble's inside off, and the line plus everything under it became
  a separate bubble ("BUT TRAFFIC" / "ACCIDENTS ARE DIFFERENT.", "MR." / "USHIDA!"). Lines stacked with (almost) no
  gap now stay together (`bubble_refine.TIGHT_GAP_LINES`). Checked against the user's own corrections on the 181
  reviewed pages: manual merges needed 35 -> 0 (Black Jack), 3 -> 0 (Black Jack, official), 6 -> 3 (Nihonkoku).
- **OCR: a tall bubble swallowed the next bubble below it.** The allowed gap grew with the bubble's height; a line
  now never joins a bubble more than 3 line heights below it (`merge_boxes.MAX_GAP_LINES`).
- **OCR: a diagram / a sum read as one bubble** ("FIRST VISIT CHARGE 250 POINTS + COMING AT NIGHT ... = TOTAL"):
  it is split at lines made only of +, =, || or arrows; the signs become skipped bubbles and stay on the page as
  drawn. Manual splits needed on the 181 pages: 50 -> 39 together with the gap limit.
- **Gemini rate limits**: a 429 now tells per-minute from DAILY quota (quotaId ...PerDay...) and how long to wait
  (retryDelay). A key rests exactly that long (a spent daily quota: 1 hour, not a new try every minute), the message
  says which one it was - no raw JSON. The research replay script sleeps only until the first key is usable again,
  stops at once when every daily quota is spent ("run tomorrow after 09:00") and stops cleanly on Ctrl+C.
- **Gemini 404 on a new key**: Google keeps the 2.5 models only for projects that used them before; a key from a new
  account got "Model 'gemini-2.5-flash' not found - pick one from Models" (Models still lists it). The message now
  says the model is not available to new Google projects and to type a newer one.
- **Settings with the backend off**: a section that could not be saved is listed in a **Not saved** bar at the
  bottom (click = go there) and marked in the section list; it is saved by itself once the backend answers. Leaving
  through a link of the app saves first and asks in the app's own dialog only if that fails; after the browser's own
  question (F5, closing the tab) "stay" scrolls to it. Settings that could not be loaded lock their cards - saving
  them would have written the page's placeholders and an empty key list over the real settings.

## [0.4.1] - 2026-11-08

### Added
- **Clean tools group** in the editor: the Clean area button keeps the last tool you picked, the arrow next to it
  lists them - **Clean area**, **Fill with background** (the whole rectangle in the most common colour around it) and
  **Redraw (AI)**. The tool is stored with the area (page JSON `cleanups[].mode`, absent = Clean area), so Rebuild
  image repeats it; Undo clean shows which tool made each area.
- **Redraw (AI)** - optional: **Settings > Add-ons** > Install downloads big-LaMa once (196 MB, MD5 checked, runs
  locally on the CPU, a few seconds per area) into `<app root>/models/lama`, with a progress bar and **Cancel**;
  **Remove** (or Storage & uninstall) deletes it again. It is used ONLY on a rectangle drawn with it: tested on real
  pages, it redraws artwork and gradients under text far better than the flat fill / TELEA, but invents texture and
  text ghosts in white boxes and on paper.
- **Ellipse** shape for the clean tools (Shape at the bottom of the clean tools list; the button shows ◯): only the
  oval inside the dragged box is touched, and Fill takes its colour right outside the oval. In an oval or spiky bubble
  the corners of a rectangle reached the outline or the art behind it - Fill then painted the whole area in that
  colour. Page JSON `cleanups[].shape: "ellipse"` (absent = rectangle).
- **Our own question dialog** (`js/dialogs.js`) instead of the browser's OK / Cancel where three answers are needed:
  after **Re-OCR bubble / Set area & re-OCR** with a different text it shows **Before / Now** (changed words marked) and
  offers **Translate again**, **Keep the translation** or **Put the old text back** (undoes the re-read: text, box and
  text lines - `POST /pages/{id}/bubbles/{bid}/reocr/undo`, only right after it). The same text = a short message.
  After **editing the original text** by hand: Translate again / Keep the translation, with "do this every time"
  (Settings > Editor: ask / translate / keep). A kept translation gets a note in the panel ("the original text changed
  after this translation" + Translate again / It fits) - for the session. Before: "Retranslate this bubble now?" with
  OK / Cancel, where Cancel looked like it cancelled the re-read.
- Settings: **"?"** next to Correction memory, OCR engine, OCR: bubble handling and Ignored text opens new Help topics
  with examples (**Settings: correction memory**, **A/B experiment**, **Settings: OCR**); short explanations under the
  less obvious switches (Approve to memory, A/B, server vs mobile detection, look for missed bubbles).
- OCR: ignored text - **ready patterns** to add with one click (site addresses, www links, Discord / Patreon, scanlation
  credits, number-only bubbles) and a **Try it** field that says whether a sample text would be dropped and by which line.
- Keyboard shortcuts **Export… / Import…** (a small JSON file - only the shortcuts, e.g. for another browser); an
  imported key that collides stays at its default. The full Backup takes them too (see below).
- Settings > About: **What's new** shows this changelog; the settings file path only after **Show path**, plus Show in Explorer.
- Pages keep their scroll position over a reload and Back / Forward (Settings, History, Memory, Projects; also with a
  `#section` left in the address by a section link; the place is kept as "this element, this far from the top" and put
  back while the page fills in - on Settings a pixel position drifted down card by card as the cards above loaded) - they scroll
  inside the main panel and fill their lists after a fetch, so the browser's own restoring never worked.
- **Every OK / Cancel and message box is the app's own window now** (`dialogs.askOk` / `dialogs.notify` instead of the
  browser's `confirm()` / `alert()`, 86 places): themed, the OK button says what it does ("Delete", "Replace the current
  data", "Sort"...), deleting is red, Esc / a click outside = Cancel. Changing the workspace folder asks with three
  answers (Move the data / Only switch / close) - with OK / Cancel, "Cancel" used to mean "switch without moving".
- **Help**: the topics are grouped by the part of the app (Start, Projects & pages, Editor, Memory & glossary, Settings &
  history), the groups fold (the group of the open topic unfolds). **Synthetic before / after pictures** (drawn by the
  app, no real comic pages, centred in the topic): erasing text the OCR missed,
  Letters vs Rectangle cleaning, bubble order, the clean tools on a box border, following the bubble's shape, the
  memory similarity threshold.
- Backup: **Include keyboard shortcuts and this browser's preferences** (on by default) - the page sends its
  `localStorage` "okt-*" entries with the export (`browser/prefs.json` in the zip, manifest `has_browser_prefs`) and the
  import writes them into the browser it runs in. A folded **"Moving between a PC and a laptop - what to check"** note
  (one computer at a time - import replaces, the same app version, GPU setting, Windows fonts, keys, models).
- Sidebar: the logo / name and a **Projects** label above the list go to the start page; with many projects the list
  scrolls on its own (the footer with Settings and Help stays in view) and from 9 projects on gets a **Find a project** field.

### Changed
- Editor messages: at the top under the toolbar instead of small at the bottom, larger, stay as long as it takes to read
  them (longer text = longer), not while the mouse is over them, with **×**. "Re-OCR: the same text" was gone before it
  could be read.
- After a re-read / an edit of the original text with a translation, **Keep the translation** is the default answer
  (right, highlighted) and **Translate again** is next to it - as merging does not translate again by default.
- Settings: the ready ignore patterns are switches (a second click takes the line out again); the tester says "a bubble
  with this text would be dropped - because of the line ..." instead of "Dropped - matches: ...". **What's new**: "you
  have version X", one fold per version (only the newest open), space between the points. **View recent** log turns
  into **Hide log**. Storage & uninstall: the whole list folds too (**Folders and sizes**). The "On this page" list no
  longer shows the "?" of the help buttons. A/B switch laid out like the other switches (its sample rate indented under
  it) - the frame put the input off the column and the bold "without" got extra gaps; a note under a row no longer
  touches the row's line. Your fonts: from 7 fonts on only 5 are shown, the rest behind **Show all N fonts**.
- Application log: lines are logged with their real level. Everything the libraries wrote to stderr was "ERROR" (uvicorn
  requests, Paddle's green "Creating model", Python warnings, progress bars) - errors.log and "Errors only" were full of
  them. Now: uvicorn's own level, warnings = WARNING, colour codes read and removed; unknown stderr text stays ERROR.
  **View recent** shows the newest entry at the top (a multi-line entry keeps its own order).
- Notices in the top right corner (health banner): a long path without spaces pushed the **×** out of the box, so such a
  notice could not be closed; the text now wraps, many notices scroll, and from 3 notices on **Dismiss all**.
- Setup: `req_gtx_1050ti_OKT.txt` no longer lists the CUDA 12 / 13 `nvidia-*` packages - nothing used them (Paddle 3.0
  loads the CUDA 11 ones, torch is the CPU build); about 3 GB less in the environment.
- **A/B experiment off by default** for new installations (it costs a second request per matched bubble). Existing
  settings files keep their value. While it is off the sample rate is greyed out but kept.
- Backup: **your fonts** are always in the zip now (before they were missing - Lettering styles that name them fell back
  to the default font on the new computer); "Include downloaded models" also takes installed add-ons (big-LaMa).
- Storage & uninstall: groups fold (remembered), each with its total; "not there" says what it means (not installed /
  not downloaded yet / none); the LaMa entry says LaMa.

### Research data
- `manual_ops` `cleanup_add` events carry `mode` ("fill" / "redraw") when another tool than Clean area was used, and
  `shape: "ellipse"` for an ellipse (new detail keys; the op keeps its meaning).
- New `manual_ops` op **`reocr_undo`**: the user undid a bubble re-read right after it ("Put the old text back"). The
  `reocr_bubble` event before it stays in the list.
- Memory: when a bubble's ORIGINAL text changes (typed, re-read, re-read undone), its memory rows saved with the old text
  and not used yet (`reuse_count` 0) get the new source + embedding (`memory_service.follow_source_edit`; matched by page +
  bubble + old text, because a page Re-OCR renumbers bubbles). Before, the pair "misread English -> Polish" stayed and
  the next save of the bubble added a second row. Used rows never change. `created_at` is not touched.

### Fixed
- Tests / docs build (`OKT_DOCS_BUILD`): `load_settings()` returned defaults, but `save_settings()` still wrote the
  user's REAL settings.json - a backup-import test replaced it with test defaults (workspace in Temp, no keys, A/B
  off). In this mode the settings file is now a throwaway one in the temp folder, and writing the real one is refused.
- Dark (and light) theme: a pressed toolbar button (Original, Preview text, OCR debug, Clean area / Fill while drawing,
  Add bubble) looked empty - the accent text colour stayed but the transparent toolbar background won over the accent
  background. Pressed = accent background again.
- Redraw (AI) download: progress moved only per 1 MB (on a slow line it looked stuck at 0 MB), a connection that hung
  waited 60 s per read; now 64 KB steps, "Connecting…" while nothing arrived, an error after 30 s without data, Cancel.
  Sizes are binary MB everywhere (the button said 196 MB, the page text 205 MB for the same file).
- Storage & uninstall: deleting something could make the total GROW - the program folder (.venv, ~59,000 files) was
  counted only for 6 s, so its size depended on how warm the disk cache was. Counting with `os.scandir` takes 0.4 s
  and is exact; a total that is still cut off says "≥".
- Text erasing: dark letters with a light outline on a coloured or dark ground (titles, captions, sound effects over
  artwork) left white blobs where the text was - only the dark letters were erased and the outline was spread into
  them. The light rim is now erased too when the ground around it is not light (`inpainting._light_rim`); text in white
  bubbles and on paper is unchanged (checked on 106 pages of a B/W manga: one page changed, same result). Pages
  already processed: **Rebuild image** after a backend restart.
- Projects and pages lists: the "Sort" label sat higher than the chosen entry, and the select arrow touched the
  direction button.
- Clean area over a box border or artwork: the whole rectangle used to be filled from its surroundings, which erased
  the border and smeared it into a dark blot. Now only the marks inside the rectangle are removed (darker or lighter
  than its background); straight lines and drawing that cross its edge stay. A rectangle with a plain border all
  round is painted whole as before (checked on all 14 saved areas: only the two smeared ones changed).
- Merge (also **Merge with ↑ / ↓**): "Translate the merged bubble again" is off by default; Settings > Editor can
  make it ticked by default. Ticking it in the dialog counts for that one merge.
- Editor toolbar: the reading-order select moved to the end, so it is the first to wrap to a 2nd row - an
  **Undo clean** button showing up no longer pushes Preview text and Original down.

### Changed (History, keys)
- History: **Columns** menu - show / hide any column; the normal and the research view keep separate sets.
  New **Model** column (model + API key; the key badge moved there from the memory-injected cell). Model and
  **Temperature** (renamed from "Temp.") are shown in the research view by default, hidden in the normal one.
  Temperatures entered afterwards read "0.4 / filled in" instead of "0.4*".
- History research summary: plainer titles and explanations ("Does the memory help more often as it grows?",
  "What if the similarity threshold were different?").
- Settings > keys: the Model field shows the effective default model in full (grey italic) instead of a cut-off
  "default: gemini-2.5-flas".

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
