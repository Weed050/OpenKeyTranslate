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
 * Display only: nothing here changes what is logged.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { diffHtml as sharedDiffHtml } from "./diff.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");
const RESEARCH_KEY = "okt-logs-research";
const PAGE_SIZE = 200;

let allRows = [];
let shownCount = PAGE_SIZE;

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
    ["logsFilterSelect", "logsChapterSelect"].forEach((id) => document.getElementById(id).addEventListener("change", () => { shownCount = PAGE_SIZE; renderRows(); }));
    document.getElementById("logsSearchInput").addEventListener("input", () => { shownCount = PAGE_SIZE; renderRows(); });
    applyResearch();
    await loadLogs();
});

const isResearch = () => document.getElementById("researchToggle").checked;

function applyResearch() {
    document.body.classList.toggle("logs-research", isResearch());
    document.getElementById("logsSummary").classList.toggle("hidden", !isResearch() || !allRows.length);
    document.getElementById("logsIntro").textContent = isResearch()
        ? "Research view: every zero-shot vs. memory-injected pair the correction-memory experiment is evaluated from. Read-only - never fed back into translation."
        : "What the AI translated, where, what it was given as hints - and what the bubble says now.";
}

async function loadLogs() {
    const tableBody = document.getElementById("logsBody");
    let logs;
    try {
        logs = await api.logs.listByProject(projectId);
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="8">Couldn't load logs: ${escapeHtml(e.message)}</td></tr>`;
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

    const chapters = [...new Set(allRows.map((g) => base(g).chapter).filter(Boolean))]
        .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    document.getElementById("logsChapterSelect").innerHTML = '<option value="">All chapters</option>'
        + chapters.map((c) => `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`).join("");
    renderSummary(allRows);
    applyResearch();
    renderRows();
}

const base = (g) => g.zero_shot || g.memory_injected;
function shownLog(g) { return g.memory_injected || g.zero_shot; }

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

function renderRows() {
    const tableBody = document.getElementById("logsBody");
    const rows = filteredRows();
    document.getElementById("countBadge").textContent = `${rows.length} bubble${rows.length === 1 ? "" : "s"}`;
    if (!rows.length) {
        tableBody.innerHTML = `<tr><td colspan="8" class="text-muted">${allRows.length
            ? "No bubbles match this filter."
            : "No translations yet - process a page to fill the history."}</td></tr>`;
        document.getElementById("logsMore").classList.add("hidden");
        return;
    }
    tableBody.innerHTML = "";
    let lastDay = null;
    rows.slice(0, shownCount).forEach((g) => {
        const day = dayLabel(base(g).created_at);
        if (day !== lastDay) {                       // newest first: one separator row per day
            const sep = document.createElement("tr");
            sep.className = "logs-day";
            sep.innerHTML = `<td colspan="8">${escapeHtml(day)}</td>`;
            tableBody.appendChild(sep);
            lastDay = day;
        }
        tableBody.appendChild(logRow(g));
    });
    const more = document.getElementById("logsMore");
    more.classList.toggle("hidden", rows.length <= shownCount);
    more.textContent = `Show ${Math.min(PAGE_SIZE, rows.length - shownCount)} more (${rows.length - shownCount} left)`;
    more.onclick = () => { shownCount += PAGE_SIZE; renderRows(); };
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

function timeLabel(iso) {
    return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }) : "";
}

function whereCell(b) {
    const page = (b.file_name || "").replace(/\.[^.]+$/, "");
    const bubble = b.bubble_no ? `bubble ${b.bubble_no}` : '<span class="text-muted" title="Deleted, merged or split since">bubble gone</span>';
    const href = `editor.html?project=${encodeURIComponent(projectId)}&page=${b.page_id}${b.bubble_no ? `&bubble=${b.bubble_no - 1}` : ""}`;
    return `<div class="logs-where">${escapeHtml(b.chapter || "?")}</div>
        <div>${escapeHtml(page)} &middot; ${bubble}</div>
        <a href="${href}" title="Open this bubble in the editor">Open &rarr;</a>`;
}

function nowCell(g) {
    const b = base(g);
    if (b.current_translation == null) return '<span class="text-muted">bubble no longer on the page</span>';
    const ai = shownLog(g).output_text || "";
    const now = b.current_translation;
    if (norm(now) === norm(ai)) {
        return b.reviewed
            ? `${escapeHtml(now)} <span class="badge" data-status="reviewed" title="You accepted the AI text unchanged">accepted</span>`
            : `<span class="text-muted">same as the AI</span> <span class="badge" data-status="translated" title="Not reviewed in the editor yet">not reviewed</span>`;
    }
    const tag = b.reviewed ? "" : ' <span class="badge" data-status="translated">not reviewed</span>';
    return `<div title="Changes from the AI text to what the bubble says now">${sharedDiffHtml(ai, now, escapeHtml)}</div>${tag}`;
}

function logRow(group) {
    const b = base(group);
    const shown = shownLog(group);
    const ab = !!(group.memory_injected && group.zero_shot);
    const tr = document.createElement("tr");
    tr.innerHTML = `
        <td class="logs-time">${escapeHtml(timeLabel(b.created_at))}</td>
        <td>${whereCell(b)}</td>
        <td class="text-mono">${escapeHtml(b.source_text)}</td>
        <td class="col-simple">${escapeHtml(shown.output_text || "")}</td>
        <td class="col-research">${group.zero_shot
            ? escapeHtml(group.zero_shot.output_text) + (ab ? ' <span class="badge" title="Same payload without memory (A/B counterfactual)">A/B</span>' : "")
            : '<span class="text-muted">-</span>'}</td>
        <td class="col-research">${group.memory_injected
            ? escapeHtml(group.memory_injected.output_text) + laneBadge(group) + similarityBadge(group.memory_injected.similarity_score) + keyBadge(group.memory_injected.key_label)
            : '<span class="text-muted">no memory match</span>'}</td>
        <td>${nowCell(group)}</td>
        <td>${injectionCell(shown)}</td>`;
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
        parts.push(`<div><div class="inj-label">Your earlier translations shown as examples (best first)</div>${examples.map((ex) => `
            <div class="inj-ex ${ex.kind === "corrected" ? "corrected" : ""}">
                ${ex.en ? `<div class="text-mono">${escapeHtml(ex.en)}</div>` : ""}
                <div>&rarr; ${escapeHtml(ex.pl || "")}</div>
                ${ex.rejected ? `<div class="inj-rejected" title="AI text the user replaced">${escapeHtml(ex.rejected)}</div>` : ""}
                <div class="text-muted" style="font-size:11px;">${ex.similarity != null ? `similarity ${Math.round(ex.similarity * 100)}%` : ""}${ex.kind ? ` · ${ex.kind}` : ""}</div>
            </div>`).join("")}</div>`);
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
    if (meta.length) parts.push(`<div class="text-muted" style="font-size:11px;">${meta.map(escapeHtml).join(" · ")}</div>`);

    if (!chips.length && !parts.length) return '<span class="text-muted">no hints</span>';
    return `<details class="inj"><summary>${chips.length ? chips.map(escapeHtml).join(", ") : "no hints - details"}</summary>
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
