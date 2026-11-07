/**
 * @file frontend/js/help.js
 * @description In-app Help: one modal dialog explaining the markers, buttons and shortcuts of every page. Loaded as a
 * deferred CLASSIC script on every page (with css/help.css), so it works the same on module and non-module pages.
 *
 * Why a dialog and not a separate page: it opens over whatever you are doing and closes with Esc, the x button or a
 * click outside it - nothing on the page changes (no navigation, the open bubble and its unsaved text stay as they
 * were). The tooltips (title="...") stay the quick per-button hint; this is the place that explains how things fit
 * together (what a marker means, what goes into the memory, which button to use when).
 *
 * Entry points: "Help" in the sidebar footer, the "?" key (when not typing), and window.oktHelp.open(topicId) - the
 * editor's bubble list header has a "?" that opens the "markers" topic. Without a topic it opens the one that fits
 * the current page / view.
 */
(function () {
    const mark = (kind) => `<span class="status-mark ${kind}"></span>`;
    const tag = (text, warn) => `<span class="help-tag${warn ? " warn" : ""}">${text}</span>`;
    const key = (k) => `<kbd>${k}</kbd>`;
    const items = (rows) => `<dl class="help-list">${rows.map(([term, text]) => `<dt>${term}</dt><dd>${text}</dd>`).join("")}</dl>`;

    const TOPICS = [
        {
            id: "start", title: "Getting started",
            html: `
                <p>OpenKeyTranslate translates manga / webtoon pages EN &rarr; PL. OCR, cleaning the image and typesetting
                run on this computer; only the extracted text is sent to the translation provider (your own key).</p>
                <ol class="help-steps">
                    <li><b>Projects</b>: create a project from a folder with the chapter folders.</li>
                    <li><b>Pages list</b> (click a project): import chapters, open a page.</li>
                    <li><b>Run OCR + translation</b> on the page, then go through the bubbles one by one in the editor and fix the translations.</li>
                    <li>Every fix you save goes into the <b>correction memory</b> and is shown to the AI as an example on later pages.</li>
                    <li>Switch to <b>Lettering</b> (button under the image) to place the text where you want it.</li>
                    <li><b>Export pages</b> from the pages list when a chapter is done (or <b>Export page</b> in the editor).</li>
                </ol>
                <p>Hover any button for a short tooltip. Press ${key("?")} anywhere (outside a text field) to open this help;
                ${key("Esc")}, the &times; button or a click outside closes it and leaves everything as it was.</p>`,
        },
        {
            id: "markers", title: "Bubble list markers",
            html: `
                <p>The marker at the right end of a row tells you what happened to that bubble. It is read from the page
                file, so it is still there after a reload.</p>
                ${items([
                    [`${mark("edited")} Edited`, `Your saved text differs from the AI draft. It is in the page, and it is
                        also stored in the correction memory as a "corrected" example (unless it is shorter than the
                        minimum word count in Settings, or a duplicate).`],
                    [`${mark("accepted")} Accepted`, `You moved on (arrow keys, the &larr; / &rarr; buttons, or Save) without
                        changing the AI text. Recorded in the page file only, as "reviewed" for the evaluation -
                        <b>nothing goes to the memory</b>. Gone again when the bubble is retranslated to a different text.`],
                    [`${mark("unsaved")} Unsaved`, `You are typing in this bubble and it is not saved yet. Save,
                        ${key("Ctrl")}+${key("Enter")} or moving to another bubble saves it. Leaving the page asks first.`],
                    [`<span class="help-none">no marker</span>`, `Not looked at yet (or retranslated since).`],
                ])}
                <p>The list header counts them: ${mark("edited")} edited, ${mark("accepted")} accepted, and how many are left.
                <b>Accept page</b> under the image gives the Accepted marker to every bubble left at once - see
                <b>Page stages &amp; review</b>.</p>
                <h4>Tags in a row</h4>
                ${items([
                    [tag("check text", true), `Dark pixels inside the bubble are not covered by the OCR text - some text may be
                        missing. Try <b>Re-OCR bubble</b> or <b>Set area &amp; re-OCR</b>.`],
                    [tag("re-read"), `The bubble was read again as a whole because the first pass missed lines.`],
                    [tag("text fixed"), `The source text was cleaned up automatically or edited by you. Hover it for the raw OCR;
                        <b>Use raw OCR</b> puts the raw text back.`],
                    [`<span class="help-none">skipped (&hellip;)</span>`, `Not translated (page number, symbols, or skipped by you):
                        the original pixels stay on the page. <b>Translate this</b> undoes it.`],
                    [`<span class="help-danger">(no AI translation)</span>`, `The model returned nothing for this bubble. Use
                        <b>Retranslate</b> or type your own text.`],
                    [`<span class="help-stripe"></span> Orange stripe`, `Picked for merging (${key("Shift")}+click).`],
                ])}
                <h4>On the page image</h4>
                <p>Every bubble has an outline with its number (the reading order). The selected bubble is highlighted;
                a dashed orange outline marks a bubble picked for merging.</p>`,
        },
        {
            id: "saving", title: "Saving & memory",
            html: `
                ${items([
                    ["Save / Ctrl+Enter", `Writes the text into the page. If it differs from the AI draft it also becomes a
                        memory correction, used as an example for similar lines later. If you fixed the same term the same
                        way more than once, an "Add to glossary?" box may pop up.`],
                    ["Moving to another bubble", `Saves your edit first (a failed save keeps you on the bubble). An unchanged
                        bubble becomes ${mark("accepted")} Accepted.`],
                    ["Reset to AI / Undo", `Only change the text box (AI draft / the text it had when opened). Nothing is
                        saved until you save or move on.`],
                    ["Approve to memory", `Only shown when Settings &rsaquo; Memory &rsaquo; "Allow approving unchanged
                        translations" is on. Stores an <b>unchanged</b> AI translation as an "approved" example. Moving on
                        never does that.`],
                    ["Used from memory", `The panel under the text box: which memory examples and glossary terms the AI got
                        for this bubble. "open in memory &#8599;" opens that entry on the Memory page in a new tab, to edit or
                        delete it.`],
                ])}
                <p>Wrong entries can be fixed later on the <b>Memory</b> page (edit, delete, find &amp; replace).</p>`,
        },
        {
            id: "bubble", title: "Editor: bubble panel",
            html: items([
                ["Delete", "Removes the bubble from the page (OCR false positives). The clean image is rebuilt without it."],
                ["Edit (original text)", "Fix an OCR mistake in the English source. Use raw OCR undoes the automatic cleanup."],
                ["Retranslate", "Translates only this bubble again; its neighbours are sent as context, memory and glossary apply. Asks first if you edited it."],
                ["Re-OCR bubble", "Reads the whole bubble interior again as one crop - finds lines the page pass missed."],
                ["Set area &amp; re-OCR", "Draw the area yourself when the detected box misses a line; exactly that area is read."],
                ["Move up / Move down", "Changes the bubble's place in the reading order."],
                ["Merge with &uarr; / &darr;", `Joins the bubble with its neighbour in the list. Any bubbles: ${key("Shift")}+click them on the page or in the list, then <b>Merge...</b> (preview with the order first).`],
                ["Split...", "The OCR glued two bubbles (or map labels) together: click the lines of the new bubble on the page - or drag a box around several of them (small text at a low zoom). The dialog asks what happens to the translation: <b>Divide automatically</b> (by sentences; one sentence: at a comma or conjunction, BUT &rarr; ALE - a part that could not be divided cleanly is tagged <b>check translation</b>), <b>I choose where to cut</b> (click the word the second part starts with - no request, no tag) or <b>Translate both parts again</b> (one request each; the parts show <i>Translating...</i> meanwhile). <b>Move the picked lines to</b> an existing bubble instead: for a line the OCR put into the wrong bubble - it goes there with its part of the translation."],
                ["Skip", "Do not translate this bubble and put the original pixels back (page numbers, junk)."],
                ["Spellcheck", "Polish spellcheck in the text box, on / off."],
                ["Save, &larr; / &rarr;", "See <b>Saving &amp; memory</b>. At the first / last bubble the arrows go to the previous / next page."],
            ]),
        },
        {
            id: "toolbar", title: "Editor: page toolbar",
            html: items([
                ["&larr; / &rarr;", "Previous / next page."],
                ["&raquo;", "Next page to review: jumps over pages that are done to the first one after this page whose translation is not reviewed yet (with the review mode \"accept text + layout\": not fully reviewed). Shortcut in Keyboard &amp; mouse."],
                ["Copy path, Open folder, Open JSON, Show original file", "The page's files on disk (More &#9662;). Show original file selects the imported page in the raw folder."],
                ["Find &amp; replace...", "More &#9662; (also on the pages list): replace words in the translations of EVERY page - e.g. a term you changed your mind about. Whole words, or a word with its Polish endings (each keeps its ending). Preview with a tick per bubble first; optionally the same in the correction memory. The AI drafts are not changed, so replaced bubbles count as edited."],
                ["Delete page", "Removes the page from the list; the file stays on disk (Show excluded brings it back)."],
                ["Retranslate", `Translation only, for the whole page. Bubbles you edited are kept; ${key("Shift")}+click overwrites them too.`],
                ["Re-OCR", "OCR + cleaning again for this page. Translations of bubbles whose text did not change are kept. Tick <b>The whole page is rotated</b> in its dialog only for a scan lying sideways or upside down (sideways text inside a normal page does not need it)."],
                ["Preview text", "Shows the page with the translation typeset into the bubbles (the clean page otherwise)."],
                ["Original", "Shows the ORIGINAL page as imported (text not erased) instead - to see what a missed line or a cleaning leftover looked like. Again (or its shortcut) goes back; the outlines stay in place."],
                ["OCR debug", "Only when switched on in Settings &gt; Editor. Draws what the OCR saw over the page: kept readings (green = confidence 0.95+, amber = lower), second-chance readings (purple), readings under the threshold (red, not used), readings / lines / bubbles removed by a later step (orange: duplicates, noise, ignore patterns, lines cut by a slice edge) and the slice cuts of a tall page (blue). Hover a box outline for its text and score; the panel switches layers and lists everything not used - click an entry to jump to it. Pages read before this version show only the kept readings until a Re-OCR."],
                ["Add bubble", `Draw a rectangle over text the OCR missed; it is read and added as a new bubble. ${key("Esc")} cancels.`],
                ["Order", "Reading order of the page (top to bottom, right to left, left to right): picking one renumbers the bubbles at once, Undo in the message. The last two entries use it for the whole chapter / project - \"whole project\" is also remembered for pages processed later (prefetched pages, new chapters). Fix single bubbles with Move up / down. Re-OCR keeps the page's order."],
                ["Rebuild image", "Rebuilds the clean image from the original (after deleting / skipping bubbles) - no OCR, no translation."],
                ["Clean area / Undo clean", `Draw a rectangle over a leftover trace (pale letter ghost, smudge): painted with the plain colour around it, or filled from the surroundings on artwork. Kept by Rebuild image and Re-OCR. Undo clean removes it; with several, a list opens - point at an entry to see its area on the page, click to undo just that one (${key("Shift")}+click: all).`],
                ["&minus; / +", `Zoom (also ${key("Ctrl")}+scroll).`],
                ["More &#9662;", "Fit width / Fit height, Reset layout, and the page tools: copy the JSON path, open the folder, delete (exclude) the page."],
                ["Reset layout", "Puts the panel sizes back to default."],
            ]),
        },
        {
            id: "lettering", title: "Editor: Lettering mode",
            html: items([
                ["Translate | Lettering", "The switch under the image, on the right (shortcut in Keyboard &amp; mouse). Lettering shows the page with the text, as an export writes it."],
                ["Text boxes", "Every drawn bubble has a box. Click one to select it; drag to move, corners to resize (also when turned; hold Alt to grow evenly from the centre), the knob above it to turn it (Shift: free angle, otherwise 5° steps)."],
                ["Font", "Per bubble: click the font and type a part of its name to search (recently used ones on top; add your own in Settings > Text &amp; fonts), <b>B</b> bold and <i>I</i> italic - imitated when the font has no bold / italic file. Default (Settings) = the default font of the project text."],
                ["Fit to bubble", "Text area = the inside of the bubble again, centred horizontally and vertically, largest size that fits. Font, outline and angle stay. In an oval bubble every line is as wide as the bubble at its height (short lines at the top and bottom) - switch off in Settings > Text &amp; fonts."],
                ["Panel", "Font size (auto = largest that fits, or a fixed size), horizontal and vertical alignment, rotation. Reset bubble = everything automatic again. Text that was diagonal in the original (map labels) is lettered along the same slant (\"auto\" next to the rotation); turning it to 0° keeps it level."],
                ["Letters &amp; outline", "Black or white letters; the outline is always the other colour and grows with the text. A white outline in a white bubble is invisible - it is for text over artwork. On a dark text area (white text on black boxes) the letters are white by default (\"auto\")."],
                ["Use in whole project", "Font, bold / italic, letters and outline of this bubble become the project's style: every bubble without a style of its own uses it - on every page, also pages processed later, the preview and exports. Bubbles you styled yourself keep their style; Reset bubble = the project style. \"remove\" next to <b>Project style</b> goes back to the Settings font. \"Use on whole page\" gives the style to every bubble of this page only."],
                ["Not reviewed yet", "Lettering opens only when every bubble of the page is reviewed (accepted or edited); otherwise the message says how many are left and \"Show it\" jumps to the first. Typed text is saved first, and the bubble open in the panel counts as accepted when it is the only one left (as when you move on with the arrow)."],
                ["Red box", "The text does not fit - make the box bigger or the font smaller."],
                ["Next page", "In Lettering, a page with bubbles not reviewed yet (no edited / accepted marker) opens in Translate - reviewing comes first. In Translate nothing switches by itself."],
                ["Export page", "Writes this page into the project folder, sub-folder exported - with the options of the last export on the pages list. \"Show file\" in the message selects it in Explorer; \"View export\" opens the last exported image (or says the page has not been exported yet)."],
                ["Accept page, Layout checked, Next page: ...", "The page review controls in the same bar (both modes) - see <b>Page stages &amp; review</b>."],
            ]),
        },
        {
            id: "keys", title: "Keyboard & mouse",
            html: `<div class="help-shortcuts"><p class="text-muted">Loading your shortcuts...</p></div>
                <p class="text-muted">Change them in Settings &gt; Keyboard shortcuts. Fixed:</p>` + items([
                [`${key("Shift")}+click`, "Pick bubbles to merge into the selected one (page or list). Again to un-pick."],
                [key("Esc"), "Cancel drawing an area / merge picks / split, close a dialog or this help."],
                [`${key("Ctrl")}+scroll`, "Zoom the page image. Plain scroll pans."],
                ["Drag the panel edges", "Resize the side panel and the bubble detail (Reset layout undoes it)."],
            ]),
        },
        {
            id: "pages", title: "Pages list",
            html: items([
                ["Stages &amp; colours", "What every colour and stage name means: <b>Page stages &amp; review</b> (also the \"?\" at the end of the legend)."],
                ["Legend over the chapters", "Every stage with its colour and page count. Click one = only those pages (again = all); Ctrl / Shift + click adds or removes a stage. It is a filter like Status."],
                ["Chapter card", "The bar (one colour per stage, the grey track = not started), \"x/y reviewed\" (pages whose translation is reviewed), the last change, and one number per stage under it - hover a number for its name. Hover a page tile for its bubble counts."],
                ["Status &#9663;", "On the right of the toolbar: shows only the pages in the ticked stages (<b>Still to review</b>, <b>Layout to check</b>). It only hides tiles in this list - the bars still count every page and &larr; / &rarr; in the editor still go through every page. <b>Open first matching page</b> starts there. The box around Status and Sort is outlined while a filter or a non-default order is on."],
                ["Sort + &uarr; / &darr;", "Pick what to sort the chapters by (chapter number, progress, share reviewed, last worked on, name); the arrow button reverses the order. The project list has the same (last worked on, name, progress, created)."],
                ["Exported files", "Opens the project folder &rsaquo; exported in Explorer (also in the \"&ctdot;\" menu of a project card)."],
                ["Import new chapters...", "Adds chapter folders to the project, with a preview of what is found."],
                ["Export pages...", "Ticked chapters (or all). <b>Reviewed by you only</b> is the default; <b>Also AI drafts</b> exports pages with bubbles nobody has checked (it asks first). With the translation, clean or both; PNG or JPG; into the project folder &rsaquo; exported or another folder; optionally one ZIP."],
                ["Rescan folders", "Compares the folders on disk with the database (new / missing files, wrong statuses)."],
                ["Go to next unprocessed", "Opens the first page that is pending or failed."],
                ["Delete selected", "Deletes the ticked chapters and their pages."],
                ["Show excluded", "Also lists pages removed with Delete page / Exclude page."],
                ["Layout, Collapse / Expand all, filter", "How the chapters are shown."],
            ]),
        },
        {
            id: "stages", title: "Page stages & review",
            html: `
                <p>Every page is in exactly one stage. The names say what is <b>done</b>; the colours are the same on the
                chapter bars, the legend, the page tiles and the project cards (they do not follow the theme colour, so
                they stay apart in every theme).</p>
                <div class="help-stages"><p class="text-muted">Loading...</p></div>
                <h4>Reviewing</h4>
                ${items([
                    ["Bubble by bubble", `The careful way and the default: Save, or moving on from a bubble without a change,
                        gives it the ${mark("edited")} Edited / ${mark("accepted")} Accepted marker. When every bubble has one, the
                        page is <i>Translation reviewed</i>.`],
                    ["Accept page (N)", `Under the image. Gives the Accepted marker to the N bubbles left (their AI text stays
                        as it is), after a question; Undo in the message. Recorded as <i>accepted with the page</i> - the
                        history and the evaluation can tell it from a bubble you read on its own.`],
                    ["Layout checked", `Under the image. You checked the bubbles themselves: boxes, reading order, cleaning,
                        lettering. Translation reviewed + this = <i>Fully reviewed</i>. Click again to take it back. A Re-OCR
                        rebuilds the bubbles, so it removes the check.`],
                    ["Next page: ...", `The review mode, also under the image (remembered in this browser).
                        <b>just go</b> = nothing is accepted by itself (default). <b>accept text</b> = Next page (button,
                        shortcut, or the arrow past the last bubble) first accepts the bubbles left. <b>accept text + layout</b>
                        = also marks the layout as checked. A fast mode is shown in orange and asks once when switched on:
                        it is quick, but a page can be waved through unread. The next page says what was accepted, with Undo.`],
                    ["Bubbles without a translation", "Are never accepted - the page stays <i>Partly translated</i> until they are translated (Retranslate) or skipped."],
                ])}
                <h4>Exported (optional)</h4>
                <p>Off by default - an exported page counts as reviewed. Status &rsaquo; <b>Show &ldquo;Exported&rdquo; as a
                stage</b> turns it on: a page is exported when it was exported with the translation (any folder, ZIP or
                Export page) after its last change; \"export old\" on a tile = changed since. <b>Export pages</b> takes
                pages whose translation is reviewed by default.</p>`,
        },
        {
            id: "memory", title: "Memory (corrections)",
            html: `
                <p>The <b>correction memory</b> remembers whole lines you fixed. When a new line is <b>similar</b> to a stored
                one, the pair (English &rarr; your Polish) is shown to the AI as an example. It helps with style and
                recurring phrases; for single names and words use the <b>Glossary</b> instead.</p>
                ${items([
                    ["corrected / approved", "corrected = you changed the AI text; approved = you accepted it unchanged with Approve to memory."],
                    ["Click an entry", "Shows the details and where it was used as a hint; edit or delete it there."],
                    ["Sorting", "Click a column name above the list (Translation, Type, Used, Saved) to sort by it, again to reverse; a faint arrow appears on hover. The order is remembered."],
                    ["Find &amp; replace", "Changes a word in many stored translations at once (e.g. a name you changed your mind about). Preview first, untick what should stay."],
                    ["Export / Import JSON", "Backup or move the memory of a project."],
                    ["Re-embed memory", "Recomputes the search vectors of all entries (once, after an update)."],
                    ["\"Memory model failed to load\"", "The small local model that finds similar lines didn't load (e.g. it had to be downloaded and there was no internet). Translation still works, only without memory. The link in the message (or Settings &gt; Correction memory &gt; Test memory model) checks whether it works now and retries the load."],
                ])}`,
        },
        {
            id: "glossary", title: "Glossary (terms)",
            html: `
                <p>The <b>glossary</b> is a list of fixed term translations of one project: names, places, ranks, invented
                words (WYVERN &rarr; WYWERNA, QUA-TOYNE &rarr; QUA-TOYNE). Unlike the memory it does not depend on the
                whole line being similar: a term is used <b>every time its English word appears</b>.</p>
                <h4>How a term is used</h4>
                <ol class="help-steps">
                    <li><b>Matching</b> the English line: whole word, any case, also plural / possessive (WYVERNS, WYVERN'S)
                        and OCR line breaks (QUA- TOYNE). When terms overlap, the longer one wins (WYVERN KNIGHTS over WYVERN).</li>
                    <li><b>In the request</b>: the matched terms go to the AI with the line, and it is told to use them and
                        inflect them in Polish itself. So write the Polish term in its <b>base form</b> (WYWERNA, not WYWERNY).</li>
                    <li><b>Check after translating</b> (Settings: glossary post-edit): if a term is missing from the translation
                        (any inflected form counts), one more short request asks the AI to put it in. The fix is kept only
                        when fewer terms are missing afterwards.</li>
                </ol>
                <h4>Adding terms</h4>
                ${items([
                    ["Add term", "Type the English and the Polish term on the Glossary tab of the Memory page."],
                    ["&ldquo;Add to glossary?&rdquo; box", "Appears in the editor after a save when you fixed the same word the same way more than once. <b>Add</b> stores it (and offers to update the old form in the memory), <b>Not a term</b> hides it for good, &times; = not now (until the tab is closed)."],
                    ["Scan corrections for suggestions", "Local and fast: spelling changes of names / transliterations fixed in at least 2 corrections."],
                    ["Find terms with AI", "One request to the provider: it reads your latest corrections and lists the term changes in base form, also translated terms (DRAGOONS &rarr; JEŹDŹCY WYWERN). Terms that do not occur in the text are dropped. You accept or reject each one."],
                    ["Export / Import JSON", "Backup, or start a new project (next volume, spin-off) with the terms of an old one."],
                ])}
                <h4>The list</h4>
                ${items([
                    ["Sorting", "Click a column name above the list (English, Polish, Used, Source, Added) to sort by it, again to reverse; a faint arrow appears on hover."],
                    ["Used", "In how many translated bubbles the term fired, i.e. was given to the AI with the bubble (counted from the translation history). 0&times; = its English word has not appeared in a translated bubble since the term was added."],
                    ["Show where it fired (N)", "Exactly those bubbles: chapter, page, bubble number (Open jumps there), the English line, what the AI wrote and what the bubble says now. The term is highlighted - in English and its Polish form (also inflected) in the translations. Two rows with the same text are two different bubbles (see their numbers)."],
                    ["Source", "auto = accepted from a suggestion; empty = typed by you."],
                ])}
                <h4>Glossary or memory - which one?</h4>
                ${items([
                    ["Glossary", "One word or a short name that must always be translated the same way."],
                    ["Memory", "A whole line, your style, a joke, a phrase - used only when a new line is similar."],
                ])}
                <p class="text-muted">Editor: the &ldquo;Used from memory&rdquo; panel under the text box lists the glossary terms the AI got for that bubble.</p>`,
        },
        {
            id: "settings", title: "Settings & history",
            html: items([
                ["Settings", "Every field saves itself when you leave it; leaving the page with unsaved input asks first. The list on the right jumps to a section."],
                ["Appearance", "Color scheme (System / Light / Dark), separate theme colors for the light and the dark look, and how strongly they tint the surfaces. Stored in this browser only."],
                ["Translation &amp; API keys", "One list of keys, used from the top: when a key hits its limit or fails, the next one switched on takes over - Groq, Gemini and OpenAI-compatible keys can be mixed. Per key: on / off, order (&#9650; &#9660;), provider, model (empty = the provider default; <b>Models</b> asks the server which ones the key can use) and the key itself, never shown again in full. <b>Test</b> translates one line through that key only and shows the result in a box under it (works / limit reached / rejected / no connection). <b>Test the pool</b> = exactly as a page would be translated."],
                ["OpenAI-compatible (beta)", "Any server with the OpenAI chat API: OpenRouter, Mistral, DeepSeek, OpenAI, or a local Ollama / LM Studio (no key needed). Give the server address (presets in the field, it ends with /v1) and a model name. Not tested with every server - report problems on GitHub. Changing the address of a saved key asks for the key again: a key is never sent to a new address silently."],
                ["A/B experiment and keys", "Both versions of a bubble (with and without memory) always go through the same key and model. If that key is busy at that moment, the pair is skipped instead of comparing two models."],
                ["OCR engine", "CPU or GPU, the text detection model (mobile = about 3x faster on a CPU), CPU threads, oneDNN - after a restart of the backend. If the OCR crashes: python scripts/ocr_selftest.py (its last report is shown there)."],
                ["Storage &amp; uninstall", "Every folder the app uses with its size: your work (projects, database, settings - only shown, never deleted from the app), model caches, logs, old copies of the settings file, data set aside by an import, the program. <b>Check sizes</b> counts them; caches and logs can be deleted there (models are downloaded again on the next start). <i>Uninstalling</i> lists the folders to delete by hand after closing the app."],
                ["History", "What the AI translated: when, where (chapter, page, bubble - Open jumps there), the hints it got (memory examples, glossary) and what the bubble says now (edited / accepted / accepted with page / not reviewed). <i>Temp.</i> = the temperature of the call (0.4* = filled in afterwards for logs from before 07.11.2026, when it wasn't logged yet). The column names stay on top while you scroll. A memory example shows EN and your PL; for a corrected one the change from the AI text is marked (struck out = the AI text you replaced). Filters: changed by you, not reviewed, chapter, search. <i>Research view</i> adds the A/B columns of the memory experiment. Read only."],
                ["History: sorting", "Click Time, Where, Source, In the bubble now, Hints or Temp.; again = reverse. By time the rows are grouped by day, by Where by chapter (then page, then bubble) - click a group row to fold it, <b>Fold all</b> for all. Rows at a time (50 / 100 / 250 / all) only says how many are drawn at once: the filters, sorting and the summary always use the whole history."],
                ["Memory page", "Click a column name (Translation, Type, Used, Saved; on the Glossary tab English, Polish, Used, Source, Added) to sort, again to reverse. Long lists show 200 entries, then Show more."],
            ]),
        },
    ];

    let dialog = null;

    function defaultTopic() {
        const page = location.pathname.split("/").pop() || "index.html";
        if (page === "editor.html") {
            return document.getElementById("editorLayout")?.classList.contains("hidden") ? "pages" : "markers";
        }
        return { "memory.html": "memory", "settings.html": "settings", "logs.html": "settings" }[page] || "start";
    }

    function build() {
        dialog = document.createElement("dialog");
        dialog.className = "help-dialog";
        dialog.setAttribute("aria-label", "Help");
        dialog.innerHTML = `
            <div class="help-frame">
                <div class="help-head">
                    <h2>Help</h2>
                    <span class="text-muted help-hint">Esc or a click outside closes it</span>
                    <button type="button" class="icon-btn help-close" aria-label="Close help" title="Close (Esc)">&times;</button>
                </div>
                <div class="help-body">
                    <nav class="help-nav">${TOPICS.map((t) => `<button type="button" data-topic="${t.id}">${t.title}</button>`).join("")}</nav>
                    <div class="help-content" tabindex="-1"></div>
                </div>
            </div>`;
        dialog.querySelector(".help-close").addEventListener("click", () => dialog.close());
        dialog.querySelector(".help-nav").addEventListener("click", (e) => {
            const btn = e.target.closest("[data-topic]");
            if (btn) show(btn.dataset.topic);
        });
        // a click on the backdrop lands on the <dialog> itself (the frame fills the visible box)
        dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
        document.body.appendChild(dialog);
    }

    function show(topicId) {
        const topic = TOPICS.find((t) => t.id === topicId) || TOPICS[0];
        dialog.querySelectorAll("[data-topic]").forEach((b) => b.classList.toggle("active", b.dataset.topic === topic.id));
        const content = dialog.querySelector(".help-content");
        content.innerHTML = `<h3>${topic.title}</h3>${topic.html}`;
        content.scrollTop = 0;
        const list = content.querySelector(".help-shortcuts");
        if (list) shortcutsReady.then((m) => { if (m) list.innerHTML = shortcutItems(m); });
        const stages = content.querySelector(".help-stages");
        if (stages) stagesReady.then((m) => { if (m) stages.innerHTML = stageTable(m); });
    }

    /* stage names / colours / meanings come from js/stages.js - the same list the pages list uses */
    const stagesReady = import("./stages.js").catch(() => null);

    function stageTable(m) {
        return `<table class="help-stage-table"><thead><tr><th></th><th>Stage</th><th>What is done</th><th>How a page gets there</th></tr></thead>
            <tbody>${[...m.STAGE_GROUPS].reverse().map((g) => `<tr>
                <td><i class="dot stage-${g.id}"></i></td><td><b>${g.label}</b></td><td>${g.tip}</td><td class="text-muted">${g.how}</td>
            </tr>`).join("")}</tbody></table>
            <p class="text-muted">Pipeline order from the top. Failed, queued and running pages count as <i>Not started</i>
            (their own tag on the tile).</p>`;
    }

    /* the user's current keyboard shortcuts (js/shortcuts.js; a classic script can still import() a module) */
    const shortcutsReady = import("./shortcuts.js").catch(() => null);
    let shortcuts = null;
    shortcutsReady.then((m) => { shortcuts = m; });

    function shortcutItems(m) {
        const bindings = m.currentBindings();
        return items(m.ACTIONS.map((a) => [
            bindings[a.id] ? m.formatCombo(bindings[a.id]).split(" + ").map((p) => key(p)).join("+") : '<span class="text-muted">no key</span>',
            `${a.label} <span class="text-muted">(${m.SCOPES[a.scope].toLowerCase()}${a.whileTyping ? ", also while typing" : ""})</span>`,
        ]));
    }

    function open(topicId) {
        if (!dialog) build();
        show(topicId || defaultTopic());
        if (!dialog.open) dialog.showModal();
        dialog.querySelector(".help-content").focus({ preventScroll: true });
    }

    function addSidebarButton() {
        const footer = document.querySelector(".sidebar-footer");
        if (!footer || document.getElementById("helpBtn")) return;
        const btn = document.createElement("button");
        btn.id = "helpBtn";
        btn.type = "button";
        btn.className = "sidebar-link sidebar-button";
        btn.title = "What the markers, buttons and shortcuts do (?)";
        btn.textContent = "Help";
        btn.addEventListener("click", () => open());
        footer.appendChild(btn);
    }

    document.addEventListener("keydown", (e) => {
        const t = e.target;
        const typing = !!t && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));
        const wanted = shortcuts
            ? shortcuts.actionFor(e, { typing, scopes: ["global"] }) === "help"
            : e.key === "?" && !e.ctrlKey && !e.metaKey && !e.altKey && !typing;     // module not loaded yet: the default
        if (!wanted) return;
        if (document.querySelector("dialog[open]:not(.help-dialog)")) return;   // another dialog is in charge
        e.preventDefault();
        open();
    });

    window.oktHelp = { open };
    // after theme.js has added its sidebar button (also on DOMContentLoaded), so Help comes last in the footer
    if (document.readyState === "complete") addSidebarButton();
    else document.addEventListener("DOMContentLoaded", addSidebarButton);
})();
