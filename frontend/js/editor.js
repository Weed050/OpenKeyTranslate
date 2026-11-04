/**
 * @file frontend/js/editor.js
 * @description Page editor: zoomable image with clickable bubble outlines,
 * a synced bubble list, and a detail panel to edit/save/reset/delete the
 * selected bubble. Left/Right arrow keys (or the Prev/Next buttons) cycle
 * through bubbles, auto-saving the current one first if it changed, and
 * fall through to the previous/next page once you run off either end.
 */
 
import { api } from "./api.js";
import { PAGE_BADGES, shownStage } from "./stages.js";
import { renderSidebar } from "./nav.js";
import { params, pageId, state, el, escapeHtml, showToast, openOrAlert, groupByChapter, compareChapterNames } from "./editor-shared.js";
import { showPagePicker } from "./pagepicker.js";
import { memoryEntryUrl, offerMemoryCleanup } from "./memory-links.js";
import { initMerge, isMergePick, toggleMergePick, clearMergePicks, renderMergeBar, openMergeDialog } from "./editor-merge.js";
import { initSplit, startSplit, cancelSplit, isSplitting, renderSplitOverlay } from "./editor-split.js";
import { initLettering, isLettering, setMode, toggleMode, enterCurrentMode, checkReviewedOnLoad, renderLetteringOverlay,
         renderLetteringPanel, letteringAction, exportPage } from "./editor-lettering.js";
import { actionFor, currentBindings, formatCombo } from "./shortcuts.js";
import { initReview, renderReviewControls, reviewBeforeNextPage, offerUndoFromPreviousPage, reviewMode } from "./editor-review.js";
import { initOcrDebug, renderOcrDebug, invalidateOcrDebug } from "./editor-ocrdebug.js";
 
const enterAt = params.get("enterAt"); // "first" | "last" | null - set when arriving via cross-page nav
const resumeBubbleIndex = params.get("bubble"); // set by the dashboard's "Resume where I left off" link
 
const SPELLCHECK_STORAGE_KEY = "okt-spellcheck-enabled";
 
function isSpellcheckEnabled() {
    return localStorage.getItem(SPELLCHECK_STORAGE_KEY) === "1";
}
function setSpellcheckEnabled(enabled) {
    localStorage.setItem(SPELLCHECK_STORAGE_KEY, enabled ? "1" : "0");
}
 
 
const PANEL_W_STORAGE_KEY = "okt-panel-width";
const DETAIL_H_STORAGE_KEY = "okt-detail-height";
 
const PANEL_W_DEFAULT = 320, PANEL_W_MIN = 240, PANEL_W_MAX = 640;
const DETAIL_H_DEFAULT_VH = 55, DETAIL_H_MIN_VH = 20, DETAIL_H_MAX_VH = 90;
 
function applyPanelWidth(px) {
    const clamped = Math.min(PANEL_W_MAX, Math.max(PANEL_W_MIN, px));
    document.getElementById("editorLayout").style.setProperty("--panel-w", `${clamped}px`);
    return clamped;
}
 
function applyDetailHeight(vh) {
    const clamped = Math.min(DETAIL_H_MAX_VH, Math.max(DETAIL_H_MIN_VH, vh));
    document.getElementById("editorLayout").style.setProperty("--detail-h", `${clamped}vh`);
    return clamped;
}
 
function restorePanelLayout() {
    const savedW = parseFloat(localStorage.getItem(PANEL_W_STORAGE_KEY));
    applyPanelWidth(Number.isFinite(savedW) ? savedW : PANEL_W_DEFAULT);
    const savedH = parseFloat(localStorage.getItem(DETAIL_H_STORAGE_KEY));
    applyDetailHeight(Number.isFinite(savedH) ? savedH : DETAIL_H_DEFAULT_VH);
}
 
function resetPanelLayout() {
    localStorage.removeItem(PANEL_W_STORAGE_KEY);
    localStorage.removeItem(DETAIL_H_STORAGE_KEY);
    applyPanelWidth(PANEL_W_DEFAULT);
    applyDetailHeight(DETAIL_H_DEFAULT_VH);
}
 
