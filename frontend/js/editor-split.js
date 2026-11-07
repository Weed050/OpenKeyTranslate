/**
 * @file frontend/js/editor-split.js
 * @description Splitting a bubble the OCR glued together (two speech bubbles, labels on a map) in the editor.
 * "Split..." shows the bubble's text lines on the page as numbered boxes; clicking a line - or dragging a box around
 * several lines (small dense text at a low zoom) - moves it to the NEW bubble.
 * The dialog shows both parts and how the EXISTING translation is divided between them:
 *   - Automatic: by sentences / length / at a comma (services/bubble_edit.split_translation), no translation request;
 *     a part that could not be divided cleanly gets the "check translation" tag until it is saved or retranslated;
 *   - I choose the cut: the translation as words - a click puts the cut before that word (no request, no tag);
 *   - Translate both parts again: one request per part (while it runs the parts show "Translating...").
 * editor.js wires its own functions in through initSplit().
 */

import { api } from "./api.js";
import { pageId, state, el, escapeHtml, showToast } from "./editor-shared.js";

/** Callbacks from editor.js: save(), reload(selectId), retranslateIds(ids), rerender(). */
let hooks = {};
let split = null;          // {bubbleId, lines: [{line_id, box, text}], picked: Set}

export function initSplit(editorHooks) {
    hooks = editorHooks;
}

export function isSplitting() {
    return split !== null;
}

export function cancelSplit() {
    if (!split) return;
    split = null;
    document.getElementById("splitBar")?.remove();
    el.imageCanvas.querySelectorAll(".split-line").forEach((n) => n.remove());
}

export async function startSplit() {
    const bubble = state.bubbles[state.selectedIndex];
    if (!bubble) return;
    if (!(await hooks.save())) return;
    let lines;
    try {
        ({ lines } = await api.pages.bubbleLines(pageId, bubble.bubble_id));
    } catch (e) {
        alert(`Split: ${e.message}`);
        return;
    }
    if (lines.length < 2) {
        showToast("This bubble has only one text line - nothing to split", 3500);
        return;
    }
    split = { bubbleId: bubble.bubble_id, lines, picked: new Set() };
    renderSplitBar();
    hooks.rerender();
    showToast("Click the lines of the NEW bubble - or drag a box around them (Esc cancels)", 4000);
}

/** Line boxes on the page; called by editor.js renderBubbleOutlines after every zoom / redraw. */
export function renderSplitOverlay(scale) {
    el.imageCanvas.querySelectorAll(".split-line").forEach((n) => n.remove());
    if (!split) return;
    split.lines.forEach((line, i) => {
        const xs = line.box.map((p) => p[0]);
        const ys = line.box.map((p) => p[1]);
        const box = document.createElement("div");
        box.className = "split-line" + (split.picked.has(line.line_id) ? " picked" : "");
        Object.assign(box.style, {
            left: `${Math.min(...xs) * scale - 2}px`, top: `${Math.min(...ys) * scale - 2}px`,
            width: `${(Math.max(...xs) - Math.min(...xs)) * scale + 4}px`, height: `${(Math.max(...ys) - Math.min(...ys)) * scale + 4}px`,
        });
        box.title = `${line.text || "(text unknown - read again when split)"}\nClick: ${split.picked.has(line.line_id) ? "back to the current bubble" : "move to the NEW bubble"}`;
        box.innerHTML = `<span class="split-line-num">${i + 1}</span>`;
        box.addEventListener("click", (e) => {
            e.stopPropagation();
            if (split.picked.has(line.line_id)) split.picked.delete(line.line_id);
            else split.picked.add(line.line_id);
            renderSplitOverlay(scale);
            renderSplitBar();
        });
        el.imageCanvas.appendChild(box);
    });
}

/**
 * Split mode: a drag on the page picks every line whose middle is inside the box (all of them already picked: the
 * drag puts them back). A plain click is left to the line boxes. Called by editor.js for a mousedown on the page.
 */
