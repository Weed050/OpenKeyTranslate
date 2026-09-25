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

const params = new URLSearchParams(window.location.search);
const pageId = params.get("page");
const enterAt = params.get("enterAt"); // "first" | "last" | null - set when arriving via cross-page nav

const state = {
    projectId: params.get("project"),
    pageId,
    bubbles: [],
    selectedIndex: null,
    savedThisSession: new Set(),
    zoomFactor: 1,
    baseScale: 1,
    naturalW: 0,
    naturalH: 0,
    jsonPath: null,
    pageFolder: null,
    flatPageList: [], // all pages in the project, sorted, for prev/next-page fallthrough
};

const el = {}; // populated in init() once the DOM exists

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

    if (!pageId && !state.projectId) {
        document.querySelector(".main-content").innerHTML = "<p>Missing ?project= or ?page= in the URL.</p>";
        return;
    }

    el.processBtn.addEventListener("click", runProcess);
    document.getElementById("zoomInBtn").addEventListener("click", () => zoomBy(1.25));
    document.getElementById("zoomOutBtn").addEventListener("click", () => zoomBy(0.8));
    document.getElementById("zoomResetBtn").addEventListener("click", () => setZoom(1, { persist: true }));
    document.getElementById("zoomFitWidthBtn").addEventListener("click", fitToWidth);
    document.getElementById("retranslateBtn").addEventListener("click", runRetranslate);
    el.imageViewport.addEventListener("wheel", onWheelZoom, { passive: false });
    window.addEventListener("keydown", onGlobalKeydown);

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
        showProcessPrompt(e);
        return;
    }

    state.bubbles = data.bubbles;
    state.projectId = String(data.project_id);
    state.jsonPath = data.json_path;
    state.pageFolder = data.page_folder;
    state.selectedIndex = null;
    state.savedThisSession = new Set();

    el.processPrompt.classList.add("hidden");
    el.pagePicker.classList.add("hidden");
    el.editorLayout.classList.remove("hidden");

    renderSidebar(state.projectId);
    renderPageTools();
    await loadChapterNav();
    renderBubbleList();
    renderBubbleDetail();
    loadImage();

    if (state.bubbles.length && (enterAt === "first" || enterAt === "last")) {
        selectBubble(enterAt === "first" ? 0 : state.bubbles.length - 1, { scroll: false });
    }
}

function showProcessPrompt(error) {
    el.editorLayout.classList.add("hidden");
    el.pagePicker.classList.add("hidden");
    el.processPrompt.classList.remove("hidden");
    el.processBtn.disabled = false;
    el.processStatus.textContent = "";

    const isNotProcessed = !error || /not.*processed/i.test(error.message || "");
    document.getElementById("processPromptMsg").textContent = isNotProcessed
        ? "This page hasn't been processed yet."
        : `Couldn't load this page: ${error.message}`;

    const backLink = document.getElementById("backToPagesLink");
    if (backLink) backLink.href = state.projectId ? `editor.html?project=${state.projectId}` : "index.html";
}

function renderPageTools() {
    el.pageToolsMenu.innerHTML = `
        <button class="copy-btn" id="copyJsonPathBtn" title="Copy the translation JSON's file path">Copy path</button>
        <button class="copy-btn" id="openFolderBtn" title="Open this page's folder in the file explorer">Open folder</button>
        <button class="copy-btn" id="openJsonBtn" title="Reveal the translation JSON in the file explorer">Open JSON</button>
    `;
    document.getElementById("copyJsonPathBtn").addEventListener("click", async (e) => {
        await navigator.clipboard.writeText(state.jsonPath || "");
        const btn = e.target;
        btn.textContent = "Copied";
        setTimeout(() => { btn.textContent = "Copy path"; }, 1200);
    });
    document.getElementById("openFolderBtn").addEventListener("click", () => openOrAlert(state.pageFolder));
    document.getElementById("openJsonBtn").addEventListener("click", () => openOrAlert(state.jsonPath));
}

async function openOrAlert(path) {
    if (!path) return;
    try {
        await api.system.openPath(path);
    } catch (e) {
        alert(`Couldn't open: ${e.message}`);
    }
}

/* ------------------------------- page picker ----------------------------- */

