/**
 * @file frontend/js/pagepicker.js
 * @description The page list shown when editor.html has ?project= but no ?page=: chapters as an accordion of cards
 * (grid or single column), page tiles per chapter, the stage legend (= quick filter), status filter, sorting,
 * expand/collapse, bulk delete, export, "Rescan folders", go to the next unprocessed page and the back-to-top button.
 * Styling: css/pagepicker.css. Stage names / colours: js/stages.js.
 */

import { api } from "./api.js";
import { state, el, showToast, escapeHtml, groupByChapter, compareChapterNames } from "./editor-shared.js";
import { openImportDialog } from "./import-dialog.js";
import { openReplaceDialog } from "./replace-dialog.js";
import { PAGE_BADGES, activeGroups, countGroups, doneScore, groupLabel, reviewedCount, shownStage, stageBarHtml,
         stageChipsHtml, stageGroup, stageTip, trackExported, setTrackExported } from "./stages.js";

const VIEW_STORAGE_KEY = "okt-chapter-view";   // "grid" | "list"
const SORT_STORAGE_KEY = "okt-chapter-sort";   // {"field": number | progress | reviewed | updated | name, "dir": asc | desc}
const ACCORDION_NAME = "chapter-accordion";
const filterKey = () => `okt-stage-filter-${state.projectId}`;   // hidden stage groups, per project

let lastPages = [];                            // the pages of the last render (filter counts, "open first matching")

const groupOf = (p) => stageGroup(p.stage || p.status, p.layout) || "excluded";

/** "5 min ago", "yesterday", "3 days ago", else the date. */
function relativeTime(epochSeconds) {
    const s = Date.now() / 1000 - epochSeconds;
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 2 * 86400) return "yesterday";
    if (s < 14 * 86400) return `${Math.round(s / 86400)} days ago`;
    return new Date(epochSeconds * 1000).toLocaleDateString();
}

/** Stage groups the user hid with the status filter / legend. */
function hiddenGroups() {
    try { return new Set(JSON.parse(localStorage.getItem(filterKey()) || "[]")); } catch { return new Set(); }
}

function setHiddenGroups(set) {
    try { localStorage.setItem(filterKey(), JSON.stringify([...set])); } catch { /* not critical */ }
}

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
    lastPages = pages;
    const byChapter = groupByChapter(pages);
    [...byChapter].forEach(([chapter, chapterPages], i) => el.pagePickerBody.appendChild(chapterCard(chapter, chapterPages, i)));
    setExclusive(exclusive);
    applyView(localStorage.getItem(VIEW_STORAGE_KEY) || "grid");

    bindToolbar(byChapter);
    applySort(loadSort());
    renderStageFilter();
    applyFilters();
    // "Export…" on the project list lands here: open the export dialog straight away (once)
    const url = new URL(window.location.href);
    if (url.searchParams.get("export") === "1") {
        url.searchParams.delete("export");
        history.replaceState(null, "", url);
        exportTranslatedPages();
    }
}

/* ------------------------------ one chapter card ------------------------------ */