function setupResizers() {
    const resizerX = document.getElementById("panelResizerX");
    const resizerY = document.getElementById("panelResizerY");
 
    resizerX.addEventListener("mousedown", (startEvent) => {
        startEvent.preventDefault();
        resizerX.classList.add("dragging");
        const onMove = (e) => {
            const width = window.innerWidth - e.clientX; // side panel is to the right
            const applied = applyPanelWidth(width);
            localStorage.setItem(PANEL_W_STORAGE_KEY, String(applied));
        };
        const onUp = () => {
            resizerX.classList.remove("dragging");
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
    });
 
    resizerY.addEventListener("mousedown", (startEvent) => {
        startEvent.preventDefault();
        resizerY.classList.add("dragging");
        const onMove = (e) => {
            const panelRect = document.getElementById("sidePanel").getBoundingClientRect();
            const distanceFromBottom = panelRect.bottom - e.clientY;
            const vh = (distanceFromBottom / window.innerHeight) * 100;
            const applied = applyDetailHeight(vh);
            localStorage.setItem(DETAIL_H_STORAGE_KEY, String(applied));
        };
        const onUp = () => {
            resizerY.classList.remove("dragging");
            window.removeEventListener("mousemove", onMove);
            window.removeEventListener("mouseup", onUp);
        };
        window.addEventListener("mousemove", onMove);
        window.addEventListener("mouseup", onUp);
    });
}
 
function autoGrowTextarea(el) {
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 300)}px`; // cap so a huge paste doesn't explode the layout
}
 
 
window.addEventListener("DOMContentLoaded", init);
 
async function init() {
    el.editorLayout = document.getElementById("editorLayout");
    el.processPrompt = document.getElementById("processPrompt");
    el.processBtn = document.getElementById("processBtn");
    el.processStatus = document.getElementById("processStatus");
    el.imageViewport = document.getElementById("imageViewport");
    el.imageState = document.getElementById("imageState");
    el.imageCanvas = document.getElementById("imageCanvas");
    el.pageImage = document.getElementById("pageImage");
    el.pageLabel = document.getElementById("pageLabel");
    el.zoomLabel = document.getElementById("zoomLabel");
    el.chapterNavBody = document.getElementById("chapterNavBody");
    el.bubbleListHeader = document.getElementById("bubbleListHeader");
    el.bubbleList = document.getElementById("bubbleList");
    el.bubbleDetail = document.getElementById("bubbleDetail");
    el.saveToast = document.getElementById("saveToast");
    el.pagePicker = document.getElementById("pagePicker");
    el.pagePickerBody = document.getElementById("pagePickerBody");
    el.pageToolsMenu = document.getElementById("pageToolsMenu");

    document.getElementById("resetLayoutBtn").addEventListener("click", resetPanelLayout);
    initMerge({
        save: saveCurrentBubble,
        reload: reloadBubbles,
        retranslate: retranslateCurrentBubble,
        select: (index) => selectBubble(index, { scroll: false }),
        rerender: () => { renderBubbleList(); applyZoom(); },
    });
    initSplit({
        save: saveCurrentBubble,
        reload: reloadBubbles,
        retranslateIds: retranslateBubbleIds,
        rerender: () => applyZoom(),
    });
    initLettering({
        select: (index) => selectBubble(index, { scroll: false }),
        rerender: () => { renderBubbleList(); applyZoom(); renderBubbleDetail(); },
        refreshImage: refreshPageImage,
        saveIfChanged: saveIfChanged,
        status: savedStatus,
        navigate: navigateBubble,
        shortcutLabel: (id) => { const c = currentBindings()[id]; return c ? formatCombo(c) : ""; },
        renderReview: renderReviewControls,
    });
    initReview({
        status: savedStatus,
        rerender: () => { renderBubbleList(); applyZoom(); },
        saveIfChanged: saveIfChanged,
    });
    initOcrDebug();            // "OCR debug" button when switched on in Settings > Editor (js/editor-ocrdebug.js)
 
    if (!pageId && !state.projectId) {
        document.querySelector(".main-content").innerHTML = "<p>Missing ?project= or ?page= in the URL.</p>";
        return;
    }
 
    el.processBtn.addEventListener("click", runProcess);
    document.getElementById("promptPrevBtn").addEventListener("click", () => navigateToAdjacentPage(-1));
    document.getElementById("promptNextBtn").addEventListener("click", () => navigateToAdjacentPage(1));
    document.getElementById("excludePromptBtn").addEventListener("click", excludeCurrentPage);
    document.getElementById("showRawBtn").addEventListener("click", () => openOrAlert(state.rawPath));
    document.getElementById("zoomInBtn").addEventListener("click", () => zoomBy(1.25));
    document.getElementById("zoomOutBtn").addEventListener("click", () => zoomBy(0.8));
    document.getElementById("zoomResetBtn").addEventListener("click", () => setZoom(1, { persist: true }));
    document.getElementById("zoomFitWidthBtn").addEventListener("click", fitToWidth);
    document.getElementById("retranslateBtn").addEventListener("click", (e) => runRetranslate(e.shiftKey));
    document.getElementById("reocrBtn").addEventListener("click", runReocr);
    document.getElementById("reinpaintBtn").addEventListener("click", runReinpaint);
    document.getElementById("previewBtn").addEventListener("click", togglePreview);
    document.getElementById("addBubbleBtn").addEventListener("click", () => setDrawMode(state.drawMode === "add" ? false : "add"));
    document.getElementById("cleanAreaBtn").addEventListener("click", () => setDrawMode(state.drawMode === "clean" ? false : "clean"));
    document.getElementById("undoCleanBtn").addEventListener("click", (e) => undoCleanup(e.shiftKey));
    document.getElementById("orderSelect").addEventListener("change", applyAutoOrder);
    el.imageCanvas.addEventListener("mousedown", onDrawStart);
    document.getElementById("prevPageBtn").addEventListener("click", () => goToAdjacentPage(-1));
    document.getElementById("nextPageBtn").addEventListener("click", () => goToAdjacentPage(1));
    document.getElementById("nextToReviewBtn").addEventListener("click", goToNextToReview);
    el.imageViewport.addEventListener("wheel", onWheelZoom, { passive: false });
    window.addEventListener("keydown", onGlobalKeydown);
    // typed text that is not saved yet (the "unsaved" marker): leaving through a link would drop it silently
    window.addEventListener("beforeunload", (event) => {
        if (!state.unsavedId) return;
        event.preventDefault();
        event.returnValue = "";
    });

    restorePanelLayout();
    setupResizers(); 
 
    renderSidebar(state.projectId);
    loadEditorSettings();   // not awaited: only decides whether "Approve to memory" is shown
 
    if (!pageId) {
        await showPagePicker();
    } else {
        await loadPage();
    }
}
 
/* ---------------------------- page loading ---------------------------- */
 
async function loadPage() {
    // Ask for the status FIRST: GET /pages/{id} answers 404 for a page that has no OCR result yet, which is a normal
    // state (pending / queued / failed) but showed up as a red error in the browser console on every such page.
    try {
        const { status } = await api.pages.status(pageId);
        if (status !== "processed") {
            await showProcessPrompt(null);
            return;
        }
    } catch { /* status endpoint unavailable - fall through to the normal load */ }

    let data;
    try {
        data = await api.pages.get(pageId);
    } catch (e) {
        await showProcessPrompt(e);
        return;
    }
 
    state.bubbles = data.bubbles;
    state.detectRotation = !!data.detect_rotation;
    state.cleanups = data.cleanups || [];
    state.bubbleOrder = data.bubble_order || "ttb";
    state.layoutCheckedAt = data.layout_checked_at || null;     // "Layout checked" (js/editor-review.js)
    document.getElementById("orderSelect").value = state.bubbleOrder;
    renderCleanupButton();
    state.projectId = String(data.project_id);
    state.jsonPath = data.json_path;
    state.pageFolder = data.page_folder;
    state.hasLines = !!data.has_lines;
    state.selectedIndex = null;
    state.unsavedId = null;

    el.processPrompt.classList.add("hidden");
    el.pagePicker.classList.add("hidden");
    el.editorLayout.classList.remove("hidden");
 
    renderSidebar(state.projectId);
    renderPageTools();
    await loadChapterNav();
    if (isLettering()) {                // Lettering shows the typeset page: load that one right away
        state.previewBeforeLettering = !!state.preview;
        state.preview = true;
    }
    loadImage();
    if (data.pending_translation) await translatePendingPage(); // prefetched page: translate NOW, with the corrections made since
    renderBubbleList();
    renderBubbleDetail();
    prefetchNextPage();
 
    if (state.bubbles.length && (enterAt === "first" || enterAt === "last")) {
        selectBubble(enterAt === "first" ? 0 : state.bubbles.length - 1, { scroll: false });
    } else if (state.bubbles.length && resumeBubbleIndex !== null) {
        const idx = Math.min(parseInt(resumeBubbleIndex, 10) || 0, state.bubbles.length - 1);
        selectBubble(idx, { scroll: false });
    }
    // Lettering mode on a page that is not fully reviewed switches to Translate (editor-lettering.js)
    await checkReviewedOnLoad();
    offerUndoFromPreviousPage();        // review mode "Next page: accept ...": Undo for the page just left
}
 
/**
 * A prefetched page only has OCR + a clean image. Translating it here (when it is opened) instead of at
 * prefetch time means correction-memory and glossary already contain what the user fixed on the previous page.
 */
async function translatePendingPage() {
    el.bubbleListHeader.textContent = "Translating with your latest corrections...";
    showToast("Translating this page with your latest corrections...", 60000);
    try {
        await api.pages.retranslate(pageId);
        const fresh = await api.pages.get(pageId);
        state.bubbles = fresh.bubbles;
        showToast("Translated", 1500);
    } catch (e) {
        console.error("[EDITOR] Pending translation failed:", e);
        showToast(`Translation failed: ${e.message} - use Retranslate`, 7000);
    }
}

function prefetchNextPage() {
    if (!state.flatPageList.length) return;
    const idx = state.flatPageList.findIndex((p) => String(p.page_id) === String(pageId));
    if (idx === -1) return;
    const next = state.flatPageList[idx + 1];
    if (!next || next.status !== "pending") return;
    api.pages.processAsync(next.page_id, false).catch(() => {}); // OCR + clean image only; translated when opened
}
 
async function showProcessPrompt(error) {
    el.editorLayout.classList.add("hidden");
    el.pagePicker.classList.add("hidden");
    el.processPrompt.classList.remove("hidden");
    el.processBtn.disabled = false;
    el.processStatus.textContent = "";
 
    const backLink = document.getElementById("backToPagesLink");
    if (backLink) backLink.href = state.projectId ? `editor.html?project=${state.projectId}` : "index.html";
 
    // load chapter/page context now too - previously only happened after a
    // successful page load, so both the sidebar nav and this info panel
    // stayed blank while viewing an unprocessed page
    await loadChapterNav();
    renderProcessPromptInfo();
 
    const isNotProcessed = !error || /not.*processed/i.test(error.message || "");
    const msgEl = document.getElementById("processPromptMsg");
 
    if (!isNotProcessed) {
        msgEl.textContent = `Couldn't load this page: ${error.message}`;
        return;
    }
 
    const errEl = document.getElementById("processPromptError");
    errEl.classList.add("hidden");
    try {
        const { status, error: lastError, raw_path } = await api.pages.status(pageId);
        state.rawPath = raw_path;
        if (status === "queued" || status === "processing") {
            msgEl.textContent = "This page is already processing in the background - it'll load automatically...";
            el.processBtn.disabled = true;
            pollUntilProcessed();
            return;
        }
        if (status === "failed") {
            msgEl.textContent = "Processing failed for this page.";
            if (lastError) { errEl.textContent = lastError; errEl.classList.remove("hidden"); }
            return;
        }
    } catch { /* status endpoint failed - fall through to the normal prompt */ }
 
    msgEl.textContent = "This page hasn't been processed yet.";
}
 
function renderProcessPromptInfo() {
    const infoEl = document.getElementById("processPromptInfo");
    if (!infoEl) return;
 
    const current = state.flatPageList.find((p) => String(p.page_id) === String(pageId));
    if (!current) {
        infoEl.innerHTML = "";
        return;
    }
 
    const chapterPages = state.flatPageList.filter((p) => p.chapter === current.chapter);
    const idxInChapter = chapterPages.findIndex((p) => String(p.page_id) === String(pageId));
 
    infoEl.innerHTML = `
        <div class="process-prompt-meta">
            <div><span class="field-label">Chapter</span> ${escapeHtml(current.chapter)}</div>
            <div><span class="field-label">Page</span> ${escapeHtml(current.file_name)} (${idxInChapter + 1} / ${chapterPages.length} in chapter)</div>
            <div><span class="field-label">Status</span> <span class="badge" data-status="${escapeHtml(current.status)}">${escapeHtml(current.status)}</span></div>
        </div>
    `;
}
 
function pollUntilProcessed() {
    clearTimeout(pollUntilProcessed._t);
    pollUntilProcessed._t = setTimeout(async () => {
        try {
            const { status } = await api.pages.status(pageId);
            if (status === "processed") {
                await loadPage();
            } else if (status === "failed") {
                document.getElementById("processPromptMsg").textContent = "Background processing failed - try running it manually.";
                el.processBtn.disabled = false;
            } else {
                pollUntilProcessed();
            }
        } catch {
            pollUntilProcessed();
        }
    }, 2000);
}
 
function renderPageTools() {
    el.pageToolsMenu.innerHTML = `
        <button class="copy-btn" id="copyJsonPathBtn" title="Copy the translation JSON's file path">Copy path</button>
        <button class="copy-btn" id="openFolderBtn" title="Open this page's folder in the file explorer">Open folder</button>
        <button class="copy-btn" id="openJsonBtn" title="Reveal the translation JSON in the file explorer">Open JSON</button>
        <button class="copy-btn danger" id="excludePageBtn" title="Remove this page from the list without deleting the file (for stray/junk scans)">Delete page</button>
    `;
    const more = document.getElementById("toolbarMore");
    if (more && !more.dataset.wired) {           // close the More menu on a click outside it / after picking an entry
        more.dataset.wired = "1";
        document.addEventListener("click", (e) => { if (!more.contains(e.target)) more.open = false; });
        more.querySelector(".toolbar-more-menu").addEventListener("click", (e) => { if (e.target.closest("button")) setTimeout(() => { more.open = false; }, 0); });
    }
    document.getElementById("copyJsonPathBtn").addEventListener("click", async (e) => {
        await navigator.clipboard.writeText(state.jsonPath || "");
        const btn = e.target;
        btn.textContent = "Copied";
        setTimeout(() => { btn.textContent = "Copy path"; }, 1200);
    });
    document.getElementById("openFolderBtn").addEventListener("click", () => openOrAlert(state.pageFolder));
    document.getElementById("openJsonBtn").addEventListener("click", () => openOrAlert(state.jsonPath));
    document.getElementById("excludePageBtn").addEventListener("click", excludeCurrentPage);
}
 
async function excludeCurrentPage() {
    if (!confirm("Remove this page from the list? The image file stays on disk - toggle 'Show excluded' in the page list to undo.")) return;
    try {
        await api.pages.exclude(pageId);
        window.location.href = `editor.html?project=${state.projectId}`;
    } catch (e) {
        alert(`Couldn't exclude page: ${e.message}`);
    }
}
 
 
 








 
 
 
 

 
async function runProcess() {
    el.processBtn.disabled = true;
    el.processStatus.textContent = "Processing — this can take a while, don't close the tab...";
    try {
        const result = await api.pages.process(pageId);
        el.processStatus.textContent = "Done — loading page...";
        await loadPage();
        if (result.translation_error) showToast(result.message, 7000);
    } catch (e) {
        console.error("[EDITOR] Processing failed:", e);
        el.processStatus.textContent = `Failed: ${e.message}`;
        el.processBtn.disabled = false;
    }
}
 
