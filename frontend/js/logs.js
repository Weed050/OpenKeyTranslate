/**
 * @file frontend/js/logs.js
 * @description Debug/TM analytics table: pairs each zero-shot and
 * memory-injected TranslationLog row from the same translation pass
 * (run_id + bubble_id), diffs the shown variant against whatever the
 * user eventually confirmed for that exact source text (if anything), and shows
 * everything that was injected into the prompt / done afterwards (injection_json):
 * memory examples (EN -> PL pairs), exact phrase, glossary, terminology post-edit, case normalization.
 *
 * The A/B comparison uses the MODEL output of both variants (injection_json.model_output, i.e. before
 * the terminology post-edit and the ALL-CAPS normalization), so only the memory differs between them.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { diffHtml as sharedDiffHtml } from "./diff.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");

let allRows = [];
let correctionBySource = new Map();

window.addEventListener("DOMContentLoaded", async () => {
    renderSidebar(projectId);
    if (!projectId) {
        document.getElementById("noProject").classList.remove("hidden");
        return;
    }
    document.getElementById("logsFilterSelect")?.addEventListener("change", renderRows);
    await loadLogs();
});

async function loadLogs() {
    const tableBody = document.getElementById("logsBody");
    let logs, corrections;
    try {
        [logs, corrections] = await Promise.all([
            api.logs.listByProject(projectId),
            api.corrections.listByProject(projectId),
        ]);
    } catch (e) {
        tableBody.innerHTML = `<tr><td colspan="5">Couldn't load logs: ${e.message}</td></tr>`;
        return;
    }

    // Corrections come back newest-first, so the first match per source text is the latest one.
    correctionBySource = new Map();
    corrections.forEach((c) => {
        if (!correctionBySource.has(c.source_text)) correctionBySource.set(c.source_text, c);
    });

    const groups = new Map();
    logs.forEach((log) => {
        log.injection = parseJson(log.injection_json) || {};
        const key = `${log.run_id}:${log.bubble_id}`;
        if (!groups.has(key)) groups.set(key, {});
        groups.get(key)[log.variant] = log;
    });

    allRows = [...groups.values()].filter((g) => g.zero_shot || g.memory_injected);
    renderSummary(allRows);
    renderRows();
}

function lane(g) {
    const log = g.memory_injected;
    if (!log) return null;
    if (log.memory_lane || log.injection.lane) return log.memory_lane || log.injection.lane;
    // logs written before memory_lane existed: exact hits have an "exact" field / no similarity threshold
    const hints = parseJson(log.hints_json);
    if (hints) return hints.exact !== undefined ? "exact" : "fuzzy";
    return log.threshold_used == null ? "exact" : "fuzzy";
}

function shownLog(g) { return g.memory_injected || g.zero_shot; }

function postedited(g) { return !!shownLog(g).injection.terms?.postedit?.applied; }

const FILTERS = {
    all: () => true,
    memory: (g) => !!g.memory_injected,
    fuzzy: (g) => lane(g) === "fuzzy",
    exact: (g) => lane(g) === "exact",
    ab: (g) => !!(g.memory_injected && g.zero_shot),
    glossary: (g) => glossaryTerms(shownLog(g)).length > 0,
    postedit: (g) => postedited(g),
};

function renderRows() {
    const tableBody = document.getElementById("logsBody");
    const mode = document.getElementById("logsFilterSelect")?.value || "all";
    const rows = allRows.filter(FILTERS[mode] || FILTERS.all);
    document.getElementById("countBadge").textContent = rows.length;

    if (!rows.length) {
        tableBody.innerHTML = `<tr><td colspan="5" class="text-muted">${allRows.length
            ? "No bubbles match this filter."
            : "No translation logs yet - process a page to generate some."}</td></tr>`;
        return;
    }
    tableBody.innerHTML = "";
    rows.forEach((g) => tableBody.appendChild(logRow(g)));
}

function logRow(group) {
    const base = group.zero_shot || group.memory_injected;
    const finalCorrection = correctionBySource.get(base.source_text);
    const shownVariant = shownLog(group);
    const zeroShotIsCounterfactual = !!(group.memory_injected && group.zero_shot);

    const tr = document.createElement("tr");
    tr.innerHTML = `
        <td class="text-mono">${escapeHtml(base.source_text)}</td>
        <td>${group.zero_shot
            ? escapeHtml(group.zero_shot.output_text) + (zeroShotIsCounterfactual ? ' <span class="badge" title="Same payload without memory (A/B counterfactual)">A/B</span>' : "")
            : '<span class="text-muted">—</span>'}</td>
        <td>${group.memory_injected
            ? escapeHtml(group.memory_injected.output_text) + laneBadge(group) + similarityBadge(group.memory_injected.similarity_score) + keyBadge(group.memory_injected.key_label)
            : '<span class="text-muted">no memory match</span>'}</td>
        <td>${finalCorrection
            ? sharedDiffHtml(shownVariant.output_text, finalCorrection.final_translation, escapeHtml)
              + ((finalCorrection.kind || "corrected") === "approved" ? ' <span class="badge" title="Accepted unchanged">approved</span>' : "")
            : '<span class="text-muted">not corrected yet</span>'}</td>
        <td>${injectionCell(shownVariant)}</td>
    `;
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
        chips.push(`${examples.length} example${examples.length > 1 ? "s" : ""}`);
        parts.push(`<div><div class="inj-label">Memory examples (best first)</div>${examples.map((ex) => `
            <div class="inj-ex ${ex.kind === "corrected" ? "corrected" : ""}">
                ${ex.en ? `<div class="text-mono">${escapeHtml(ex.en)}</div>` : ""}
                <div>&rarr; ${escapeHtml(ex.pl || "")}</div>
                ${ex.rejected ? `<div class="inj-rejected" title="AI text the user replaced">${escapeHtml(ex.rejected)}</div>` : ""}
                <div class="text-muted" style="font-size:11px;">${ex.similarity != null ? `similarity ${Math.round(ex.similarity * 100)}%` : ""}${ex.kind ? ` · ${ex.kind}` : ""}</div>
            </div>`).join("")}</div>`);
    }
    if (exact !== undefined && exact !== null) {
        chips.push("exact");
        parts.push(`<div><div class="inj-label">Exact phrase from memory</div>${escapeHtml(exact)}</div>`);
    }
    if (glossary.length) {
        chips.push(`glossary ${glossary.length}`);
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
    if (meta.length) parts.push(`<div class="text-muted" style="font-size:11px;">${meta.map(escapeHtml).join(" · ")}</div>`);

    if (!parts.length) return '<span class="text-muted">nothing (plain zero-shot)</span>';
    return `<details class="inj"><summary>${chips.length ? chips.map(escapeHtml).join(", ") : "details"}</summary>
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
    box.classList.remove("hidden");
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
