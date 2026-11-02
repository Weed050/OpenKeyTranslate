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
                <p>The list header counts them: ${mark("edited")} edited, ${mark("accepted")} accepted, and how many are left.</p>
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
                ["Split...", "The OCR glued two bubbles (or map labels) together: click the lines of the new bubble on the page. The existing translation is divided by sentences - nothing is translated again unless you tick it; a part that could not be divided is tagged <b>check translation</b>."],
                ["Skip", "Do not translate this bubble and put the original pixels back (page numbers, junk)."],
                ["Spellcheck", "Polish spellcheck in the text box, on / off."],
                ["Save, &larr; / &rarr;", "See <b>Saving &amp; memory</b>. At the first / last bubble the arrows go to the previous / next page."],
            ]),
        },
        {
            id: "toolbar", title: "Editor: page toolbar",
            html: items([
                ["&larr; / &rarr;", "Previous / next page."],
                ["Copy path, Open folder, Open JSON", "The page's files on disk."],
                ["Delete page", "Removes the page from the list; the file stays on disk (Show excluded brings it back)."],
                ["Retranslate", `Translation only, for the whole page. Bubbles you edited are kept; ${key("Shift")}+click overwrites them too.`],
                ["Re-OCR", "OCR + cleaning again for this page. Translations of bubbles whose text did not change are kept. Tick <b>The whole page is rotated</b> in its dialog only for a scan lying sideways or upside down (sideways text inside a normal page does not need it)."],
                ["Preview text", "Shows the page with the translation typeset into the bubbles (the clean page otherwise)."],
                ["Add bubble", `Draw a rectangle over text the OCR missed; it is read and added as a new bubble. ${key("Esc")} cancels.`],
                ["Order + Sort", "Reading order (top to bottom, right to left, left to right). Sort renumbers the bubbles; fix single ones with Move up / down."],
                ["Rebuild image", "Rebuilds the clean image from the original (after deleting / skipping bubbles) - no OCR, no translation."],
                ["Clean area / Undo clean", `Draw a rectangle over a leftover trace (pale letter ghost, smudge): painted with the plain colour around it, or filled from the surroundings on artwork. Kept by Rebuild image and Re-OCR. Undo clean removes the last one (${key("Shift")}+click: all).`],
                ["&minus; / + / Fit width / Fit height", `Zoom (also ${key("Ctrl")}+scroll).`],
                ["Reset layout", "Puts the panel sizes back to default."],
            ]),
        },
        {
            id: "lettering", title: "Editor: Lettering mode",
            html: items([
                ["Translate | Lettering", "The switch under the image, on the right (shortcut in Keyboard &amp; mouse). Lettering shows the page with the text, as an export writes it."],
                ["Text boxes", "Every drawn bubble has a box. Click one to select it; drag to move, corners to resize (also when turned), the knob above it to turn it (Shift: free angle, otherwise 5° steps)."],
                ["Panel", "Font size (auto = largest that fits, or a fixed size), alignment, rotation, white outline for text over artwork. Reset bubble = everything automatic again. Text that was diagonal in the original (map labels) is lettered along the same slant (\"auto\" next to the rotation); turning it to 0° keeps it level."],
                ["Red box", "The text does not fit - make the box bigger or the font smaller."],
                ["Next page", "In Lettering, a page with bubbles not reviewed yet (no edited / accepted marker) opens in Translate - reviewing comes first. In Translate nothing switches by itself."],
                ["Export page", "Writes this page into the project folder, sub-folder exported - with the options of the last export on the pages list."],
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
                ["Status badges", "processed / pending / queued / processing / failed / excluded - per page."],
                ["Import new chapters...", "Adds chapter folders to the project, with a preview of what is found."],
                ["Export pages...", "Ticked chapters (or all): with the translation, clean or both; PNG or JPG; into the project folder &rsaquo; exported or another folder; optionally one ZIP."],
                ["Rescan folders", "Compares the folders on disk with the database (new / missing files, wrong statuses)."],
                ["Go to next unprocessed", "Opens the first page that is pending or failed."],
                ["Delete selected", "Deletes the ticked chapters and their pages."],
                ["Show excluded", "Also lists pages removed with Delete page / Exclude page."],
                ["Layout, Collapse / Expand all, filter", "How the chapters are shown."],
            ]),
        },
        {
            id: "memory", title: "Memory & glossary",
            html: `
                <h4>Corrections</h4>
                ${items([
                    ["corrected / approved", "corrected = you changed the AI text; approved = you accepted it unchanged with Approve to memory."],
                    ["Click an entry", "Shows the details and where it was used as a hint; edit or delete it there."],
                    ["Find &amp; replace", "Changes a word in many stored translations at once (e.g. a name you changed your mind about). Preview first, untick what should stay."],
                    ["Export / Import JSON", "Backup or move the memory of a project."],
                    ["Re-embed memory", "Recomputes the search vectors of all entries (once, after an update)."],
                ])}
                <h4>Glossary</h4>
                ${items([
                    ["Terms", "Fixed term translations, always given to the AI when the English word appears (names, invented words)."],
                    ["Scan corrections for suggestions", "Local and fast: spelling changes of names fixed in at least 2 corrections."],
                    ["Find terms with AI", "One request to the provider: it reads your latest corrections and lists term changes in base form."],
                ])}`,
        },
        {
            id: "settings", title: "Settings & logs",
            html: items([
                ["Settings", "Every field saves itself when you leave it; leaving the page with unsaved input asks first. The list on the right jumps to a section."],
                ["Appearance", "Color scheme (System / Light / Dark), separate theme colors for the light and the dark look, and how strongly they tint the surfaces. Stored in this browser only."],
                ["Translation (BYOK)", "Your API keys, provider and model per key. Keys are never shown again in full."],
                ["Logs", "Every zero-shot vs memory-injected translation pair (the A/B experiment), with what was given to the AI. Read only."],
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