async function runRetranslate(overwriteEdited = false) {
    const warn = overwriteEdited
        ? "Re-translate EVERY bubble, including the ones you edited? Your edits on this page will be overwritten (corrections already in memory stay)."
        : "Re-translate this page? Bubbles you edited and skipped bubbles are kept (Shift+click overwrites edited ones too).";
    if (!confirm(warn)) return;

    const btn = document.getElementById("retranslateBtn");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Retranslating...";

    try {
        const result = await api.pages.retranslate(pageId, overwriteEdited);
        showToast(result.message, 3500);
        await loadPage();
    } catch (e) {
        alert(`Retranslate failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

/** Re-OCR confirmation with the page's "whole page is rotated" choice. Resolves to null (cancelled) or the choice. */
function askReocrOptions() {
    return new Promise((resolve) => {
        const dlg = document.createElement("dialog");
        dlg.className = "import-dialog";
        dlg.style.width = "min(560px, 94vw)";
        dlg.innerHTML = `
            <h2 style="margin:0;">Run OCR + inpainting again on this page?</h2>
            <p class="text-muted" style="margin:0;">Bubbles whose text did not change keep their translation and your edits. New or changed bubbles are translated. This can take a while.</p>
            <label class="checkbox-label" title="Turns on PaddleOCR's whole-image orientation step for this page only (it is off by default because it wrongly turns upright manga strips). Sideways text INSIDE an upright page does not need it.">
                <input type="checkbox" id="reocrRotated" ${state.detectRotation ? "checked" : ""}> The whole page is rotated (scan lying sideways or upside down)
            </label>
            <div class="import-footer">
                <button data-act="cancel">Cancel</button>
                <button class="primary" data-act="ok">Re-OCR</button>
            </div>`;
        let answer = null;
        dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
        dlg.querySelector('[data-act="ok"]').addEventListener("click", () => {
            answer = { detectRotation: dlg.querySelector("#reocrRotated").checked };
            dlg.close();
        });
        dlg.addEventListener("close", () => { dlg.remove(); resolve(answer); });
        document.body.appendChild(dlg);
        dlg.showModal();
    });
}

async function runReocr() {
    const options = await askReocrOptions();
    if (!options) return;
    const btn = document.getElementById("reocrBtn");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Re-OCR...";
    try {
        const result = await api.pages.reocr(pageId, options.detectRotation);
        showToast(result.message, 5000);
        await loadPage();
    } catch (e) {
        alert(`Re-OCR failed: ${e.message}\n(The previous result is untouched.)`);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
}

async function runReinpaint() {
    try {
        const result = await api.pages.reinpaint(pageId);
        refreshPageImage();
        showToast(result.message);
    } catch (e) {
        if (/line data/i.test(e.message) && confirm(`${e.message}\n\nRun Re-OCR now?`)) await runReocr();
        else if (!/line data/i.test(e.message)) alert(`Rebuild failed: ${e.message}`);
    }
}

/** Clean page, or - with "Preview text" on - the page with the translation typeset in (rendered by the server). */
function pageImageSrc(bust = false) {
    const base = state.preview ? api.pages.renderUrl(pageId) : api.pages.imageUrl(pageId);
    return bust ? `${base}${base.includes("?") ? "&" : "?"}t=${Date.now()}` : base;
}

function refreshPageImage() {
    el.pageImage.src = pageImageSrc(true);
}

function togglePreview() {
    if (isLettering()) {
        showToast("Lettering always shows the text on the page - switch to Translate for the clean page", 3000);
        return;
    }
    state.preview = !state.preview;
    document.getElementById("previewBtn").classList.toggle("primary", state.preview);
    refreshPageImage();
    showToast(state.preview ? "Preview: translation typeset into the bubbles" : "Showing the clean page", 2500);
}

/* --------------------------- chapter / page nav ------------------------- */
 
async function loadChapterNav() {
    if (!state.projectId) return;
    let pages;
    try {
        pages = await api.pages.listByProject(state.projectId);
    } catch (e) {
        el.chapterNavBody.innerHTML = `<p class="sidebar-error">Couldn't load pages.</p>`;
        return;
    }
 
    state.flatPageList = [...pages].sort((a, b) => {
        const chapterCmp = compareChapterNames(a.chapter, b.chapter);
        return chapterCmp !== 0 ? chapterCmp : a.order - b.order;
    });
 
    const byChapter = groupByChapter(pages);
 
    el.chapterNavBody.innerHTML = "";
    for (const [chapter, chapterPages] of byChapter) {
        const label = document.createElement("div");
        label.className = "chapter-group-label";
        label.textContent = chapter;
        el.chapterNavBody.appendChild(label);
 
        chapterPages.sort((a, b) => a.order - b.order);
        chapterPages.forEach((p) => {
            const row = document.createElement("a");
            row.href = `editor.html?project=${state.projectId}&page=${p.page_id}`;
            row.className = "page-nav-row" + (String(p.page_id) === String(pageId) ? " selected" : "");
            const stage = shownStage(p.stage || p.status, p.layout);
            const [label, what] = PAGE_BADGES[stage] || [stage, ""];
            row.title = what;
            row.innerHTML = `${escapeHtml(p.file_name)} <span class="badge stage-badge" data-status="${escapeHtml(stage)}">${escapeHtml(label)}</span>`;
            el.chapterNavBody.appendChild(row);
        });
    }
 
    const current = pages.find((p) => String(p.page_id) === String(pageId));
    if (current) el.pageLabel.textContent = `${current.chapter} / ${current.file_name}`;
}
 
/* -------------------------------- image -------------------------------- */
 
function zoomStorageKey() {
    return `okt-zoom-project-${state.projectId}`;
}
 
function lastEditStorageKey(projectId) {
    return `okt-lastedit-project-${projectId}`;
}
 
function recordLastEdit() {
    if (!state.projectId || state.selectedIndex === null) return;
    try {
        localStorage.setItem(lastEditStorageKey(state.projectId), JSON.stringify({
            pageId, bubbleIndex: state.selectedIndex,
        }));
    } catch { /* localStorage unavailable - not critical */ }
}
 
function loadImage() {
    el.imageCanvas.classList.add("hidden");
    el.imageState.classList.remove("hidden");
    el.imageState.innerHTML = `<p>Loading page image...</p>`;
 
    const img = el.pageImage;
    img.onload = () => {
        state.naturalW = img.naturalWidth;
        state.naturalH = img.naturalHeight;
        state.baseScale = el.imageViewport.clientHeight / state.naturalH;
 
        const remembered = parseFloat(localStorage.getItem(zoomStorageKey()));
        state.zoomFactor = Number.isFinite(remembered) ? remembered : 1;
 
        el.imageState.classList.add("hidden");
        el.imageCanvas.classList.remove("hidden");
        applyZoom();
    };
    img.onerror = () => {
        el.imageState.innerHTML = `
            <p>Couldn't load the page image.</p>
            <button id="retryImageBtn">Retry</button>
        `;
        document.getElementById("retryImageBtn").addEventListener("click", loadImage);
    };
    img.src = pageImageSrc();
}
 
function onWheelZoom(event) {
    if (!event.ctrlKey && !event.metaKey) return; // plain scroll = native pan, left alone
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? 1.1 : 0.9);
}
 
function zoomBy(factor) {
    setZoom(state.zoomFactor * factor, { persist: true });
}
 
function fitToWidth() {
    if (!state.naturalW) return;
    const targetTotalWidth = el.imageViewport.clientWidth * 0.8;
    const currentUnzoomedWidth = state.naturalW * state.baseScale;
    setZoom(targetTotalWidth / currentUnzoomedWidth, { persist: true });
}
 
function setZoom(factor, { persist = false } = {}) {
    state.zoomFactor = Math.min(20, Math.max(0.3, factor));
    applyZoom();
    if (persist) localStorage.setItem(zoomStorageKey(), String(state.zoomFactor));
}
 
function applyZoom() {
    if (!state.naturalW) return;
    const scale = state.baseScale * state.zoomFactor;
    const w = state.naturalW * scale;
    const h = state.naturalH * scale;
    el.imageCanvas.style.width = `${w}px`;
    el.imageCanvas.style.height = `${h}px`;
    el.zoomLabel.textContent = `${Math.round(state.zoomFactor * 100)}%`;
    renderBubbleOutlines(scale);
    renderOcrDebug();                   // OCR debug view on top (labels follow the zoom)
}
 
function bubbleRect(bubble, scale) {
    const xs = bubble.box.map((p) => p[0]);
    const ys = bubble.box.map((p) => p[1]);
    const x1 = Math.min(...xs), x2 = Math.max(...xs);
    const y1 = Math.min(...ys), y2 = Math.max(...ys);
    return { x: x1 * scale, y: y1 * scale, w: (x2 - x1) * scale, h: (y2 - y1) * scale };
}
 
function renderBubbleOutlines(scale) {
    el.imageCanvas.querySelectorAll(".bubble-outline").forEach((n) => n.remove());
    if (isLettering()) {                // Lettering mode: the text areas instead of the OCR boxes (editor-lettering.js)
        renderLetteringOverlay(scale);
        return;
    }
    el.imageCanvas.querySelectorAll(".letter-area").forEach((n) => n.remove());
    state.bubbles.forEach((bubble, index) => {
        const rect = bubbleRect(bubble, scale);
        const box = document.createElement("div");
        box.className = "bubble-outline" + (index === state.selectedIndex ? " selected" : "") + (isMergePick(bubble.bubble_id) ? " merge-pick" : "");
        box.style.left = `${rect.x}px`;
        box.style.top = `${rect.y}px`;
        box.style.width = `${rect.w}px`;
        box.style.height = `${rect.h}px`;
        box.title = `Bubble ${index + 1} (Shift+click: merge with the selected bubble)`;
        const badge = document.createElement("span");
        badge.className = "bubble-index";
        badge.textContent = String(index + 1);
        box.appendChild(badge);
        box.addEventListener("click", (e) => (e.shiftKey ? toggleMergePick(index) : selectBubble(index, { scroll: false })));
        el.imageCanvas.appendChild(box);
    });
    renderSplitOverlay(scale);          // Split mode: the selected bubble's lines on top (editor-split.js)
}
 
