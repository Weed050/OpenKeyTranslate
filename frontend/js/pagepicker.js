/**
 * @file frontend/js/pagepicker.js
 * @description The page list shown when editor.html has ?project= but no ?page=: chapters as an accordion of cards
 * (grid or single column), page tiles per chapter, filter, expand/collapse, bulk delete, "Rescan folders", go to the
 * next unprocessed page and the back-to-top button. Styling: css/pagepicker.css.
 */

import { api } from "./api.js";
import { state, el, showToast, escapeHtml, groupByChapter, compareChapterNames } from "./editor-shared.js";
import { openImportDialog } from "./import-dialog.js";

const VIEW_STORAGE_KEY = "okt-chapter-view";   // "grid" | "list"
const ACCORDION_NAME = "chapter-accordion";

// Exclusive mode = opening a chapter closes the others (<details name=...>). "Expand all" and an active filter need
// several open at once, and browsers enforce the exclusivity, so those switch it off until "Collapse all".
let exclusive = true;

const chapterEls = () => el.pagePickerBody.querySelectorAll("details.page-picker-chapter");

function setExclusive(on) {
    exclusive = on;
    chapterEls().forEach((d) => (on ? d.setAttribute("name", ACCORDION_NAME) : d.removeAttribute("name")));
}

/** Shown when the URL has ?project= but no ?page= yet - lets the user pick one. */
export async function showPagePicker() {
    el.editorLayout.classList.add("hidden");
    el.processPrompt.classList.add("hidden");
    el.pagePicker.classList.remove("hidden");
    el.pagePickerBody.innerHTML = `<p class="text-muted">Loading pages...</p>`;

    const includeExcluded = document.getElementById("showExcludedCheckbox")?.checked || false;

    let pages;
    try {
        pages = await api.pages.listByProject(state.projectId, includeExcluded);
    } catch (e) {
        el.pagePickerBody.innerHTML = `<p class="text-muted">Couldn't load pages: ${escapeHtml(e.message)}</p>`;
        return;
    }

    if (!pages.length) {
        el.pagePickerBody.innerHTML = `<p class="text-muted">No pages found for this project yet - import scans from the Dashboard.</p>`;
        return;
    }

    document.getElementById("pagePickerControls").classList.remove("hidden");

    el.pagePickerBody.innerHTML = "";
    const byChapter = groupByChapter(pages);
    for (const [chapter, chapterPages] of byChapter) el.pagePickerBody.appendChild(chapterCard(chapter, chapterPages));
    setExclusive(exclusive);
    applyView(localStorage.getItem(VIEW_STORAGE_KEY) || "grid");

    bindToolbar(byChapter);
}

/* ------------------------------ one chapter card ------------------------------ */

