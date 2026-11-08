/**
 * @file frontend/js/editor-merge.js
 * @description Merging bubbles in the editor. Shift+click on the page (or on a row of the bubble list) picks bubbles
 * to merge into the selected one; they need not be neighbours in the list (the OCR may have put another bubble
 * between two pieces of one speech bubble). The dialog shows the joined text BEFORE anything is saved: the default
 * order comes from the server (reading-order setting ttb / rtl / ltr applied to the pieces) and can be changed with
 * the arrows. editor.js wires its own functions in through initMerge().
 */

import { api } from "./api.js";
import { pageId, state, escapeHtml, showToast } from "./editor-shared.js";

import { notify } from "./dialogs.js";
/** Callbacks from editor.js: save(), reload(selectId), retranslate(silent), select(index), rerender(). */
let hooks = {};

/**
 * "Translate the merged bubble again": OFF by default (merging two pieces of one sentence with the arrows usually
 * needs no new request - the joined translation is fine or a quick edit). The default is a browser-local switch in
 * Settings > Editor (js/settings.js, same key); ticking the box in the dialog counts for that one merge only.
 */
const MERGE_RETRANSLATE_KEY = "okt-merge-retranslate";
function retranslateByDefault() {
    try { return localStorage.getItem(MERGE_RETRANSLATE_KEY) === "1"; } catch { return false; }
}

export function initMerge(editorHooks) {
    hooks = editorHooks;
}

export function isMergePick(bubbleId) {
    return state.mergePicks.includes(bubbleId);
}

function numberOf(bubbleId) {
    return state.bubbles.findIndex((b) => b.bubble_id === bubbleId) + 1;
}

/** Shift+click on bubble `index`: add it to / remove it from the merge selection of the selected bubble. */
export function toggleMergePick(index) {
    const bubble = state.bubbles[index];
    if (!bubble) return;
    if (state.selectedIndex === null) {          // nothing selected yet: the first Shift+click just selects
        hooks.select(index);
        return;
    }
    if (bubble.bubble_id === state.bubbles[state.selectedIndex].bubble_id) return;
    const at = state.mergePicks.indexOf(bubble.bubble_id);
    if (at === -1) state.mergePicks.push(bubble.bubble_id);
    else state.mergePicks.splice(at, 1);
    hooks.rerender();
    renderMergeBar();
}

export function clearMergePicks() {
    const had = state.mergePicks.length > 0;
    state.mergePicks = [];
    if (had) hooks.rerender();
    renderMergeBar();
}

/** The floating "Merge bubbles 2, 5" bar; also drops picks that no longer exist after a reload. */
export function renderMergeBar() {
    document.getElementById("mergeBar")?.remove();
    state.mergePicks = state.mergePicks.filter((id) => state.bubbles.some((b) => b.bubble_id === id));
    if (!state.mergePicks.length || state.selectedIndex === null) return;
    const base = state.bubbles[state.selectedIndex];
    const nums = [base.bubble_id, ...state.mergePicks].map(numberOf).sort((a, b) => a - b);
    const bar = document.createElement("div");
    bar.id = "mergeBar";
    bar.className = "merge-bar";
    bar.innerHTML = `
        <span>Merge bubbles <b>${nums.join(", ")}</b> <span class="text-muted">(Shift+click adds or removes, Esc cancels)</span></span>
        <span class="merge-bar-actions">
            <button class="primary" data-act="merge">Merge...</button>
            <button data-act="cancel">Cancel</button>
        </span>`;
    bar.querySelector('[data-act="merge"]').addEventListener("click", () => openMergeDialog(state.mergePicks.slice()));
    bar.querySelector('[data-act="cancel"]').addEventListener("click", clearMergePicks);
    document.body.appendChild(bar);
}

