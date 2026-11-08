/**
 * @file frontend/js/logs.js
 * @description Translation history of a project. Every row = one bubble of one translation pass (the zero-shot and
 * memory-injected TranslationLog rows of the same run_id + bubble_id are paired): when, where (chapter, page,
 * bubble number - with a link into the editor), the source, what the AI gave, what the bubble holds NOW (with a diff)
 * and everything that went into the prompt with it (memory examples, glossary, terminology post-edit...).
 *
 * "Research view" (a switch, remembered) adds the thesis columns: the zero-shot counterfactual next to the
 * memory-injected output, lane / similarity badges, Model (+ key) and Temperature, and the summary numbers of the
 * memory experiment. "Columns" shows / hides columns; the normal and the research view remember separate sets
 * (localStorage okt-logs-cols-normal / -research), hidden columns are not built, so the rest share the width.
 * The A/B comparison uses the MODEL output of both variants (injection_json.model_output, i.e. before the
 * terminology post-edit and the ALL-CAPS normalization), so only the memory differs between them.
 *
 * Sorting: click a column name (Time, Where, Source, In the bubble now, Hints - bubbles with memory / glossary first). By time the rows are grouped by day, by
 * Where by chapter; both groups fold with a click. Rows are DRAWN in chunks (50 / 100 / 250 / all) - the whole
 * history is loaded once, so filters, sorting and the summary always see every bubble.
 * Display only: nothing here changes what is logged.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { diffHtml as sharedDiffHtml } from "./diff.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");
const RESEARCH_KEY = "okt-logs-research";
const SORT_KEY = "okt-logs-sort";             // {"key": time | where | source | now, "dir": asc | desc}
const PAGE_SIZE_KEY = "okt-logs-page-size";   // 50 | 100 | 250 | 0 (= all)

let allRows = [];
let memoryEntries = [];                      // corrections of the project (created_at): memory size of old logs
let shownCount = 0;
const folded = new Set(JSON.parse(sessionStorage.getItem("okt-logs-folded") || "[]"));   // group labels folded in this tab
const saveFolded = () => sessionStorage.setItem("okt-logs-folded", JSON.stringify([...folded]));

function pageSize() {
    const n = parseInt(localStorage.getItem(PAGE_SIZE_KEY) ?? "100", 10);
    return [50, 100, 250, 0].includes(n) ? n : 100;
}
const resetShown = () => { shownCount = pageSize() || Infinity; };

window.addEventListener("DOMContentLoaded", async () => {
    renderSidebar(projectId);
    if (!projectId) {
        document.getElementById("noProject").classList.remove("hidden");
        return;
    }
    const research = document.getElementById("researchToggle");
    research.checked = localStorage.getItem(RESEARCH_KEY) === "1";
    research.addEventListener("change", () => {
        localStorage.setItem(RESEARCH_KEY, research.checked ? "1" : "0");
        applyResearch();
        renderRows();
    });
    resetShown();
    const sizeSelect = document.getElementById("logsPageSize");
    sizeSelect.value = String(pageSize());
    sizeSelect.addEventListener("change", () => {
        localStorage.setItem(PAGE_SIZE_KEY, sizeSelect.value);
        resetShown();
        renderRows();
    });
    ["logsFilterSelect", "logsChapterSelect"].forEach((id) => document.getElementById(id).addEventListener("change", () => { resetShown(); renderRows(); }));
    document.getElementById("logsSearchInput").addEventListener("input", () => { resetShown(); renderRows(); });
    document.getElementById("foldAllBtn").addEventListener("click", () => {
        const labels = [...new Set(sortedRows().slice(0, shownCount).map(groupLabel).filter(Boolean))];
        const foldAll = labels.some((d) => !folded.has(d));
        labels.forEach((d) => (foldAll ? folded.add(d) : folded.delete(d)));
        saveFolded();
        renderRows();
    });
    const colsMenu = document.getElementById("colsMenu");
    document.addEventListener("click", (e) => { if (colsMenu.open && !colsMenu.contains(e.target)) colsMenu.open = false; });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") colsMenu.open = false; });
    applyResearch();
    watchBand();
    await loadLogs();
});

/**
 * The sticky filter band: its height goes into --band-h (the column names stick right under it), and it gets the
 * "stuck" class (title, count, bottom line) once the page is scrolled past its place.
 */
function watchBand() {
    const band = document.getElementById("logsBand");
    const wrap = document.querySelector(".logs-table-wrap");
    const setHeight = () => wrap.style.setProperty("--band-h", `${band.offsetHeight}px`);
    new ResizeObserver(setHeight).observe(band);
    setHeight();
    // the band sticks at the scroller's top PADDING edge, not at the window edge: "stuck" from exactly that moment, and
    // the band covers the padding gap above itself (logs.html .logs-band.stuck::before) so no row shows through it
    const root = document.querySelector(".main-content");
    const gap = parseFloat(getComputedStyle(root).paddingTop) || 0;
    band.style.setProperty("--band-gap", `${gap}px`);
    const sentinel = document.getElementById("logsBandSentinel");
    new IntersectionObserver(([entry]) => band.classList.toggle("stuck", !entry.isIntersecting),
        { root, rootMargin: `-${gap}px 0px 0px 0px` }).observe(sentinel);
}

