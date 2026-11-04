/**
 * @file frontend/js/editor-lettering.js
 * @description Lettering mode of the editor: placing the translated text on the page. The same page, list and
 * zoom as Translate mode; one button under the image (or the toggleMode shortcut) switches between them.
 *
 * - The image always shows the typeset page (server render), so what you see is what an export writes.
 * - Every drawn bubble's text area is a box on the page (GET /pages/{id}/layout). The selected box can be moved
 *   (drag), resized (corners - also when it is rotated) and turned (the knob above it). On release the change is
 *   stored in the bubble's "layout" (PATCH .../layout) and the page is rendered again.
 * - The side panel sets font size (auto or fixed), alignment, rotation and the white outline, or resets everything.
 * - Moving to another page in Lettering mode: a page whose bubbles are not all reviewed (edited / accepted marker)
 *   switches to Translate - reviewing comes first; in Translate mode nothing switches by itself.
 * editor.js wires its own functions in through initLettering().
 */

import { api, API_BASE } from "./api.js";
import { pageId, state, el, escapeHtml, showToast } from "./editor-shared.js";

const MODE_KEY = "okt-editor-mode";
const EXPORT_KEY = "okt-export-options";

/** Callbacks from editor.js: select(i), rerender(), refreshImage(), saveIfChanged(), status(bubble), navigate(dir),
 *  shortcutLabel(actionId), renderReview(slot) (page review controls, js/editor-review.js). */
let hooks = {};

export function initLettering(editorHooks) {
    hooks = editorHooks;
    try { state.mode = localStorage.getItem(MODE_KEY) === "lettering" ? "lettering" : "translate"; } catch { state.mode = "translate"; }
    state.layout = {};
}

export function isLettering() {
    return state.mode === "lettering";
}

/* ------------------------------------------------------------------ mode */

/**
 * Why Lettering can not be opened on this page right now, or null. ONE rule for the switch, the shortcut and a page
 * load: the page must be processed and every bubble that is not skipped must be reviewed (accepted or edited) -
 * Lettering places the final text, so an unreviewed bubble would be typeset with a draft (or with nothing).
 */
export function letteringBlocker() {
    if (document.getElementById("editorLayout")?.classList.contains("hidden")) return { message: "This page is not processed yet." };
    const open = state.bubbles.filter((b) => !b.skip && !hooks.status(b));
    if (!open.length) return null;
    const untranslated = open.filter((b) => !(b.translation || "").trim()).length;
    const n = open.length;
    return {
        message: `${n} bubble${n > 1 ? "s" : ""} not reviewed yet${untranslated ? ` (${untranslated} without a translation)` : ""} - accept or edit ${n > 1 ? "them" : "it"} first.`,
        first: state.bubbles.indexOf(open[0]),
    };
}

export async function setMode(mode, { quiet = false } = {}) {
    if (mode === state.mode) return;
    if (mode === "lettering") {
        const blocked = letteringBlocker();
        if (blocked) {
            // stay in Translate (the view does not change); offer a jump to the first bubble that needs the review
            showToast(`Lettering: ${blocked.message}`, 5000, blocked.first >= 0 && blocked.first !== undefined
                ? { label: "Show it", run: () => hooks.select(blocked.first) } : null);
            renderModeBar();
            return;
        }
        if (!(await hooks.saveIfChanged())) return;
        if (letteringBlocker()) return setMode(mode, { quiet });   // the save changed a status: show the message
    }
    state.mode = mode;
    try { localStorage.setItem(MODE_KEY, mode); } catch { /* this page only */ }
    await enterCurrentMode();
    if (!quiet) showToast(mode === "lettering" ? "Lettering: drag the text boxes on the page" : "Translate: review and edit the text", 2200);
}

export function toggleMode() {
    return setMode(isLettering() ? "translate" : "lettering");
}