function scrollToBubble(index) {
    const bubble = state.bubbles[index];
    if (!bubble) return;
    const scale = state.baseScale * state.zoomFactor;
    const rect = bubbleRect(bubble, scale);
    const targetLeft = rect.x + rect.w / 2 - el.imageViewport.clientWidth / 2;
    const targetTop = rect.y + rect.h / 2 - el.imageViewport.clientHeight / 2;
    el.imageViewport.scrollTo({ left: targetLeft, top: targetTop, behavior: "smooth" });
}
 
/* ------------------------------ bubble list ----------------------------- */
 
/*
 * Review status of a bubble, shown as a marker in the list (legend: Help > "Bubble list markers"). Read from the page
 * data, so it survives a reload: `translation` only changes when the user saves (the backend writes ai_translation =
 * translation when it translates), and the backend stamps confirmed_translation on every save / move-on.
 *   unsaved  - the open textarea differs from the saved text (saved by Save, Ctrl+Enter or moving to another bubble)
 *   edited   - the saved text differs from the AI draft (also stored in the correction memory unless too short)
 *   accepted - the AI text was kept: you moved on without changing it (page JSON only, never memory)
 *   null     - not looked at yet, or retranslated since
 */
const STATUS_MARKS = {
    unsaved: "Unsaved changes - saved by Save, Ctrl+Enter or moving to another bubble",
    edited: "Edited by you - your saved text differs from the AI draft",
    accepted: "Accepted - you moved on without changing the AI text (not stored in memory)",
};

/** What is stored for the bubble: "edited" | "accepted" | null (typing in progress does not count). */
function savedStatus(bubble) {
    const text = bubble.translation || "";
    if (bubble.skip || !text) return null;
    if (text !== (bubble.ai_translation || "")) return "edited";
    return isConfirmed(bubble) ? "accepted" : null;
}

function bubbleStatus(bubble) {
    if (!bubble.skip && state.unsavedId === bubble.bubble_id) return "unsaved";
    return savedStatus(bubble);
}

function isConfirmed(bubble) {
    return !!bubble.confirmed_at && bubble.confirmed_translation === (bubble.translation || "");
}

function statusMarkHtml(status) {
    return status ? `<span class="status-mark ${status}" title="${STATUS_MARKS[status]}"></span>` : "";
}

/** Swap only one row's marker (typing must not rebuild the whole list on every key). */
function refreshRowMark(index) {
    const row = el.bubbleList.children[index];
    const bubble = state.bubbles[index];
    if (!row || !bubble) return;
    row.querySelector(".status-mark")?.remove();
    row.insertAdjacentHTML("beforeend", statusMarkHtml(bubbleStatus(bubble)));
}

/** Header: bubble count + how many are edited / accepted, and the "?" that opens the marker legend. */
function renderBubbleListHeader() {
    const counts = { edited: 0, accepted: 0 };
    state.bubbles.forEach((b) => {
        const s = savedStatus(b);
        if (s) counts[s] += 1;
    });
    const open = state.bubbles.filter((b) => !b.skip).length - counts.edited - counts.accepted;
    el.bubbleListHeader.innerHTML = `
        <span>Bubbles — ${state.bubbles.length}</span>
        <span class="bubble-list-counts">
            <span title="${STATUS_MARKS.edited}"><span class="status-mark edited"></span>${counts.edited}</span>
            <span title="${STATUS_MARKS.accepted}"><span class="status-mark accepted"></span>${counts.accepted}</span>
            <span title="Not reviewed yet (no marker)">${open} left</span>
        </span>
        <button type="button" class="help-chip" id="markersHelpBtn" title="What do the markers mean? (Help)">?</button>`;
    document.getElementById("markersHelpBtn").addEventListener("click", () => window.oktHelp?.open("markers"));
    renderReviewControls(document.getElementById("reviewSlot"));   // "Accept page (N)" follows the markers
}

function renderBubbleList() {
    renderBubbleListHeader();
    el.bubbleList.innerHTML = "";
    state.bubbles.forEach((bubble, index) => {
        const isSkipped = !!bubble.skip;
        const isEmpty = !isSkipped && !bubble.translation && !bubble.ai_translation;
        const textFixed = bubble.text_raw && bubble.text_raw !== bubble.text;
        const row = document.createElement("div");
        row.className = "bubble-row" + (index === state.selectedIndex ? " selected" : "") + (isSkipped ? " skipped" : "")
            + (isMergePick(bubble.bubble_id) ? " merge-pick" : "");
        const preview = isSkipped
            ? `skipped (${escapeHtml(bubble.skip_reason || "")}): ${escapeHtml(bubble.text || "")}`
            : isEmpty ? '<span style="color: var(--danger);">(no AI translation)</span>' : escapeHtml(bubble.translation || bubble.text || "(empty)");
        row.innerHTML = `
            <span class="bubble-num">${index + 1}</span>
            <span class="bubble-preview">${preview}</span>
            ${bubble.missed_text ? '<span class="bubble-tag warn" title="Dark pixels inside this bubble are not covered by the OCR text - some text may be missing. Try Re-OCR bubble.">check text</span>' : ""}
            ${bubble.refined ? '<span class="bubble-tag" title="This bubble was read again as a whole because the first pass missed lines">re-read</span>' : ""}
            ${textFixed ? `<span class="bubble-tag" title="Source text was cleaned / edited. Raw OCR: ${escapeHtml(bubble.text_raw)}">text fixed</span>` : ""}
            ${bubble.split_check && !isSkipped ? '<span class="bubble-tag warn" title="Part of a split bubble whose translation could not be divided cleanly - check it (saving or retranslating clears this)">check translation</span>' : ""}
            ${statusMarkHtml(bubbleStatus(bubble))}
        `;
        row.addEventListener("click", (e) => (e.shiftKey ? toggleMergePick(index) : selectBubble(index)));
        el.bubbleList.appendChild(row);
    });
}
 
 
/* ------------------------------ bubble detail ---------------------------- */
 
async function selectBubble(index, { scroll = true, save = true } = {}) {
    if (save && state.selectedIndex !== null && state.selectedIndex !== index) {
        const ok = await saveCurrentBubble();
        if (!ok) return; // save failed or in-flight — don't jump away
    }
    if (state.selectedIndex !== index) {
        state.mergePicks = [];          // picks belong to the bubble they would be merged into
        cancelSplit();                  // so do the lines picked for a split
    }
    state.selectedIndex = index;
    renderBubbleList();
    applyZoom();
    renderBubbleDetail();
    renderMergeBar();
    if (scroll) scrollToBubble(index);
}
 