const isResearch = () => document.getElementById("researchToggle").checked;

function applyResearch() {
    document.getElementById("logsSummary").classList.toggle("hidden", !isResearch() || !allRows.length);
    document.getElementById("logsIntro").textContent = isResearch()
        ? "Research view: every zero-shot vs. memory-injected pair the correction-memory experiment is evaluated from. Read-only - never fed back into translation. Click a column name to sort."
        : "What the AI translated, where, what it was given as hints - and what the bubble says now. Click a column name to sort.";
    renderColumnMenu();
    renderHead();
}

async function loadLogs() {
    const tableBody = document.getElementById("logsBody");
    let logs;
    try {
        [logs, memoryEntries] = await Promise.all([
            api.logs.listByProject(projectId),
            api.corrections.listByProject(projectId).catch(() => []),     // only for the hit-rate table
        ]);
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="${visibleColumns().length}">Couldn't load logs: ${escapeHtml(e.message)}</td></tr>`;
        return;
    }
    const groups = new Map();
    logs.forEach((log) => {
        log.injection = parseJson(log.injection_json) || {};
        const key = `${log.run_id}:${log.bubble_id}`;
        if (!groups.has(key)) groups.set(key, {});
        groups.get(key)[log.variant] = log;
    });
    allRows = [...groups.values()].filter((g) => g.zero_shot || g.memory_injected);

    const chapters = [...new Set(allRows.map((g) => base(g).chapter).filter(Boolean))].sort(natural);
    document.getElementById("logsChapterSelect").innerHTML = '<option value="">All chapters</option>'
        + chapters.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    renderSummary(allRows);
    countFilterOptions();
    applyResearch();
    renderRows();
}

/** "With memory (any lane) - 8": how many bubbles each filter would show (the rare ones are easy to miss). */
function countFilterOptions() {
    document.querySelectorAll("#logsFilterSelect option").forEach((opt) => {
        opt.dataset.label = opt.dataset.label || opt.textContent;
        const test = FILTERS[opt.value];
        opt.textContent = test && opt.value !== "all" ? `${opt.dataset.label} - ${allRows.filter(test).length}` : opt.dataset.label;
    });
}

const base = (g) => g.zero_shot || g.memory_injected;
function shownLog(g) { return g.memory_injected || g.zero_shot; }
const natural = (a, b) => String(a ?? "").localeCompare(String(b ?? ""), undefined, { numeric: true, sensitivity: "base" });

function lane(g) {
    const log = g.memory_injected;
    if (!log) return null;
    if (log.memory_lane || log.injection.lane) return log.memory_lane || log.injection.lane;
    // logs written before memory_lane existed: exact hits have an "exact" field / no similarity threshold
    const hints = parseJson(log.hints_json);
    if (hints) return hints.exact !== undefined ? "exact" : "fuzzy";
    return log.threshold_used == null ? "exact" : "fuzzy";
}

function postedited(g) { return !!shownLog(g).injection.terms?.postedit?.applied; }

const FILTERS = {
    all: () => true,
    changed: (g) => base(g).current_translation != null && norm(base(g).current_translation) !== norm(shownLog(g).output_text),
    unreviewed: (g) => base(g).reviewed === false,
    memory: (g) => !!g.memory_injected,
    fuzzy: (g) => lane(g) === "fuzzy",
    exact: (g) => lane(g) === "exact",
    ab: (g) => !!(g.memory_injected && g.zero_shot),
    glossary: (g) => glossaryTerms(shownLog(g)).length > 0,
    postedit: (g) => postedited(g),
};
const norm = (t) => (t || "").trim();

function filteredRows() {
    const mode = document.getElementById("logsFilterSelect").value || "all";
    const chapter = document.getElementById("logsChapterSelect").value;
    const q = document.getElementById("logsSearchInput").value.trim().toLowerCase();
    return allRows.filter((g) => {
        const b = base(g);
        if (chapter && b.chapter !== chapter) return false;
        if (q && ![b.source_text, shownLog(g).output_text, b.current_translation, b.file_name].some((t) => (t || "").toLowerCase().includes(q))) return false;
        return (FILTERS[mode] || FILTERS.all)(g);
    });
}

/* ------------------------------------------------------------------ columns + sorting */

