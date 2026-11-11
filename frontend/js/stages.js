/**
 * @file frontend/js/stages.js
 * @description Work stages of a page - ONE definition for the page list (tiles, chapter bars, legend / status
 * filter), the project cards, the editor's chapter nav and the Help table. The backend decides the stage
 * (services/page_progress.py); this file only names, orders, colours and groups it.
 *
 * Names say what IS done, in pipeline order:
 *   Not started -> OCR done -> Partly translated -> Translated (AI) -> Translation reviewed -> Fully reviewed -> (Exported)
 * "Translation reviewed" = the text of every bubble was accepted or edited by the user; "Fully reviewed" = that AND
 * the bubbles themselves (boxes, reading order, cleaning, lettering) marked as checked in the editor.
 * "Exported" is optional (off by default): an export can go to any folder or a ZIP and is not part of the review, so
 * by default an exported page counts as reviewed. The legend / status filter turns it on. A page exported BEFORE its
 * review stays in its stage; with "Exported" on its tile says "exported" and the project card counts it
 * (exportedDraftNote). An export whose file was deleted no longer counts (services/page_progress.py).
 * Colours: css/tokens.css --stage-* (fixed muted hues, one per stage - see the comment there).
 */

/** Bar / filter groups, best first (= left to right on a bar). `how` = how a page gets there (Help table). */
export const STAGE_GROUPS = [
    { id: "exported", label: "Exported", short: "exported",
      tip: "Reviewed and exported after its last change",
      how: "Export pages / Export page after the review. Only shown when “Show exported” is on in the legend." },
    { id: "checked", label: "Fully reviewed", short: "fully reviewed",
      tip: "Translation reviewed AND the bubbles (boxes, order, cleaning, lettering) checked",
      how: "Translation reviewed + “Layout checked” in the editor (button under the image, or the review mode)." },
    { id: "reviewed", label: "Translation reviewed", short: "translation reviewed",
      tip: "You accepted or edited the text of every bubble - ready to export",
      how: "Every bubble has the Edited or Accepted marker (Save, moving on, or Accept page)." },
    { id: "translated", label: "Translated (AI)", short: "AI-translated",
      tip: "Every bubble translated by the AI, not all of them reviewed by you yet",
      how: "The translation ran for every bubble; the review is still open." },
    { id: "partial", label: "Partly translated", short: "partly translated",
      tip: "Some bubbles have a translation, some not (failed batch, bubbles added / split later)",
      how: "A translation batch failed, or bubbles were added / split / re-read after it - Retranslate fills the gaps." },
    { id: "ocr", label: "OCR done", short: "OCR done",
      tip: "Text read and erased by the OCR, nothing translated yet",
      how: "OCR + cleaning ran (also prefetched pages): opening the page translates it, or use Retranslate." },
    { id: "todo", label: "Not started", short: "not started",
      tip: "OCR has not run yet (also queued, running or failed)",
      how: "Imported only. Open the page (Run OCR + translation) or “Go to next unprocessed”." },
];

const BY_ID = Object.fromEntries(STAGE_GROUPS.map((g) => [g.id, g]));

/** Badge of one page: exact status, also the ones grouped under "Not started". */
export const PAGE_BADGES = {
    pending: ["not started", "OCR, cleaning and translation have not run yet"],
    queued: ["queued", "Waiting for the background processing"],
    processing: ["processing", "OCR / cleaning / translation running"],
    failed: ["failed", "Processing failed - open the page to see why"],
    excluded: ["excluded", "Left out of the project (Restore brings it back)"],
    ...Object.fromEntries(STAGE_GROUPS.filter((g) => g.id !== "todo").map((g) => [g.id, [g.short, g.tip]])),
};

/** Weight of a stage for "progress" sorting (0 = not started ... 5 = fully reviewed / exported). */
const WEIGHT = { todo: 0, ocr: 1, partial: 2, translated: 3, reviewed: 4, checked: 5, exported: 5 };

const TRACK_EXPORTED_KEY = "okt-track-exported";

export function trackExported() {
    try { return localStorage.getItem(TRACK_EXPORTED_KEY) === "1"; } catch { return false; }
}

export function setTrackExported(on) {
    try { localStorage.setItem(TRACK_EXPORTED_KEY, on ? "1" : "0"); } catch { /* not critical */ }
}

/**
 * Stage of a page as shown: exported folds back into the review stage it had unless tracked. The backend reports
 * one stage only, so a folded export shows as "Translation reviewed", or "Fully reviewed" when the page list
 * says its layout was checked (`layout`).
 */
