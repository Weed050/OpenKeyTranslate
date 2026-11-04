/**
 * @file frontend/js/logs.js
 * @description Translation history of a project. Every row = one bubble of one translation pass (the zero-shot and
 * memory-injected TranslationLog rows of the same run_id + bubble_id are paired): when, where (chapter, page,
 * bubble number - with a link into the editor), the source, what the AI gave, what the bubble holds NOW (with a diff)
 * and everything that went into the prompt with it (memory examples, glossary, terminology post-edit...).
 *
 * "Research view" (a switch, remembered) adds the thesis columns: the zero-shot counterfactual next to the
 * memory-injected output, lane / similarity / key badges and the summary numbers of the memory experiment.
 * The A/B comparison uses the MODEL output of both variants (injection_json.model_output, i.e. before the
 * terminology post-edit and the ALL-CAPS normalization), so only the memory differs between them.
 *
 * Sorting: click a column name (Time, Where, Source, In the bubble now). By time the rows are grouped by day, by
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
    renderHead();
}

async function loadLogs() {
    const tableBody = document.getElementById("logsBody");
    let logs;
    try {
        logs = await api.logs.listByProject(projectId);
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="${columns().length}">Couldn't load logs: ${escapeHtml(e.message)}</td></tr>`;
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
    applyResearch();
    renderRows();
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
        { id: "hints", label: "Hints given to the AI", width: "21%", cell: (g) => `<td>${injectionCell(shownLog(g))}</td>` },
    );
    return cols;
}

function loadSort() {
    let s;
    try { s = JSON.parse(localStorage.getItem(SORT_KEY) || "null"); } catch { s = null; }
    const col = s && columns().find((c) => c.id === s.key && c.sort);
    return col ? { key: s.key, dir: s.dir === "asc" ? "asc" : "desc" } : { key: "time", dir: "desc" };
}

function renderHead() {
    const cols = columns();
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
    const cols = columns();
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
        ? withMeta(escapeHtml(g.memory_injected.output_text), laneBadge(g) + similarityBadge(g.memory_injected.similarity_score) + keyBadge(g.memory_injected.key_label))
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
    ].join("");
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