/** What the bubble holds now, as one word: edited / accepted / accepted with the page / not reviewed / gone. */
function nowStatus(g) {
    const b = base(g);
    if (b.current_translation == null) return "gone";
    if (!b.reviewed) return "open";
    if (norm(b.current_translation) !== norm(shownLog(g).output_text)) return "edited";
    return b.page_accepted ? "page" : "accepted";
}
const STATUS_RANK = { edited: 0, accepted: 1, page: 2, open: 3, gone: 4 };

/** Hints column order: memory + glossary first, then memory, glossary, nothing. */
function hintRank(g) {
    const memory = !!g.memory_injected;
    const glossary = glossaryTerms(shownLog(g)).length > 0;
    return memory && glossary ? 0 : memory ? 1 : glossary ? 2 : 3;
}

/**
 * Columns of the current view (research: zero-shot + memory-injected instead of one "AI translation" column).
 * width: fixed px / % - the text columns without one share what is left. sort: the comparator (ascending) of a
 * sortable column + its natural first direction.
 */
function columns() {
    const cols = [
        { id: "time", label: "Time", width: "78px", sort: { dir: "desc", cmp: (a, b) => natural(base(a).created_at, base(b).created_at) || base(a).id - base(b).id },
          cell: (g, ctx) => `<td class="logs-time">${timeCell(base(g).created_at, ctx.sortKey !== "time")}</td>` },
        { id: "where", label: "Where", width: "180px",
          sort: { dir: "asc", cmp: (a, b) => natural(base(a).chapter, base(b).chapter) || natural(base(a).file_name, base(b).file_name)
                                             || (base(a).bubble_no || 1e9) - (base(b).bubble_no || 1e9) },
          title: "Chapter, page and the bubble's number on the page now - sorted by chapter, then page, then bubble",
          cell: (g) => `<td>${whereCell(base(g))}</td>` },
        { id: "source", label: "Source (EN)", sort: { dir: "asc", cmp: (a, b) => natural(base(a).source_text, base(b).source_text) },
          cell: (g) => `<td>${escapeHtml(base(g).source_text)}</td>` },
    ];
    if (isResearch()) {
        cols.push(
            { id: "zero", label: "Zero-shot (no memory)", cell: (g) => `<td>${zeroShotCell(g)}</td>` },
            { id: "memory", label: "Memory-injected", cell: (g) => `<td>${memoryCell(g)}</td>` },
        );
    } else {
        cols.push({ id: "ai", label: "AI translation", cell: (g) => `<td>${escapeHtml(shownLog(g).output_text || "")}</td>` });
    }
    cols.push(
        { id: "now", label: "In the bubble now", title: "Sorted: edited, accepted, accepted with the page, not reviewed, bubble gone",
          sort: { dir: "asc", cmp: (a, b) => STATUS_RANK[nowStatus(a)] - STATUS_RANK[nowStatus(b)] },
          cell: (g) => `<td>${nowCell(g)}</td>` },
        { id: "hints", label: "Hints given to the AI", width: "21%",
          title: "Sorted: memory + glossary, memory only, glossary only, no hints (then newest first)",
          sort: { dir: "asc", cmp: (a, b) => hintRank(a) - hintRank(b) || natural(base(b).created_at, base(a).created_at) },
          cell: (g) => `<td>${injectionCell(shownLog(g))}</td>` },
        { id: "model", label: "Model", width: "150px", hidden: { normal: true },
          title: "Model that wrote the shown translation and the API key that served it (the A/B pair always uses the same key and model)",
          sort: { dir: "asc", cmp: (a, b) => natural(shownLog(a).model_used, shownLog(b).model_used) || natural(shownLog(a).key_label, shownLog(b).key_label) },
          cell: (g) => `<td class="logs-model">${modelCell(shownLog(g))}</td>` },
        { id: "temp", label: "Temperature", width: "104px", hidden: { normal: true },
          title: "Sampling temperature of the call (logged since 07.11.2026; \"filled in\" = older rows, value entered afterwards from the setting of that time). Split the A/B data by it when it changed",
          sort: { dir: "asc", cmp: (a, b) => (temperatureOf(a) ?? 99) - (temperatureOf(b) ?? 99) || natural(base(b).created_at, base(a).created_at) },
          cell: (g) => `<td class="logs-temp">${temperatureCell(shownLog(g))}</td>` },
    );
    return cols;
}

/* ------------------------------------------------------------------ which columns are shown */

/** localStorage key of the column choice: the normal and the research view keep separate sets. */
const colsKey = () => `okt-logs-cols-${isResearch() ? "research" : "normal"}`;

function columnChoice() {
    try { return JSON.parse(localStorage.getItem(colsKey()) || "{}") || {}; } catch { return {}; }
}

/** Shown in this view: the user's choice, else the column's default (Model / Temperature: research view only). */
function isShown(col, choice = columnChoice()) {
    if (typeof choice[col.id] === "boolean") return choice[col.id];
    return !col.hidden?.[isResearch() ? "research" : "normal"];
}