/** Apply state.mode after a page load or a switch: preview image, text areas, panel, mode bar. */
export async function enterCurrentMode() {
    document.body.classList.toggle("lettering-mode", isLettering());
    if (isLettering()) {
        if (state.previewBeforeLettering === undefined) state.previewBeforeLettering = !!state.preview;
        state.preview = true;
        await loadLayout();
    } else {
        if (state.previewBeforeLettering !== undefined) state.preview = state.previewBeforeLettering;
        state.previewBeforeLettering = undefined;
        state.layout = {};
    }
    document.getElementById("previewBtn")?.classList.toggle("primary", !!state.preview);
    hooks.refreshImage();
    renderModeBar();
    hooks.rerender();
}

/** Lettering mode on a page that is not fully reviewed: back to Translate (called after a page is loaded). */
export async function checkReviewedOnLoad() {
    if (!isLettering()) return enterCurrentMode();
    const blocked = letteringBlocker();
    if (blocked) {
        state.mode = "translate";
        try { localStorage.setItem(MODE_KEY, "translate"); } catch { /* ignore */ }
        await enterCurrentMode();
        showToast(`Switched to Translate: ${blocked.message}`, 5000);
        return;
    }
    await enterCurrentMode();
}

async function loadLayout() {
    try {
        const { bubbles } = await api.pages.layout(pageId);
        state.layout = Object.fromEntries(bubbles.map((p) => [p.bubble_id, p]));
    } catch (e) {
        state.layout = {};
        if (state.bubbles.some((b) => !b.skip && (b.translation || "").trim())) showToast(`Text areas: ${e.message}`, 4000);
    }
}

/* ------------------------------------------------------------------ mode bar under the image */

export function exportOptions() {
    try {
        return { content: "translated", format: "png", quality: 92, ...JSON.parse(localStorage.getItem(EXPORT_KEY) || "{}") };
    } catch {
        return { content: "translated", format: "png", quality: 92 };
    }
}

export function renderModeBar() {
    const bar = document.getElementById("modeBar");
    if (!bar) return;
    const opts = exportOptions();
    const what = { translated: "with text", clean: "clean", both: "with text + clean" }[opts.content] || "with text";
    const toggleKey = hooks.shortcutLabel("toggleMode");
    const exportKey = hooks.shortcutLabel("exportPage");
    // the switch is used most: on the right, next to the side panel it changes; rarely used things on the left
    bar.innerHTML = `
        <button type="button" id="exportPageBtn" title="Write this page into the project's exported folder (${what}, ${opts.format.toUpperCase()}) - the options are those of the last export on the project page${exportKey ? ` (${exportKey})` : ""}">Export page</button>
        ${isLettering() ? '<span class="text-muted mode-hint">Drag a box to move it, corners resize (Alt: evenly from the centre), the knob turns it</span>' : ""}
        <a class="btn" id="viewExportBtn" href="${API_BASE}/pages/${pageId}/exported" target="_blank" rel="noopener" title="Open the last exported image of this page in a new tab">View export</a>
        <span class="review-slot" id="reviewSlot"></span>
        <span class="mode-bar-spacer"></span>
        ${toggleKey ? `<span class="text-muted mode-hint"><kbd>${escapeHtml(toggleKey)}</kbd> switches</span>` : ""}
        <div class="segmented mode-switch" role="radiogroup" aria-label="Editor mode" title="Switch the editor mode${toggleKey ? ` (${toggleKey})` : ""}">
            <button type="button" data-mode="translate" class="${isLettering() ? "" : "selected"}" role="radio" aria-checked="${!isLettering()}">Translate</button>
            <button type="button" data-mode="lettering" class="${isLettering() ? "selected" : ""}" role="radio" aria-checked="${isLettering()}">Lettering</button>
        </div>`;
    bar.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    bar.querySelector("#exportPageBtn").addEventListener("click", exportPage);
    hooks.renderReview?.(bar.querySelector("#reviewSlot"));
}

export async function exportPage() {
    if (!(await hooks.saveIfChanged())) return;
    const opts = exportOptions();
    try {
        const r = await api.pages.exportPage(pageId, opts);
        // the file is selected in Explorer: the exported folder lives in the project folder (AppData by default)
        showToast(`Exported: ${(r.files || []).map((f) => f.split(/[\\/]/).pop()).join(", ")}`, 7000,
                  r.files?.length ? { label: "Show file", run: () => api.system.openPath(r.files[0]).catch((e) => showToast(e.message, 4000)) } : null);
        renderModeBar();
    } catch (e) {
        showToast(`Export failed: ${e.message}`, 6000);
    }
}