function renderBubbleDetail() {
    if (isLettering()) {                // Lettering mode has its own panel (editor-lettering.js)
        renderLetteringPanel();
        return;
    }
    if (state.selectedIndex === null) {
        el.bubbleDetail.classList.remove("hidden");
        // same place for the arrows as with a bubble open (bottom of the panel): muscle memory finds them
        el.bubbleDetail.innerHTML = `
            <p class="text-muted detail-empty">${state.bubbles.length ? "Pick a bubble in the list or on the page." : "No text on this page."}</p>
            <div class="detail-actions detail-bottom" style="justify-content:flex-end;">
                <div class="detail-nav-actions">
                    <button id="prevBtn" class="nav-crosses-page" title="Previous page">\u23EE</button>
                    <button id="nextBtn" class="nav-crosses-page" title="Next page">\u23ED</button>
                </div>
            </div>
        `;
        document.getElementById("prevBtn").addEventListener("click", () => goToAdjacentPage(-1));
        document.getElementById("nextBtn").addEventListener("click", () => goToAdjacentPage(1));
        return;
    }
 
    const bubble = state.bubbles[state.selectedIndex];
    const isFirst = state.selectedIndex === 0;
    const isLast = state.selectedIndex === state.bubbles.length - 1;
    if (state.unsavedId) {
        // the textarea is rebuilt from the saved text below, so whatever was typed before is no longer pending
        const stale = state.bubbles.findIndex((b) => b.bubble_id === state.unsavedId);
        state.unsavedId = null;
        refreshRowMark(stale);
    }
    const isSkipped = !!bubble.skip;
    const isEmpty = !isSkipped && !bubble.translation && !bubble.ai_translation;
    const textFixed = bubble.text_raw && bubble.text_raw !== bubble.text;
    const canSplit = state.hasLines && (bubble.line_count || 0) >= 2;
    const skipWhy = { number: "only digits (page number?)", symbols: "only symbols / punctuation", manual: "skipped by you", empty: "empty text" }[bubble.skip_reason] || bubble.skip_reason || "";
 
    el.bubbleDetail.classList.remove("hidden");
    el.bubbleDetail.innerHTML = `
        <div class="bubble-detail-title">
            <span>Bubble ${state.selectedIndex + 1} of ${state.bubbles.length}</span>
            <button id="deleteBubbleBtn" class="icon-btn danger" title="Delete this bubble from the page (use for OCR false positives)">Delete</button>
        </div>
 
 
        <div>
            <div class="source-head">
                <label>Original text</label>
                <span style="display:flex; gap:6px;">
                    ${textFixed ? '<button id="undoCleanupBtn" class="copy-btn" title="Put the raw OCR string back (the automatic cleanup was wrong)">Use raw OCR</button>' : ""}
                    <button id="editSourceBtn" class="copy-btn" title="Fix an OCR mistake in the source text">Edit</button>
                </span>
            </div>
            <div class="source-text" id="sourceView">${escapeHtml(bubble.text || "")}</div>
            ${textFixed ? `<div class="source-raw">Raw OCR: ${escapeHtml(bubble.text_raw)}</div>` : ""}
            <div class="source-edit hidden" id="sourceEdit">
                <textarea id="sourceInput" rows="3" spellcheck="false">${escapeHtml(bubble.text || "")}</textarea>
                <div class="detail-actions">
                    <button id="saveSourceBtn" class="primary">Save source</button>
                    <button id="cancelSourceBtn">Cancel</button>
                </div>
            </div>
        </div>

        <div>
            <label>Translated text${isSkipped ? ` <span class="bubble-tag warn" title="Not translated; the original pixels stay on the page">skipped - ${escapeHtml(skipWhy)}</span>` : ""}</label>
            <textarea id="translationInput" rows="4" lang="pl" spellcheck="${isSpellcheckEnabled()}" ${isSkipped ? "disabled" : ""}
                placeholder="${isSkipped ? "Skipped - press Translate this below to translate it" : isEmpty ? "The AI returned no translation for this bubble (see the backend log) - type your own (saved as written from scratch) or press Retranslate" : ""}">${escapeHtml(bubble.translation || "")}</textarea>
        </div>
        <div id="memoryUsedPanel" class="memory-used"></div>

        <!-- every button is always there, in the same place (disabled where it does not apply): muscle memory -->
        <div class="detail-tools">
            <button id="resetBtn" ${isSkipped ? "disabled" : ""} title="Revert to the AI's original translation (does not save)">Reset to AI</button>
            <button id="undoBtn" ${isSkipped ? "disabled" : ""} title="Revert to the value shown when this bubble was opened">Undo</button>
            <button id="retranslateBubbleBtn" ${isSkipped ? "disabled" : ""} title="Translate only this bubble again (neighbours are sent as context; memory + glossary apply)">Retranslate</button>
            <button id="reocrBubbleBtn" title="Read this bubble again as ONE crop of its whole interior - finds lines the page pass missed. The previous text stays available under 'Use raw OCR'.">Re-OCR bubble</button>
            <button id="reocrAreaBtn" title="Draw the area yourself - for a bubble whose detected box misses a line (e.g. text below the box). That exact area is read.">Set area &amp; re-OCR</button>
            <button id="moveUpBtn" ${isFirst ? "disabled" : ""} title="Move this bubble one place earlier in the reading order">Move up</button>
            <button id="moveDownBtn" ${isLast ? "disabled" : ""} title="Move this bubble one place later in the reading order">Move down</button>
            <button id="mergePrevBtn" ${isFirst ? "disabled" : ""} title="Join this bubble with the one above it (the OCR cut one speech bubble into pieces)">Merge with &uarr;</button>
            <button id="mergeNextBtn" ${isLast ? "disabled" : ""} title="Join this bubble with the one below it">Merge with &darr;</button>
            <button id="splitBtn" ${canSplit ? "" : "disabled"} title="${canSplit ? "The OCR glued two bubbles (or map labels) together: pick the lines of the new bubble on the page. The translation is divided too - no new translation unless you ask" : "Only for a bubble with at least two text lines"}">Split...</button>
            ${isSkipped
                ? '<button id="unskipBtn" class="primary" title="Translate this bubble anyway (its text will be erased from the image)">Translate this</button>'
                : '<button id="skipBtn" title="Do not translate this bubble and put the original pixels back (page numbers, junk)">Skip</button>'}
            <button id="spellcheckToggleBtn" ${isSkipped ? "disabled" : ""} class="${isSpellcheckEnabled() ? "primary" : ""}" title="Toggle Polish spellcheck">Spellcheck: ${isSpellcheckEnabled() ? "On" : "Off"}</button>
        </div>

        <div class="detail-actions detail-bottom" style="align-items:center;">
            <button id="saveBtn" class="primary" ${isSkipped ? "disabled" : ""} title="Save this translation (Ctrl/Cmd+Enter also works while typing)">Save</button>
            ${state.storeApproved ? `<button id="approveBtn" ${isSkipped ? "disabled" : ""} title="Store this UNCHANGED translation in the correction memory as an approved example (Settings: memory_store_approved). Moving on never does that.">Approve to memory</button>` : ""}
            <span id="saveInlineStatus" style="font-size:12px;"></span>
            <div class="detail-nav-actions" style="margin-left:auto;">
                <button id="prevBtn" class="${isFirst ? "nav-crosses-page" : ""}"
                    title="${isFirst ? "Go to the previous page (saves this bubble first)" : "Previous bubble — saves this one first if changed (Left arrow)"}">${isFirst ? "\u23EE" : "\u2190"}</button>
                <button id="nextBtn" class="${isLast ? "nav-crosses-page" : ""}"
                    title="${isLast ? "Go to the next page (saves this bubble first)" : "Next bubble — saves this one first if changed (Right arrow)"}">${isLast ? "\u23ED" : "\u2192"}</button>
            </div>
        </div>
    `;

    document.getElementById("deleteBubbleBtn").addEventListener("click", () => deleteBubble());
    document.getElementById("prevBtn").addEventListener("click", () => navigateBubble(-1));
    document.getElementById("nextBtn").addEventListener("click", () => navigateBubble(1));

    // ---- source text: edit / use raw OCR ----
    const sourceEdit = document.getElementById("sourceEdit");
    document.getElementById("editSourceBtn").addEventListener("click", () => {
        sourceEdit.classList.toggle("hidden");
        document.getElementById("sourceInput").focus();
    });
    document.getElementById("cancelSourceBtn").addEventListener("click", () => sourceEdit.classList.add("hidden"));
    document.getElementById("saveSourceBtn").addEventListener("click", () => saveSourceText(document.getElementById("sourceInput").value));
    document.getElementById("undoCleanupBtn")?.addEventListener("click", () => saveSourceText(bubble.text_raw));

    // tools that also work on a skipped bubble (wrong box, glued / cut bubbles)
    document.getElementById("reocrBubbleBtn").addEventListener("click", reocrCurrentBubble);
    document.getElementById("reocrAreaBtn").addEventListener("click", () => setDrawMode("reocr", bubble.bubble_id));
    document.getElementById("moveUpBtn").addEventListener("click", () => moveCurrentBubble(-1));
    document.getElementById("moveDownBtn").addEventListener("click", () => moveCurrentBubble(1));
    document.getElementById("mergePrevBtn").addEventListener("click", () => mergeCurrentBubble(-1));
    document.getElementById("mergeNextBtn").addEventListener("click", () => mergeCurrentBubble(1));
    document.getElementById("splitBtn").addEventListener("click", startSplit);
    if (isSkipped) {
        document.getElementById("unskipBtn").addEventListener("click", () => setSkip(false));
        document.getElementById("memoryUsedPanel").innerHTML = '<div class="field-label text-muted">Not translated - the original pixels stay on the page.</div>';
        return;
    }

    const textarea = document.getElementById("translationInput");
    const loadedValue = bubble.translation || "";
    // live "unsaved" marker on this bubble's row while the text differs from what is saved
    const updateUnsaved = () => {
        const id = textarea.value.trim() !== loadedValue ? bubble.bubble_id : null;
        if (state.unsavedId === id) return;
        state.unsavedId = id;
        refreshRowMark(state.bubbles.indexOf(bubble));
    };

    autoGrowTextarea(textarea);
    textarea.addEventListener("input", () => { autoGrowTextarea(textarea); updateUnsaved(); });

    document.getElementById("spellcheckToggleBtn").addEventListener("click", (e) => {
    const next = !isSpellcheckEnabled();
    setSpellcheckEnabled(next);
    textarea.spellcheck = next;
    e.target.textContent = `Spellcheck: ${next ? "On" : "Off"}`;
    e.target.classList.toggle("primary", next);
    });
 
    document.getElementById("resetBtn").addEventListener("click", () => {
        textarea.value = bubble.ai_translation || "";
        updateUnsaved();
    });
    document.getElementById("undoBtn").addEventListener("click", () => {
        textarea.value = loadedValue;
        updateUnsaved();
    });
    document.getElementById("retranslateBubbleBtn").addEventListener("click", () => retranslateCurrentBubble());
    document.getElementById("skipBtn").addEventListener("click", () => setSkip(true));
    document.getElementById("saveBtn").addEventListener("click", () => saveCurrentBubble());
    document.getElementById("approveBtn")?.addEventListener("click", () => approveCurrentBubble());
 
    // Ctrl+Enter (shortcut "save") is handled by onGlobalKeydown - also while typing here
    loadMemoryUsed(bubble.bubble_id);
}

/* ------------- "Used from memory": what the memory gave the AI for this bubble (links open the Memory page) ------------- */

