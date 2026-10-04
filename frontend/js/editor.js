/**
 * @file frontend/js/editor.js
 * @description Page editor: zoomable image with clickable bubble outlines,
 * a synced bubble list, and a detail panel to edit/save/reset/delete the
 * selected bubble. Left/Right arrow keys (or the Prev/Next buttons) cycle
 * through bubbles, auto-saving the current one first if it changed, and
 * fall through to the previous/next page once you run off either end.
 */
 
import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { params, pageId, state, el, escapeHtml, showToast, openOrAlert, groupByChapter, compareChapterNames } from "./editor-shared.js";
import { showPagePicker } from "./pagepicker.js";
 
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
 
    if (!pageId && !state.projectId) {
        document.querySelector(".main-content").innerHTML = "<p>Missing ?project= or ?page= in the URL.</p>";
        return;
    }
 
    el.processBtn.addEventListener("click", runProcess);
    document.getElementById("excludePromptBtn").addEventListener("click", excludeCurrentPage);
    document.getElementById("showRawBtn").addEventListener("click", () => openOrAlert(state.rawPath));
    document.getElementById("zoomInBtn").addEventListener("click", () => zoomBy(1.25));
    document.getElementById("zoomOutBtn").addEventListener("click", () => zoomBy(0.8));
    document.getElementById("zoomResetBtn").addEventListener("click", () => setZoom(1, { persist: true }));
    document.getElementById("zoomFitWidthBtn").addEventListener("click", fitToWidth);
    document.getElementById("retranslateBtn").addEventListener("click", (e) => runRetranslate(e.shiftKey));
    document.getElementById("reocrBtn").addEventListener("click", runReocr);
    document.getElementById("reinpaintBtn").addEventListener("click", runReinpaint);
    document.getElementById("prevPageBtn").addEventListener("click", () => goToAdjacentPage(-1));
    document.getElementById("nextPageBtn").addEventListener("click", () => goToAdjacentPage(1));
    el.imageViewport.addEventListener("wheel", onWheelZoom, { passive: false });
    window.addEventListener("keydown", onGlobalKeydown);
 
    restorePanelLayout();
    setupResizers(); 
 
    renderSidebar(state.projectId);
 
    if (!pageId) {
        await showPagePicker();
    } else {
        await loadPage();
    }
}
 
/* ---------------------------- page loading ---------------------------- */
 
