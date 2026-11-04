/**
 * @file frontend/js/editor-review.js
 * @description Page-level review in the editor (controls in the mode bar under the image):
 *
 * - "Accept page (N)": every bubble with a translation that has no Edited / Accepted marker yet gets the Accepted
 *   stamp at once. The backend marks these stamps confirmed_via = "page" (services/page_export.review_page), so the
 *   evaluation can tell "accepted with the page" from "read and accepted on its own".
 * - "Layout checked": the user checked the BUBBLES of the page - boxes, reading order, cleaning, lettering - not only
 *   their text. Translation reviewed + layout checked = stage "Fully reviewed" (js/stages.js).
 * - Review mode (what Next page does, kept in localStorage):
 *     bubble  "Next page: just go"            - nothing is accepted by itself (default, the careful one)
 *     text    "Next page: accept text"        - Next page = Accept page, then go
 *     all     "Next page: accept text + layout" - also marks the layout as checked
 *   It is a double-edged sword (a page can be waved through unread), so it is never on by default, the select is
 *   always in sight, and the next page offers an Undo for what the previous one accepted.
 * editor.js wires its own functions in through initReview().
 */

import { api } from "./api.js";
import { pageId, state, escapeHtml, showToast } from "./editor-shared.js";

const MODE_KEY = "okt-review-mode";
const UNDO_KEY = "okt-review-undo";        // sessionStorage: what "Next page" accepted on the page before

export const REVIEW_MODES = {
    bubble: { label: "Next page: just go", tip: "Next page accepts nothing - every bubble is reviewed on its own (Save, or moving on from it)" },
    text: { label: "Next page: accept text", tip: "Next page first accepts every bubble you have not reviewed yet (their AI text stays as it is), then goes on" },
    all: { label: "Next page: accept text + layout", tip: "Next page accepts every unreviewed bubble AND marks the bubbles / lettering of the page as checked, then goes on" },
};

/** Callbacks from editor.js: status(bubble) -> "edited" | "accepted" | null, rerender(), saveIfChanged(). */
let hooks = {};

export function initReview(editorHooks) {
    hooks = editorHooks;
}

export function reviewMode() {
    try {
        const m = localStorage.getItem(MODE_KEY);
        return REVIEW_MODES[m] ? m : "bubble";
    } catch {
        return "bubble";
    }
}

function setReviewMode(mode) {
    try { localStorage.setItem(MODE_KEY, mode); } catch { /* this page only */ }
}

/** Bubbles "Accept page" would stamp: translated, not skipped, no marker yet. */
function openBubbles() {
    return state.bubbles.filter((b) => !b.skip && (b.translation || "").trim() && !hooks.status(b));
}

const untranslatedCount = () => state.bubbles.filter((b) => !b.skip && !(b.translation || "").trim()).length;

/** Local copy of the backend stamp, so the markers change at once. */
function applyAccepted(ids) {
    const now = new Date().toISOString();
    const set = new Set(ids);
    state.bubbles.forEach((b) => {
        if (!set.has(b.bubble_id)) return;
        b.confirmed_translation = b.translation || "";
        b.confirmed_at = now;
        b.confirmed_via = "page";
        delete b.split_check;
    });
}

function applyUnaccepted(ids) {
    const set = new Set(ids);
    state.bubbles.forEach((b) => {
        if (!set.has(b.bubble_id) || b.confirmed_via !== "page") return;
        delete b.confirmed_translation;
        delete b.confirmed_at;
        delete b.confirmed_via;
    });
}

/** The review controls inside the mode bar (editor-lettering.js renderModeBar calls this with its slot). */
export function renderReviewControls(slot) {
    if (!slot) return;
    const open = openBubbles().length;
    const layoutOn = !!state.layoutCheckedAt;
    const mode = reviewMode();
    slot.innerHTML = `
        <button type="button" id="acceptPageBtn" ${open ? "" : "disabled"}
                title="${open ? `Mark the ${open} bubble(s) without a marker as Accepted - their AI text stays as it is. Undo in the message.` : "Every translated bubble of this page is reviewed"}">&#10003; Accept page${open ? ` (${open})` : ""}</button>
        <button type="button" id="layoutCheckBtn" class="toggle-btn${layoutOn ? " on" : ""}" aria-pressed="${layoutOn}"
                title="${layoutOn ? "Bubbles / layout of this page are marked as checked - click to undo" : "Mark the bubbles of this page as checked: boxes, reading order, cleaning, lettering. Translation reviewed + this = Fully reviewed"}">&#9638; Layout checked</button>
        <select id="reviewModeSelect" class="review-mode${mode !== "bubble" ? " is-fast" : ""}" title="${escapeHtml(REVIEW_MODES[mode].tip)}">
            ${Object.entries(REVIEW_MODES).map(([id, m]) => `<option value="${id}" ${id === mode ? "selected" : ""}>${escapeHtml(m.label)}</option>`).join("")}
        </select>`;
    slot.querySelector("#acceptPageBtn").addEventListener("click", () => acceptPage({ ask: true }));
    slot.querySelector("#layoutCheckBtn").addEventListener("click", toggleLayoutChecked);
    slot.querySelector("#reviewModeSelect").addEventListener("change", (e) => {
        const m = e.target.value;
        if (m !== "bubble" && !confirm(`${REVIEW_MODES[m].label}\n\n${REVIEW_MODES[m].tip}.\n\nBubbles accepted this way are recorded as "accepted with the page". Turn it on?`)) {
            e.target.value = reviewMode();
            return;
        }
        setReviewMode(m);
        refresh();
        showToast(m === "bubble" ? "Review mode: bubble by bubble" : `Review mode: ${REVIEW_MODES[m].label}`, 3000);
    });
}