async function loadMemoryUsed(bubbleId) {
    const panel = document.getElementById("memoryUsedPanel");
    if (!panel || !pageId) return;
    let used;
    try {
        used = await api.corrections.usedFor(pageId, bubbleId);
    } catch (e) {
        panel.innerHTML = "";
        return;
    }
    const current = state.selectedIndex !== null ? state.bubbles[state.selectedIndex] : null;
    const target = document.getElementById("memoryUsedPanel");
    if (!target || !current || current.bubble_id !== bubbleId) return;     // the user moved on meanwhile

    const entries = used.entries || [];
    const glossary = used.glossary || [];
    if (!entries.length && !glossary.length) {
        const why = used.stale
            ? "the source text changed after the last translation (merge / split / edited text) - Retranslate shows what memory gives now"
            : "nothing for this AI draft";
        target.innerHTML = `<div class="field-label">Used from memory: <span class="text-muted">${why}</span></div>`;
        return;
    }
    const laneNote = used.lane === "exact" ? "exact line (reused verbatim unless grammar requires otherwise)" : "similar lines (examples)";
    const rows = entries.map((e) => {
        const sim = e.similarity != null ? `${Math.round(e.similarity * 100)}%` : "";
        const changed = e.exists && e.pl_now && e.pl_now !== e.pl_shown;
        const link = e.exists
            ? `<a href="${memoryEntryUrl(state.projectId, e.correction_id)}" target="_blank" rel="noopener" title="Open this entry on the Memory page (new tab) - edit or delete it there">open in memory &#8599;</a>`
            : `<span class="text-muted" title="This memory entry was deleted after the translation">deleted</span>`;
        return `
            <div class="memory-used-row">
                ${sim ? `<span class="badge" title="Similarity to this bubble's source">${sim}</span>` : ""}
                ${e.kind ? `<span class="badge">${escapeHtml(e.kind)}</span>` : ""}
                <span class="memory-used-text">
                    <span class="text-muted">${escapeHtml(e.en || "")}</span><br>
                    &rarr; ${escapeHtml(changed ? e.pl_now : (e.pl_shown || e.pl_now || ""))}
                    ${changed ? `<span class="text-muted" title="The model was given the old text">(model saw: ${escapeHtml(e.pl_shown)})</span>` : ""}
                </span>
                ${link}
            </div>`;
    }).join("");
    const terms = glossary.length
        ? `<div class="memory-used-row text-muted">Glossary: ${glossary.map((g) => `${escapeHtml(g.source_term)} &rarr; ${escapeHtml(g.target_term)}`).join(", ")}</div>`
        : "";
    target.innerHTML = `
        <details class="memory-used-box">
            <summary class="field-label">Used from memory: ${entries.length} ${entries.length ? `&middot; ${laneNote}` : ""}${glossary.length ? ` &middot; ${glossary.length} glossary term(s)` : ""}</summary>
            ${rows}${terms}
        </details>`;
}

/* ---------------- per-bubble source edit / skip / retranslate ---------------- */

function replaceCurrentBubble(updated) {
    state.bubbles[state.selectedIndex] = updated;
    renderBubbleList();
    renderBubbleDetail();
}

async function saveSourceText(newText) {
    const bubble = state.bubbles[state.selectedIndex];
    const text = (newText || "").trim();
    if (!text) { alert("Source text can't be empty (use Skip or Delete instead)."); return; }
    if (text === bubble.text) { renderBubbleDetail(); return; }
    try {
        const result = await api.pages.patchBubble(pageId, bubble.bubble_id, { text });
        replaceCurrentBubble(result.bubble);
        if (result.reinpainted) refreshPageImage();
        showToast("Source text saved");
        if (!result.bubble.skip && confirm("Source text changed. Retranslate this bubble now?")) await retranslateCurrentBubble(true);
    } catch (e) {
        alert(`Couldn't save source text: ${e.message}`);
    }
}

async function setSkip(skip) {
    const bubble = state.bubbles[state.selectedIndex];
    if (skip && (bubble.translation || "").trim() && !confirm("Skip this bubble? Its translation stays in the file but it will not be translated, and the original pixels come back on the page.")) return;
    try {
        const result = await api.pages.patchBubble(pageId, bubble.bubble_id, { skip });
        replaceCurrentBubble(result.bubble);
        if (result.reinpainted) refreshPageImage();
        else if (result.needs_reocr) showToast("Saved, but this older page can't rebuild the image - run Re-OCR to update it.", 6000);
        if (!skip && !(result.bubble.translation || "").trim()) await retranslateCurrentBubble(true);
        else showToast(skip ? "Bubble skipped" : "Bubble will be translated");
    } catch (e) {
        alert(`Couldn't change skip: ${e.message}`);
    }
}

/** After a server-side bubble edit: fetch the bubbles again (ids / order / flags changed) and select `selectId`. */
async function reloadBubbles(selectId) {
    const fresh = await api.pages.get(pageId);
    state.bubbles = fresh.bubbles;
    state.hasLines = !!fresh.has_lines;
    state.layoutCheckedAt = fresh.layout_checked_at || null;     // a Re-OCR writes a new page JSON without it
    const idx = state.bubbles.findIndex((b) => b.bubble_id === selectId);
    state.selectedIndex = idx === -1 ? null : idx;
    renderBubbleList();
    renderBubbleDetail();
    applyZoom();
    refreshPageImage();
    renderMergeBar();
    invalidateOcrDebug();                             // a Re-OCR / bubble edit changed the readings
    if (isLettering()) await enterCurrentMode();      // text areas follow the new bubbles
}

async function reocrCurrentBubble() {
    const bubble = state.bubbles[state.selectedIndex];
    const btn = document.getElementById("reocrBubbleBtn");
    if (btn) { btn.disabled = true; btn.textContent = "Reading..."; }
    try {
        const result = await api.pages.reocrBubble(pageId, bubble.bubble_id);
        await reloadBubbles(bubble.bubble_id);
        showToast(result.text_changed ? "Bubble re-read - text changed" : "Bubble re-read - same text");
        if (result.text_changed && !result.bubble.skip && (result.bubble.translation || "").trim()
            && confirm("The source text changed. Retranslate this bubble now?")) await retranslateCurrentBubble(true);
    } catch (e) {
        alert(`Re-OCR bubble failed: ${e.message}`);
        renderBubbleDetail();
    }
}

/** "Merge with ↑ / ↓": the list neighbour, through the same preview dialog as Shift+click picks (editor-merge.js). */
async function mergeCurrentBubble(direction) {
    const other = state.bubbles[state.selectedIndex + direction];
    if (other) await openMergeDialog([other.bubble_id]);
}

async function moveCurrentBubble(direction) {
    const index = state.selectedIndex;
    const target = index + direction;
    if (target < 0 || target >= state.bubbles.length) return;
    const order = state.bubbles.map((b) => b.bubble_id);
    [order[index], order[target]] = [order[target], order[index]];
    try {
        await api.pages.reorderBubbles(pageId, { order });
        await reloadBubbles(state.bubbles[index].bubble_id);
    } catch (e) {
        alert(`Reorder failed: ${e.message}`);
    }
}

const ORDER_NAMES = { ttb: "top to bottom", rtl: "right to left", ltr: "left to right" };

/**
 * The order select: a mode sorts this page at once (Undo in the message puts the previous order back); the last two
 * entries apply the page's current mode to every processed page of the chapter / project (after a confirmation).
 */
async function applyAutoOrder() {
    const select = document.getElementById("orderSelect");
    const choice = select.value;
    const keep = state.selectedIndex === null ? null : state.bubbles[state.selectedIndex].bubble_id;
    if (choice === "chapter" || choice === "project") {
        select.value = state.bubbleOrder;
        const where = choice === "chapter" ? "every processed page of this chapter" : "every processed page of the WHOLE project";
        if (!confirm(`Sort ${where} ${ORDER_NAMES[state.bubbleOrder]}?\n\nManual Move up / down changes on those pages are replaced. Translations are not changed.`)) return;
        try {
            const { pages } = await api.pages.reorderBubbles(pageId, { auto: state.bubbleOrder, scope: choice });
            await reloadBubbles(keep);
            showToast(`Sorted ${pages} page(s) ${ORDER_NAMES[state.bubbleOrder]}`, 3000);
        } catch (e) {
            alert(`Sort failed: ${e.message}`);
        }
        return;
    }
    const previous = state.bubbles.map((b) => b.bubble_id);
    const previousMode = state.bubbleOrder;
    try {
        await api.pages.reorderBubbles(pageId, { auto: choice });
        state.bubbleOrder = choice;
        await reloadBubbles(keep);
        const changed = state.bubbles.some((b, i) => b.bubble_id !== previous[i]);
        showToast(changed ? `Sorted ${ORDER_NAMES[choice]} - check the numbers on the page` : `Already ${ORDER_NAMES[choice]}`, 2500,
            changed ? { label: "Undo", run: async () => {
                await api.pages.reorderBubbles(pageId, { order: previous });
                state.bubbleOrder = previousMode;
                select.value = previousMode;
                await reloadBubbles(keep);
            } } : null);
    } catch (e) {
        select.value = previousMode;
        alert(`Sort failed: ${e.message}`);
    }
}

/* ------------------------------ add a bubble by drawing ------------------------------ */

/**
 * mode: false | "add" (new bubble from the drawn area) | "reocr" (read the drawn area as the selected bubble's text)
 *       | "clean" (repaint a leftover trace - stored with the page, see cleanArea).
 */
function setDrawMode(mode, targetId = null) {
    state.drawMode = mode;
    state.drawTarget = mode === "reocr" ? targetId : null;
    el.imageCanvas.classList.toggle("draw-mode", !!mode);
    document.getElementById("addBubbleBtn").classList.toggle("primary", mode === "add");
    document.getElementById("cleanAreaBtn").classList.toggle("primary", mode === "clean");
    const hints = {
        reocr: "Drag a rectangle around ALL the text of this bubble (Esc cancels)",
        clean: "Drag a rectangle over the leftover trace - keep it inside the bubble / plain area (Esc cancels)",
    };
    if (mode) showToast(hints[mode] || "Drag a rectangle over the text (Esc cancels)", 3500);
}

/* ------------------------------ manual cleanup of leftover traces ------------------------------ */

function renderCleanupButton() {
    const btn = document.getElementById("undoCleanBtn");
    const n = (state.cleanups || []).length;
    btn.classList.toggle("hidden", !n);
    btn.textContent = n > 1 ? `Undo clean (${n})` : "Undo clean";
}

async function cleanArea(box) {
    showToast("Cleaning the area...", 20000);
    try {
        const result = await api.pages.addCleanup(pageId, box);
        state.cleanups = result.cleanups;
        renderCleanupButton();
        refreshPageImage();
        showToast("Area cleaned - Undo clean takes it back");
    } catch (err) {
        showToast(`Clean area failed: ${err.message}`, 6000);
    }
}

async function undoCleanup(all) {
    if (all && !confirm(`Remove all ${state.cleanups.length} cleaned areas of this page?`)) return;
    try {
        const result = await api.pages.removeCleanups(pageId, all);
        state.cleanups = result.cleanups;
        renderCleanupButton();
        refreshPageImage();
        showToast(all ? "Cleaned areas removed" : "Last cleaned area removed");
    } catch (err) {
        showToast(`Undo clean failed: ${err.message}`, 6000);
    }
}