/* ------------------------------------------------------------------ text boxes on the page */

const rad = (deg) => (deg * Math.PI) / 180;

/** Draw every text area (called by editor.js renderBubbleOutlines instead of the OCR outlines). */
export function renderLetteringOverlay(scale) {
    el.imageCanvas.querySelectorAll(".letter-area").forEach((n) => n.remove());
    state.bubbles.forEach((bubble, index) => {
        const p = state.layout[bubble.bubble_id];
        if (!p) return;
        const selected = index === state.selectedIndex;
        const box = document.createElement("div");
        box.className = "letter-area" + (selected ? " selected" : "") + (p.overflow ? " overflow" : "") + (p.manual_box ? " manual" : "");
        placeBox(box, p.box, p.angle, scale);
        box.title = `Bubble ${index + 1}${p.overflow ? " - the text does not fit" : ""}`;
        box.innerHTML = `<span class="letter-index">${index + 1}</span>`;
        if (selected) {
            box.insertAdjacentHTML("beforeend", ["nw", "ne", "sw", "se"].map((h) => `<i class="lh lh-${h}" data-h="${h}"></i>`).join("")
                + '<i class="lrot" data-h="rotate" title="Turn (Shift: free, otherwise snaps to 5°)"></i>');
            box.addEventListener("mousedown", (e) => startDrag(e, bubble.bubble_id, box, scale));
        } else {
            box.addEventListener("click", () => hooks.select(index));
        }
        el.imageCanvas.appendChild(box);
    });
}

function placeBox(node, b, angle, scale) {
    Object.assign(node.style, {
        left: `${b[0] * scale}px`, top: `${b[1] * scale}px`,
        width: `${(b[2] - b[0]) * scale}px`, height: `${(b[3] - b[1]) * scale}px`,
        transform: angle ? `rotate(${angle}deg)` : "",
    });
}

function startDrag(e, bubbleId, node, scale) {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    const p = state.layout[bubbleId];
    const kind = e.target.dataset.h || "move";
    const [x1, y1, x2, y2] = p.box;
    const start = { x: e.clientX, y: e.clientY, cx: (x1 + x2) / 2, cy: (y1 + y2) / 2, w: x2 - x1, h: y2 - y1, angle: p.angle || 0 };
    const canvasRect = el.imageCanvas.getBoundingClientRect();
    let box = [...p.box];
    let angle = start.angle;
    let moved = false;

    const onMove = (ev) => {
        const dx = (ev.clientX - start.x) / scale;
        const dy = (ev.clientY - start.y) / scale;
        if (!moved && Math.hypot(dx, dy) * scale < 3) return;
        moved = true;
        if (kind === "move") {
            box = [x1 + dx, y1 + dy, x2 + dx, y2 + dy];
        } else if (kind === "rotate") {
            const mx = (ev.clientX - canvasRect.left) / scale - start.cx;
            const my = (ev.clientY - canvasRect.top) / scale - start.cy;
            angle = (Math.atan2(mx, -my) * 180) / Math.PI;
            if (!ev.shiftKey) angle = Math.round(angle / 5) * 5;
        } else {
            // resize in the box's own (rotated) frame; the opposite corner stays where it is
            const a = rad(start.angle);
            const lx = dx * Math.cos(a) + dy * Math.sin(a);
            const ly = -dx * Math.sin(a) + dy * Math.cos(a);
            const sx = kind.includes("e") ? 1 : -1;
            const sy = kind.includes("s") ? 1 : -1;
            if (ev.altKey) {                         // Alt: grow / shrink evenly in every direction, the centre stays
                const w = Math.max(12, start.w + 2 * sx * lx);
                const h = Math.max(12, start.h + 2 * sy * ly);
                box = [start.cx - w / 2, start.cy - h / 2, start.cx + w / 2, start.cy + h / 2];
                placeBox(node, box, angle, scale);
                return;
            }
            const w = Math.max(12, start.w + sx * lx);
            const h = Math.max(12, start.h + sy * ly);
            const ax = -sx * start.w / 2, ay = -sy * start.h / 2;              // anchor in local coordinates
            const gx = start.cx + ax * Math.cos(a) - ay * Math.sin(a);         // ... and on the page
            const gy = start.cy + ax * Math.sin(a) + ay * Math.cos(a);
            const nax = -sx * w / 2, nay = -sy * h / 2;
            const cx = gx - (nax * Math.cos(a) - nay * Math.sin(a));
            const cy = gy - (nax * Math.sin(a) + nay * Math.cos(a));
            box = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2];
        }
        placeBox(node, box, angle, scale);
    };
    const onUp = async () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        if (!moved) return;
        const changes = kind === "rotate" ? { angle: Math.round(angle * 10) / 10 } : { box: box.map((v) => Math.round(v)) };
        await patchLayout(bubbleId, changes);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
}