async function loadPage() {
    let data;
    try {
        data = await api.pages.get(pageId);
    } catch (e) {
        await showProcessPrompt(e);
        return;
    }
 
    state.bubbles = data.bubbles;
    state.projectId = String(data.project_id);
    state.jsonPath = data.json_path;
    state.pageFolder = data.page_folder;
    state.hasLines = !!data.has_lines;
    state.selectedIndex = null;
    state.savedThisSession = new Set();
 
    el.processPrompt.classList.add("hidden");
    el.pagePicker.classList.add("hidden");
    el.editorLayout.classList.remove("hidden");
 
    renderSidebar(state.projectId);
    renderPageTools();
    await loadChapterNav();
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

async function runReocr() {
    if (!confirm("Run OCR + inpainting again on this page?\n\nBubbles whose text did not change keep their translation and your edits. New or changed bubbles are translated. This can take a while.")) return;
    const btn = document.getElementById("reocrBtn");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Re-OCR...";
    try {
        const result = await api.pages.reocr(pageId);
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

function refreshPageImage() {
    el.pageImage.src = `${api.pages.imageUrl(pageId)}?t=${Date.now()}`;
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
            row.innerHTML = `${escapeHtml(p.file_name)} <span class="badge" data-status="${escapeHtml(p.status)}">${escapeHtml(p.status)}</span>`;
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
    img.src = api.pages.imageUrl(pageId);
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
    state.bubbles.forEach((bubble, index) => {
        const rect = bubbleRect(bubble, scale);
        const box = document.createElement("div");
        box.className = "bubble-outline" + (index === state.selectedIndex ? " selected" : "");
        box.style.left = `${rect.x}px`;
        box.style.top = `${rect.y}px`;
        box.style.width = `${rect.w}px`;
        box.style.height = `${rect.h}px`;
        box.title = `Bubble ${index + 1}`;
        box.addEventListener("click", () => selectBubble(index, { scroll: false }));
        el.imageCanvas.appendChild(box);
    });
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
 
function renderBubbleList() {
    el.bubbleListHeader.textContent = `Bubbles — ${state.bubbles.length}`;
    el.bubbleList.innerHTML = "";
    state.bubbles.forEach((bubble, index) => {
        const isSkipped = !!bubble.skip;
        const isEmpty = !isSkipped && !bubble.translation && !bubble.ai_translation;
        const textFixed = bubble.text_raw && bubble.text_raw !== bubble.text;
        const row = document.createElement("div");
        row.className = "bubble-row" + (index === state.selectedIndex ? " selected" : "") + (isSkipped ? " skipped" : "");
        const preview = isSkipped
            ? `skipped (${escapeHtml(bubble.skip_reason || "")}): ${escapeHtml(bubble.text || "")}`
            : isEmpty ? '<span style="color: var(--danger);">(no AI translation)</span>' : escapeHtml(bubble.translation || bubble.text || "(empty)");
        row.innerHTML = `
            <span class="bubble-num">${index + 1}</span>
            <span class="bubble-preview">${preview}</span>
            ${textFixed ? `<span class="bubble-tag" title="Source text was cleaned / edited. Raw OCR: ${escapeHtml(bubble.text_raw)}">text fixed</span>` : ""}
            ${state.savedThisSession.has(bubble.bubble_id) ? '<span class="bubble-saved-dot" title="Saved this session"></span>' : ""}
        `;
        row.addEventListener("click", () => selectBubble(index));
        el.bubbleList.appendChild(row);
    });
}
 
 
/* ------------------------------ bubble detail ---------------------------- */
 
async function selectBubble(index, { scroll = true, save = true } = {}) {
    if (save && state.selectedIndex !== null && state.selectedIndex !== index) {
        const ok = await saveCurrentBubble();
        if (!ok) return; // save failed or in-flight — don't jump away
    }
    state.selectedIndex = index;
    renderBubbleList();
    applyZoom();
    renderBubbleDetail();
    if (scroll) scrollToBubble(index);
}
 
function renderBubbleDetail() {
    if (state.selectedIndex === null) {
        el.bubbleDetail.classList.remove("hidden");
        el.bubbleDetail.innerHTML = `
            <div class="detail-actions" style="justify-content:flex-end;">
                <div class="detail-nav-actions">
                    <button id="prevBtn" title="Previous page">&#8592;</button>
                    <button id="nextBtn" title="Next page">&#8594;</button>
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
    const isSkipped = !!bubble.skip;
    const isEmpty = !isSkipped && !bubble.translation && !bubble.ai_translation;
    const textFixed = bubble.text_raw && bubble.text_raw !== bubble.text;
    const skipWhy = { number: "only digits (page number?)", symbols: "only symbols / punctuation", manual: "skipped by you", empty: "empty text" }[bubble.skip_reason] || bubble.skip_reason || "";
 
    el.bubbleDetail.classList.remove("hidden");
    el.bubbleDetail.innerHTML = `
        <div class="bubble-detail-title">
            <span>Bubble ${state.selectedIndex + 1} of ${state.bubbles.length}</span>
            <button id="deleteBubbleBtn" class="icon-btn danger" title="Delete this bubble from the page (use for OCR false positives)">Delete</button>
        </div>
 
        ${isEmpty ? `
        <div class="empty-warning">
            AI returned no translation for this bubble. Check the backend
            terminal for the error, or try "Retranslate" in the toolbar above.
            Typing your own text here will still save it as a correction,
            but it'll be recorded as written from scratch, not as an edit
            of an AI draft.
        </div>` : ""}
 
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

        ${isSkipped ? `
        <div class="skip-note">
            <span>Skipped - ${escapeHtml(skipWhy)}. Not translated, original pixels stay on the page.</span>
            <button id="unskipBtn" title="Translate this bubble anyway (its text will be erased from the image)">Translate this</button>
        </div>` : `
        <div>
            <label>Translated text</label>
            <textarea id="translationInput" rows="4" lang="pl" spellcheck="${isSpellcheckEnabled()}">${escapeHtml(bubble.translation || "")}</textarea>
        </div>
 
        <div class="detail-actions">
            <button id="resetBtn" title="Revert to the AI's original translation (does not save)">Reset to AI</button>
            <button id="undoBtn" title="Revert to the value shown when this bubble was opened">Undo</button>
            <button id="retranslateBubbleBtn" title="Translate only this bubble again (neighbours are sent as context; memory + glossary apply)">Retranslate</button>
            <button id="skipBtn" title="Do not translate this bubble and put the original pixels back (page numbers, junk)">Skip</button>
            <button id="spellcheckToggleBtn" class="${isSpellcheckEnabled() ? "primary" : ""}" title="Toggle Polish spellcheck">Spellcheck: ${isSpellcheckEnabled() ? "On" : "Off"}</button>
        </div>`}
 
        <div class="detail-actions" style="align-items:center;">
            ${isSkipped ? "" : '<button id="saveBtn" class="primary" title="Save this translation (Ctrl/Cmd+Enter also works while typing)">Save</button>'}
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

    if (isSkipped) {
        document.getElementById("unskipBtn").addEventListener("click", () => setSkip(false));
        return;
    }

    const textarea = document.getElementById("translationInput");
    const loadedValue = bubble.translation || "";
 
    autoGrowTextarea(textarea);
    textarea.addEventListener("input", () => autoGrowTextarea(textarea));

    document.getElementById("spellcheckToggleBtn").addEventListener("click", (e) => {
    const next = !isSpellcheckEnabled();
    setSpellcheckEnabled(next);
    textarea.spellcheck = next;
    e.target.textContent = `Spellcheck: ${next ? "On" : "Off"}`;
    e.target.classList.toggle("primary", next);
    });
 
    document.getElementById("resetBtn").addEventListener("click", () => {
        textarea.value = bubble.ai_translation || "";
    });
    document.getElementById("undoBtn").addEventListener("click", () => {
        textarea.value = loadedValue;
    });
    document.getElementById("retranslateBubbleBtn").addEventListener("click", () => retranslateCurrentBubble());
    document.getElementById("skipBtn").addEventListener("click", () => setSkip(true));
    document.getElementById("saveBtn").addEventListener("click", () => saveCurrentBubble());
 
    textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            saveCurrentBubble();
        }
    });
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
    if (!textarea) return true; // skipped bubble: nothing to save
    const newValue = textarea.value.trim();
 
    if (newValue === (bubble.translation || "")) {
        flashInlineStatus("No changes to save");
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
        state.savedThisSession.add(bubble.bubble_id);
        renderBubbleList();
        recordLastEdit();
        const memoryNote = result.correction_id ? "" : " (not added to memory: unchanged/too short)";
        showToast("Saved" + memoryNote);
        flashInlineStatus("Saved \u2713");
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
 
function onGlobalKeydown(event) {
    const tag = document.activeElement?.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT") return; // let arrow keys move the cursor while typing
 
    if (event.key === "ArrowLeft") navigateBubble(-1);
    if (event.key === "ArrowRight") navigateBubble(1);
}
