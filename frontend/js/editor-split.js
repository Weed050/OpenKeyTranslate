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
    clauses: "One sentence for both parts - it was cut at a comma or conjunction (BUT -> ALE). Check both parts.",
    unsplit: "The translation could not be divided - it stays with the first part, the second is empty. Edit or retranslate it.",
};

async function openSplitDialog() {
    if (!split) return;
    const newLines = [...split.picked];
    const bubbleId = split.bubbleId;
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
        <label class="checkbox-label" id="splitRetranslateRow" title="One translation request per part. Off: the divided translation is kept and you fix it by hand">
            <input type="checkbox" id="splitRetranslate"> Translate both parts again
        </label>
        <div class="import-footer">
            <button data-act="cancel">Cancel</button>
            <button class="primary" data-act="ok">Split</button>
        </div>`;
    document.body.appendChild(dlg);
    const targetSel = dlg.querySelector("#splitTarget");
    const okBtn = dlg.querySelector('[data-act="ok"]');

    const piece = (num, text, translation, reread) => `
        <div class="merge-piece">
            <span class="bubble-num">${num}</span>
            <span class="merge-piece-text">${text ? escapeHtml(text) : reread ? '<span class="text-muted">(read again by OCR when split)</span>' : ""}
                <br><span class="text-muted">${(translation || "").trim() ? escapeHtml(translation) : "(no translation)"}</span></span>
        </div>`;

    async function refresh() {
        const target = targetSel.value || null;
        const body = dlg.querySelector("#splitBody");
        okBtn.disabled = true;
        let preview;
        try {
            preview = await api.pages.splitPreview(pageId, bubbleId, newLines, target);
        } catch (e) {
            body.innerHTML = `<p class="import-error">${escapeHtml(e.message)}</p>`;
            return;
        }
        const hasTranslation = preview.pieces.some((p) => (p.translation || "").trim());
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
            : `<div class="merge-pieces">${preview.pieces.map((p, i) => piece(i + 1, p.text, p.translation, p.reread)).join("")}</div>`;
        if (hasTranslation) body.insertAdjacentHTML("beforeend", `<p class="text-muted" style="margin:0;">${METHOD_NOTE[preview.method] || ""}</p>`);
        if (preview.edited_source) body.insertAdjacentHTML("beforeend", '<p class="import-error">You had edited the source text of this bubble - the parts use the OCR text of their lines.</p>');
        okBtn.textContent = target ? `Move to bubble ${targetNum}` : "Split";
        dlg.querySelector("#splitRetranslateRow").classList.toggle("hidden", !!target);
        okBtn.disabled = false;
    }
    targetSel.addEventListener("change", refresh);

    dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
    okBtn.addEventListener("click", async (ev) => {
        const target = targetSel.value || null;
        const retranslate = !target && dlg.querySelector("#splitRetranslate").checked;
        ev.target.disabled = true;
        try {
            const result = await api.pages.splitBubble(pageId, bubbleId, newLines, target);
            dlg.close();
            cancelSplit();
            await hooks.reload(target ? result.target : result.kept);
            showToast(target ? "Lines moved" : retranslate ? "Bubble split - translating both parts..." : "Bubble split");
            if (retranslate) await hooks.retranslateIds(result.bubbles.filter((b) => !b.skip).map((b) => b.bubble_id));
        } catch (e) {
            ev.target.disabled = false;
            alert(`${target ? "Move" : "Split"} failed: ${e.message}`);
        }
    });
    dlg.showModal();
    refresh();
}
