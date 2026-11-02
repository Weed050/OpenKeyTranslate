/**
 * @file frontend/js/editor-split.js
 * @description Splitting a bubble the OCR glued together (two speech bubbles, labels on a map) in the editor.
 * "Split..." shows the bubble's text lines on the page as numbered boxes; clicking a line moves it to the NEW bubble.
 * The preview shows both parts with the EXISTING translation divided between them (by sentences, no translation
 * request - services/bubble_edit.split_translation); Retranslate is offered, not done by default. A part whose
 * translation could not be divided cleanly gets the "check translation" tag in the bubble list until it is saved or
 * retranslated. editor.js wires its own functions in through initSplit().
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
    showToast("Click the lines that belong to the NEW bubble (Esc cancels)", 3500);
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

function renderSplitBar() {
    document.getElementById("splitBar")?.remove();
    if (!split) return;
    const bar = document.createElement("div");
    bar.id = "splitBar";
    bar.className = "merge-bar split-bar";
    const n = split.picked.size;
    bar.innerHTML = `
        <span>Split: click the lines of the <b>new</b> bubble <span class="text-muted">(${n} of ${split.lines.length} picked, Esc cancels)</span></span>
        <span class="merge-bar-actions">
            <button class="primary" data-act="preview" ${n && n < split.lines.length ? "" : "disabled"}>Split...</button>
            <button data-act="cancel">Cancel</button>
        </span>`;
    bar.querySelector('[data-act="preview"]').addEventListener("click", openSplitDialog);
    bar.querySelector('[data-act="cancel"]').addEventListener("click", () => { cancelSplit(); hooks.rerender(); });
    document.body.appendChild(bar);
}

const METHOD_NOTE = {
    sentences: "The translation was divided by sentences.",
    proportional: "The sentences did not match one to one - the translation was divided by length. Check both parts.",
    unsplit: "The translation could not be divided - it stays with the first part, the second is empty. Edit or retranslate it.",
};

async function openSplitDialog() {
    if (!split) return;
    const newLines = [...split.picked];
    const bubbleId = split.bubbleId;
    let preview;
    try {
        preview = await api.pages.splitPreview(pageId, bubbleId, newLines);
    } catch (e) {
        alert(`Split preview failed: ${e.message}`);
        return;
    }
    const hasTranslation = preview.pieces.some((p) => (p.translation || "").trim());
    const dlg = document.createElement("dialog");
    dlg.className = "import-dialog merge-dialog";
    dlg.addEventListener("close", () => dlg.remove());
    dlg.innerHTML = `
        <h2 style="margin:0;">Split into 2 bubbles</h2>
        <div class="merge-pieces">
            ${preview.pieces.map((p, i) => `
                <div class="merge-piece">
                    <span class="bubble-num">${i + 1}</span>
                    <span class="merge-piece-text">${p.text ? escapeHtml(p.text) : '<span class="text-muted">(read again by OCR when split)</span>'}
                        <br><span class="text-muted">${(p.translation || "").trim() ? escapeHtml(p.translation) : "(no translation)"}</span></span>
                </div>`).join("")}
        </div>
        ${hasTranslation ? `<p class="text-muted" style="margin:0;">${METHOD_NOTE[preview.method] || ""}</p>` : ""}
        ${preview.edited_source ? '<p class="import-error">You had edited the source text of this bubble - the parts use the OCR text of their lines.</p>' : ""}
        <label class="checkbox-label" title="One translation request per part. Off: the divided translation is kept and you fix it by hand">
            <input type="checkbox" id="splitRetranslate"> Translate both parts again
        </label>
        <div class="import-footer">
            <button data-act="cancel">Cancel</button>
            <button class="primary" data-act="ok">Split</button>
        </div>`;
    document.body.appendChild(dlg);
    dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
    dlg.querySelector('[data-act="ok"]').addEventListener("click", async (ev) => {
        const retranslate = dlg.querySelector("#splitRetranslate").checked;
        ev.target.disabled = true;
        try {
            const result = await api.pages.splitBubble(pageId, bubbleId, newLines);
            dlg.close();
            cancelSplit();
            await hooks.reload(result.kept);
            showToast(retranslate ? "Bubble split - translating both parts..." : "Bubble split");
            if (retranslate) await hooks.retranslateIds(result.bubbles.filter((b) => !b.skip).map((b) => b.bubble_id));
        } catch (e) {
            ev.target.disabled = false;
            alert(`Split failed: ${e.message}`);
        }
    });
    dlg.showModal();
}