/** The columns actually built (hidden ones are left out entirely, the rest share the width). Sorting still uses columns(). */
function visibleColumns() {
    const choice = columnChoice();
    const shown = columns().filter((c) => isShown(c, choice));
    return shown.length ? shown : columns().slice(0, 1);
}

function renderColumnMenu() {
    const pop = document.getElementById("colsPop");
    if (!pop) return;
    const choice = columnChoice();
    const cols = columns();
    const shownCount = cols.filter((c) => isShown(c, choice)).length;
    pop.innerHTML = `<div class="cols-head">${isResearch() ? "Research view" : "Normal view"} columns</div>`
        + cols.map((c) => {
            const on = isShown(c, choice);
            return `<label title="${escapeHtml(c.title || "")}"><input type="checkbox" data-col="${c.id}"${on ? " checked" : ""}${on && shownCount === 1 ? " disabled" : ""}> ${escapeHtml(c.label)}</label>`;
        }).join("")
        + '<button type="button" id="colsReset">Default columns</button>';
    pop.querySelectorAll("input[data-col]").forEach((box) => box.addEventListener("change", () => {
        const next = columnChoice();
        next[box.dataset.col] = box.checked;
        localStorage.setItem(colsKey(), JSON.stringify(next));
        renderColumnMenu();
        renderHead();
        renderRows();
    }));
    pop.querySelector("#colsReset").addEventListener("click", () => {
        localStorage.removeItem(colsKey());
        renderColumnMenu();
        renderHead();
        renderRows();
    });
}

function modelCell(log) {
    if (!log.model_used && !log.key_label) return '<span class="cell-none">-</span>';
    return withMeta(escapeHtml(log.model_used || "?"), keyBadge(log.key_label));
}

/** injection_json.temperature of the shown translation (null = not logged; the A/B pair shares one pool and value). */
function temperatureOf(g) {
    const t = shownLog(g).injection?.temperature;
    return typeof t === "number" ? t : null;
}

function temperatureCell(log) {
    const inj = log.injection || {};
    if (typeof inj.temperature !== "number") return '<span class="cell-none" title="Not logged">-</span>';
    const filled = inj.temperature_backfilled;
    return filled
        ? withMeta(`${inj.temperature}`, `<span class="cell-none" title="Translated before the temperature was logged: the value was entered afterwards (${escapeHtml(filled)}) from the setting of that time">filled in</span>`)
        : `${inj.temperature}`;
}

function loadSort() {
    let s;
    try { s = JSON.parse(localStorage.getItem(SORT_KEY) || "null"); } catch { s = null; }
    const col = s && columns().find((c) => c.id === s.key && c.sort);
    return col ? { key: s.key, dir: s.dir === "asc" ? "asc" : "desc" } : { key: "time", dir: "desc" };
}

function renderHead() {
    const cols = visibleColumns();
    const sort = loadSort();
    document.getElementById("logsCols").innerHTML = cols.map((c) => `<col${c.width ? ` style="width:${c.width}"` : ""}>`).join("");
    const head = document.getElementById("logsHead");
    head.innerHTML = cols.map((c) => {
        const sorted = c.sort && sort.key === c.id;
        const tip = c.sort ? `${c.title ? c.title + ". " : ""}Click to sort${sorted ? " (again: reverse)" : ""}` : c.title || "";
        return `<th data-col="${c.id}" class="${c.sort ? "sortable" : ""}${sorted ? " sorted" : ""}" title="${escapeHtml(tip)}">${escapeHtml(c.label)}${sorted
            ? `<span class="arrow">${sort.dir === "asc" ? "▴" : "▾"}</span>` : ""}</th>`;
    }).join("");
    head.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
        const col = cols.find((c) => c.id === th.dataset.col);
        const cur = loadSort();
        const dir = cur.key === col.id ? (cur.dir === "asc" ? "desc" : "asc") : col.sort.dir;
        localStorage.setItem(SORT_KEY, JSON.stringify({ key: col.id, dir }));
        resetShown();
        renderHead();
        renderRows();
    }));
}

function sortedRows() {
    const { key, dir } = loadSort();
    const col = columns().find((c) => c.id === key);
    const sign = dir === "asc" ? 1 : -1;
    return filteredRows().sort((a, b) => sign * col.sort.cmp(a, b));
}

/** Group label of a row: its day when sorted by time, its chapter when sorted by Where, none otherwise. */
function groupLabel(g) {
    const { key } = loadSort();
    if (key === "time") return dayLabel(base(g).created_at);
    if (key === "where") return base(g).chapter || "No chapter";
    return null;
}

/* ------------------------------------------------------------------ rows */