/* ------------------------------------------------------------------ changes */

/* ------------------------------------------------------------------ style that carries over to the next bubbles */

// font, B / I, outline and colour chosen on one bubble go with the user to the next ones (a series uses one font);
// the area, size, alignment and angle belong to each bubble and never carry over.
const STYLE_DEFAULTS = { font: null, bold: false, italic: false, stroke: "none", color: "black" };
const CARRY_KEY = "okt-letter-carry";
const carriedTo = new Set();         // bubbles already given the carried style in this visit (not again after a Reset)

const styleKey = () => `okt-letter-style-${state.projectId || "x"}`;

function carryOn() {
    try { return localStorage.getItem(CARRY_KEY) !== "0"; } catch { return true; }
}

function lastStyle() {
    try { return JSON.parse(localStorage.getItem(styleKey()) || "null"); } catch { return null; }
}

/** The style of a bubble's placement (GET layout) - every key, defaults included. */
function styleOfPlacement(p) {
    return { font: p.font || null, bold: !!p.bold, italic: !!p.italic, stroke: p.stroke || "none", color: p.color || "black" };
}

const isDefaultStyle = (st) => Object.keys(STYLE_DEFAULTS).every((k) => (st[k] ?? STYLE_DEFAULTS[k]) === STYLE_DEFAULTS[k]);
const ownStyle = (bubble) => Object.keys(STYLE_DEFAULTS).some((k) => bubble.layout && k in bubble.layout);

/** A bubble shown for the first time, with no style of its own: give it the last style the user chose. */
async function carryStyle(b) {
    if (carriedTo.has(b.bubble_id)) return;
    carriedTo.add(b.bubble_id);
    const st = lastStyle();
    if (!carryOn() || !st || isDefaultStyle(st) || ownStyle(b) || b.skip) return;
    await patchLayout(b.bubble_id, st, { remember: false });
    const what = [st.font || "default font", st.bold && "bold", st.italic && "italic", st.stroke !== "none" && `${st.stroke} outline`,
                  st.color === "white" && "white letters"].filter(Boolean).join(", ");
    showToast(`Style carried over: ${what}`, 4000, { label: "Undo", run: () => patchLayout(b.bubble_id, { ...STYLE_DEFAULTS }, { remember: false }) });
}

/** This bubble's style on every other translated bubble of the page. */
async function styleToPage(b) {
    const st = styleOfPlacement(state.layout[b.bubble_id]);
    const others = state.bubbles.filter((x) => x !== b && !x.skip && state.layout[x.bubble_id]);
    if (!others.length) return;
    try {
        for (const x of others) {
            const r = await api.pages.setLayout(pageId, x.bubble_id, st);
            if (Object.keys(r.layout).length) x.layout = r.layout; else delete x.layout;
            if (r.placement) state.layout[x.bubble_id] = r.placement;
            carriedTo.add(x.bubble_id);
        }
        showToast(`Style used on ${others.length} more bubble${others.length > 1 ? "s" : ""}`, 2500);
    } catch (e) {
        showToast(`Lettering: ${e.message}`, 5000);
    }
    hooks.refreshImage();
    hooks.rerender();
}