function chapterCard(chapter, chapterPages, index) {
    const live = chapterPages.filter((p) => p.status !== "excluded");
    const counts = countGroups(live);
    const doneCount = reviewedCount(counts);
    const fullyDone = live.length > 0 && doneCount === live.length;
    const chapterId = chapterPages[0].chapter_id;
    const updated = Math.max(0, ...live.map((p) => p.updated || 0));

    const details = document.createElement("details");
    details.className = "page-picker-chapter";
    details.dataset.chapter = chapter;
    details.dataset.order = index;                  // natural chapter order (groupByChapter), for "Sort: chapter number"
    details.dataset.score = doneScore(counts);
    details.dataset.reviewed = live.length ? doneCount / live.length : 0;
    details.dataset.updated = updated;
    details.pages = chapterPages;                   // tiles are built on the first open (buildGrid)

    // name on its own line, then the bar + "x/y reviewed", then one number chip per stage
    const summary = document.createElement("summary");
    summary.innerHTML = `
        <span class="chapter-title">
            <span class="chapter-head-row">
                ${fullyDone ? '<span class="chapter-done-check" title="The translation of every page is reviewed">&#10003;</span>' : ""}
                <span class="chapter-name">${escapeHtml(chapter)}</span>
                ${updated ? `<span class="chapter-updated" title="Last change on a page of this chapter">${escapeHtml(relativeTime(updated))}</span>` : ""}
            </span>
            <span class="chapter-progress" title="${escapeHtml(stageTip(counts))}">
                <span class="stage-bar chapter-bar">${stageBarHtml(counts)}</span>
                <span class="chapter-count">${doneCount}/${live.length} reviewed</span>
            </span>
            <span class="stage-chips">${stageChipsHtml(counts, escapeHtml)}</span>
        </span>
        <input type="checkbox" class="chapter-select" data-chapter-id="${chapterId}" data-chapter-name="${escapeHtml(chapter)}" title="Select (bulk delete, export)">
        <button class="copy-btn danger chapter-delete-btn" title="Delete this chapter and its pages">Delete</button>
    `;
    details.appendChild(summary);

    // Scroll the opened chapter to the top - but only when the USER clicked it (Expand all / filter open many at once,
    // and scrolling to each of them ended at the last one). The gap above comes from scroll-margin-top in the CSS.
    let userClick = false;
    summary.addEventListener("click", (e) => { userClick = !e.target.closest("input, button"); });
    details.addEventListener("toggle", () => {
        if (details.open) buildGrid(details);
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
    return details;
}

/**
 * Page tiles of a chapter, built when it is opened for the first time: a big project (hundreds of pages) does not
 * draw every tile up front - the filter works on the page data, the tiles only follow it.
 */
function buildGrid(details) {
    if (details.querySelector(".page-picker-grid")) return;
    const grid = document.createElement("div");
    grid.className = "page-picker-grid";
    [...details.pages].sort((a, b) => a.order - b.order).forEach((p) => grid.appendChild(pageTile(p)));
    details.appendChild(grid);
    applyFilterToGrid(details);
}

function pageTile(p) {
    const row = document.createElement("div");
    row.className = "page-picker-row" + (p.status === "excluded" ? " is-excluded" : "") + (p.error ? " has-error" : "");
    row.dataset.filename = p.file_name.toLowerCase();
    row.dataset.group = groupOf(p);

    const link = document.createElement("a");
    link.href = `editor.html?project=${state.projectId}&page=${p.page_id}`;
    const stage = shownStage(p.stage || p.status, p.layout);
    const [label, what] = PAGE_BADGES[stage] || [stage, ""];
    const lines = [p.file_name, what];
    if (p.bubbles) lines.push(`${p.translated ?? p.bubbles}/${p.bubbles} bubbles translated, ${p.reviewed}/${p.bubbles} reviewed`);
    if (p.page_accepted) lines.push(`${p.page_accepted} of them accepted with the whole page`);
    if (p.layout) lines.push("Bubbles / layout checked");
    const outdated = trackExported() && p.export === "outdated";
    if (outdated) lines.push("Changed after the last export");
    link.title = p.error ? `${p.file_name}\n${p.error}` : lines.filter(Boolean).join("\n");
    const progress = stage === "translated" && p.reviewed ? ` ${p.reviewed}/${p.bubbles}` : "";   // review in progress
    const layoutMark = p.layout && stage !== "checked" && stage !== "exported"
        ? '<span class="layout-mark" title="Bubbles / layout checked (the translation is not reviewed yet)">&#9638;</span>' : "";
    link.innerHTML = `
        <span class="page-name">${escapeHtml(p.file_name)}</span>
        ${layoutMark}
        <span class="badge stage-badge" data-status="${escapeHtml(stage)}">${escapeHtml(label)}${progress}${outdated ? " &middot; export old" : ""}</span>
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
    document.getElementById("pageSearchInput").oninput = () => applyFilters();
    document.getElementById("chapterSortSelect").onchange = (e) => applySort({ field: e.target.value, dir: SORT_FIELDS[e.target.value]?.dir });
    document.getElementById("chapterSortDir").onclick = () => {
        const s = loadSort();
        applySort({ field: s.field, dir: s.dir === "asc" ? "desc" : "asc" });
    };
    document.getElementById("collapseAllBtn").onclick = () => { chapterEls().forEach((d) => { d.open = false; }); setExclusive(true); };
    document.getElementById("expandAllBtn").onclick = () => { setExclusive(false); chapterEls().forEach((d) => { d.open = true; }); };
    document.getElementById("showExcludedCheckbox").onchange = () => showPagePicker();
    document.getElementById("goToNextUnprocessedBtn").onclick = goToNextUnprocessed;
    document.getElementById("addChaptersBtn").onclick = () => openImportDialog(showPagePicker);
    document.getElementById("openExportedBtn").onclick = () => api.projects.openExported(state.projectId).catch((e) => alert(e.message));
    document.getElementById("rescanBtn").onclick = rescanFolders;
    document.getElementById("replaceTranslationsBtn").onclick = () => openReplaceDialog(state.projectId);
    document.getElementById("exportPagesBtn").onclick = exportTranslatedPages;
    document.getElementById("chapterViewBtn").onclick = () =>
        applyView(el.pagePickerBody.classList.contains("as-list") ? "grid" : "list");
    setupBulkDelete(byChapter);
}

function applyView(mode) {
    el.pagePickerBody.classList.toggle("as-list", mode === "list");
    document.getElementById("chapterViewBtn").textContent = `Layout: ${mode}`;
    localStorage.setItem(VIEW_STORAGE_KEY, mode);
}

/* ---------------------- sorting, filename filter and status filter ---------------------- */

/** Sort fields: the value read from a chapter card + the natural direction (picked when the field changes). */
const SORT_FIELDS = {
    number: { dir: "asc", key: (d) => Number(d.dataset.order) },
    progress: { dir: "desc", key: (d) => Number(d.dataset.score) },
    reviewed: { dir: "desc", key: (d) => Number(d.dataset.reviewed) },
    updated: { dir: "desc", key: (d) => Number(d.dataset.updated) },
    name: { dir: "asc", key: (d) => d.dataset.chapter },
};

function loadSort() {
    let s;
    try { s = JSON.parse(localStorage.getItem(SORT_STORAGE_KEY) || "null"); } catch { s = localStorage.getItem(SORT_STORAGE_KEY); }
    // older versions stored a bare mode: number | least | most | recent
    const legacy = { least: { field: "progress", dir: "asc" }, most: { field: "progress", dir: "desc" }, recent: { field: "updated", dir: "desc" } };
    if (!s || typeof s !== "object") s = legacy[s] || { field: "number", dir: "asc" };
    if (!SORT_FIELDS[s.field]) s = { field: "number", dir: "asc" };
    if (s.dir !== "asc" && s.dir !== "desc") s.dir = SORT_FIELDS[s.field].dir;
    return s;
}

/** Sort / filter not at their defaults: the view tools on the right get the accent outline. */
function markViewTools() {
    const s = loadSort();
    document.getElementById("pageViewTools").classList.toggle("is-active",
        hiddenGroups().size > 0 || s.field !== "number" || s.dir !== "asc");
}

function applySort({ field, dir }) {
    if (!SORT_FIELDS[field]) field = "number";
    if (dir !== "asc" && dir !== "desc") dir = SORT_FIELDS[field].dir;
    localStorage.setItem(SORT_STORAGE_KEY, JSON.stringify({ field, dir }));
    document.getElementById("chapterSortSelect").value = field;
    const btn = document.getElementById("chapterSortDir");
    btn.textContent = dir === "asc" ? "↑" : "↓";
    btn.title = `${dir === "asc" ? "Ascending" : "Descending"} - click to reverse`;
    const { key } = SORT_FIELDS[field];
    const sign = dir === "asc" ? 1 : -1;
    const cmp = (a, b) => {
        const x = key(a), y = key(b);
        const c = typeof x === "string" ? compareChapterNames(x, y) : x - y;
        return sign * c || Number(a.dataset.order) - Number(b.dataset.order);
    };
    [...chapterEls()].sort(cmp).forEach((d) => el.pagePickerBody.appendChild(d));
    markViewTools();
}

/**
 * The legend over the chapters: every stage with its colour, name and page count - so nobody has to look the
 * colours up. It is also the quickest filter: click = only that stage (again = all), Ctrl / Shift + click = add or
 * remove it. Same hidden-stage set as the Status menu.
 */
function renderLegend(counts) {
    let legend = document.getElementById("stageLegend");
    if (!legend) {
        legend = document.createElement("div");
        legend.id = "stageLegend";
        legend.className = "stage-legend";
        el.pagePickerBody.before(legend);
    }
    const hidden = hiddenGroups();
    legend.innerHTML = activeGroups().map((g) => `
        <button type="button" class="stage-legend-item${hidden.has(g.id) ? " off" : ""}" data-group="${g.id}"
                aria-pressed="${!hidden.has(g.id)}" title="${escapeHtml(g.tip)}&#10;&#10;Click: only these pages (again: all). Ctrl / Shift + click: add / remove.">
            <i class="dot stage-${g.id}"></i><span>${escapeHtml(g.label)}</span><b>${counts[g.id]}</b>
        </button>`).join("")
        + `<button type="button" class="help-chip stage-legend-help" title="What do the stages mean? (Help)">?</button>`;
    legend.querySelectorAll(".stage-legend-item").forEach((b) => b.addEventListener("click", (e) => {
        const id = b.dataset.group;
        const all = activeGroups().map((g) => g.id);
        let set = hiddenGroups();
        if (e.ctrlKey || e.metaKey || e.shiftKey) {
            if (set.has(id)) set.delete(id); else set.add(id);
        } else {
            const solo = set.size === all.length - 1 && !set.has(id);
            set = solo ? new Set() : new Set(all.filter((x) => x !== id));
        }
        setHiddenGroups(set);
        renderStageFilter();
        applyFilters();
    }));
    legend.querySelector(".stage-legend-help").onclick = () => window.oktHelp?.open("stages");
}

/**
 * The status filter only HIDES page tiles (and chapters left without a visible page) in this list. The chapter bars
 * keep showing every page - the bar is the truth, the filter is a view. Nothing else reads it: the editor's
 * previous / next page still walk every page, so a page can never get "lost" behind a forgotten filter.
 */
function renderStageFilter() {
    const menu = document.getElementById("stageFilterMenu");
    const hidden = hiddenGroups();
    const counts = countGroups(lastPages.filter((p) => p.status !== "excluded"));
    renderLegend(counts);
    menu.innerHTML = `
        <div class="stage-filter-head">Show pages that are</div>
        ${activeGroups().map((g) => `
            <label class="checkbox-label" title="${escapeHtml(g.tip)}">
                <input type="checkbox" data-group="${g.id}" ${hidden.has(g.id) ? "" : "checked"}>
                <i class="dot stage-${g.id}"></i> ${escapeHtml(g.label)} <span class="text-muted">${counts[g.id]}</span>
            </label>`).join("")}
        <div class="stage-filter-actions">
            <button type="button" data-act="all">All</button>
            <button type="button" data-act="work" title="Everything whose translation still needs you: OCR done, partly translated, translated by the AI">Still to review</button>
            <button type="button" data-act="layout" title="Translation reviewed, bubbles / lettering not checked yet">Layout to check</button>
        </div>
        <hr>
        <label class="checkbox-label" title="An export can go to any folder or a ZIP; off = an exported page counts as reviewed">
            <input type="checkbox" data-act="track" ${trackExported() ? "checked" : ""}> Show &ldquo;Exported&rdquo; as a stage</label>
        <button type="button" data-act="first" class="primary" title="Open the first page (in chapter order) that the filter shows">Open first matching page</button>`;
    menu.querySelectorAll("input[data-group]").forEach((box) => box.addEventListener("change", () => {
        const set = hiddenGroups();
        if (box.checked) set.delete(box.dataset.group); else set.add(box.dataset.group);
        setHiddenGroups(set);
        renderLegend(counts);
        applyFilters();
    }));
    const only = (keep) => {
        setHiddenGroups(new Set(activeGroups().map((g) => g.id).filter((id) => !keep.includes(id))));
        renderStageFilter();
        applyFilters();
    };
    menu.querySelector('[data-act="all"]').onclick = () => only(activeGroups().map((g) => g.id));
    menu.querySelector('[data-act="work"]').onclick = () => only(["ocr", "partial", "translated"]);
    menu.querySelector('[data-act="layout"]').onclick = () => only(["reviewed"]);
    menu.querySelector('[data-act="track"]').onchange = (e) => { setTrackExported(e.target.checked); showPagePicker(); };
    menu.querySelector('[data-act="first"]').onclick = openFirstMatching;
}

function pageMatches(p, query, hidden) {
    const group = groupOf(p);
    return (!query || p.file_name.toLowerCase().includes(query)) && (group === "excluded" || !hidden.has(group));
}

const currentQuery = () => document.getElementById("pageSearchInput").value.trim().toLowerCase();

/** Tiles of an already built grid follow the filter. */
function applyFilterToGrid(details) {
    const query = currentQuery();
    const hidden = hiddenGroups();
    details.querySelectorAll(".page-picker-row").forEach((row) => {
        const match = (!query || row.dataset.filename.includes(query))
            && (row.dataset.group === "excluded" || !hidden.has(row.dataset.group));
        row.classList.toggle("hidden", !match);
    });
}

function applyFilters() {
    const query = currentQuery();
    const hidden = hiddenGroups();
    const filtering = !!query || hidden.size > 0;
    setExclusive(!filtering);
    let shown = 0;
    chapterEls().forEach((details) => {
        const n = details.pages.filter((p) => pageMatches(p, query, hidden)).length;
        shown += n;
        details.classList.toggle("hidden", n === 0);
        applyFilterToGrid(details);
        if (query) details.open = n > 0;             // auto-expand chapters with a match while searching by name
    });
    const btn = document.getElementById("stageFilterBtn");
    btn.classList.toggle("active", hidden.size > 0);
    btn.querySelector(".filter-count").textContent = hidden.size ? `${activeGroups().length - hidden.size}/${activeGroups().length}` : "";
    markViewTools();
    let note = document.getElementById("filterNote");
    if (!note) {
        note = document.createElement("p");
        note.id = "filterNote";
        note.className = "text-muted filter-note";
        el.pagePickerBody.before(note);
    }
    const which = hidden.size ? activeGroups().filter((g) => !hidden.has(g.id)).map((g) => groupLabel(g.id)).join(", ") : "";
    note.textContent = filtering
        ? `Filter on${which ? ` (${which})` : ""}: ${shown} page(s) shown. The chapter bars still count every page.` : "";
}

function pagesInOrder(pages) {
    return [...pages].sort((a, b) => {
        const c = compareChapterNames(a.chapter, b.chapter);
        return c !== 0 ? c : a.order - b.order;
    });
}

function openFirstMatching() {
    const hidden = hiddenGroups();
    const next = pagesInOrder(lastPages).find((p) => p.status !== "excluded" && pageMatches(p, currentQuery(), hidden));
    if (!next) {
        alert("No page matches the filter.");
        return;
    }
    window.location.href = `editor.html?project=${state.projectId}&page=${next.page_id}`;
}

async function goToNextUnprocessed() {
    let pages;
    try {
        pages = await api.pages.listByProject(state.projectId);
    } catch (e) {
        alert(`Couldn't load pages: ${e.message}`);
        return;
    }
    const next = pagesInOrder(pages).find((p) => p.status === "pending" || p.status === "failed");
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

/* --------------------------- export translated pages --------------------------- */

const EXPORT_KEY = "okt-export-options";      // shared with the editor's "Export page" (editor-lettering.js)

function exportPrefs() {
    try {
        return { content: "translated", format: "png", quality: 92, zip: false, includeUntranslated: false, onlyReviewed: true,
                 ...JSON.parse(localStorage.getItem(EXPORT_KEY) || "{}") };
    } catch {
        return { content: "translated", format: "png", quality: 92, zip: false, includeUntranslated: false, onlyReviewed: true };
    }
}

/**
 * Export dialog: which pages (reviewed only / also AI drafts - with a warning), what, format, where (project's
 * exported/ folder or another one), ZIP. `pages` = the processed pages in scope, for the counts. Resolves to options
 * or null.
 */
function askExportOptions(label, pages) {
    const stageOf = (p) => p.stage || p.status;
    const done = pages.filter((p) => ["reviewed", "checked", "exported"].includes(stageOf(p))).length;
    const drafts = pages.filter((p) => ["translated", "partial"].includes(stageOf(p))).length;
    const prefs = exportPrefs();
    return new Promise((resolve) => {
        let customPath = "";
        const dlg = document.createElement("dialog");
        dlg.className = "import-dialog export-dialog";
        const radio = (name, value, text, checked, title = "") =>
            `<label class="checkbox-label" title="${title}"><input type="radio" name="${name}" value="${value}" ${checked ? "checked" : ""}> ${text}</label>`;
        dlg.innerHTML = `
            <h2 style="margin:0;">Export pages - ${escapeHtml(label)}</h2>
            <div class="export-grid">
                <span class="text-muted">Pages</span>
                <div class="export-options">
                    ${radio("which", "reviewed", `Reviewed by you only (${done})`, prefs.onlyReviewed, "Pages whose translation you reviewed: every bubble accepted or edited")}
                    ${radio("which", "all", `Also AI drafts (+${drafts} not reviewed)`, !prefs.onlyReviewed, "Pages with bubbles you have not looked at yet - the AI text goes out unchecked")}
                    <span class="export-warn hidden" id="exportWarn">${drafts} page(s) contain AI text nobody has reviewed yet.</span>
                </div>
                <span class="text-muted">What</span>
                <div class="export-options">
                    ${radio("content", "translated", "With the translation", prefs.content === "translated")}
                    ${radio("content", "clean", "Clean (text erased)", prefs.content === "clean", "For lettering in another program")}
                    ${radio("content", "both", "Both (clean copies get _clean)", prefs.content === "both")}
                </div>
                <span class="text-muted">Format</span>
                <div class="export-options">
                    ${radio("format", "png", "PNG (lossless)", prefs.format === "png")}
                    ${radio("format", "jpg", "JPG, quality", prefs.format === "jpg")}
                    <input type="number" id="exportQuality" min="50" max="100" value="${prefs.quality}" style="width:70px;">
                </div>
                <span class="text-muted">Where</span>
                <div class="export-options">
                    ${radio("where", "project", "Project folder &rsaquo; exported", true, "Next to raw and processed - one sub-folder per chapter")}
                    ${radio("where", "custom", "Another folder...", false)}
                    <span class="text-mono text-muted" id="exportPath"></span>
                </div>
                <span></span>
                <div class="export-options">
                    <label class="checkbox-label"><input type="checkbox" id="exportZip" ${prefs.zip ? "checked" : ""}> Pack into one ZIP file</label>
                    <label class="checkbox-label" title="Otherwise pages without any translation are left out"><input type="checkbox" id="exportUntranslated" ${prefs.includeUntranslated ? "checked" : ""}> Also pages without a translation (clean)</label>
                </div>
            </div>
            <div class="import-footer">
                <button data-act="cancel">Cancel</button>
                <button class="primary" data-act="ok">Export</button>
            </div>`;
        document.body.appendChild(dlg);
        let answer = null;
        const warn = dlg.querySelector("#exportWarn");
        const syncWarn = () => warn.classList.toggle("hidden",
            !(drafts && dlg.querySelector('input[name="which"]:checked').value === "all"));
        dlg.querySelectorAll('input[name="which"]').forEach((r) => r.addEventListener("change", syncWarn));
        syncWarn();
        dlg.querySelectorAll('input[name="where"]').forEach((r) => r.addEventListener("change", async () => {
            if (r.value !== "custom" || !r.checked) return;
            try {
                customPath = (await api.projects.selectFolder()).path || "";
            } catch (e) {
                alert(`Couldn't open the folder dialog: ${e.message}`);
            }
            if (!customPath) dlg.querySelector('input[name="where"][value="project"]').checked = true;
            dlg.querySelector("#exportPath").textContent = customPath;
        }));
        dlg.querySelector('[data-act="cancel"]').addEventListener("click", () => dlg.close());
        dlg.querySelector('[data-act="ok"]').addEventListener("click", () => {
            const value = (name) => dlg.querySelector(`input[name="${name}"]:checked`).value;
            const opts = {
                content: value("content"), format: value("format"),
                quality: Math.max(50, Math.min(100, parseInt(dlg.querySelector("#exportQuality").value, 10) || 92)),
                zip: dlg.querySelector("#exportZip").checked, includeUntranslated: dlg.querySelector("#exportUntranslated").checked,
                onlyReviewed: value("which") === "reviewed",
            };
            if (!opts.onlyReviewed && drafts && opts.content !== "clean"
                && !confirm(`${drafts} page(s) have bubbles you have not reviewed - their AI text is exported as it is.

Export them anyway?`)) return;
            try { localStorage.setItem(EXPORT_KEY, JSON.stringify(opts)); } catch { /* ignore */ }
            answer = { ...opts, path: value("where") === "custom" ? customPath : "" };
            dlg.close();
        });
        dlg.addEventListener("close", () => { dlg.remove(); resolve(answer); });
        dlg.showModal();
    });
}

async function exportTranslatedPages() {
    const btn = document.getElementById("exportPagesBtn");
    const chapterIds = [...el.pagePickerBody.querySelectorAll("input.chapter-select:checked")].map((b) => parseInt(b.dataset.chapterId, 10));
    const label = chapterIds.length ? `${chapterIds.length} selected chapter(s)` : "all chapters";
    const inScope = lastPages.filter((p) => p.status === "processed" && (!chapterIds.length || chapterIds.includes(p.chapter_id)));
    const opts = await askExportOptions(label, inScope);
    if (!opts) return;

    const original = btn.textContent;
    btn.disabled = true;
    try {
        const { job_id } = await api.projects.exportPages(state.projectId, {
            path: opts.path, chapter_ids: chapterIds, content: opts.content, format: opts.format, quality: opts.quality,
            zip: opts.zip, include_untranslated: opts.includeUntranslated, only_reviewed: opts.onlyReviewed,
        });
        for (;;) {
            const job = await api.system.job(job_id);
            if (job.state === "running") {
                btn.textContent = `Exporting ${job.done_bytes}/${job.total_bytes || "?"}`;
                await new Promise((r) => setTimeout(r, 600));
                continue;
            }
            if (job.state === "done") {
                const where = job.result?.path;
                showToast(job.message, 8000, where ? { label: "Open", run: () => api.system.openPath(where).catch((e) => alert(e.message)) } : null);
                if (trackExported()) showPagePicker();          // the pages just exported change stage
            }
            else alert(`Export failed: ${job.message}`);
            break;
        }
    } catch (e) {
        alert(`Export failed: ${e.message}`);
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
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

/* the status filter popover closes on a click elsewhere or Esc */
document.addEventListener("click", (e) => {
    const f = document.getElementById("stageFilter");
    if (f?.open && !f.contains(e.target)) f.open = false;
});
document.addEventListener("keydown", (e) => {
    const f = document.getElementById("stageFilter");
    if (e.key === "Escape" && f?.open) f.open = false;
});