function renderRows() {
    const tableBody = document.getElementById("logsBody");
    const rows = sortedRows();
    const cols = visibleColumns();
    const span = cols.length;
    document.getElementById("countBadge").textContent = `${rows.length} bubble${rows.length === 1 ? "" : "s"}`;
    document.getElementById("bandCount").textContent = document.getElementById("countBadge").textContent;
    const more = document.getElementById("logsMore");
    const note = document.getElementById("logsShownNote");
    if (!rows.length) {
        tableBody.innerHTML = `<tr><td colspan="${span}" class="text-muted">${allRows.length
            ? "No bubbles match this filter."
            : "No translations yet - process a page to fill the history."}</td></tr>`;
        more.classList.add("hidden");
        note.textContent = "";
        return;
    }
    tableBody.innerHTML = "";
    const shown = rows.slice(0, shownCount);
    const perGroup = new Map();
    rows.forEach((g) => { const d = groupLabel(g); if (d) perGroup.set(d, (perGroup.get(d) || 0) + 1); });
    const { key } = loadSort();
    const ctx = { sortKey: key };
    let last = null;
    shown.forEach((g) => {
        const label = groupLabel(g);
        if (label && label !== last) {               // one separator row per day / chapter, a click folds it
            const sep = document.createElement("tr");
            sep.className = "logs-group" + (folded.has(label) ? " folded" : "");
            const n = perGroup.get(label);
            sep.innerHTML = `<td colspan="${span}"><span class="chev">&#9656;</span>${escapeHtml(label)}<span class="group-count">${n} bubble${n === 1 ? "" : "s"}</span></td>`;
            sep.title = folded.has(label) ? `Click to show this ${key === "where" ? "chapter" : "day"}` : `Click to fold this ${key === "where" ? "chapter" : "day"}`;
            sep.addEventListener("click", () => {
                if (folded.has(label)) folded.delete(label); else folded.add(label);
                saveFolded();
                renderRows();
            });
            tableBody.appendChild(sep);
            last = label;
        }
        if (!label || !folded.has(label)) tableBody.appendChild(logRow(g, cols, ctx));
    });
    const foldBtn = document.getElementById("foldAllBtn");
    foldBtn.disabled = !perGroup.size;
    const noun = key === "where" ? "chapters" : "days";
    foldBtn.textContent = perGroup.size && [...perGroup.keys()].every((d) => folded.has(d)) ? `Unfold all ${noun}` : `Fold all ${noun}`;
    const left = rows.length - shown.length;
    more.classList.toggle("hidden", left <= 0);
    const step = pageSize() || left;
    more.textContent = `Show ${Math.min(step, left)} more`;
    more.onclick = () => { shownCount += step; renderRows(); };
    note.textContent = left > 0 ? `${shown.length} of ${rows.length} shown` : `all ${rows.length} shown`;
}

function dayLabel(iso) {
    if (!iso) return "Unknown date";
    const d = new Date(iso);
    const today = new Date();
    const yesterday = new Date(Date.now() - 864e5);
    if (d.toDateString() === today.toDateString()) return "Today";
    if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
    return d.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "long", day: "numeric" });
}

/** Time of day; the date under it when the rows are not grouped by day (sorted by something else than Time). */
function timeCell(iso, withDate) {
    if (!iso) return "";
    const d = new Date(iso);
    const t = escapeHtml(d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }));
    return withDate ? `${t}<div class="logs-date">${escapeHtml(d.toLocaleDateString(undefined, { day: "numeric", month: "short" }))}</div>` : t;
}

function whereCell(b) {
    const page = (b.file_name || "").replace(/\.[^.]+$/, "");
    const bubble = b.bubble_no ? `#${b.bubble_no}` : '<span title="Deleted, merged or split since">bubble gone</span>';
    const href = `editor.html?project=${encodeURIComponent(projectId)}&page=${b.page_id}${b.bubble_no ? `&bubble=${b.bubble_no - 1}` : ""}`;
    return `<div class="logs-where">
        <span class="w-chapter">${escapeHtml(b.chapter || "?")}</span>
        <a class="w-open" href="${href}" title="Open this bubble in the editor">Open</a>
        <span class="w-page">${escapeHtml(page)} &middot; ${bubble}</span>
    </div>`;
}

/** Text + badges on their own line under it (badges never squeeze the text). */
const withMeta = (text, badges = "") => `<div>${text}</div><div class="cell-meta">${badges}</div>`;

function zeroShotCell(g) {
    const ab = !!(g.memory_injected && g.zero_shot);
    return g.zero_shot
        ? withMeta(escapeHtml(g.zero_shot.output_text), ab ? '<span class="badge" title="Same payload without memory (A/B counterfactual)">A/B</span>' : "")
        : '<span class="cell-none">-</span>';
}

function memoryCell(g) {
    return g.memory_injected
        ? withMeta(escapeHtml(g.memory_injected.output_text), laneBadge(g) + similarityBadge(g.memory_injected.similarity_score))
        : '<span class="cell-none" title="No memory entry was similar enough - only the zero-shot translation exists">-</span>';
}

