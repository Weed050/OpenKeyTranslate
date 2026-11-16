/**
 * @file frontend/js/editor-progress.js
 * @description What a page run (Run OCR + translation, Re-OCR, a page processed in the background) is doing right now:
 * the steps with the current one marked, a bar, the elapsed time and a line about what it waits for (a key resting
 * after its limit, the OCR busy with another page). Read from GET /pages/{id}/status -> progress
 * (backend services/run_progress.py) every 0.7 s while the run goes on.
 *
 * Why: the editor used to show "Processing - this can take a while" for 10-60 s with nothing moving, and a slow first
 * page (OCR on a processor, a free key waiting a minute after its limit) looked like a hang.
 */

import { api } from "./api.js";
import { escapeHtml } from "./editor-shared.js";

const STEP_LABELS = {
    load: "Opening the image",
    ocr: "Reading the text (OCR)",
    translate: "Translating",
    clean: "Erasing the original letters",
    save: "Saving the page",
};
/** Short form for a button label (Re-OCR) */
const STEP_SHORT = { load: "opening", ocr: "reading", translate: "translating", clean: "erasing", save: "saving" };
/** Share of a typical run each step takes - only for the bar, the list says what really happens */
const WEIGHT = { load: 2, ocr: 55, translate: 28, clean: 12, save: 3 };
/** Steps without a counter of their own creep along this time scale (seconds) and never reach their end */
const CREEP_S = { load: 1, ocr: 12, translate: 8, clean: 4, save: 1 };

export function formatElapsed(seconds) {
    const s = Math.max(0, Math.round(seconds || 0));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** 0..1 inside the current step: its counter when it has one, else a slow creep that stops short of the end. */
function stepFraction(p) {
    if (p.total) return Math.max(0, Math.min(1, p.done / p.total));
    return 0.85 * (1 - Math.exp(-(p.step_elapsed || 0) / (CREEP_S[p.step] || 5)));
}

/** 0..100 for the whole run. */
export function overallPercent(p) {
    const steps = p.steps || Object.keys(WEIGHT);
    const total = steps.reduce((sum, s) => sum + (WEIGHT[s] || 5), 0);
    let before = 0;
    for (const s of steps) {
        if (s === p.step) break;
        before += WEIGHT[s] || 5;
    }
    return Math.min(99, Math.round(100 * (before + (WEIGHT[p.step] || 5) * stepFraction(p)) / total));
}

/** The step list + bar for the "Run OCR + translation" screen. */
export function progressHtml(p) {
    const steps = p.steps || Object.keys(STEP_LABELS);
    const at = steps.indexOf(p.step);
    const counter = p.total && p.step === "ocr" && p.total > 1 ? ` <span class="run-count">${Math.round(100 * p.done / p.total)}%</span>` : "";
    const rows = steps.map((s, i) => {
        const cls = i < at ? "done" : i === at ? "now" : "";
        const detail = i === at && p.detail ? `<div class="run-detail">${escapeHtml(p.detail)}</div>` : "";
        const time = i === at && (p.step_elapsed || 0) >= 3 ? `<span class="run-time">${formatElapsed(p.step_elapsed)}</span>` : "";
        return `<li class="${cls}"><span class="run-dot" aria-hidden="true"></span><span class="run-label">${STEP_LABELS[s] || s}${i === at ? counter : ""}${detail}</span>${time}</li>`;
    }).join("");
    return `
        <div class="run-bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${overallPercent(p)}">
            <span style="width:${overallPercent(p)}%"></span>
        </div>
        <ol class="run-steps">${rows}</ol>
        <div class="run-foot text-muted">${p.kind === "reocr" ? "Re-OCR" : "Running"} for ${formatElapsed(p.elapsed)}</div>`;
}

/** "reading 45%" / "translating" - for a button that shows the run (Re-OCR). */
export function shortLabel(p) {
    const what = STEP_SHORT[p.step] || p.step;
    return p.step === "ocr" && p.total > 1 ? `${what} ${Math.round(100 * p.done / p.total)}%` : what;
}

/**
 * Polls the page's status while a run goes on and calls onProgress(progress) with every new snapshot (null once the
 * backend has no run of this page any more). Returns stop(). Errors of a single poll are ignored - the run itself
 * reports its own result.
 */
export function watchRun(pageId, onProgress, intervalMs = 700) {
    let stopped = false;
    let timer = null;
    const tick = async () => {
        if (stopped) return;
        try {
            const { progress } = await api.pages.status(pageId);
            if (!stopped) onProgress(progress || null);
        } catch { /* backend busy or restarting - the next poll tries again */ }
        if (!stopped) timer = setTimeout(tick, intervalMs);
    };
    timer = setTimeout(tick, 250);
    return () => { stopped = true; clearTimeout(timer); };
}