function chapterCard(chapter, chapterPages) {
    const processedCount = chapterPages.filter((p) => p.status === "processed").length;
    const fullyDone = processedCount === chapterPages.length;
    const chapterId = chapterPages[0].chapter_id;

    const details = document.createElement("details");
    details.className = "page-picker-chapter";
    details.dataset.chapter = chapter;

    const summary = document.createElement("summary");
    summary.style.setProperty("--done", `${Math.round((100 * processedCount) / chapterPages.length)}%`);
    summary.innerHTML = `
        <span class="chapter-title">
            ${fullyDone ? '<span class="chapter-done-check" title="All pages processed">&#10003;</span>' : ""}
            <span class="chapter-name" title="${escapeHtml(chapter)}">${escapeHtml(chapter)}</span>
            <span class="chapter-count" title="Processed pages">${processedCount}/${chapterPages.length}</span>
            <input type="checkbox" class="chapter-select" data-chapter-id="${chapterId}" data-chapter-name="${escapeHtml(chapter)}" title="Select for bulk delete">
        </span>
        <button class="copy-btn danger chapter-delete-btn" title="Delete this chapter and its pages">Delete chapter</button>
    `;
    details.appendChild(summary);

    // Scroll the opened chapter to the top - but only when the USER clicked it (Expand all / filter open many at once,
    // and scrolling to each of them ended at the last one). The gap above comes from scroll-margin-top in the CSS.
    let userClick = false;
    summary.addEventListener("click", (e) => { userClick = !e.target.closest("input, button"); });
    details.addEventListener("toggle", () => {
        if (!details.open || !userClick) return;
        userClick = false;
        setTimeout(() => details.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    });

    summary.querySelector(".chapter-select").addEventListener("click", (e) => e.stopPropagation()); // don't toggle <details>
    summary.querySelector(".chapter-delete-btn").addEventListener("click", async (event) => {
        event.preventDefault();
        if (!confirm(`Delete chapter "${chapter}"? Removes its pages and files on disk. Cannot undo.`)) return;
        try {
            await api.projects.deleteChapter(chapterId);
            await showPagePicker();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    });

    const grid = document.createElement("div");
    grid.className = "page-picker-grid";
    [...chapterPages].sort((a, b) => a.order - b.order).forEach((p) => grid.appendChild(pageTile(p)));
    details.appendChild(grid);
    return details;
}

function pageTile(p) {
    const row = document.createElement("div");
    row.className = "page-picker-row" + (p.status === "excluded" ? " is-excluded" : "") + (p.error ? " has-error" : "");
    row.dataset.filename = p.file_name.toLowerCase();

    const link = document.createElement("a");
    link.href = `editor.html?project=${state.projectId}&page=${p.page_id}`;
    link.title = p.error ? `${p.file_name}\n${p.error}` : p.file_name;
    link.innerHTML = `
        <span class="page-name">${escapeHtml(p.file_name)}</span>
        <span class="badge" data-status="${escapeHtml(p.status)}">${escapeHtml(p.status)}</span>
    `;
    row.appendChild(link);

    if (p.status === "excluded") {
        const restoreBtn = document.createElement("button");
        restoreBtn.className = "copy-btn";
        restoreBtn.textContent = "Restore";
        restoreBtn.title = "Bring this page back into the list";
        restoreBtn.addEventListener("click", async (e) => {
            e.preventDefault();
            try {
                await api.pages.restore(p.page_id);
                await showPagePicker();
            } catch (err) {
                alert(`Restore failed: ${err.message}`);
            }
        });
        row.appendChild(restoreBtn);
    }
    return row;
}

/* ---------------------------------- toolbar ---------------------------------- */

function bindToolbar(byChapter) {
    document.getElementById("pageSearchInput").oninput = (e) => filterChapters(e.target.value.toLowerCase());
    document.getElementById("collapseAllBtn").onclick = () => { chapterEls().forEach((d) => { d.open = false; }); setExclusive(true); };
    document.getElementById("expandAllBtn").onclick = () => { setExclusive(false); chapterEls().forEach((d) => { d.open = true; }); };
    document.getElementById("showExcludedCheckbox").onchange = () => showPagePicker();
    document.getElementById("goToNextUnprocessedBtn").onclick = goToNextUnprocessed;
    document.getElementById("addChaptersBtn").onclick = () => openImportDialog(showPagePicker);
    document.getElementById("rescanBtn").onclick = rescanFolders;
    document.getElementById("chapterViewBtn").onclick = () =>
        applyView(el.pagePickerBody.classList.contains("as-list") ? "grid" : "list");
    setupBulkDelete(byChapter);
}

function applyView(mode) {
    el.pagePickerBody.classList.toggle("as-list", mode === "list");
    document.getElementById("chapterViewBtn").textContent = `Layout: ${mode}`;
    localStorage.setItem(VIEW_STORAGE_KEY, mode);
}

function filterChapters(query) {
    if (query) setExclusive(false); else setExclusive(true);
    chapterEls().forEach((details) => {
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

async function goToNextUnprocessed() {
    let pages;
    try {
        pages = await api.pages.listByProject(state.projectId);
    } catch (e) {
        alert(`Couldn't load pages: ${e.message}`);
        return;
    }
    const sorted = [...pages].sort((a, b) => {
        const c = compareChapterNames(a.chapter, b.chapter);
        return c !== 0 ? c : a.order - b.order;
    });
    const next = sorted.find((p) => p.status === "pending" || p.status === "failed");
    if (!next) {
        alert("No unprocessed pages left in this project.");
        return;
    }
    window.location.href = `editor.html?project=${state.projectId}&page=${next.page_id}`;
}

/* ------------------------------ bulk chapter delete ------------------------------ */

function setupBulkDelete(byChapter) {
    const btn = document.getElementById("deleteSelectedChaptersBtn");
    const boxes = () => [...el.pagePickerBody.querySelectorAll("input.chapter-select")];
    const refresh = () => {
        const n = boxes().filter((b) => b.checked).length;
        btn.textContent = n ? `Delete selected (${n})` : "Delete selected";
        btn.disabled = n === 0;
    };
    boxes().forEach((b) => b.addEventListener("change", refresh));
    refresh();

    btn.onclick = async () => {
        const chosen = boxes().filter((b) => b.checked);
        if (!chosen.length) return;
        const lines = chosen.map((b) => {
            const pages = byChapter.get(b.dataset.chapterName) || [];
            const done = pages.filter((p) => p.status === "processed").length;
            return `- ${b.dataset.chapterName}: ${pages.length} pages, ${done} processed`;
        });
        if (!confirm(`Delete ${chosen.length} chapter(s)?\n\n${lines.join("\n")}\n\nRemoves their pages and files on disk (raw + processed). Corrections and glossary stay. Cannot undo.`)) return;
        try {
            const result = await api.projects.deleteChapters(chosen.map((b) => parseInt(b.dataset.chapterId, 10)));
            showToast(result.message, 3000);
            await showPagePicker();
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    };
}

/* ------------------------------- rescan folders ------------------------------- */

async function rescanFolders() {
    const btn = document.getElementById("rescanBtn");
    btn.disabled = true;
    const original = btn.textContent;
    btn.textContent = "Scanning...";
    try {
        const r = await api.projects.rescan(state.projectId);
        const section = (title, lines) => (lines.length ? `${title}\n${lines.map((l) => "  - " + l).join("\n")}\n\n` : "");
        const text = section("ADDED / REGISTERED", r.added) + section("REMOVED (folder gone)", r.removed)
            + section("STATUS FIXED", r.fixed) + section("WARNINGS (nothing was deleted)", r.warnings);
        const dlg = document.createElement("dialog");
        dlg.className = "import-dialog";
        dlg.innerHTML = `<h2>Rescan folders</h2><p class="text-muted">${escapeHtml(r.message)}</p>
            <pre class="rescan-report text-mono">${escapeHtml(text || "Disk and database agree - nothing to do.")}</pre>
            <div class="import-footer"><button class="primary">OK</button></div>`;
        dlg.querySelector("button").addEventListener("click", () => dlg.close());
        dlg.addEventListener("close", () => dlg.remove());
        document.body.appendChild(dlg);
        dlg.showModal();
        await showPagePicker();
    } catch (e) {
        alert(`Rescan failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

/* -------------------------------- back-to-top button -------------------------------- */

const scrollBtn = document.getElementById("scrollToTopBtn");
const scroller = document.getElementById("pagePicker");
if (scrollBtn && scroller) {
    scroller.addEventListener("scroll", () => scrollBtn.classList.toggle("visible", scroller.scrollTop > 150));
    scrollBtn.addEventListener("click", () => scroller.scrollTo({ top: 0, behavior: "smooth" }));
}