export function splitDragStart(event) {
    if (!split || event.button !== 0) return false;
    event.preventDefault();
    const canvasRect = el.imageCanvas.getBoundingClientRect();
    const sx = event.clientX - canvasRect.left;
    const sy = event.clientY - canvasRect.top;
    let rectEl = null;
    const geometry = (e) => {
        const x = Math.max(0, Math.min(e.clientX - canvasRect.left, canvasRect.width));
        const y = Math.max(0, Math.min(e.clientY - canvasRect.top, canvasRect.height));
        return { left: Math.min(sx, x), top: Math.min(sy, y), width: Math.abs(x - sx), height: Math.abs(y - sy) };
    };
    const onMove = (e) => {
        const g = geometry(e);
        if (!rectEl && g.width + g.height < 6) return;
        if (!rectEl) {
            rectEl = document.createElement("div");
            rectEl.className = "draw-rect split-drag";
            el.imageCanvas.appendChild(rectEl);
        }
        Object.assign(rectEl.style, { left: `${g.left}px`, top: `${g.top}px`, width: `${g.width}px`, height: `${g.height}px` });
    };
    const onUp = (e) => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        if (!rectEl || !split) return;               // a click: the line box under it handles it
        rectEl.remove();
        // the click that ends a drag must not select a bubble outline (that would cancel the split)
        window.addEventListener("click", (c) => { c.stopPropagation(); c.preventDefault(); }, { capture: true, once: true });
        const g = geometry(e);
        const scale = state.baseScale * state.zoomFactor;
        const inside = split.lines.filter((l) => {
            const cx = (Math.min(...l.box.map((p) => p[0])) + Math.max(...l.box.map((p) => p[0]))) / 2 * scale;
            const cy = (Math.min(...l.box.map((p) => p[1])) + Math.max(...l.box.map((p) => p[1]))) / 2 * scale;
            return cx >= g.left && cx <= g.left + g.width && cy >= g.top && cy <= g.top + g.height;
        });
        if (!inside.length) return;
        const allPicked = inside.every((l) => split.picked.has(l.line_id));
        inside.forEach((l) => (allPicked ? split.picked.delete(l.line_id) : split.picked.add(l.line_id)));
        renderSplitOverlay(scale);
        renderSplitBar();
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return true;
}

function renderSplitBar() {
    document.getElementById("splitBar")?.remove();
    if (!split) return;
    const bar = document.createElement("div");
    bar.id = "splitBar";
    bar.className = "merge-bar split-bar";
    const n = split.picked.size;
    bar.innerHTML = `
        <span>Split: click the lines of the <b>new</b> bubble or drag a box around them <span class="text-muted">(${n} of ${split.lines.length} picked, Esc cancels)</span></span>
        <span class="merge-bar-actions">
            <button class="primary" data-act="preview" ${n && n < split.lines.length ? "" : "disabled"}>Split...</button>
            <button data-act="cancel">Cancel</button>
        </span>`;
    bar.querySelector('[data-act="preview"]').addEventListener("click", openSplitDialog);
    bar.querySelector('[data-act="cancel"]').addEventListener("click", () => { cancelSplit(); hooks.rerender(); });
    document.body.appendChild(bar);
}

const METHOD_NOTE = {
    sentences: "divided by sentences",
    proportional: "the sentences did not match one to one - divided by length, check both parts",
    clauses: "one sentence for both parts - cut at a comma or conjunction (BUT -> ALE), check both parts",
    unsplit: "could not be divided - it stays with the first part, the second is empty",
};

const words = (text) => (text || "").trim().split(/\s+/).filter(Boolean);

