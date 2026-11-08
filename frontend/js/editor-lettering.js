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
 * - Font, bold / italic, letters and outline can be made the PROJECT's style ("Use in whole project", stored in the
 *   project folder - services/project_prefs.py): every bubble without a style of its own uses it, also on pages
 *   processed later and in exports. (Before 2026-11-05 the style "carried over" in the browser only to bubbles the
 *   user opened, so the rest of the project kept the default font.)
 * - Letters are white by default on a dark text area (auto_color from GET /layout).
 * - Moving to another page in Lettering mode: a page whose bubbles are not all reviewed (edited / accepted marker)
 *   switches to Translate - reviewing comes first; in Translate mode nothing switches by itself.
 * editor.js wires its own functions in through initLettering().
 */

import { api, API_BASE } from "./api.js";
import { pageId, state, el, escapeHtml, showToast } from "./editor-shared.js";

import { askOk } from "./dialogs.js";
const MODE_KEY = "okt-editor-mode";
const EXPORT_KEY = "okt-export-options";

/** Callbacks from editor.js: select(i), rerender(), refreshImage(), saveIfChanged(), acceptOpen(), status(bubble),
 *  navigate(dir), shortcutLabel(actionId), renderReview(slot) (page review controls, js/editor-review.js). */
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
    const current = state.selectedIndex === null ? null : state.bubbles[state.selectedIndex];
    return {
        message: `${n} bubble${n > 1 ? "s" : ""} not reviewed yet${untranslated ? ` (${untranslated} without a translation)` : ""} - accept or edit ${n > 1 ? "them" : "it"} first.`,
        first: state.bubbles.indexOf(open[0]),
        // only the bubble that is open in the panel is left (the user read it and switches - like moving on with ->)
        onlyOpen: n === 1 && open[0] === current && !!(current.translation || "").trim(),
    };
}