/** Shown when the URL has ?project= but no ?page= yet - lets the user pick one. */
async function showPagePicker() {
    el.editorLayout.classList.add("hidden");
    el.processPrompt.classList.add("hidden");
    el.pagePicker.classList.remove("hidden");
    el.pagePickerBody.innerHTML = `<p class="text-muted">Loading pages...</p>`;

    let pages;
    try {
        pages = await api.pages.listByProject(state.projectId);
    } catch (e) {
        el.pagePickerBody.innerHTML = `<p class="text-muted">Couldn't load pages: ${e.message}</p>`;
        return;
    }

    if (!pages.length) {
        el.pagePickerBody.innerHTML = `<p class="text-muted">No pages found for this project yet - import scans from the Dashboard.</p>`;
        return;
    }

    document.getElementById("pagePickerControls").classList.remove("hidden");

    el.pagePickerBody.innerHTML = "";
    const byChapter = groupByChapter(pages);
    for (const [chapter, chapterPages] of byChapter) {
        const details = document.createElement("details");
        details.className = "chapter-nav";
        details.dataset.chapter = chapter;

        const summary = document.createElement("summary");
        summary.textContent = `${chapter} (${chapterPages.length})`;
        details.appendChild(summary);

        const card = document.createElement("div");
        card.className = "card";
        card.style.margin = "8px 0 16px";
        chapterPages.sort((a, b) => a.order - b.order);
        chapterPages.forEach((p) => {
            const row = document.createElement("a");
            row.href = `editor.html?project=${state.projectId}&page=${p.page_id}`;
            row.className = "row page-picker-row";
            row.dataset.filename = p.file_name.toLowerCase();
            row.style.display = "flex";
            row.style.textDecoration = "none";
            row.innerHTML = `
                <span style="color: var(--text-primary);">${escapeHtml(p.file_name)}</span>
                <span class="badge" data-status="${escapeHtml(p.status)}">${escapeHtml(p.status)}</span>
            `;
            card.appendChild(row);
        });
        details.appendChild(card);
        el.pagePickerBody.appendChild(details);
    }

    document.getElementById("pageSearchInput").oninput = (e) => filterPagePicker(e.target.value.toLowerCase());
    document.getElementById("collapseAllBtn").onclick = () => togglePagePickerChapters(false);
    document.getElementById("expandAllBtn").onclick = () => togglePagePickerChapters(true);

    document.getElementById("addChaptersBtn").onclick = async () => {
        const data = await api.projects.selectFolder();
        if (!data.path) return;
        try {
            const result = await api.projects.importChapters(state.projectId, data.path);
            alert(result.message);
            await showPagePicker();
        } catch (e) {
            alert(`Import failed: ${e.message}`);
        }
    };
}

function filterPagePicker(query) {
    const chapters = el.pagePickerBody.querySelectorAll("details.chapter-nav");
    chapters.forEach((details) => {
        let anyVisible = false;
        details.querySelectorAll(".page-picker-row").forEach((row) => {
            const match = !query || row.dataset.filename.includes(query);
            row.classList.toggle("hidden", !match);
            if (match) anyVisible = true;
        });
        details.classList.toggle("hidden", !anyVisible);
        if (query) details.open = anyVisible; // auto-expand chapters with a match while searching
    });
}

function togglePagePickerChapters(open) {
    el.pagePickerBody.querySelectorAll("details.chapter-nav").forEach((d) => { d.open = open; });
}

function groupByChapter(pages) {
    const map = new Map();
    pages.forEach((p) => {
        if (!map.has(p.chapter)) map.set(p.chapter, []);
        map.get(p.chapter).push(p);
    });
    // Natural sort ("Chapter_2" before "Chapter_10") instead of the Map's arrival order.
    return new Map([...map.entries()].sort((a, b) => compareChapterNames(a[0], b[0])));
}

function compareChapterNames(a, b) {
    const numA = parseInt(a.match(/\d+/)?.[0] ?? "0", 10);
    const numB = parseInt(b.match(/\d+/)?.[0] ?? "0", 10);
    if (numA !== numB) return numA - numB;
    return a.localeCompare(b);
}

async function runProcess() {
    el.processBtn.disabled = true;
    el.processStatus.textContent = "Processing — this can take a while, don't close the tab...";
    try {
        await api.pages.process(pageId);
        el.processStatus.textContent = "Done — loading page...";
        await loadPage();
    } catch (e) {
        console.error("[EDITOR] Processing failed:", e);
        el.processStatus.textContent = `Failed: ${e.message}`;
        el.processBtn.disabled = false;
    }
}