export async function patchLayout(bubbleId, changes, { remember = true } = {}) {
    try {
        const r = await api.pages.setLayout(pageId, bubbleId, changes);
        if (remember && r.placement && Object.keys(changes).some((k) => k in STYLE_DEFAULTS)) {
            try { localStorage.setItem(styleKey(), JSON.stringify(styleOfPlacement(r.placement))); } catch { /* this page only */ }
        }
        const bubble = state.bubbles.find((b) => b.bubble_id === bubbleId);
        if (bubble) {
            if (Object.keys(r.layout).length) bubble.layout = r.layout;
            else delete bubble.layout;
        }
        if (r.placement) state.layout[bubbleId] = r.placement;
        hooks.refreshImage();
        hooks.rerender();
    } catch (e) {
        showToast(`Lettering: ${e.message}`, 5000);
        hooks.rerender();
    }
}

function selected() {
    return state.selectedIndex === null ? null : state.bubbles[state.selectedIndex];
}

/** Keyboard actions of Lettering mode (shortcuts fontBigger / fontSmaller / rotateLeft / rotateRight / resetLayout). */
export function letteringAction(action) {
    const b = selected();
    const p = b && state.layout[b.bubble_id];
    if (!p) return;
    if (action === "fontBigger") patchLayout(b.bubble_id, { font_size: p.font_size + 2 });
    else if (action === "fontSmaller") patchLayout(b.bubble_id, { font_size: Math.max(6, p.font_size - 2) });
    else if (action === "rotateRight") patchLayout(b.bubble_id, { angle: (p.angle || 0) + 5 });
    else if (action === "rotateLeft") patchLayout(b.bubble_id, { angle: (p.angle || 0) - 5 });
    else if (action === "resetLayout") patchLayout(b.bubble_id, { reset: true });
}

/* ------------------------------------------------------------------ fonts */

const RECENT_FONTS_KEY = "okt-recent-fonts";
let fontList = null;                 // [{family, source, polish, styles}] from GET /fonts/, loaded once per page

async function loadFonts() {
    if (fontList) return fontList;
    try {
        fontList = (await api.fonts.list()).fonts;
    } catch {
        fontList = [];
    }
    return fontList;
}

function recentFonts() {
    try { return JSON.parse(localStorage.getItem(RECENT_FONTS_KEY) || "[]"); } catch { return []; }
}

function rememberFont(family) {
    if (!family) return;
    const list = [family, ...recentFonts().filter((f) => f !== family)].slice(0, 6);
    try { localStorage.setItem(RECENT_FONTS_KEY, JSON.stringify(list)); } catch { /* this page only */ }
}

function fontOptions(current) {
    const fonts = fontList || [];
    const known = new Set(fonts.map((f) => f.family));
    const opt = (f) => `<option value="${escapeHtml(f.family)}" ${f.family === current ? "selected" : ""}>${escapeHtml(f.family)}${f.polish ? "" : " (no Polish!)"}</option>`;
    const recent = recentFonts().filter((r) => known.has(r)).map((r) => fonts.find((f) => f.family === r));
    const mine = fonts.filter((f) => f.source === "user");
    const polish = fonts.filter((f) => f.source === "system" && f.polish);
    const other = fonts.filter((f) => f.source === "system" && !f.polish);
    return `<option value="" ${current ? "" : "selected"}>Default (Settings)</option>`
        + (current && !known.has(current) ? `<option value="${escapeHtml(current)}" selected>${escapeHtml(current)} (not found)</option>` : "")
        + (recent.length ? `<optgroup label="Recently used">${recent.map(opt).join("")}</optgroup>` : "")
        + (mine.length ? `<optgroup label="Your fonts">${mine.map(opt).join("")}</optgroup>` : "")
        + `<optgroup label="System fonts with Polish letters">${polish.map(opt).join("")}</optgroup>`
        + (other.length ? `<optgroup label="Other system fonts">${other.map(opt).join("")}</optgroup>` : "");
}

/* ------------------------------------------------------------------ side panel */