export async function setMode(mode, { quiet = false } = {}) {
    if (mode === state.mode) return;
    if (mode === "lettering") {
        // typed text first: it used to be checked BEFORE saving, so an edited last bubble blocked the switch
        if (!(await hooks.saveIfChanged())) return;
        let blocked = letteringBlocker();
        if (blocked?.onlyOpen) {
            // the last bubble is open and unchanged: switching modes = moving on from it, as the arrow does
            const n = state.selectedIndex + 1;
            if (await hooks.acceptOpen()) {
                blocked = letteringBlocker();
                if (!blocked) {
                    showToast(`Bubble ${n} accepted - Lettering`, 2200);
                    quiet = true;
                }
            }
        }
        if (blocked) {
            // stay in Translate (the view does not change); offer a jump to the first bubble that needs the review
            showToast(`Lettering: ${blocked.message}`, 5000, blocked.first >= 0 && blocked.first !== undefined
                ? { label: "Show it", run: () => hooks.select(blocked.first) } : null);
            renderModeBar();
            return;
        }
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
    if (state.projectId && state.projectStyleFor !== state.projectId) {
        try {
            state.projectStyle = (await api.projects.prefs(state.projectId)).lettering_style || {};
            state.projectStyleFor = state.projectId;
        } catch { state.projectStyle = state.projectStyle || {}; }
    }
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
        <button type="button" class="link-btn" id="viewExportBtn" title="Open the last exported image of this page in a new tab">View export</button>
        <span class="review-slot" id="reviewSlot"></span>
        <span class="mode-bar-spacer"></span>
        ${toggleKey ? `<span class="text-muted mode-hint"><kbd>${escapeHtml(toggleKey)}</kbd> switches</span>` : ""}
        <div class="segmented mode-switch" role="radiogroup" aria-label="Editor mode" title="Switch the editor mode${toggleKey ? ` (${toggleKey})` : ""}">
            <button type="button" data-mode="translate" class="${isLettering() ? "" : "selected"}" role="radio" aria-checked="${!isLettering()}">Translate</button>
            <button type="button" data-mode="lettering" class="${isLettering() ? "selected" : ""}" role="radio" aria-checked="${isLettering()}">Lettering</button>
        </div>`;
    bar.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    bar.querySelector("#exportPageBtn").addEventListener("click", exportPage);
    bar.querySelector("#viewExportBtn").addEventListener("click", viewExport);
    hooks.renderReview?.(bar.querySelector("#reviewSlot"));
}

/** "View export": the page's last export in a new tab - or a message when there is none yet (it opened an empty
 *  tab with a JSON error before). The check is quick, so the new tab still counts as opened by the click. */
async function viewExport() {
    const url = `${API_BASE}/pages/${pageId}/exported`;
    try {
        const r = await fetch(url, { cache: "no-store" });
        if (r.status === 404) {
            showToast("This page has not been exported yet - Export page writes it (into the project's exported folder)", 5000,
                { label: "Export now", run: exportPage });
            return;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const w = window.open(URL.createObjectURL(await r.blob()), "_blank");
        if (!w) showToast("The browser blocked the new tab - allow pop-ups for this page", 5000);
    } catch (e) {
        showToast(`View export: ${e.message}`, 5000);
    }
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

/* ------------------------------------------------------------------ style of the whole project */

// font, B / I, outline and colour can be the PROJECT's style (a series uses one font); the area, size, alignment and
// angle belong to each bubble. A bubble's own choice wins over the project style (the server keeps only differences).
const STYLE_DEFAULTS = { font: null, bold: false, italic: false, stroke: "none", color: "black" };

/** The style of a bubble's placement (GET layout) - every key, defaults included. */
function styleOfPlacement(p) {
    return { font: p.font || null, bold: !!p.bold, italic: !!p.italic, stroke: p.stroke || "none", color: p.color || "black" };
}

function describeStyle(st) {
    const parts = [st.font || "Settings font", st.bold && "bold", st.italic && "italic",
                   st.color === "white" && "white letters", st.stroke && st.stroke !== "none" && `${st.stroke} outline`];
    return parts.filter(Boolean).join(", ");
}

const isDefaultStyle = (st) => Object.keys(STYLE_DEFAULTS).every((k) => (st[k] ?? STYLE_DEFAULTS[k]) === STYLE_DEFAULTS[k]);

/** "Use in whole project": this bubble's font / B / I / letters / outline for every bubble without its own style. */
async function styleToProject(b) {
    const p = state.layout[b.bubble_id];
    const st = styleOfPlacement(p);
    if (p.auto_color) st.color = "black";       // white only because THIS area is dark - not a project choice
    const what = describeStyle(st);
    if (!(await askOk(`Project style: ${what}\n\nUsed by every bubble of the project that has no style of its own - also on pages processed later and in exports. Bubbles you styled yourself keep their style (Reset bubble = project style).`, { ok: "Use in whole project" }))) return;
    try {
        const prefs = await api.projects.setPrefs(state.projectId, { lettering_style: isDefaultStyle(st) ? null : st });
        state.projectStyle = prefs.lettering_style || {};
        // this page's bubbles that only repeat the new project style: drop the copies, so they follow later changes
        for (const x of state.bubbles) {
            const own = x.layout || {};
            const same = Object.keys(STYLE_DEFAULTS).filter((k) => k in own && (own[k] || STYLE_DEFAULTS[k]) === (st[k] ?? STYLE_DEFAULTS[k]));
            if (same.length) {
                // bulk: one click, many requests - the page's manual_ops count it once (routers/pages.py set_bubble_layout)
                const r = await api.pages.setLayout(pageId, x.bubble_id, { ...Object.fromEntries(same.map((k) => [k, st[k] ?? STYLE_DEFAULTS[k]])), bulk: "project_style" });
                if (Object.keys(r.layout).length) x.layout = r.layout; else delete x.layout;
            }
        }
        await loadLayout();
        showToast(`Project style: ${what}`, 3000);
    } catch (e) {
        showToast(`Project style: ${e.message}`, 5000);
    }
    hooks.refreshImage();
    hooks.rerender();
}

async function clearProjectStyle() {
    if (!(await askOk("Remove the project style? Bubbles without a style of their own go back to the Settings font and plain black letters.", { ok: "Remove", danger: true }))) return;
    try {
        state.projectStyle = (await api.projects.setPrefs(state.projectId, { lettering_style: null })).lettering_style || {};
        await loadLayout();
        showToast("Project style removed", 2500);
    } catch (e) {
        showToast(`Project style: ${e.message}`, 5000);
    }
    hooks.refreshImage();
    hooks.rerender();
}

/** This bubble's style on every other translated bubble of the page. */
async function styleToPage(b) {
    const st = styleOfPlacement(state.layout[b.bubble_id]);
    const others = state.bubbles.filter((x) => x !== b && !x.skip && state.layout[x.bubble_id]);
    if (!others.length) return;
    try {
        for (const x of others) {
            const r = await api.pages.setLayout(pageId, x.bubble_id, { ...st, bulk: "page_style" });
            if (Object.keys(r.layout).length) x.layout = r.layout; else delete x.layout;
            if (r.placement) state.layout[x.bubble_id] = r.placement;
        }
        showToast(`Style used on ${others.length} more bubble${others.length > 1 ? "s" : ""}`, 2500);
    } catch (e) {
        showToast(`Lettering: ${e.message}`, 5000);
    }
    hooks.refreshImage();
    hooks.rerender();
}

export async function patchLayout(bubbleId, changes) {
    try {
        const r = await api.pages.setLayout(pageId, bubbleId, changes);
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

/** Groups of the font list: [label, [{family, polish, source}]] - recently used, your fonts, system fonts. */
function fontGroups() {
    const fonts = fontList || [];
    const known = new Set(fonts.map((f) => f.family));
    const recent = recentFonts().filter((r) => known.has(r)).map((r) => fonts.find((f) => f.family === r));
    return [
        ["Recently used", recent],
        ["Your fonts", fonts.filter((f) => f.source === "user")],
        ["System fonts with Polish letters", fonts.filter((f) => f.source === "system" && f.polish)],
        ["Other system fonts (no Polish letters)", fonts.filter((f) => f.source === "system" && !f.polish)],
    ].filter(([, list]) => list.length);
}

/**
 * Font picker with a search box (a plain select with ~200 system fonts was a long scroll): click the button, type a
 * part of the name, arrows + Enter or a click picks. System fonts are shown in their own face (the browser has them);
 * fonts you added are in the app's folder only, so they show in the normal face. onPick(family | "" = Settings font).
 */
function openFontPicker(anchor, current, onPick) {
    document.getElementById("fontPicker")?.remove();
    const pop = document.createElement("div");
    pop.id = "fontPicker";
    pop.className = "font-picker-pop";
    pop.innerHTML = `<input type="search" class="font-picker-search" placeholder="Search fonts..." spellcheck="false" aria-label="Search fonts">
        <div class="font-picker-list" role="listbox"></div>`;
    document.body.appendChild(pop);
    const r = anchor.getBoundingClientRect();
    const below = window.innerHeight - r.bottom - 12;
    const height = Math.min(360, Math.max(below, r.top - 12));
    Object.assign(pop.style, { left: `${Math.max(8, Math.min(r.left, window.innerWidth - 300))}px`, width: `${Math.max(r.width, 280)}px`,
                               maxHeight: `${height}px` });
    if (below >= Math.min(360, r.top - 12)) pop.style.top = `${r.bottom + 4}px`;
    else pop.style.bottom = `${window.innerHeight - r.top + 4}px`;
    const input = pop.querySelector("input");
    const list = pop.querySelector(".font-picker-list");
    let active = 0;

    const options = () => [...list.querySelectorAll(".font-opt")];
    const mark = (i) => {
        const all = options();
        if (!all.length) return;
        active = (i + all.length) % all.length;
        all.forEach((o, k) => o.classList.toggle("active", k === active));
        all[active].scrollIntoView({ block: "nearest" });
    };
    const render = () => {
        const q = input.value.trim().toLowerCase();
        const opt = (f) => `<div class="font-opt${f.family === current ? " current" : ""}" role="option" data-family="${escapeHtml(f.family)}"
            ${f.source === "system" ? `style="font-family:'${escapeHtml(f.family.replace(/'/g, ""))}', sans-serif"` : ""}>${escapeHtml(f.family)}${f.polish ? "" : ' <span class="text-muted">(no Polish!)</span>'}</div>`;
        let html = q && !"default (settings)".includes(q) ? "" : `<div class="font-opt${current ? "" : " current"}" role="option" data-family="">Default (Settings)</div>`;
        const seen = new Set();
        for (const [label, fonts] of fontGroups()) {
            const hits = fonts.filter((f) => (!q || f.family.toLowerCase().includes(q)) && !(q && seen.has(f.family)));
            hits.forEach((f) => seen.add(f.family));
            if (hits.length) html += `<div class="font-group">${escapeHtml(label)}</div>${hits.map(opt).join("")}`;
        }
        list.innerHTML = html || '<div class="font-empty text-muted">No font with that name - add fonts in Settings &gt; Text &amp; fonts</div>';
        mark(q ? 0 : Math.max(0, options().findIndex((o) => o.classList.contains("current"))));
    };
    const close = () => {
        pop.remove();
        document.removeEventListener("mousedown", outside, true);
    };
    const pick = (o) => { close(); if (o) onPick(o.dataset.family); };
    const outside = (e) => { if (!pop.contains(e.target) && e.target !== anchor) close(); };
    list.addEventListener("mousedown", (e) => { e.preventDefault(); pick(e.target.closest(".font-opt")); });
    input.addEventListener("input", render);
    input.addEventListener("keydown", (e) => {
        if (e.key === "ArrowDown") { e.preventDefault(); mark(active + 1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); mark(active - 1); }
        else if (e.key === "Enter") { e.preventDefault(); pick(options()[active]); }
        else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); anchor.focus(); }
    });
    document.addEventListener("mousedown", outside, true);
    render();
    input.focus();
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
    const seg = (name, options, current) => `<div class="segmented" data-field="${name}">${options.map(([v, label, title]) =>
        `<button type="button" data-value="${v}" class="${String(v) === String(current) ? "selected" : ""}" title="${title || label}">${label}</button>`).join("")}</div>`;
    panel.innerHTML = `
        <div class="lettering-panel">
            <div class="field-label">Bubble ${index + 1}</div>
            <div class="lettering-text">${escapeHtml(b.translation || "")}</div>
            <p class="text-muted lettering-note">Edit the words in Translate - here only where and how they are drawn. On the page: drag the box to move it, corners resize (Alt: evenly from the centre), the knob above turns it.</p>
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
                    <button type="button" id="letterFont" class="font-picker-btn" title="Font of this bubble - click and type to search (fonts: Settings > Text &amp; fonts)"
                        ${p.font ? `style="font-family:'${escapeHtml(p.font.replace(/'/g, ""))}', inherit"` : ""}>${escapeHtml(p.font || "Default (Settings)")}<span class="chev">&#9662;</span></button>
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
            <div class="lettering-row"><span>Letters</span>${seg("color", [["black", "Black", "Black letters (outline: white)"], ["white", "White", "White letters (outline: black) - text on dark artwork"]], p.color || "black")}
                ${p.auto_color ? '<span class="text-muted" title="The text area is dark, so the letters are white unless you choose">auto</span>' : ""}</div>
            <div class="lettering-row"><span>Outline</span>${seg("stroke", [["none", "None"], ["thin", "Thin", "Thin outline in the other colour"], ["thick", "Thick", "Thick outline in the other colour"]], p.stroke)}</div>
            ${p.stroke !== "none" && (p.color || "black") === "black" ? '<p class="text-muted lettering-note">The outline is white: in a white bubble it is invisible - it shows where the text lies over artwork.</p>' : ""}
            <div class="lettering-row lettering-style-row">
                <button type="button" data-act="styleToProject" title="Font, bold / italic, letters and outline of this bubble for EVERY bubble of the project without a style of its own - also pages processed later and exports">Use in whole project</button>
                <button type="button" data-act="styleToPage" title="Font, bold / italic, letters and outline of this bubble on every bubble of this page (as their own style)">Use on whole page</button>
            </div>
            ${Object.keys(state.projectStyle || {}).length ? `<p class="text-muted lettering-note">Project style: ${escapeHtml(describeStyle(state.projectStyle))}
                <button type="button" class="link-btn" data-act="clearProjectStyle" title="Back to the Settings font and plain black letters for bubbles without their own style">remove</button></p>` : ""}
            <div class="detail-actions detail-bottom">
                <button type="button" data-act="fit" title="Text area = the inside of the bubble, centred both ways, largest size that fits (font, outline and angle stay)">Fit to bubble</button>
                <button type="button" data-act="reset" title="Everything of this bubble back to automatic">Reset bubble</button>
                ${nav}
            </div>
        </div>`;
    const id = b.bubble_id;
    panel.querySelectorAll(".segmented[data-field] button").forEach((btn) => btn.addEventListener("click", () => {
        const field = btn.parentElement.dataset.field;
        // black on an area that made the letters white automatically: stored on purpose (it is the default otherwise)
        patchLayout(id, { [field]: btn.dataset.value, ...(field === "color" && p.auto_color ? { color_explicit: true } : {}) });
    }));
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
    act("styleToProject", () => styleToProject(b));
    act("clearProjectStyle", clearProjectStyle);
    const fontBtn = panel.querySelector("#letterFont");
    fontBtn.addEventListener("click", () => openFontPicker(fontBtn, p.font || "", (family) => {
        rememberFont(family);
        patchLayout(id, { font: family || null });
    }));
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