function nowCell(g) {
    const b = base(g);
    const status = nowStatus(g);
    if (status === "gone") return '<span class="cell-none">bubble no longer on the page</span>';
    const ai = shownLog(g).output_text || "";
    const now = b.current_translation;
    const badge = {
        open: '<span class="badge stage-badge" data-status="translated" title="Not reviewed in the editor yet">not reviewed</span>',
        accepted: '<span class="badge stage-badge" data-status="reviewed" title="You accepted the AI text unchanged">accepted</span>',
        page: '<span class="badge stage-badge" data-status="reviewed" title="Accepted together with the whole page (Accept page / review mode) - maybe not read on its own">accepted with page</span>',
        edited: '<span class="badge stage-badge" data-status="checked" title="You edited the AI text">edited</span>',
    }[status];
    if (norm(now) === norm(ai)) {
        return status === "open" ? withMeta('<span class="cell-none">same as the AI</span>', badge) : withMeta(escapeHtml(now), badge);
    }
    return withMeta(`<span title="Changes from the AI text to what the bubble says now">${sharedDiffHtml(ai, now, escapeHtml)}</span>`, badge);
}

function logRow(group, cols, ctx) {
    const tr = document.createElement("tr");
    tr.className = "log-row";
    tr.innerHTML = cols.map((c) => c.cell(group, ctx)).join("");
    return tr;
}

/** Everything the model got with this bubble and what happened afterwards, as a collapsible block. */
function injectionCell(log) {
    const inj = log.injection || {};
    const legacyHints = parseJson(log.hints_json) || {};
    const examples = inj.examples || legacyHints.examples
        || (legacyHints.hints || []).map((pl) => ({ pl }));          // logs from before the EN->PL pairs
    const exact = inj.exact ?? legacyHints.exact;
    const glossary = inj.glossary || glossaryTerms(log);
    const terms = inj.terms;
    const parts = [];
    const chips = [];

    if (examples && examples.length) {
        chips.push(`${examples.length} from memory`);
        // one card per example: EN source, then the user's Polish - for a corrected one as a diff from the AI text
        // the user replaced (what the model was shown as "rejected"), so the change is visible at a glance
        parts.push(`<div><div class="inj-label">Your earlier translations shown as examples (best first)</div>${examples.map((ex) => {
            const meta = [ex.similarity != null ? `${Math.round(ex.similarity * 100)}% similar` : "",
                          ex.kind === "corrected" ? "you corrected the AI" : ex.kind === "approved" ? "you approved it" : ex.kind || ""].filter(Boolean).join(" · ");
            const pl = ex.rejected && ex.rejected !== ex.pl
                ? `<span title="AI text you replaced -> your text">${sharedDiffHtml(ex.rejected, ex.pl || "", escapeHtml)}</span>`
                : escapeHtml(ex.pl || "");
            return `<div class="inj-ex ${ex.kind === "corrected" ? "corrected" : ""}">
                ${meta ? `<div class="ex-meta">${escapeHtml(meta)}</div>` : ""}
                ${ex.en ? `<span class="k">EN</span><span>${escapeHtml(ex.en)}</span>` : ""}
                <span class="k">PL</span><span>${pl}</span>
            </div>`;
        }).join("")}</div>`);
    }
    if (exact !== undefined && exact !== null) {
        chips.push("exact phrase");
        parts.push(`<div><div class="inj-label">Exact phrase from memory</div>${escapeHtml(exact)}</div>`);
    }
    if (glossary.length) {
        chips.push(`${glossary.length} glossary`);
        parts.push(`<div><div class="inj-label">Glossary</div>${glossary.map((t) =>
            `${escapeHtml(t.source_term)} &rarr; ${escapeHtml(t.target_term)}`).join("<br>")}</div>`);
    }
    if (terms) {
        const missing = terms.missing || [];
        const pe = terms.postedit;
        if (missing.length) chips.push(pe?.applied ? "term fixed" : "term missing");
        parts.push(`<div><div class="inj-label">Terminology check</div>
            ${missing.length ? `<span class="inj-missing">missing in model output: ${missing.map(escapeHtml).join(", ")}</span>` : "all terms used"}
            ${pe ? `<div>post-edit ${pe.applied ? "applied" : "rejected (did not fix the term)"}:</div>
                    <div class="text-muted">draft: ${escapeHtml(pe.draft || "")}</div>
                    <div>result: ${escapeHtml(pe.result || "")}</div>
                    ${pe.still_missing?.length ? `<div class="inj-missing">still missing: ${pe.still_missing.map(escapeHtml).join(", ")}</div>` : ""}` : ""}
            ${terms.postedit_error ? `<div class="inj-missing">post-edit failed: ${escapeHtml(terms.postedit_error)}</div>` : ""}
        </div>`);
    }
    if (inj.model_output !== undefined && inj.model_output !== log.output_text) {
        parts.push(`<div><div class="inj-label">Raw model output</div>${escapeHtml(inj.model_output)}</div>`);
    }
    const meta = [];
    if (inj.case_normalized) { chips.push("CAPS"); meta.push("upper-cased to match the source"); }
    if (inj.batch_size) meta.push(`batch of ${inj.batch_size} bubble(s)`);
    if (inj.context_bubbles) meta.push(`${inj.context_bubbles} context bubble(s)`);
    if (inj.ab) meta.push(`A/B: ${inj.ab.replace("_", " ")}`);
    if (log.model_used) meta.push(log.model_used);
    if (typeof inj.temperature === "number") meta.push(`temperature ${inj.temperature}${inj.temperature_backfilled ? " (filled in afterwards)" : ""}`);
    if (log.key_label) meta.push(`key ${log.key_label}`);
    if (meta.length) parts.push(`<div class="inj-meta">${meta.map(escapeHtml).join(" · ")}</div>`);

    if (!chips.length && !parts.length) return '<span class="cell-none">-</span>';
    return `<details class="inj"><summary class="${chips.length ? "" : "inj-empty"}" title="${chips.length ? "What the AI got with this bubble" : "No memory or glossary hints - click for the request details"}">${chips.length ? chips.map(escapeHtml).join(", ") : "no hints"}</summary>
        <div class="inj-body">${parts.join("")}</div></details>`;
}

