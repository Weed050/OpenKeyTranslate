/**
 * @file frontend/js/logs.js
 * @description Debug/TM analytics table: pairs each zero-shot and
 * memory-injected TranslationLog row from the same translation pass
 * (run_id + bubble_id), and diffs the shown variant against whatever the
 * user eventually confirmed for that exact source text (if anything).
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";

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
            ? escapeHtml(group.memory_injected.output_text) + similarityBadge(group.memory_injected.similarity_score) + keyBadge(group.memory_injected.key_label)
            : '<span class="text-muted">no memory match</span>'}</td>
        <td>${finalCorrection
            ? diffHtml(shownVariant.output_text, finalCorrection.final_translation)
            : '<span class="text-muted">not corrected yet</span>'}</td>
    `;
    return tr;
}

function similarityBadge(score) {
    if (score === null || score === undefined) return "";
    return ` <span class="badge">${Math.round(score * 100)}%</span>`;
}

function keyBadge(label) {
    if (!label) return "";
    return ` <span class="badge" title="API key used">${escapeHtml(label)}</span>`;
}

/* --- lightweight word-level diff (LCS-based), for the last column --- */

function wordDiff(a, b) {
    const aw = a.split(/(\s+)/);
    const bw = b.split(/(\s+)/);
    const m = aw.length, n = bw.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) {
        for (let j = n - 1; j >= 0; j--) {
            dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }
    let i = 0, j = 0;
    const parts = [];
    while (i < m && j < n) {
        if (aw[i] === bw[j]) { parts.push({ t: aw[i], type: "same" }); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { parts.push({ t: aw[i], type: "removed" }); i++; }
        else { parts.push({ t: bw[j], type: "added" }); j++; }
    }
    while (i < m) { parts.push({ t: aw[i], type: "removed" }); i++; }
    while (j < n) { parts.push({ t: bw[j], type: "added" }); j++; }
    return parts;
}

function diffHtml(a, b) {
    if (a.trim() === b.trim()) return `<span class="text-muted">identical</span>`;
    return wordDiff(a, b)
        .map((p) => {
            if (p.type === "same") return escapeHtml(p.t);
            const cls = p.type === "removed" ? "diff-removed" : "diff-added";
            return `<span class="${cls}">${escapeHtml(p.t)}</span>`;
        })
        .join("");
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