async function runRetranslate() {
    if (!confirm("Re-translate every bubble on this page? This overwrites the current AI translation text (your saved corrections in memory are untouched).")) return;

    const btn = document.getElementById("retranslateBtn");
    const originalText = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Retranslating...";

    try {
        const result = await api.pages.retranslate(pageId);
        showToast(result.message);
        await loadPage();
    } catch (e) {
        alert(`Retranslate failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = originalText;
    }
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
        const isEmpty = !bubble.translation && !bubble.ai_translation;
        const row = document.createElement("div");
        row.className = "bubble-row" + (index === state.selectedIndex ? " selected" : "");
        row.innerHTML = `
            <span class="bubble-num">${index + 1}</span>
            <span class="bubble-preview">${isEmpty ? '<span style="color: var(--danger);">(no AI translation)</span>' : escapeHtml(bubble.translation || bubble.text || "(empty)")}</span>
            ${state.savedThisSession.has(bubble.bubble_id) ? '<span class="bubble-saved-dot" title="Saved this session"></span>' : ""}
        `;
        row.addEventListener("click", () => selectBubble(index));
        el.bubbleList.appendChild(row);
    });
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
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
        el.bubbleDetail.classList.add("hidden");
        return;
    }

    const bubble = state.bubbles[state.selectedIndex];
    const isFirst = state.selectedIndex === 0;
    const isLast = state.selectedIndex === state.bubbles.length - 1;
    const isEmpty = !bubble.translation && !bubble.ai_translation;

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
            <label>Original text</label>
            <div class="source-text">${escapeHtml(bubble.text || "")}</div>
        </div>

        <div>
            <label>Translated text</label>
            <textarea id="translationInput" rows="4">${escapeHtml(bubble.translation || "")}</textarea>
        </div>

        <div class="detail-actions">
            <button id="resetBtn" title="Revert to the AI's original translation (does not save)">Reset to AI</button>
            <button id="undoBtn" title="Revert to the value shown when this bubble was opened">Undo</button>
        </div>

        <div class="detail-actions" style="align-items:center;">
            <button id="saveBtn" class="primary" title="Save this translation (Ctrl/Cmd+Enter also works while typing)">Save</button>
            <span id="saveInlineStatus" style="font-size:12px;"></span>
            <div class="detail-nav-actions" style="margin-left:auto;">
                <button id="prevBtn" class="${isFirst ? "nav-crosses-page" : ""}"
                    title="${isFirst ? "Go to the previous page (saves this bubble first)" : "Previous bubble — saves this one first if changed (Left arrow)"}">${isFirst ? "\u23EE" : "\u2190"}</button>
                <button id="nextBtn" class="${isLast ? "nav-crosses-page" : ""}"
                    title="${isLast ? "Go to the next page (saves this bubble first)" : "Next bubble — saves this one first if changed (Right arrow)"}">${isLast ? "\u23ED" : "\u2192"}</button>
            </div>
        </div>
    `;

    const textarea = document.getElementById("translationInput");
    const loadedValue = bubble.translation || "";

    document.getElementById("resetBtn").addEventListener("click", () => {
        textarea.value = bubble.ai_translation || "";
    });
    document.getElementById("undoBtn").addEventListener("click", () => {
        textarea.value = loadedValue;
    });
    document.getElementById("deleteBubbleBtn").addEventListener("click", () => deleteBubble());
    document.getElementById("saveBtn").addEventListener("click", () => saveCurrentBubble());
    document.getElementById("prevBtn").addEventListener("click", () => navigateBubble(-1));
    document.getElementById("nextBtn").addEventListener("click", () => navigateBubble(1));

    textarea.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            saveCurrentBubble();
        }
    });
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
        showToast("Bubble deleted");
    } catch (e) {
        console.error("[EDITOR] Delete failed:", e);
        showToast(`Delete failed: ${e.message}`);
    }
}

async function navigateBubble(direction) {
    if (state.selectedIndex === null || !state.bubbles.length) return;

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

function onGlobalKeydown(event) {
    const tag = document.activeElement?.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT") return; // let arrow keys move the cursor while typing

    if (event.key === "ArrowLeft") navigateBubble(-1);
    if (event.key === "ArrowRight") navigateBubble(1);
}

function showToast(message) {
    el.saveToast.textContent = message;
    el.saveToast.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.saveToast.classList.remove("show"), 1800);
}