function parseJson(text) {
    if (!text) return null;
    try { return JSON.parse(text); } catch { return null; }
}

function glossaryTerms(log) {
    return parseJson(log?.glossary_terms_used) || [];
}

/** Model output of a variant: before post-edit / case normalization when known, so A/B compares memory only. */
function modelText(log) {
    const raw = log.injection?.model_output;
    return (raw !== undefined && raw !== null ? raw : log.output_text || "").trim();
}

/** Quick health numbers for the memory experiment: does the hint fire at all, and does it change the output? */
function renderSummary(rows) {
    const box = document.getElementById("logsSummary");
    const injected = rows.filter((g) => g.memory_injected);
    const fuzzy = injected.filter((g) => lane(g) === "fuzzy");
    const exact = injected.filter((g) => lane(g) === "exact");
    const paired = fuzzy.filter((g) => g.zero_shot);
    const changed = paired.filter((g) => modelText(g.zero_shot).toLowerCase() !== modelText(g.memory_injected).toLowerCase());
    const sims = fuzzy.map((g) => g.memory_injected.similarity_score).filter((s) => s != null);
    const thresholds = [...new Set(rows.flatMap((g) => [g.memory_injected, g.zero_shot]).filter(Boolean).map((l) => l.threshold_used).filter((t) => t != null))];
    const glossaryFired = rows.filter((g) => glossaryTerms(shownLog(g)).length).length;
    const termMissing = rows.filter((g) => (shownLog(g).injection.terms?.missing || []).length).length;
    const termFixed = rows.filter((g) => postedited(g)).length;
    const pct = (n, of = rows.length) => of ? ` (${Math.round((100 * n) / of)}%)` : "";
    const stat = (label, value, hint = "") => `<div title="${escapeHtml(hint)}"><div class="field-label">${label}</div><div>${value}</div></div>`;
    box.innerHTML = [
        stat("Bubbles translated", rows.length),
        stat("Memory: fuzzy examples", `${fuzzy.length}${pct(fuzzy.length)}`, "Bubbles that got EN->PL example pairs from the translation memory"),
        stat("Memory: exact phrase", `${exact.length}${pct(exact.length)}`, "Short lines reused verbatim (YES -> TAK) - reported separately, they say little about the fuzzy memory"),
        stat("Examples changed the output", `${changed.length}${paired.length ? ` / ${paired.length}` : ""}`, "Fuzzy-lane A/B pairs where the model output with memory differs from the output of the IDENTICAL payload without memory (same glossary and context). Compared before post-edit / CAPS, case-insensitive."),
        stat("Similarity of examples", sims.length ? `${Math.min(...sims).toFixed(2)} - ${Math.max(...sims).toFixed(2)}` : "-", "min - max cosine similarity of the best example (fuzzy lane)"),
        stat("Threshold(s) seen", thresholds.length ? thresholds.map((t) => t.toFixed(2)).join(", ") : "-"),
        stat("Glossary fired", `${glossaryFired}${pct(glossaryFired)}`, "Bubbles with at least one glossary term injected"),
        stat("Term missing / fixed", `${termMissing} / ${termFixed}`, "Bubbles whose model output missed a glossary term / of those, fixed by the terminology post-edit"),
    ].join("") + hitRateHtml(rows);
}

/* ------------------------------------------------------------------ memory hit rate vs memory size */

const SIZE_BUCKETS = [[0, 10], [10, 25], [25, 50], [50, 100], [100, 200], [200, 400], [400, Infinity]];
const WHAT_IF = [0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9];