function onDrawStart(event) {
    if (!state.drawMode || event.button !== 0) return;
    const mode = state.drawMode;
    const targetId = state.drawTarget;
    event.preventDefault();
    const canvasRect = el.imageCanvas.getBoundingClientRect();
    const startX = event.clientX - canvasRect.left;
    const startY = event.clientY - canvasRect.top;
    const rectEl = document.createElement("div");
    rectEl.className = "draw-rect";
    el.imageCanvas.appendChild(rectEl);

    const geometry = (e) => {
        const x = Math.max(0, Math.min(e.clientX - canvasRect.left, canvasRect.width));
        const y = Math.max(0, Math.min(e.clientY - canvasRect.top, canvasRect.height));
        return { left: Math.min(startX, x), top: Math.min(startY, y), width: Math.abs(x - startX), height: Math.abs(y - startY) };
    };
    const onMove = (e) => {
        const g = geometry(e);
        Object.assign(rectEl.style, { left: `${g.left}px`, top: `${g.top}px`, width: `${g.width}px`, height: `${g.height}px` });
    };
    const onUp = async (e) => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
        const g = geometry(e);
        rectEl.remove();
        setDrawMode(false);
        if (g.width < 8 || g.height < 8) return;               // a click, not a drag
        const scale = state.baseScale * state.zoomFactor;       // canvas pixels -> image pixels
        const box = [g.left / scale, g.top / scale, (g.left + g.width) / scale, (g.top + g.height) / scale].map((v) => Math.round(v));
        if (mode === "clean") {
            await cleanArea(box);
            return;
        }
        showToast("Reading the selected area...", 20000);
        try {
            const saved = await saveCurrentBubble();
            if (!saved) return;
            if (mode === "reocr") {
                const result = await api.pages.reocrBubble(pageId, targetId, box);
                await reloadBubbles(targetId);
                showToast(result.text_changed ? "Area read - text changed" : "Area read - same text");
                if (result.text_changed && !result.bubble.skip && (result.bubble.translation || "").trim()
                    && confirm("The source text changed. Retranslate this bubble now?")) await retranslateCurrentBubble(true);
                return;
            }
            const result = await api.pages.addBubble(pageId, box);
            await reloadBubbles(result.bubble.bubble_id);
            showToast("Bubble added");
            if (!result.bubble.skip && !state.bubbles[state.selectedIndex]?.translation) await retranslateCurrentBubble(true);
        } catch (err) {
            showToast(`${mode === "reocr" ? "Re-OCR of the area" : "Add bubble"} failed: ${err.message}`, 6000);
        }
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
}

/** Translate the given bubbles again, one request each (the parts of a split bubble, when the user asks for it). */
async function retranslateBubbleIds(ids) {
    let failed = 0;
    for (const id of ids) {
        const bubble = state.bubbles.find((b) => b.bubble_id === id);
        if (!bubble) continue;
        try {
            const result = await api.pages.retranslateBubble(pageId, id);
            bubble.translation = result.translation;
            bubble.ai_translation = result.ai_translation;
            delete bubble.split_check;
        } catch (e) {
            failed += 1;
            console.error(`[EDITOR] retranslate ${id} failed:`, e);
        }
    }
    renderBubbleList();
    renderBubbleDetail();
    showToast(failed ? `${failed} part(s) could not be translated - use Retranslate` : "Parts translated", failed ? 5000 : 1800);
}

async function retranslateCurrentBubble(silent = false) {
    const bubble = state.bubbles[state.selectedIndex];
    const edited = (bubble.translation || "") !== (bubble.ai_translation || "");
    if (!silent && edited && !confirm("You edited this translation. Retranslate and overwrite it?")) return;
    const btn = document.getElementById("retranslateBubbleBtn");
    if (btn) { btn.disabled = true; btn.textContent = "..."; }
    try {
        const result = await api.pages.retranslateBubble(pageId, bubble.bubble_id);
        bubble.translation = result.translation;
        bubble.ai_translation = result.ai_translation;
        renderBubbleList();
        renderBubbleDetail();
        showToast("Bubble retranslated");
    } catch (e) {
        alert(`Retranslate failed: ${e.message}`);
        renderBubbleDetail();
    }
}
 
let saveInFlight = false;
 
function setNavButtonsDisabled(disabled) {
    ["saveBtn", "prevBtn", "nextBtn"].forEach((id) => {
        const btn = document.getElementById(id);
        if (btn) btn.disabled = disabled;
    });
}
 
async function saveCurrentBubble() {
    if (state.selectedIndex === null) return true;
    if (saveInFlight) return false; // block concurrent save = block duplicate POSTs
 
    const bubble = state.bubbles[state.selectedIndex];
    const textarea = document.getElementById("translationInput");
    if (!textarea || textarea.disabled || bubble.skip) return true; // skipped bubble: nothing to save / confirm
    const newValue = textarea.value.trim();

    if (newValue === (bubble.translation || "")) {
        // Unchanged = the user read it and moved on: confirm it once as REVIEWED (stamped in the page JSON for the
        // evaluation; "accepted" marker in the list). It does NOT go into the correction memory - that needs the
        // explicit "Approve to memory". Already confirmed with this text (earlier visit / session): nothing to send.
        if (newValue && !isConfirmed(bubble)) {
            await confirmUnchanged(bubble, newValue);
        } else {
            flashInlineStatus("No changes to save");
        }
        return true;
    }

    saveInFlight = true;
    setNavButtonsDisabled(true);
    try {
        const result = await api.corrections.save(pageId, bubble.bubble_id, {
            source_text: bubble.text,
            ai_translation: bubble.ai_translation,
            final_translation: newValue,
        });
        bubble.translation = newValue;
        markConfirmed(bubble, newValue);
        state.unsavedId = null;
        renderBubbleList();
        recordLastEdit();
        if (state.preview) refreshPageImage();      // the typeset text follows what was just saved
        const memoryNote = result.correction_id ? "" : " (not added to memory: duplicate/too short)";
        showToast("Saved" + memoryNote);
        flashInlineStatus("Saved \u2713");
        offerGlossarySuggestions(result.glossary_suggestions);
        return true;
    } catch (e) {
        console.error("[EDITOR] Save failed:", e);
        showToast(`Save failed: ${e.message}`);
        flashInlineStatus("Save failed", true);
        return false;
    } finally {
        saveInFlight = false;
        setNavButtonsDisabled(false);
    }
}
 
/** Silent "reviewed" confirmation of an unchanged translation (approve: false = no memory entry). Never blocks navigation. */
async function confirmUnchanged(bubble, value) {
    try {
        await api.corrections.save(pageId, bubble.bubble_id, {
            source_text: bubble.text,
            ai_translation: bubble.ai_translation,
            final_translation: value,
            approve: false,
        });
        markConfirmed(bubble, value);
        refreshRowMark(state.bubbles.indexOf(bubble));
        renderBubbleListHeader();
    } catch (e) {
        console.warn("[EDITOR] review confirmation not stored:", e);
    }
}

/** Local copy of the backend's review stamp (page_export.update_bubble_translation), so the marker shows at once. */
function markConfirmed(bubble, value) {
    bubble.confirmed_translation = value;
    bubble.confirmed_at = new Date().toISOString();
    delete bubble.split_check;          // the backend drops it too: the user has looked at this part's translation
}

/** Explicit "Approve to memory": an unchanged translation becomes an approved memory entry (edits: use Save). */
async function approveCurrentBubble() {
    if (state.selectedIndex === null) return;
    const bubble = state.bubbles[state.selectedIndex];
    const value = (document.getElementById("translationInput")?.value || "").trim();
    if (!value) return;
    if (value !== (bubble.translation || "")) {
        await saveCurrentBubble();      // edited: that is a correction, stored as "corrected"
        return;
    }
    try {
        const result = await api.corrections.save(pageId, bubble.bubble_id, {
            source_text: bubble.text,
            ai_translation: bubble.ai_translation,
            final_translation: value,
            approve: true,
        });
        markConfirmed(bubble, value);
        renderBubbleList();
        if (result.correction_id) {
            flashInlineStatus("Approved to memory ✓");
            showToast("Approved - stored in memory");
        } else {
            showToast("Not added to memory (duplicate, too short, or approvals are off in Settings)");
        }
    } catch (e) {
        showToast(`Approve failed: ${e.message}`);
    }
}

/** memory_store_approved decides whether the "Approve to memory" button exists at all (off by default). */
async function loadEditorSettings() {
    try {
        const settings = await api.settings.get();
        state.storeApproved = settings.memory_store_approved === true;
        const typing = document.getElementById("translationInput");
        const untouched = !typing || state.selectedIndex === null
            || typing.value === (state.bubbles[state.selectedIndex]?.translation || "");
        if (state.storeApproved && state.selectedIndex !== null && untouched) renderBubbleDetail();   // never wipe typing
    } catch (e) {
        console.warn("[EDITOR] settings not loaded, approve button hidden:", e);
    }
}

/* ---------- glossary suggestions offered right after a correction (glossary_suggest_on_save) ---------- */

function dismissedTermsKey() { return `okt-glossary-dismissed-${state.projectId}`; }

function dismissedTerms() {
    try { return new Set(JSON.parse(localStorage.getItem(dismissedTermsKey()) || "[]")); } catch { return new Set(); }
}

function dismissTerm(sourceTerm) {
    const set = dismissedTerms();
    set.add(sourceTerm.toUpperCase());
    try { localStorage.setItem(dismissedTermsKey(), JSON.stringify([...set])); } catch { /* ignore */ }
}

/** Terms closed with × ("not now"): not offered again until the browser tab is closed (sessionStorage, per project). */
function snoozedTermsKey() { return `okt-glossary-snoozed-${state.projectId}`; }

function snoozedTerms() {
    try { return new Set(JSON.parse(sessionStorage.getItem(snoozedTermsKey()) || "[]")); } catch { return new Set(); }
}