async function openSplitDialog() {
    if (!split) return;
    const newLines = [...split.picked];
    const bubbleId = split.bubbleId;
    const source = state.bubbles.find((b) => b.bubble_id === bubbleId);
    const fullWords = words(source?.translation);
    // where the picked lines go: a new bubble, or an existing one (the OCR put a line into the wrong bubble)
    const targets = state.bubbles
        .map((b, i) => ({ b, i }))
        .filter(({ b }) => b.bubble_id !== bubbleId && !b.skip);
    const dlg = document.createElement("dialog");
    dlg.className = "import-dialog merge-dialog";
    dlg.addEventListener("close", () => dlg.remove());
    dlg.innerHTML = `
        <h2 style="margin:0;">Split bubble</h2>
        <label class="split-target">Move the picked lines to
            <select id="splitTarget">
                <option value="">a new bubble</option>
                ${targets.map(({ b, i }) => `<option value="${escapeHtml(b.bubble_id)}">bubble ${i + 1}: ${escapeHtml((b.text || "").slice(0, 50))}</option>`).join("")}
            </select>
        </label>
        <div id="splitBody"><p class="text-muted">Loading…</p></div>
        <fieldset class="split-how ${fullWords.length ? "" : "hidden"}">
            <legend>The translation</legend>
            <label class="checkbox-label"><input type="radio" name="splitHow" value="auto" checked> Divide automatically <span class="text-muted" id="splitAutoNote"></span></label>
            <label class="checkbox-label" title="Click a word below: the cut goes right before it. No translation request."><input type="radio" name="splitHow" value="manual"> I choose where to cut</label>
            <div class="split-cut hidden" id="splitCut" title="Click a word: the second part starts there"></div>
            <label class="checkbox-label" id="splitRetranslateRow" title="One translation request per part (memory + glossary apply)"><input type="radio" name="splitHow" value="retranslate"> Translate both parts again</label>
        </fieldset>
        <div class="import-footer">
            <button data-act="cancel">Cancel</button>
            <button class="primary" data-act="ok">Split</button>
        </div>`;
    document.body.appendChild(dlg);
    const targetSel = dlg.querySelector("#splitTarget");
    const okBtn = dlg.querySelector('[data-act="ok"]');
    const cutBox = dlg.querySelector("#splitCut");
    const how = () => dlg.querySelector('input[name="splitHow"]:checked')?.value || "auto";
    let cut = null;            // index of the first word of the second part (manual)
    let preview = null;

    const piece = (num, text, translation, reread, pending) => `
        <div class="merge-piece">
            <span class="bubble-num">${num}</span>
            <span class="merge-piece-text">${text ? escapeHtml(text) : reread ? '<span class="text-muted">(read again by OCR when split)</span>' : ""}
                <br><span class="text-muted">${pending ? "<i>translated again after the split</i>" : (translation || "").trim() ? escapeHtml(translation) : "(no translation)"}</span></span>
        </div>`;

    const manualParts = () => [fullWords.slice(0, cut).join(" "), fullWords.slice(cut).join(" ")];

    function renderCut() {
        cutBox.classList.toggle("hidden", how() !== "manual");
        if (how() !== "manual") return;
        cutBox.innerHTML = fullWords.map((w, i) => `${i === cut ? '<span class="cut-mark" aria-label="cut">|</span>' : ""}<span class="cut-word ${i < cut ? "first" : "second"}" data-i="${i}">${escapeHtml(w)}</span>`).join(" ");
        cutBox.querySelectorAll(".cut-word").forEach((w) => w.addEventListener("click", () => {
            cut = Math.max(1, Math.min(fullWords.length - 1, parseInt(w.dataset.i, 10) || 1));
            refresh();
        }));
    }

    async function refresh() {
        const target = targetSel.value || null;
        const body = dlg.querySelector("#splitBody");
        okBtn.disabled = true;
        dlg.querySelector("#splitRetranslateRow").classList.toggle("hidden", !!target);
        if (target && how() === "retranslate") dlg.querySelector('input[value="auto"]').checked = true;
        const manual = how() === "manual" && cut !== null ? manualParts() : null;
        try {
            preview = await api.pages.splitPreview(pageId, bubbleId, newLines, target, manual);
        } catch (e) {
            body.innerHTML = `<p class="import-error">${escapeHtml(e.message)}</p>`;
            return;
        }
        if (cut === null) {            // the automatic cut as the starting point of a manual one
            const firstWords = words(preview.pieces[0]?.translation).length;
            cut = Math.max(1, Math.min(fullWords.length - 1, firstWords || Math.ceil(fullWords.length / 2)));
        }
        const pending = how() === "retranslate";
        const sourceNum = state.bubbles.findIndex((b) => b.bubble_id === bubbleId) + 1;
        const targetNum = target ? state.bubbles.findIndex((b) => b.bubble_id === target) + 1 : null;
        const moved = preview.pieces.find((p) => newLines.includes(p.line_ids[0]));
        const stays = preview.pieces.find((p) => p !== moved);
        body.innerHTML = target
            ? `<div class="merge-pieces">
                   ${piece(sourceNum, stays.text, stays.translation, stays.reread)}
                   ${piece(targetNum, preview.target.text, preview.target.translation, false)}
               </div>
               <p class="text-muted" style="margin:0;">Bubble ${sourceNum} keeps the rest; bubble ${targetNum} gets the picked lines and their part of the translation, joined in reading order. No translation request.</p>`
            : `<div class="merge-pieces">${preview.pieces.map((p, i) => piece(i + 1, p.text, p.translation, p.reread, pending)).join("")}</div>`;
        dlg.querySelector("#splitAutoNote").textContent = preview.method !== "manual" && METHOD_NOTE[preview.method] ? `(${METHOD_NOTE[preview.method]})` : "";
        if (preview.edited_source) body.insertAdjacentHTML("beforeend", '<p class="import-error">You had edited the source text of this bubble - the parts use the OCR text of their lines.</p>');
        okBtn.textContent = target ? `Move to bubble ${targetNum}` : "Split";
        renderCut();
        okBtn.disabled = false;
    }
    targetSel.addEventListener("change", refresh);
    dlg.querySelectorAll('input[name="splitHow"]').forEach((r) => r.addEventListener("change", refresh));

    dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
    okBtn.addEventListener("click", async (ev) => {
        const target = targetSel.value || null;
        const mode = how();
        const retranslate = !target && mode === "retranslate";
        const manual = mode === "manual" && cut !== null ? manualParts() : null;
        ev.target.disabled = true;
        try {
            const result = await api.pages.splitBubble(pageId, bubbleId, newLines, target, manual);
            dlg.close();
            cancelSplit();
            const parts = retranslate ? result.bubbles.filter((b) => !b.skip).map((b) => b.bubble_id) : [];
            parts.forEach((id) => state.translating.add(id));   // shown as "Translating..." until the answer is in
            await hooks.reload(target ? result.target : result.kept);
            showToast(target ? "Lines moved" : retranslate ? "Bubble split - translating both parts..." : "Bubble split");
            if (retranslate) await hooks.retranslateIds(parts);
        } catch (e) {
            ev.target.disabled = false;
            alert(`${target ? "Move" : "Split"} failed: ${e.message}`);
        }
    });
    dlg.showModal();
    refresh();
}