/**
 * Memory size when the bubble was translated: logged since 2026-11-05 (injection_json.memory_probe.size); older logs:
 * the entries saved before that moment (approximate - deleted entries are not counted, a re-saved entry moved).
 */
function memorySizeAt(g) {
    const probe = shownLog(g).injection?.memory_probe;
    if (probe && probe.size != null) return { size: probe.size, exact: true };
    const t = base(g).created_at;
    if (!t) return null;
    return { size: memoryEntries.filter((c) => c.created_at && c.created_at < t).length, exact: false };
}

/**
 * The research question "does the memory fire more often as it grows - and at which size / threshold?":
 * share of translated bubbles that got memory, per memory size; and, for bubbles logged with a probe, how often the
 * best entry WOULD have cleared other thresholds (cosine only - the word-overlap gate is not applied here).
 */
function hitRateHtml(rows) {
    const buckets = SIZE_BUCKETS.map(([lo, hi]) => ({ lo, hi, n: 0, fuzzy: 0, exact: 0, approx: 0 }));
    rows.forEach((g) => {
        const at = memorySizeAt(g);
        if (!at) return;
        const bucket = buckets.find((b) => at.size >= b.lo && at.size < b.hi);
        bucket.n += 1;
        if (!at.exact) bucket.approx += 1;
        if (lane(g) === "fuzzy") bucket.fuzzy += 1;
        if (lane(g) === "exact") bucket.exact += 1;
    });
    const used = buckets.filter((b) => b.n);
    if (!used.length) return "";
    const pc = (n, of) => (of ? `${((100 * n) / of).toFixed(1)}%` : "-");
    const label = (b) => (b.hi === Infinity ? `${b.lo}+` : `${b.lo}-${b.hi - 1}`);
    const sizeRows = used.map((b) => `<tr><td>${label(b)}${b.approx ? '<span class="text-muted" title="Some of these bubbles were translated before the memory size was logged - size estimated from the save times of the entries"> ≈</span>' : ""}</td>
        <td>${b.n}</td><td>${pc(b.fuzzy + b.exact, b.n)}</td><td>${pc(b.fuzzy, b.n)}</td><td>${pc(b.exact, b.n)}</td></tr>`).join("");

    const probed = rows.map((g) => shownLog(g).injection?.memory_probe).filter((p) => p && p.lane === "fuzzy" && p.candidates);
    const current = probed.length ? probed[probed.length - 1].threshold : null;
    const whatIf = probed.length ? `
        <table class="hit-table"><caption>What if the similarity threshold were different?
            <span class="hit-sub">${probed.length} longer bubbles also logged how similar their best memory entry was, even below the threshold.
            Share of them that would have got examples at each threshold (cosine only; the current one is highlighted).</span></caption>
            <tr><th>Threshold</th>${WHAT_IF.map((t) => `<th class="${current != null && Math.abs(t - current) < 0.026 ? "now" : ""}">${t.toFixed(2)}</th>`).join("")}</tr>
            <tr><td>Would get examples</td>${WHAT_IF.map((t) => `<td>${pc(probed.filter((p) => p.best != null && p.best >= t).length, probed.length)}</td>`).join("")}</tr>
        </table>`
        : `<p class="text-muted hit-note"><b>What if the threshold were different?</b> - no data in this project yet. Bubbles translated since
            v0.3.1 (05.11.2026) also log how similar their best memory entry was, even below the threshold; once there are some,
            a table here shows how many would have got examples at a lower or a higher threshold.</p>`;
    return `<div class="hit-rate">
        <table class="hit-table"><caption>Does the memory help more often as it grows?
            <span class="hit-sub">Bubbles grouped by how many entries the project memory had when they were translated; the %
            columns = share of those bubbles that got something from memory. ≈ = memory size estimated (translated before it was logged).</span></caption>
            <tr><th>Memory size</th><th>Bubbles</th><th>Got memory</th><th>Examples (fuzzy)</th><th>Exact phrase</th></tr>${sizeRows}
        </table>${whatIf}</div>`;
}

function laneBadge(group) {
    const l = lane(group);
    if (!l) return "";
    return ` <span class="badge" title="${l === "exact" ? "Exact phrase reused from memory" : "Fuzzy EN->PL examples from memory"}">${l}</span>`;
}

function similarityBadge(score) {
    if (score === null || score === undefined) return "";
    return ` <span class="badge">${Math.round(score * 100)}%</span>`;
}

function keyBadge(label) {
    if (!label) return "";
    return ` <span class="badge" title="API key used">${escapeHtml(label)}</span>`;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
}

document.getElementById("exportLogsBtn")?.addEventListener("click", async () => {
    const data = await api.logs.export(projectId);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `logs_${data.project_name}.json`;
    a.click();
    URL.revokeObjectURL(url);
});