function snoozeTerms(sourceTerms) {
    const set = snoozedTerms();
    sourceTerms.forEach((t) => set.add(t.toUpperCase()));
    try { sessionStorage.setItem(snoozedTermsKey(), JSON.stringify([...set])); } catch { /* ignore */ }
}

/*
 * One box at a time (each save replaces it), up to 3 rows, every row on its own:
 *   Add        -> glossary term + offer to update old forms in the memory
 *   Not a term -> never offered again in this project (localStorage)
 *   ×          -> "not now": the rows still open are not offered again until this tab is closed
 * A row left alone disappears with the next save and can come back when the same fix is saved again.
 */
function offerGlossarySuggestions(suggestions) {
    const dismissed = dismissedTerms();
    const snoozed = snoozedTerms();
    const fresh = (suggestions || []).filter((s) => !dismissed.has(s.source_term.toUpperCase()) && !snoozed.has(s.source_term.toUpperCase()));
    document.getElementById("glossarySuggestBox")?.remove();
    if (!fresh.length || !state.projectId) return;

    const box = document.createElement("div");
    box.id = "glossarySuggestBox";
    box.className = "glossary-suggest";
    box.innerHTML = `
        <div class="glossary-suggest-title">
            <span>Add to glossary? <span class="text-muted">(you fixed this the same way more than once)</span></span>
            <button type="button" class="glossary-suggest-close" aria-label="Close" title="Not now - these terms are not offered again until you close this tab ('Not a term' hides one for good)">&times;</button>
        </div>`;
    box.querySelector(".glossary-suggest-close").addEventListener("click", () => {
        snoozeTerms([...box.querySelectorAll(".glossary-suggest-row")].map((r) => r.dataset.term));
        box.remove();
    });
    fresh.slice(0, 3).forEach((s) => {
        const row = document.createElement("div");
        row.className = "glossary-suggest-row";
        row.dataset.term = s.source_term;
        row.innerHTML = `
            <span><b>${escapeHtml(s.source_term)}</b> → <b>${escapeHtml(s.new_translation)}</b>
                <span class="text-muted">(was ${escapeHtml(s.old_translation || "-")}, ${s.occurrences}×)</span></span>
            <span class="glossary-suggest-actions">
                <button class="primary" data-act="add">Add</button>
                <button data-act="no">Not a term</button>
            </span>`;
        row.querySelector('[data-act="add"]').addEventListener("click", async () => {
            try {
                await api.glossary.add(state.projectId, {
                    source_term: s.source_term, target_term: s.new_translation,
                    auto_detected: true, occurrences: s.occurrences,
                });
                showToast(`Glossary: ${s.source_term} → ${s.new_translation}`);
                offerMemoryCleanup(state.projectId, s.old_translation, s.new_translation);
            } catch (e) {
                showToast(`Glossary: ${e.message}`, 3500);
            }
            row.remove();
            if (!box.querySelector(".glossary-suggest-row")) box.remove();
        });
        row.querySelector('[data-act="no"]').addEventListener("click", () => {
            dismissTerm(s.source_term);
            row.remove();
            if (!box.querySelector(".glossary-suggest-row")) box.remove();
        });
        box.appendChild(row);
    });
    document.body.appendChild(box);
}

function flashInlineStatus(text, isError = false) {
    const statusEl = document.getElementById("saveInlineStatus");
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.style.color = isError ? "var(--danger)" : "var(--success)";
    clearTimeout(flashInlineStatus._t);
    flashInlineStatus._t = setTimeout(() => { if (statusEl) statusEl.textContent = ""; }, 2200);
}
 
async function deleteBubble() {
    if (state.selectedIndex === null) return;
    const bubble = state.bubbles[state.selectedIndex];
    if (!confirm(`Delete bubble ${state.selectedIndex + 1}? This removes it from the page entirely.`)) return;
 
    try {
        await api.pages.deleteBubble(pageId, bubble.bubble_id);
        state.bubbles.splice(state.selectedIndex, 1);
        state.selectedIndex = state.bubbles.length ? Math.min(state.selectedIndex, state.bubbles.length - 1) : null;
        renderBubbleList();
        renderBubbleDetail();
        applyZoom();
        if (state.selectedIndex !== null) scrollToBubble(state.selectedIndex); // the list jumped to the next bubble - move the image too
        refreshPageImage();    // backend rebuilt the clean image without this bubble's text lines
        recordLastEdit();
        showToast("Bubble deleted");
    } catch (e) {
        console.error("[EDITOR] Delete failed:", e);
        showToast(`Delete failed: ${e.message}`);
    }
}
 
async function navigateBubble(direction) {
    if (state.selectedIndex === null) {
        // nothing selected yet (fresh page) or a page with NO bubbles: arrows used to do nothing at all,
        // so a blank page was a dead end for keyboard navigation.
        if (state.bubbles.length) await selectBubble(direction > 0 ? 0 : state.bubbles.length - 1, { save: false });
        else await navigateToAdjacentPage(direction);
        return;
    }
    if (!state.bubbles.length) return;

    const next = state.selectedIndex + direction;
    if (next < 0 || next >= state.bubbles.length) {
        const ok = await saveCurrentBubble();
        if (!ok) return;
        await navigateToAdjacentPage(direction);
        return;
    }
    await selectBubble(next); // saves current bubble internally
}
 
async function navigateToAdjacentPage(direction) {
    if (!state.flatPageList.length) return;
    const idx = state.flatPageList.findIndex((p) => String(p.page_id) === String(pageId));
    if (idx === -1) return;
 
    const targetIdx = idx + direction;
    if (targetIdx < 0 || targetIdx >= state.flatPageList.length) {
        window.location.href = `editor.html?project=${state.projectId}`;
        return;
    }
 
    const target = state.flatPageList[targetIdx];
    if (direction > 0) await reviewBeforeNextPage();    // review mode "Next page: accept ..." (editor-review.js)
    const enter = direction > 0 ? "first" : "last";
    window.location.href = `editor.html?project=${state.projectId}&page=${target.page_id}&enterAt=${enter}`;
}
 
async function goToAdjacentPage(direction) {
    if (state.selectedIndex !== null) {
        const ok = await saveCurrentBubble();
        if (!ok) return;
    }
    await navigateToAdjacentPage(direction);
}
 
/**
 * "Next page to review" (&raquo; next to the page arrows): the first page AFTER this one (then from the start) that still
 * needs the user - its translation is not reviewed (not started, OCR done, partly translated, translated by the AI);
 * with the review mode "accept text + layout" also pages whose layout is not checked yet. Done pages are skipped, so
 * the review goes on where it is needed without the page list. The fast review modes accept this page first, as
 * Next page does (editor-review.js). Stages: the page list loaded with the chapter nav (services/page_progress.py).
 */
async function goToNextToReview() {
    const list = state.flatPageList || [];
    if (!list.length) return;
    const withLayout = reviewMode() === "all";
    const done = (p) => (withLayout
        ? p.stage === "checked" || (p.stage === "exported" && p.layout)
        : ["reviewed", "checked", "exported"].includes(p.stage));
    const idx = list.findIndex((p) => String(p.page_id) === String(pageId));
    const order = [...list.slice(idx + 1), ...list.slice(0, Math.max(idx, 0))];
    const target = order.find((p) => p.status !== "excluded" && !done(p));
    if (!target) {
        showToast(withLayout ? "Every other page is fully reviewed" : "The translation of every other page is reviewed", 4000);
        return;
    }
    if (state.selectedIndex !== null && !(await saveCurrentBubble())) return;
    await reviewBeforeNextPage();
    window.location.href = `editor.html?project=${state.projectId}&page=${target.page_id}&enterAt=first`;
}

/**
 * Keys of the editor through the shortcut registry (js/shortcuts.js; the user's own keys from Settings). Fixed:
 * Escape cancels a drawing / merge picks / split. While typing in a text field only the "also while typing"
 * actions fire (Ctrl+Enter save, Alt+L mode, ...), so arrows still move the cursor. Help ("?") is help.js's.
 */
function onGlobalKeydown(event) {
    if (document.querySelector("dialog[open]")) return;   // Help / merge / Re-OCR dialog: keys belong to it
    if (event.key === "Escape" && state.drawMode) { setDrawMode(false); return; }
    if (event.key === "Escape" && state.mergePicks.length) { clearMergePicks(); return; }
    if (event.key === "Escape" && isSplitting()) { cancelSplit(); applyZoom(); return; }
    const t = document.activeElement;
    const typing = !!t && (t.isContentEditable || ["TEXTAREA", "INPUT", "SELECT"].includes(t.tagName));
    const action = actionFor(event, { typing, scopes: ["global", "editor", isLettering() ? "lettering" : "translate"] });
    if (!action || action === "help") return;
    event.preventDefault();
    const actions = {
        prevBubble: () => navigateBubble(-1),
        nextBubble: () => navigateBubble(1),
        prevPage: () => goToAdjacentPage(-1),
        nextPage: () => goToAdjacentPage(1),
        nextToReview: () => goToNextToReview(),
        toggleMode: () => toggleMode(),
        cleanArea: () => setDrawMode(state.drawMode === "clean" ? false : "clean"),
        exportPage: () => exportPage(),
        save: () => saveCurrentBubble(),
        addBubble: () => setDrawMode(state.drawMode === "add" ? false : "add"),
        splitBubble: () => { if (state.selectedIndex !== null && (state.bubbles[state.selectedIndex].line_count || 0) >= 2) startSplit(); },
    };
    (actions[action] || (() => letteringAction(action)))();
}

/** Save the open translation only if it was changed (a mode switch / export must not stamp it "accepted"). */
async function saveIfChanged() {
    const textarea = document.getElementById("translationInput");
    if (state.selectedIndex === null || !textarea) return true;
    if (textarea.value.trim() === (state.bubbles[state.selectedIndex].translation || "")) return true;
    return saveCurrentBubble();
}
