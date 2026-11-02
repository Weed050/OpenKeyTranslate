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

import { api } from "./api.js";
import { pageId, state, el, escapeHtml, showToast } from "./editor-shared.js";

const MODE_KEY = "okt-editor-mode";
const EXPORT_KEY = "okt-export-options";

/** Callbacks from editor.js: select(i), rerender(), refreshImage(), saveIfChanged(), status(bubble), navigate(dir),
 *  shortcutLabel(actionId). */
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

export async function setMode(mode, { quiet = false } = {}) {
    if (mode === state.mode) return;
    if (mode === "lettering" && !(await hooks.saveIfChanged())) return;
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
    const open = state.bubbles.filter((b) => !b.skip && !hooks.status(b)).length;
    if (open) {
        state.mode = "translate";
        try { localStorage.setItem(MODE_KEY, "translate"); } catch { /* ignore */ }
        await enterCurrentMode();
        showToast(`${open} bubble${open > 1 ? "s" : ""} on this page not reviewed yet - switched to Translate`, 4000);
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
        ${isLettering() ? '<span class="text-muted mode-hint">Drag a box to move it, corners resize, the knob turns it</span>' : ""}
        <span class="mode-bar-spacer"></span>
        ${toggleKey ? `<span class="text-muted mode-hint"><kbd>${escapeHtml(toggleKey)}</kbd> switches</span>` : ""}
        <div class="segmented mode-switch" role="radiogroup" aria-label="Editor mode" title="Switch the editor mode${toggleKey ? ` (${toggleKey})` : ""}">
            <button type="button" data-mode="translate" class="${isLettering() ? "" : "selected"}" role="radio" aria-checked="${!isLettering()}">Translate</button>
            <button type="button" data-mode="lettering" class="${isLettering() ? "selected" : ""}" role="radio" aria-checked="${isLettering()}">Lettering</button>
        </div>`;
    bar.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
    bar.querySelector("#exportPageBtn").addEventListener("click", exportPage);
}

export async function exportPage() {
    if (!(await hooks.saveIfChanged())) return;
    const opts = exportOptions();
    try {
        const r = await api.pages.exportPage(pageId, opts);
        showToast(`Exported to ${r.folder}`, 5000);
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

/* ------------------------------------------------------------------ side panel */

export function renderLetteringPanel() {
    const panel = el.bubbleDetail;
    panel.classList.remove("hidden");
    const b = selected();
    if (!b) {
        panel.innerHTML = '<p class="text-muted">Pick a bubble in the list or a text box on the page.</p>';
        return;
    }
    const index = state.selectedIndex;
    const p = state.layout[b.bubble_id];
    const nav = `
        <div class="detail-nav-actions" style="margin-left:auto;">
            <button id="letterPrevBtn" title="Previous bubble">&#8592;</button>
            <button id="letterNextBtn" title="Next bubble">&#8594;</button>
        </div>`;
    if (!p) {
        const why = b.skip ? "This bubble is skipped - nothing is drawn." : "No translation yet - nothing is drawn. Translate it first.";
        panel.innerHTML = `<div class="lettering-panel"><div class="field-label">Bubble ${index + 1}</div><p class="text-muted">${why}</p>
            <div class="detail-actions">${nav}</div></div>`;
        wireNav(panel);
        return;
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
            <div class="lettering-row"><span>Align</span>${seg("align", [["left", "Left"], ["center", "Center"], ["right", "Right"]], p.align)}</div>
            <div class="lettering-row"><span>Rotation</span>
                <span class="lettering-size">
                    <button type="button" data-act="rotLeft" title="-5°">&#8634;</button>
                    <input type="number" id="letterAngle" min="-180" max="180" step="1" value="${Math.round(p.angle || 0)}" title="Degrees, clockwise">
                    <button type="button" data-act="rotRight" title="+5°">&#8635;</button>
                    ${p.angle ? '<button type="button" data-act="rotZero">0°</button>' : ""}
                    ${p.angle && !p.manual_angle ? '<span class="text-muted" title="The original text is tilted - the translation follows it">auto</span>' : ""}
                </span></div>
            <div class="lettering-row"><span>Outline</span>${seg("stroke", [["none", "None"], ["thin", "Thin", "Thin white outline - text over artwork"], ["thick", "Thick", "Thick white outline"]], p.stroke)}</div>
            <div class="detail-actions">
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
    panel.querySelector("#letterSize").addEventListener("change", (e) => patchLayout(id, { font_size: parseInt(e.target.value, 10) || null }));
    panel.querySelector("#letterAngle").addEventListener("change", (e) => patchLayout(id, { angle: parseFloat(e.target.value) || 0 }));
    wireNav(panel);
}

function wireNav(panel) {
    panel.querySelector("#letterPrevBtn")?.addEventListener("click", () => hooks.navigate(-1));
    panel.querySelector("#letterNextBtn")?.addEventListener("click", () => hooks.navigate(1));
}