export function shownStage(stage, layout = false) {
    if (stage === "exported" && !trackExported()) return layout ? "checked" : "reviewed";
    return stage;
}

/** Group a page stage / DB status belongs to (bar segment, filter checkbox). Excluded pages: null. */
export function stageGroup(stage, layout = false) {
    if (stage === "excluded") return null;
    const s = shownStage(stage, layout);
    return BY_ID[s] ? s : "todo";
}

/** Groups visible in the UI right now (exported only when tracked). */
export function activeGroups() {
    return STAGE_GROUPS.filter((g) => g.id !== "exported" || trackExported());
}

export function groupLabel(id) {
    return BY_ID[id]?.label || id;
}

/** {group: n} from a list of pages ({stage, status, layout}) or from the backend's {stage: n} map (project card). */
export function countGroups(stageCounts) {
    const out = Object.fromEntries(STAGE_GROUPS.map((g) => [g.id, 0]));
    if (Array.isArray(stageCounts)) {
        for (const p of stageCounts) {
            const g = stageGroup(p.stage || p.status, p.layout);
            if (g) out[g] += 1;
        }
        return out;
    }
    for (const [stage, n] of Object.entries(stageCounts)) {
        if (stage === "exported_checked" || stage === "exported_draft") continue;   // extra counts, not stages (services/page_progress.py)
        const g = stageGroup(stage);
        if (g) out[g] += n;
    }
    const exportedChecked = stageCounts.exported_checked || 0;
    if (exportedChecked && !trackExported()) {           // folded exports that were fully reviewed
        out.reviewed -= exportedChecked;
        out.checked += exportedChecked;
    }
    return out;
}

const total = (counts) => Object.values(counts).reduce((a, b) => a + b, 0);

/** 0..1: how far along the pages are (weighted by stage) - "Sort: progress". */
export function doneScore(counts) {
    const n = total(counts);
    return n ? Object.entries(counts).reduce((a, [g, c]) => a + (WEIGHT[g] || 0) * c, 0) / (5 * n) : 0;
}

/** Pages whose TEXT is reviewed (translation reviewed, fully reviewed, exported). */
export function reviewedCount(counts) {
    return counts.reviewed + counts.checked + counts.exported;
}

/** The bar: one <span class="seg stage-X"> per non-empty group, widths in %; the empty track = not started. */
export function stageBarHtml(counts) {
    const n = total(counts);
    return activeGroups().filter((g) => g.id !== "todo" && counts[g.id])
        .map((g) => `<span class="seg stage-${g.id}" style="width:${(100 * counts[g.id]) / n}%"></span>`).join("");
}

/** Multi-line tooltip: "3 Translation reviewed\n2 Translated (AI)..." (empty groups left out). */
export function stageTip(counts) {
    return activeGroups().filter((g) => counts[g.id]).map((g) => `${counts[g.id]}  ${g.label}`).join("\n") || "no pages";
}

/**
 * Small number chips of a chapter card: a dot in the stage colour + the count, the name in the tooltip
 * (the legend above the list names the colours). Not started is shown as "N left".
 */
export function stageChipsHtml(counts, esc) {
    const chips = activeGroups().filter((g) => g.id !== "todo" && counts[g.id])
        .map((g) => `<span class="stage-chip" title="${esc(`${counts[g.id]} ${g.label}: ${g.tip}`)}"><i class="dot stage-${g.id}"></i>${counts[g.id]}</span>`);
    if (counts.todo) chips.push(`<span class="stage-chip muted" title="${esc(BY_ID.todo.tip)}">${counts.todo} not started</span>`);
    return chips.join("");
}

/** Project card: "n exported before the review" (AI drafts exported as they were) - only while "Exported" is shown. */
export function exportedDraftNote(stageCounts, esc) {
    const n = (stageCounts && stageCounts.exported_draft) || 0;
    if (!n || !trackExported()) return "";
    return `<span class="legend-note" title="${esc("Exported with AI text nobody has reviewed yet - they stay in their stage (Translated (AI) etc.) until you review them")}">${n} exported before the review</span>`;
}

/** Legend chips with dots, names and counts (project card; the page list builds a clickable one from this list). */
export function stageLegendHtml(counts, esc) {
    return activeGroups().filter((g) => counts[g.id] || g.id === "reviewed")
        .map((g) => `<span title="${esc(g.tip)}"><i class="dot stage-${g.id}"></i>${counts[g.id]} ${esc(g.label)}</span>`).join("");
}