function refresh() {
    hooks.rerender();
    renderReviewControls(document.getElementById("reviewSlot"));
}

/** "Accept page" button. Undo is offered in the message. */
async function acceptPage({ ask }) {
    if (!(await hooks.saveIfChanged())) return;
    const open = openBubbles();
    if (!open.length) return;
    if (ask && !confirm(`Accept ${open.length} bubble(s) you have not reviewed yet?\n\nTheir AI text is kept as it is and marked as Accepted (recorded as "accepted with the page").`)) return;
    try {
        const r = await api.pages.review(pageId, { accept: true });
        applyAccepted(r.accepted);
        refresh();
        const left = untranslatedCount();
        showToast(`Accepted ${r.accepted.length} bubble(s)${left ? ` - ${left} without a translation still open` : ""}`, 6000,
                  r.accepted.length ? { label: "Undo", run: () => undoAccept(pageId, r.accepted, false) } : null);
    } catch (e) {
        showToast(`Accept page failed: ${e.message}`, 5000);
    }
}

async function toggleLayoutChecked() {
    const on = !state.layoutCheckedAt;
    try {
        const r = await api.pages.review(pageId, { layout: on });
        state.layoutCheckedAt = r.layout_checked_at;
        refresh();
        showToast(on ? "Layout checked ✓" : "Layout check removed", 2000);
    } catch (e) {
        showToast(`Couldn't save the layout check: ${e.message}`, 5000);
    }
}

async function undoAccept(targetPageId, ids, layoutWasSet) {
    try {
        await api.pages.review(targetPageId, { unaccept: ids, ...(layoutWasSet ? { layout: false } : {}) });
        if (String(targetPageId) === String(pageId)) {
            applyUnaccepted(ids);
            if (layoutWasSet) state.layoutCheckedAt = null;
            refresh();
        }
        showToast("Undone", 2000);
    } catch (e) {
        showToast(`Undo failed: ${e.message}`, 5000);
    }
}

/**
 * Called by editor.js right before it leaves the page with Next page (button, shortcut, or running off the last
 * bubble). In the "text" / "all" modes it accepts the page first; the next page shows the Undo. Never blocks the
 * navigation: a failed request is only reported.
 */
export async function reviewBeforeNextPage() {
    const mode = reviewMode();
    if (mode === "bubble" || !state.bubbles?.length || document.getElementById("editorLayout")?.classList.contains("hidden")) return;
    const wantLayout = mode === "all" && !state.layoutCheckedAt;
    if (!openBubbles().length && !wantLayout) return;
    try {
        const r = await api.pages.review(pageId, { accept: true, ...(wantLayout ? { layout: true } : {}) });
        if (r.accepted.length || wantLayout) {
            sessionStorage.setItem(UNDO_KEY, JSON.stringify({
                pageId: String(pageId), label: document.getElementById("pageLabel")?.textContent || "",
                ids: r.accepted, layout: wantLayout, untranslated: untranslatedCount(),
            }));
        }
    } catch (e) {
        console.warn("[REVIEW] page not accepted:", e);
    }
}

/** On a page load: tell what Next page accepted on the page before, with an Undo (once). */
export function offerUndoFromPreviousPage() {
    let info;
    try { info = JSON.parse(sessionStorage.getItem(UNDO_KEY) || "null"); } catch { info = null; }
    sessionStorage.removeItem(UNDO_KEY);
    if (!info || info.pageId === String(pageId)) return;
    const parts = [];
    if (info.ids.length) parts.push(`${info.ids.length} bubble(s) accepted`);
    if (info.layout) parts.push("layout checked");
    const where = info.label ? ` (${info.label})` : "";
    const left = info.untranslated ? ` - ${info.untranslated} without a translation left open` : "";
    showToast(`Previous page${where}: ${parts.join(" + ")}${left}`, 8000,
              { label: "Undo", run: () => undoAccept(info.pageId, info.ids, info.layout) });
}
