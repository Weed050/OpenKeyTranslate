/**
 * @file frontend/js/memory.js
 * @description Correction-memory browser (the "TM analytics" panel):
 * lists every stored Correction for a project with a trash button, so a
 * stale or wrong hint can be removed from future retrieval.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(projectId);
    if (!projectId) {
        document.getElementById("noProject").classList.remove("hidden");
        return;
    }
    loadCorrections();
});

let allCorrections = [];

async function loadCorrections() {
    const listEl = document.getElementById("correctionList");
    try {
        allCorrections = await api.corrections.listByProject(projectId);
    } catch (e) {
        listEl.innerHTML = `<p class="text-muted">Couldn't load corrections: ${e.message}</p>`;
        return;
    }

    document.getElementById("countBadge").textContent = allCorrections.length;

    if (!allCorrections.length) {
        listEl.innerHTML = `<p class="text-muted">No corrections stored yet for this project.</p>`;
        return;
    }

    renderList();
}

function renderList() {
    const listEl = document.getElementById("correctionList");
    const query = (document.getElementById("memorySearchInput").value || "").toLowerCase();
    const sortMode = document.getElementById("memorySortSelect").value;

    let items = allCorrections.filter((c) =>
        !query ||
        c.source_text.toLowerCase().includes(query) ||
        c.final_translation.toLowerCase().includes(query)
    );

    const sorters = {
        newest: (a, b) => new Date(b.created_at) - new Date(a.created_at),
        oldest: (a, b) => new Date(a.created_at) - new Date(b.created_at),
        most_used: (a, b) => (b.reuse_count || 0) - (a.reuse_count || 0),
        least_used: (a, b) => (a.reuse_count || 0) - (b.reuse_count || 0),
    };
    items = [...items].sort(sorters[sortMode] || sorters.newest);

    listEl.innerHTML = "";
    if (!items.length) {
        listEl.innerHTML = `<p class="text-muted">No corrections match "${escapeHtml(query)}".</p>`;
        return;
    }
    items.forEach((c) => listEl.appendChild(correctionRow(c)));
}

function correctionRow(c) {
    const details = document.createElement("details");
    details.className = "correction-row";
    details.innerHTML = `
        <summary>
            <span class="correction-summary-text">${escapeHtml(c.final_translation)}</span>
            <span class="badge" title="Times used as a hint">used ${c.reuse_count || 0}×</span>
        </summary>
        <div class="correction-body">
            <div>
                <div class="field-label">Source (EN)</div>
                <div class="field-value">${escapeHtml(c.source_text)}</div>
            </div>
            <div>
                <div class="field-label">AI's original proposal</div>
                <div class="field-value">${escapeHtml(c.ai_translation || "(none)")}</div>
            </div>
            <div>
                <div class="field-label">User's final translation</div>
                <div class="field-value">${escapeHtml(c.final_translation)}</div>
            </div>
            <div class="text-muted" style="font-size:11px;">Saved ${formatDate(c.created_at)}</div>
            <button class="danger" data-id="${c.id}">Delete from memory</button>
        </div>
    `;
    details.querySelector("button.danger").addEventListener("click", (event) => {
        event.preventDefault();
        deleteCorrection(c.id, details);
    });
    return details;
}

async function deleteCorrection(id, rowEl) {
    if (!confirm("Remove this correction from memory? It will no longer be suggested as a hint.")) return;
    try {
        await api.corrections.delete(id);
        rowEl.remove();
        allCorrections = allCorrections.filter((c) => c.id !== id);
        const badge = document.getElementById("countBadge");
        badge.textContent = Math.max(0, parseInt(badge.textContent || "0", 10) - 1);
    } catch (e) {
        alert(`Delete failed: ${e.message}`);
    }
}

function formatDate(iso) {
    if (!iso) return "unknown date";
    return new Date(iso).toLocaleString();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}


document.getElementById("memorySearchInput")?.addEventListener("input", renderList);
document.getElementById("memorySortSelect")?.addEventListener("change", renderList);

document.getElementById("exportMemoryBtn")?.addEventListener("click", async () => {
    const data = await api.corrections.export(projectId);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `corrections_${data.project_name}.json`;
    a.click();
    URL.revokeObjectURL(url);
});

document.getElementById("importMemoryBtn")?.addEventListener("click", () => {
    document.getElementById("importMemoryFile").click();
});
document.getElementById("importMemoryFile")?.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const payload = JSON.parse(await file.text());
        const result = await api.corrections.import(projectId, payload);
        alert(result.message);
        loadCorrections();
    } catch (err) {
        alert(`Import failed: ${err.message}`);
    }
    e.target.value = "";
});