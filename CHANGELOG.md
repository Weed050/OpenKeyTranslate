# Changelog

All notable changes to OpenKeyTranslate. Versions follow `APP_VERSION` in `backend/core/config.py`.

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
