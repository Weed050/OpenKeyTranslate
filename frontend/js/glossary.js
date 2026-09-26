/**
 * @file frontend/js/glossary.js
 * @description Deterministic term-base panel: list/add/delete glossary
 * terms, scan existing Corrections for repeat-fix suggestions the user can
 * accept, and inspect which logged translations a term actually fired on
 * (see services/glossary_service.py get_term_usage).
 */

import { api } from "./api.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");

let allTerms = [];
const usageCache = new Map();

window.addEventListener("DOMContentLoaded", () => {
    if (!projectId) return; // noProject banner already handled by memory.js
    loadGlossary();
});

async function loadGlossary() {
    const listEl = document.getElementById("glossaryList");
    try {
        allTerms = await api.glossary.listByProject(projectId);
    } catch (e) {
        listEl.innerHTML = `<p class="text-muted">Couldn't load glossary: ${e.message}</p>`;
        return;
    }

    document.getElementById("glossaryCountBadge").textContent = `${allTerms.length} terms`;

    if (!allTerms.length) {
        listEl.innerHTML = `<p class="text-muted">No glossary terms yet. Add one above, or scan corrections for suggestions.</p>`;
        return;
    }

    listEl.innerHTML = "";
    allTerms.forEach((t) => listEl.appendChild(termRow(t)));
}

function termRow(t) {
    const details = document.createElement("details");
    details.className = "item-row";
    details.innerHTML = `
        <summary>
            <span class="item-summary-text">${escapeHtml(t.source_term)} &rarr; ${escapeHtml(t.target_term)}</span>
            ${t.auto_detected ? `<span class="badge" title="Accepted from a suggestion">auto</span>` : ""}
        </summary>
        <div class="item-body">
            <div style="display:flex; gap:8px;">
                <button class="copy-btn usage-toggle" data-usage="${t.id}">Show usage</button>
                <button class="danger" data-id="${t.id}">Delete term</button>
            </div>
            <div class="usage-panel hidden" id="glossary-usage-${t.id}"></div>
        </div>
    `;
    details.querySelector("button.danger").addEventListener("click", (event) => {
        event.preventDefault();
        deleteTerm(t.id, details);
    });
    details.querySelector("[data-usage]").addEventListener("click", (event) => {
        event.preventDefault();
        toggleUsage(t.id);
    });
    return details;
}

async function toggleUsage(termId) {
    const panel = document.getElementById(`glossary-usage-${termId}`);
    if (!panel) return;

    if (!panel.classList.contains("hidden")) {
        panel.classList.add("hidden");
        return;
    }

    panel.classList.remove("hidden");
    if (usageCache.has(termId)) {
        renderUsage(panel, usageCache.get(termId));
        return;
    }

    panel.innerHTML = `<p class="usage-empty">Loading...</p>`;
    try {
        const usage = await api.glossary.usage(termId);
        usageCache.set(termId, usage);
        renderUsage(panel, usage);
    } catch (e) {
        panel.innerHTML = `<p class="usage-empty">Couldn't load usage: ${e.message}</p>`;
    }
}

function renderUsage(panel, usage) {
    if (!usage.length) {
        panel.innerHTML = `<p class="usage-empty">Never fired in a translation yet.</p>`;
        return;
    }
    panel.innerHTML = usage.map((u) => `
        <div class="usage-row">
            <div><strong>${escapeHtml(u.source_text)}</strong> <span class="badge">${escapeHtml(u.variant)}</span></div>
            <div>&rarr; ${escapeHtml(u.output_text)}</div>
            <div class="text-muted" style="font-size:10px;">Page #${u.page_id} · ${formatDate(u.created_at)}</div>
        </div>
    `).join("");
}

async function deleteTerm(id, rowEl) {
    if (!confirm("Remove this glossary term? It will stop being injected into future translations.")) return;
    try {
        await api.glossary.delete(id);
        rowEl.remove();
        allTerms = allTerms.filter((t) => t.id !== id);
        usageCache.delete(id);
        document.getElementById("glossaryCountBadge").textContent = `${allTerms.length} terms`;
    } catch (e) {
        alert(`Delete failed: ${e.message}`);
    }
}

document.getElementById("glossaryAddBtn")?.addEventListener("click", async () => {
    const sourceInput = document.getElementById("glossarySourceInput");
    const targetInput = document.getElementById("glossaryTargetInput");
    const status = document.getElementById("glossaryStatus");

    const source_term = sourceInput.value.trim();
    const target_term = targetInput.value.trim();
    if (!source_term || !target_term) {
        status.textContent = "Enter both an English and a Polish term.";
        return;
    }

    try {
        await api.glossary.add(projectId, { source_term, target_term });
        sourceInput.value = "";
        targetInput.value = "";
        status.textContent = "Term added.";
        loadGlossary();
    } catch (e) {
        status.textContent = `Failed: ${e.message}`;
    }
});

document.getElementById("scanSuggestionsBtn")?.addEventListener("click", async () => {
    const block = document.getElementById("suggestionsBlock");
    const list = document.getElementById("suggestionsList");
    const status = document.getElementById("glossaryStatus");

    status.textContent = "Scanning corrections...";
    let suggestions;
    try {
        suggestions = await api.glossary.suggestions(projectId);
    } catch (e) {
        status.textContent = `Scan failed: ${e.message}`;
        return;
    }
    status.textContent = "";

    if (!suggestions.length) {
        block.classList.add("hidden");
        alert("No repeat-fix patterns found in the stored corrections yet.");
        return;
    }

    block.classList.remove("hidden");
    list.innerHTML = "";
    suggestions.forEach((s) => list.appendChild(suggestionRow(s)));
});

function suggestionRow(s) {
    const row = document.createElement("div");
    row.className = "suggestion-row";
    row.innerHTML = `
        <span><strong>${escapeHtml(s.source_term)}</strong></span>
        <span class="suggestion-arrow">${escapeHtml(s.old_translation)} &rarr; ${escapeHtml(s.new_translation)}</span>
        <span class="badge" title="How many corrections show this pattern">${s.occurrences}×</span>
        <span class="suggestion-example" title="${escapeHtml(s.example_source_text)}">${escapeHtml(s.example_source_text)}</span>
        <button class="primary" data-accept>Accept</button>
        <button class="copy-btn" data-reject>Dismiss</button>
    `;
    row.querySelector("[data-accept]").addEventListener("click", async () => {
        try {
            await api.glossary.add(projectId, {
                source_term: s.source_term, target_term: s.new_translation,
                auto_detected: true, occurrences: s.occurrences,
            });
            row.remove();
            loadGlossary();
        } catch (e) {
            alert(`Couldn't add term: ${e.message}`);
        }
    });
    row.querySelector("[data-reject]").addEventListener("click", () => row.remove());
    return row;
}

document.getElementById("exportGlossaryBtn")?.addEventListener("click", async () => {
    const data = await api.glossary.export(projectId);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `glossary_${data.project_name}.json`;
    a.click();
    URL.revokeObjectURL(url);
});

document.getElementById("importGlossaryBtn")?.addEventListener("click", () => {
    document.getElementById("importGlossaryFile").click();
});
document.getElementById("importGlossaryFile")?.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    try {
        const payload = JSON.parse(await file.text());
        const result = await api.glossary.import(projectId, payload);
        alert(result.message);
        loadGlossary();
    } catch (err) {
        alert(`Import failed: ${err.message}`);
    }
    e.target.value = "";
});

function formatDate(iso) {
    if (!iso) return "unknown date";
    return new Date(iso).toLocaleString();
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}