export function renderLetteringPanel() {
    const panel = el.bubbleDetail;
    panel.classList.remove("hidden");
    const b = selected();
    if (!b) {
        panel.innerHTML = `<p class="text-muted detail-empty">Pick a bubble in the list or a text box on the page.</p>
            <div class="detail-actions detail-bottom">${navButtons()}</div>`;
        wireNav(panel);
        return;
    }
    if (!fontList) loadFonts().then(() => { if (isLettering()) renderLetteringPanel(); });
    const index = state.selectedIndex;
    const p = state.layout[b.bubble_id];
    const nav = navButtons();
    if (!p) {
        const why = b.skip ? "This bubble is skipped - nothing is drawn." : "No translation yet - nothing is drawn. Translate it first.";
        panel.innerHTML = `<div class="lettering-panel"><div class="field-label">Bubble ${index + 1}</div><p class="text-muted">${why}</p>
            <div class="detail-actions detail-bottom">${nav}</div></div>`;
        wireNav(panel);
        return;
    }
    if (!carriedTo.has(b.bubble_id)) {
        carryStyle(b);               // re-renders the panel when it changes something
    }
    const seg = (name, options, current) => `<div class="segmented" data-field="${name}">${options.map(([v, label, title]) =>
        `<button type="button" data-value="${v}" class="${String(v) === String(current) ? "selected" : ""}" title="${title || label}">${label}</button>`).join("")}</div>`;
    panel.innerHTML = `
        <div class="lettering-panel">
            <div class="field-label">Bubble ${index + 1}</div>
            <div class="lettering-text">${escapeHtml(b.translation || "")}</div>
            <p class="text-muted lettering-note">Edit the words in Translate - here only where and how they are drawn.</p>
            ${p.overflow ? '<p class="import-error">The text does not fit this area - make the box bigger or the font smaller.</p>' : ""}
            <div class="lettering-row"><span>Area</span>
                <span>${p.manual_box ? "drawn by you" : "automatic (bubble inside)"}</span>
                ${p.manual_box ? '<button type="button" data-act="areaAuto" title="Back to the automatic area">Automatic</button>' : ""}</div>
            <div class="lettering-row"><span>Font size</span>
                <span class="lettering-size">
                    <button type="button" data-act="smaller" title="Smaller (fixed size)">&minus;</button>
                    <input type="number" id="letterSize" min="6" max="300" value="${p.font_size}" title="Font size in px">
                    <button type="button" data-act="bigger" title="Bigger (fixed size)">+</button>
                    ${p.manual_size ? '<button type="button" data-act="sizeAuto" title="Largest size that fits">Auto</button>' : '<span class="text-muted">auto</span>'}
                </span></div>
            <div class="lettering-row"><span>Font</span>
                <span class="lettering-font">
                    <select id="letterFont" title="Font of this bubble (fonts: Settings > Text &amp; fonts)">${fontOptions(p.font || "")}</select>
                    <button type="button" data-act="bold" class="${p.bold ? "selected" : ""}" title="Bold (imitated when the font has no bold file)"><b>B</b></button>
                    <button type="button" data-act="italic" class="${p.italic ? "selected" : ""}" title="Italic (imitated when the font has no italic file)"><i>I</i></button>
                </span></div>
            <div class="lettering-row"><span>Align</span>${seg("align", [["left", "Left"], ["center", "Center"], ["right", "Right"]], p.align)}</div>
            <div class="lettering-row"><span>Vertical</span>${seg("valign", [["top", "Top"], ["middle", "Middle"], ["bottom", "Bottom"]], p.valign || "middle")}</div>
            <div class="lettering-row"><span>Rotation</span>
                <span class="lettering-size">
                    <button type="button" data-act="rotLeft" title="-5°">&#8634;</button>
                    <input type="number" id="letterAngle" min="-180" max="180" step="1" value="${Math.round(p.angle || 0)}" title="Degrees, clockwise">
                    <button type="button" data-act="rotRight" title="+5°">&#8635;</button>
                    ${p.angle ? '<button type="button" data-act="rotZero">0°</button>' : ""}
                    ${p.angle && !p.manual_angle ? '<span class="text-muted" title="The original text is tilted - the translation follows it">auto</span>' : ""}
                </span></div>
            <div class="lettering-row"><span>Letters</span>${seg("color", [["black", "Black", "Black letters (outline: white)"], ["white", "White", "White letters (outline: black) - text on dark artwork"]], p.color || "black")}</div>
            <div class="lettering-row"><span>Outline</span>${seg("stroke", [["none", "None"], ["thin", "Thin", "Thin outline in the other colour"], ["thick", "Thick", "Thick outline in the other colour"]], p.stroke)}</div>
            ${p.stroke !== "none" && (p.color || "black") === "black" ? '<p class="text-muted lettering-note">The outline is white: in a white bubble it is invisible - it shows where the text lies over artwork.</p>' : ""}
            <div class="lettering-row lettering-style-row">
                <label class="checkbox-label" title="Font, bold / italic, letters and outline chosen here are given to the next bubbles you open (that have no style of their own)">
                    <input type="checkbox" id="letterCarry" ${carryOn() ? "checked" : ""}> Next bubbles get this style</label>
                <button type="button" data-act="styleToPage" title="Font, bold / italic, letters and outline of this bubble on every bubble of this page">Use on whole page</button>
            </div>
            <div class="detail-actions detail-bottom">
                <button type="button" data-act="fit" title="Text area = the inside of the bubble, centred both ways, largest size that fits (font, outline and angle stay)">Fit to bubble</button>
                <button type="button" data-act="reset" title="Everything of this bubble back to automatic">Reset bubble</button>
                ${nav}
            </div>
        </div>`;
    const id = b.bubble_id;
    panel.querySelectorAll(".segmented[data-field] button").forEach((btn) => btn.addEventListener("click", () =>
        patchLayout(id, { [btn.parentElement.dataset.field]: btn.dataset.value })));
    const act = (name, fn) => panel.querySelector(`[data-act="${name}"]`)?.addEventListener("click", fn);
    act("areaAuto", () => patchLayout(id, { box: null }));
    act("smaller", () => patchLayout(id, { font_size: Math.max(6, p.font_size - 2) }));
    act("bigger", () => patchLayout(id, { font_size: p.font_size + 2 }));
    act("sizeAuto", () => patchLayout(id, { font_size: null }));
    act("rotLeft", () => patchLayout(id, { angle: (p.angle || 0) - 5 }));
    act("rotRight", () => patchLayout(id, { angle: (p.angle || 0) + 5 }));
    act("rotZero", () => patchLayout(id, { angle: 0 }));
    act("reset", () => patchLayout(id, { reset: true }));
    act("fit", () => patchLayout(id, { box: null, font_size: null, align: "center", valign: "middle" }));
    act("bold", () => patchLayout(id, { bold: !p.bold }));
    act("italic", () => patchLayout(id, { italic: !p.italic }));
    act("styleToPage", () => styleToPage(b));
    panel.querySelector("#letterCarry").addEventListener("change", (e) => {
        try { localStorage.setItem(CARRY_KEY, e.target.checked ? "1" : "0"); } catch { /* this page only */ }
        if (e.target.checked) {      // what is on this bubble now is what carries over
            try { localStorage.setItem(styleKey(), JSON.stringify(styleOfPlacement(p))); } catch { /* ignore */ }
        }
    });
    panel.querySelector("#letterFont").addEventListener("change", (e) => {
        rememberFont(e.target.value);
        patchLayout(id, { font: e.target.value || null });
    });
    panel.querySelector("#letterSize").addEventListener("change", (e) => patchLayout(id, { font_size: parseInt(e.target.value, 10) || null }));
    panel.querySelector("#letterAngle").addEventListener("change", (e) => patchLayout(id, { angle: parseFloat(e.target.value) || 0 }));
    wireNav(panel);
}

function navButtons() {
    return `
        <div class="detail-nav-actions" style="margin-left:auto;">
            <button id="letterPrevBtn" title="Previous bubble">&#8592;</button>
            <button id="letterNextBtn" title="Next bubble">&#8594;</button>
        </div>`;
}

function wireNav(panel) {
    panel.querySelector("#letterPrevBtn")?.addEventListener("click", () => hooks.navigate(-1));
    panel.querySelector("#letterNextBtn")?.addEventListener("click", () => hooks.navigate(1));
}
