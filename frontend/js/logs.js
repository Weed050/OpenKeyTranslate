/**
 * @file frontend/js/logs.js
 * @description Debug/TM analytics table: pairs each zero-shot and
 * memory-injected TranslationLog row from the same translation pass
 * (run_id + bubble_id), and diffs the shown variant against whatever the
 * user eventually confirmed for that exact source text (if anything).
 */
 
import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { diffHtml as sharedDiffHtml } from "./diff.js";
 
const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");
 
window.addEventListener("DOMContentLoaded", async () => {
    renderSidebar(projectId);
    if (!projectId) {
        document.getElementById("noProject").classList.remove("hidden");
        return;
    }
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
        tableBody.innerHTML = `<tr><td colspan="4">Couldn't load logs: ${e.message}</td></tr>`;
        return;
    }
 
    // Corrections come back newest-first, so the first match per source text is the latest one.
    const correctionBySource = new Map();
    corrections.forEach((c) => {
        if (!correctionBySource.has(c.source_text)) correctionBySource.set(c.source_text, c);
    });
 
    const groups = new Map();
    logs.forEach((log) => {
        const key = `${log.run_id}:${log.bubble_id}`;
        if (!groups.has(key)) groups.set(key, {});
        groups.get(key)[log.variant] = log;
    });
 
    const rows = [...groups.values()].filter((g) => g.zero_shot || g.memory_injected);
    document.getElementById("countBadge").textContent = rows.length;
    renderSummary(rows);
 
    if (!rows.length) {
        tableBody.innerHTML = `<tr><td colspan="4" class="text-muted">No translation logs yet - process a page to generate some.</td></tr>`;
        return;
    }
 
    tableBody.innerHTML = "";
    rows.forEach((g) => tableBody.appendChild(logRow(g, correctionBySource)));
}
 
function logRow(group, correctionBySource) {
    const base = group.zero_shot || group.memory_injected;
    const finalCorrection = correctionBySource.get(base.source_text);
    const shownVariant = group.memory_injected || group.zero_shot;
 
    const tr = document.createElement("tr");
    tr.innerHTML = `
        <td class="text-mono">${escapeHtml(base.source_text)}</td>
        <td>${group.zero_shot ? escapeHtml(group.zero_shot.output_text) : '<span class="text-muted">—</span>'}</td>
        <td>${group.memory_injected
            ? escapeHtml(group.memory_injected.output_text) + similarityBadge(group.memory_injected.similarity_score) + keyBadge(group.memory_injected.key_label) + glossaryBadge(group.memory_injected)
            : '<span class="text-muted">no memory match</span>'}</td>
        <td>${finalCorrection
            ? sharedDiffHtml(shownVariant.output_text, finalCorrection.final_translation, escapeHtml)
            : '<span class="text-muted">not corrected yet</span>'}</td>
    `;
    return tr;
}
 
function glossaryTerms(log) {
    try { return log.glossary_terms_used ? JSON.parse(log.glossary_terms_used) : []; } catch { return []; }
}

function glossaryBadge(log) {
    const terms = glossaryTerms(log);
    if (!terms.length) return "";
    const text = terms.map((t) => `${t.source_term} \u2192 ${t.target_term}`).join(", ");
    return ` <span class="badge" title="Glossary terms injected: ${escapeHtml(text)}">glossary ${terms.length}</span>`;
}

/** Quick health numbers for the memory experiment: does the hint fire at all, and does it change the output? */
function renderSummary(rows) {
    const box = document.getElementById("logsSummary");
    const injected = rows.filter((g) => g.memory_injected);
    const changed = injected.filter((g) => g.zero_shot && g.zero_shot.output_text !== g.memory_injected.output_text);
    const sims = injected.map((g) => g.memory_injected.similarity_score).filter((s) => s != null);
    const thresholds = [...new Set(rows.flatMap((g) => [g.memory_injected, g.zero_shot]).filter(Boolean).map((l) => l.threshold_used).filter((t) => t != null))];
    const glossaryFired = rows.filter((g) => glossaryTerms(g.zero_shot || g.memory_injected).length || (g.memory_injected && glossaryTerms(g.memory_injected).length)).length;
    const pct = (n) => rows.length ? ` (${Math.round((100 * n) / rows.length)}%)` : "";
    const stat = (label, value, hint = "") => `<div title="${escapeHtml(hint)}"><div class="field-label">${label}</div><div>${value}</div></div>`;
    box.innerHTML = [
        stat("Bubbles translated", rows.length),
        stat("Memory hint used", `${injected.length}${pct(injected.length)}`, "Bubbles where a stored correction cleared the similarity threshold"),
        stat("Hint changed the output", `${changed.length}${injected.length ? ` / ${injected.length}` : ""}`, "memory-injected text differs from the hint-free (zero-shot) text of the same bubble"),
        stat("Similarity of hints", sims.length ? `${Math.min(...sims).toFixed(2)} - ${Math.max(...sims).toFixed(2)}` : "-", "min - max cosine similarity among used hints"),
        stat("Threshold(s) seen", thresholds.length ? thresholds.map((t) => t.toFixed(2)).join(", ") : "-"),
        stat("Glossary fired", `${glossaryFired}${pct(glossaryFired)}`, "Bubbles with at least one glossary term injected"),
    ].join("");
    box.classList.remove("hidden");
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
    div.textContent = text;
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