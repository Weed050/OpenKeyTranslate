/**
 * @file frontend/js/editor-ocrdebug.js
 * @description The editor's OCR debug view: the page with everything the OCR saw drawn over it - every kept reading
 * (colour = confidence), second-chance readings (utils/ocr_rescue.py), readings under the text threshold, readings /
 * lines / bubbles a later step removed (with the reason) and the slice cuts of a tall page.
 *
 * Why: when a bubble is missing, cut in two or garbled, the bubble list only shows the end result; the cause (read
 * with 0.62 and rejected, dropped as a duplicate, cut by a slice edge, matched an ignore pattern) was visible only in
 * the backend console. Switched on in Settings > Editor (browser-local "okt-ocr-debug"), then the "OCR debug" button
 * over the page toggles it.
 *
 * One SVG over the page in IMAGE pixels (viewBox = natural size): zooming scales it for free, only the score labels
 * are redrawn (their size follows the zoom). Boxes react to the mouse only on their outline, so a click inside a box
 * still reaches the bubble below. Data: GET /pages/{id}/ocr-debug (routers/pages.py), fetched once per page.
 */

import { api } from "./api.js";
import { state, el, pageId, escapeHtml } from "./editor-shared.js";

const SETTING_KEY = "okt-ocr-debug";
const LAYERS_KEY = "okt-ocr-debug-layers";
const SVG_NS = "http://www.w3.org/2000/svg";

const LAYERS = [
    { id: "read", label: "Read", tip: "Every reading the OCR kept. Green = confidence 0.95 or more, amber = lower (still over the threshold)." },
    { id: "rescued", label: "Second chance", tip: "Lines read again after a first, rejected reading: stacked letters, too tight boxes, slanted lines." },
    { id: "rejected", label: "Under threshold", tip: "Text was detected here, but its reading scored under the threshold - not used." },
    { id: "dropped", label: "Removed later", tip: "Readings, lines or bubbles a later step removed: duplicates, noise, ignore patterns, lines cut by a slice edge." },
    { id: "slices", label: "Slices", tip: "Where a tall page was cut for the OCR. Pages up to 2000 px are read in one pass." },
    { id: "scores", label: "Scores", tip: "Confidence numbers on the boxes." },
];

let active = false;
let data = null;
let layers = loadLayers();

function loadLayers() {
    try {
        const saved = JSON.parse(localStorage.getItem(LAYERS_KEY) || "null");
        if (saved && typeof saved === "object") return { ...defaultLayers(), ...saved };
    } catch { /* default */ }
    return defaultLayers();
}

function defaultLayers() {
    return Object.fromEntries(LAYERS.map((l) => [l.id, true]));
}

export function ocrDebugAvailable() {
    return localStorage.getItem(SETTING_KEY) === "1";
}

/** Show the toolbar button when the view is switched on in Settings. */
export function initOcrDebug() {
    const btn = document.getElementById("ocrDebugBtn");
    if (!btn) return;
    btn.classList.toggle("hidden", !ocrDebugAvailable());
    btn.addEventListener("click", toggleOcrDebug);
}

async function toggleOcrDebug() {
    active = !active;
    document.getElementById("ocrDebugBtn")?.classList.toggle("primary", active);
    if (active && !data) await fetchData();
    renderPanel();
    renderOcrDebug();
}

async function fetchData() {
    try {
        data = await api.pages.ocrDebug(pageId);
    } catch (e) {
        data = { error: e.message };
    }
}

/** After a Re-OCR / bubble edit: the readings changed - fetch again when the view is on. */
export async function invalidateOcrDebug() {
    data = null;
    if (!active) return;
    await fetchData();
    renderPanel();
    renderOcrDebug();
}

/* ------------------------------------------------------------------ drawing */

function entries() {
    if (!data || data.error) return { read: [], rescued: [], rejected: [], dropped: [], slices: [] };
    const items = data.items || [];
    const dbg = data.debug || {};
    return {
        read: items.filter((i) => !i.rescued),
        rescued: items.filter((i) => i.rescued),
        rejected: dbg.rejected || [],
        dropped: dbg.dropped || [],
        slices: dbg.slices || [],
    };
}