/** Preview + confirm dialog for merging `otherIds` into the selected bubble. */
export async function openMergeDialog(otherIds) {
    if (state.selectedIndex === null || !otherIds.length) return;
    const base = state.bubbles[state.selectedIndex];
    if (!(await hooks.save())) return;           // the server joins the SAVED translations: save what was typed first
    let preview;
    try {
        preview = await api.pages.mergePreview(pageId, base.bubble_id, otherIds);
    } catch (e) {
        notify(`Merge preview failed: ${e.message}`);
        return;
    }
    let order = preview.order.slice();
    const byId = new Map(state.bubbles.map((b) => [b.bubble_id, b]));
    const anyTranslation = order.some((id) => (byId.get(id)?.translation || "").trim());

    const dlg = document.createElement("dialog");
    dlg.className = "import-dialog merge-dialog";
    dlg.addEventListener("close", () => dlg.remove());
    document.body.appendChild(dlg);

    const render = () => {
        const covered = (preview.covered || []).map(numberOf).filter((n) => n > 0);
        dlg.innerHTML = `
            <h2 style="margin:0;">Merge ${order.length} bubbles</h2>
            <p class="text-muted" style="margin:0;">Texts are joined in this order (from the reading-order setting). Use the arrows if the pieces should be read differently.</p>
            <div class="merge-pieces">
                ${order.map((id, i) => {
                    const b = byId.get(id) || {};
                    return `<div class="merge-piece">
                        <span class="bubble-num">${numberOf(id)}</span>
                        <span class="merge-piece-text">${escapeHtml(b.text || "")}
                            ${(b.translation || "").trim() ? `<br><span class="text-muted">${escapeHtml(b.translation)}</span>` : ""}</span>
                        <span class="merge-piece-move">
                            <button data-up="${i}" ${i === 0 ? "disabled" : ""} title="Earlier">&uarr;</button>
                            <button data-down="${i}" ${i === order.length - 1 ? "disabled" : ""} title="Later">&darr;</button>
                        </span>
                    </div>`;
                }).join("")}
            </div>
            <div class="merge-result">
                <div><span class="text-muted">Source text:</span> ${escapeHtml(preview.text || "")}</div>
                ${(preview.translation || "").trim() ? `<div><span class="text-muted">Joined translation:</span> ${escapeHtml(preview.translation)}</div>` : ""}
            </div>
            ${covered.length ? `<p class="import-error">The merged box also covers bubble${covered.length > 1 ? "s" : ""} ${covered.join(", ")} (not merged) - the translation would be typeset over ${covered.length > 1 ? "them" : "it"}. Merge only pieces of ONE speech bubble.</p>` : ""}
            <label class="checkbox-label" title="One request to the translator for the whole merged bubble (when the joined translation of the pieces reads badly). Ticked or not by default: Settings > Editor.">
                <input type="checkbox" id="mergeRetranslate" ${anyTranslation && retranslateByDefault() ? "checked" : ""}> Translate the merged bubble again
            </label>
            <div class="import-footer">
                <button data-act="cancel">Cancel</button>
                <button class="primary" data-act="ok">Merge</button>
            </div>`;
        dlg.querySelectorAll("[data-up],[data-down]").forEach((btn) => btn.addEventListener("click", async () => {
            const i = Number(btn.dataset.up ?? btn.dataset.down);
            const j = btn.dataset.up !== undefined ? i - 1 : i + 1;
            [order[i], order[j]] = [order[j], order[i]];
            try {
                preview = await api.pages.mergePreview(pageId, base.bubble_id, otherIds, order);
            } catch (e) {
                showToast(`Preview failed: ${e.message}`, 4000);
            }
            render();
        }));
        dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
        dlg.querySelector('[data-act="ok"]').addEventListener("click", async (ev) => {
            const retranslate = dlg.querySelector("#mergeRetranslate").checked;
            ev.target.disabled = true;
            try {
                const result = await api.pages.mergeBubble(pageId, base.bubble_id, otherIds, order);
                dlg.close();
                state.mergePicks = [];
                await hooks.reload(base.bubble_id);
                showToast(`Merged ${order.length} bubbles`);
                if (result.reinpainted === false) showToast("Merged, but this older page can't rebuild the image - run Re-OCR to update it.", 6000);
                if (retranslate) await hooks.retranslate(true);
            } catch (e) {
                ev.target.disabled = false;
                notify(`Merge failed: ${e.message}`);
            }
        });
    };
    render();
    dlg.showModal();
}