function bbox(box) {
    const xs = box.map((p) => p[0]);
    const ys = box.map((p) => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

function svgEl(tag, attrs = {}) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
}

const RESCUE_LABEL = { stacked: "stacked letters", line: "re-read line", tilted: "slanted line read whole" };

function scoreClass(score) {
    return score >= 0.95 ? "high" : "mid";
}

/** One box: visible outline + a wider invisible outline that takes the mouse (tooltip), + the score label. */
function boxGroup(kind, entry, title, scale, extraClass = "") {
    const g = svgEl("g", { class: `ocrd-box ocrd-${kind} ${extraClass}`.trim() });
    const points = entry.box.map((p) => `${p[0]},${p[1]}`).join(" ");
    g.appendChild(svgEl("polygon", { points, class: "ocrd-line" }));
    g.appendChild(svgEl("polygon", { points, class: "ocrd-hit" }));
    const t = svgEl("title");
    t.textContent = title;
    g.appendChild(t);
    if (layers.scores && entry.score !== undefined && kind !== "dropped") {
        const [x1, y1] = bbox(entry.box);
        const size = 10.5 / scale;
        const label = svgEl("text", { x: x1, y: Math.max(size, y1 - 2 / scale), class: "ocrd-score", "font-size": size,
                                      "stroke-width": 3 / scale });
        label.textContent = Number(entry.score).toFixed(2);
        g.appendChild(label);
    }
    return g;
}

/** Redraw the overlay (called by editor.js applyZoom as well - labels follow the zoom). */
export function renderOcrDebug() {
    el.imageCanvas?.querySelector(".ocr-debug-svg")?.remove();
    if (!active || !data || data.error || !state.naturalW) return;
    const scale = state.baseScale * state.zoomFactor;
    const W = state.naturalW;
    const H = state.naturalH;
    const svg = svgEl("svg", { class: "ocr-debug-svg", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none" });
    const e = entries();

    if (layers.slices && e.slices.length > 1) {
        e.slices.forEach(([a, b], i) => {
            const next = e.slices[i + 1];
            if (next && next[0] < b) {
                svg.appendChild(svgEl("rect", { x: 0, y: next[0], width: W, height: b - next[0], class: "ocrd-overlap" }));
            }
            svg.appendChild(svgEl("line", { x1: 0, x2: W, y1: a, y2: a, class: "ocrd-cut" }));
            svg.appendChild(svgEl("line", { x1: 0, x2: W, y1: b, y2: b, class: "ocrd-cut end" }));
            const size = 11 / scale;
            const label = svgEl("text", { x: 4 / scale, y: a + size + 2 / scale, class: "ocrd-slice-label", "font-size": size,
                                          "stroke-width": 3 / scale });
            label.textContent = `slice ${i + 1}: ${a}–${b} px`;
            svg.appendChild(label);
        });
    }
    if (layers.dropped) {
        e.dropped.forEach((d, i) => svg.appendChild(boxGroup("dropped", d,
            `Removed: ${d.why}\n"${d.text}"${d.score ? ` (${Number(d.score).toFixed(2)})` : ""}`, scale, `ocrd-i-dropped-${i}`)));
    }
    if (layers.rejected) {
        e.rejected.forEach((r, i) => svg.appendChild(boxGroup("rejected", r,
            `Under the threshold ${data.threshold}: "${r.text}" scored ${Number(r.score).toFixed(2)} - not used`, scale, `ocrd-i-rejected-${i}`)));
    }
    if (layers.read) {
        e.read.forEach((r) => svg.appendChild(boxGroup("read", r,
            `"${r.text}" - confidence ${Number(r.score).toFixed(2)}${r.slice_id !== null && r.slice_id !== undefined && e.slices.length > 1 ? `, slice ${r.slice_id + 1}` : ""}`,
            scale, `score-${scoreClass(r.score)}`)));
    }
    if (layers.rescued) {
        e.rescued.forEach((r) => svg.appendChild(boxGroup("rescued", r,
            `Second chance (${RESCUE_LABEL[r.rescued] || r.rescued}): "${r.text}" - ${Number(r.score).toFixed(2)}`, scale)));
    }
    el.imageCanvas.appendChild(svg);
}

/* ------------------------------------------------------------------ the panel */

function renderPanel() {
    document.getElementById("ocrDebugPanel")?.remove();
    if (!active) return;
    const pane = document.querySelector(".image-pane");
    if (!pane) return;
    const panel = document.createElement("div");
    panel.id = "ocrDebugPanel";
    panel.className = "ocr-debug-panel";
    if (!data) {
        panel.innerHTML = `<div class="ocrd-head"><b>OCR debug</b></div><p class="text-muted">Loading…</p>`;
        pane.appendChild(panel);
        return;
    }
    if (data.error) {
        panel.innerHTML = `<div class="ocrd-head"><b>OCR debug</b><button type="button" class="ocrd-close" title="Close">×</button></div>
            <p class="text-muted">${escapeHtml(data.error)}</p>`;
        panel.querySelector(".ocrd-close").addEventListener("click", toggleOcrDebug);
        pane.appendChild(panel);
        return;
    }
    const e = entries();
    const counts = { read: e.read.length, rescued: e.rescued.length, rejected: e.rejected.length, dropped: e.dropped.length,
                     slices: e.slices.length };
    const legend = LAYERS.map((l) => {
        const n = l.id === "scores" ? "" : l.id === "slices"
            ? (e.slices.length > 1 ? `${e.slices.length}` : e.slices.length ? "one pass" : "") : String(counts[l.id]);
        return `<label class="ocrd-layer" title="${escapeHtml(l.tip)}">
            <input type="checkbox" data-layer="${l.id}" ${layers[l.id] ? "checked" : ""}>
            <i class="ocrd-swatch ocrd-sw-${l.id}"></i>${l.label}${n ? ` <span class="text-muted">${n}</span>` : ""}</label>`;
    }).join("");
    const dbg = data.debug;
    const when = dbg?.at ? new Date(dbg.at).toLocaleString() : "";
    const lost = [...e.rejected.map((r, i) => ({ kind: "rejected", i, text: r.text, why: `under threshold (${Number(r.score).toFixed(2)})` })),
                  ...e.dropped.map((d, i) => ({ kind: "dropped", i, text: d.text, why: d.why }))];
    panel.innerHTML = `
        <div class="ocrd-head"><b>OCR debug</b><button type="button" class="ocrd-close" title="Close the debug view">×</button></div>
        <div class="ocrd-layers">${legend}</div>
        <div class="ocrd-meta text-muted">Threshold ${data.threshold}${when ? ` · OCR ${escapeHtml(when)}` : ""}. Hover a box outline for its text and score.</div>
        ${dbg ? "" : `<div class="ocrd-old">This page was read before the debug data existed: only the kept readings are shown.
            <b>Re-OCR</b> the page to see rejected and removed readings and the slices too.</div>`}
        ${lost.length ? `<details class="ocrd-list"><summary>Not used (${lost.length})</summary>
            ${lost.map((x) => `<button type="button" class="ocrd-item ocrd-item-${x.kind}" data-kind="${x.kind}" data-i="${x.i}">
                <span class="ocrd-text">${escapeHtml(x.text || "—")}</span><span class="ocrd-why">${escapeHtml(x.why)}</span></button>`).join("")}
        </details>` : ""}`;
    panel.querySelector(".ocrd-close").addEventListener("click", toggleOcrDebug);
    panel.querySelectorAll("input[data-layer]").forEach((box) => box.addEventListener("change", () => {
        layers[box.dataset.layer] = box.checked;
        localStorage.setItem(LAYERS_KEY, JSON.stringify(layers));
        renderOcrDebug();
    }));
    panel.querySelectorAll(".ocrd-item").forEach((btn) => btn.addEventListener("click", () => {
        const list = btn.dataset.kind === "rejected" ? e.rejected : e.dropped;
        showEntry(btn.dataset.kind, Number(btn.dataset.i), list[Number(btn.dataset.i)]);
    }));
    pane.appendChild(panel);
}

/** Scroll the page to one entry of the "Not used" list and let its box blink. */
function showEntry(kind, i, entry) {
    if (!entry) return;
    if (!layers[kind]) {
        layers[kind] = true;
        localStorage.setItem(LAYERS_KEY, JSON.stringify(layers));
        renderPanel();
        renderOcrDebug();
    }
    const scale = state.baseScale * state.zoomFactor;
    const [x1, y1, x2, y2] = bbox(entry.box);
    el.imageViewport.scrollTo({
        left: ((x1 + x2) / 2) * scale - el.imageViewport.clientWidth / 2,
        top: ((y1 + y2) / 2) * scale - el.imageViewport.clientHeight / 2,
        behavior: "smooth",
    });
    const g = el.imageCanvas.querySelector(`.ocrd-i-${kind}-${i}`);
    if (g) {
        g.classList.remove("flash");
        void g.getBoundingClientRect();
        g.classList.add("flash");
    }
}
