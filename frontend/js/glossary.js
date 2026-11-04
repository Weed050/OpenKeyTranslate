/**
 * @file frontend/js/glossary.js
 * @description Deterministic term-base panel: list/add/delete glossary
 * terms, scan existing Corrections for repeat-fix suggestions the user can
 * accept, and inspect which logged translations a term actually fired on
 * (see services/glossary_service.py get_term_usage).
 *
 * "Used" = translated bubbles where the term FIRED (was given to the AI with the bubble), counted from the
 * translation logs; "Show usage" lists exactly those bubbles - where they are, what the AI wrote and what the bubble
 * says now, with the term highlighted in the texts.
 */

import { api } from "./api.js";
import { offerMemoryCleanup } from "./memory-links.js";
import { diffHtml } from "./diff.js";

const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");

let allTerms = [];
const usageCache = new Map();
let termSort = "en_asc";        // column header sort (memory.html #glossaryCols); first click natural, second reversed
const TERM_SORTS = { en: ["en_asc", "en_desc"], pl: ["pl_asc", "pl_desc"], used: ["most_used", "least_used"],
                     auto: ["auto_first", "manual_first"], added: ["newest", "oldest"] };
const cmp = (a, b) => (a || "").localeCompare(b || "", "pl", { sensitivity: "base" });
const TERM_SORTERS = {
    en_asc: (a, b) => cmp(a.source_term, b.source_term),
    en_desc: (a, b) => cmp(b.source_term, a.source_term),
    pl_asc: (a, b) => cmp(a.target_term, b.target_term),
    pl_desc: (a, b) => cmp(b.target_term, a.target_term),
    auto_first: (a, b) => (b.auto_detected ? 1 : 0) - (a.auto_detected ? 1 : 0) || cmp(a.source_term, b.source_term),
    manual_first: (a, b) => (a.auto_detected ? 1 : 0) - (b.auto_detected ? 1 : 0) || cmp(a.source_term, b.source_term),
    most_used: (a, b) => (b.used || 0) - (a.used || 0) || cmp(a.source_term, b.source_term),
    least_used: (a, b) => (a.used || 0) - (b.used || 0) || cmp(a.source_term, b.source_term),
    newest: (a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0),
    oldest: (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0),
};

document.querySelectorAll("#glossaryCols button[data-sort]").forEach((btn) => btn.addEventListener("click", () => {
    const [first, second] = TERM_SORTS[btn.dataset.sort];
    termSort = termSort === first ? second : first;
    renderTerms();
}));

function renderTerms() {
    const listEl = document.getElementById("glossaryList");
    listEl.innerHTML = "";
    [...allTerms].sort(TERM_SORTERS[termSort]).forEach((t) => listEl.appendChild(termRow(t)));
    document.querySelectorAll("#glossaryCols button[data-sort]").forEach((btn) => {
        const i = TERM_SORTS[btn.dataset.sort].indexOf(termSort);
        btn.classList.toggle("sorted", i >= 0);
        btn.querySelector(".arrow").textContent = i < 0 ? "" : i === 0 ? "\u25BE" : "\u25B4";
    });
}

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

    renderTerms();
}

function termRow(t) {
    const details = document.createElement("details");
    details.className = "item-row cols";
    const added = t.created_at ? new Date(t.created_at) : null;
    details.innerHTML = `
        <summary>
            <span class="cell main"><span>${escapeHtml(t.source_term)}</span></span>
            <span class="cell main"><span>&rarr; ${escapeHtml(t.target_term)}</span></span>
            <span class="cell num" title="${t.used ? `Fired in ${t.used} translated bubble(s)${t.last_used ? `, last on ${escapeHtml(new Date(t.last_used).toLocaleString())}` : ""}` : "Never given to the AI yet (the word did not appear in a translated bubble since it was added)"}">${t.used || 0}&times;</span>
            <span class="cell">${t.auto_detected ? `<span class="badge" title="Accepted from a suggestion">auto</span>` : ""}</span>
            <span class="cell num" title="${added ? escapeHtml(added.toLocaleString()) : ""}">${added ? escapeHtml(added.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })) : ""}</span>
        </summary>
        <div class="item-body">
            <div style="display:flex; gap:8px;">
                <button class="copy-btn usage-toggle" data-usage="${t.id}" title="The translated bubbles where this term was given to the AI: where they are, what the AI wrote, what the bubble says now">Show where it fired (${t.used || 0})</button>
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
        renderUsage(panel, usageCache.get(termId), termById(termId));
        return;
    }

    panel.innerHTML = `<p class="usage-empty">Loading...</p>`;
    try {
        const usage = await api.glossary.usage(termId);
        usageCache.set(termId, usage);
        renderUsage(panel, usage, termById(termId));
    } catch (e) {
        panel.innerHTML = `<p class="usage-empty">Couldn't load usage: ${e.message}</p>`;
    }
}

const termById = (id) => allTerms.find((t) => t.id === id) || {};

/**
 * Escaped text with every occurrence of `term` wrapped in <mark class="term">. Matches the term at a word start and
 * any ending after it (WYWERN also marks WYWERNÓW / WYWERNY - Polish inflects), case-insensitive.
 */
function markTerm(text, term) {
    const safe = escapeHtml(text || "");
    const t = escapeHtml(term || "").trim();
    if (!t) return safe;
    const re = new RegExp(`(?<![\\p{L}\\p{N}])(${t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})([\\p{L}\\p{N}]*)`, "giu");
    return safe.replace(re, '<mark class="term">$1$2</mark>');
}

function renderUsage(panel, usage, term) {
    if (!usage.length) {
        panel.innerHTML = `<p class="usage-empty">Never fired yet: no translated bubble contained &ldquo;${escapeHtml(term.source_term || "")}&rdquo; since the term was added.</p>`;
        return;
    }
    const head = `<div class="usage-head">Given to the AI with ${usage.length} bubble(s), newest first. Highlighted: ${escapeHtml(term.source_term || "")} &rarr; ${escapeHtml(term.target_term || "")}.</div>`;
    panel.innerHTML = head + usage.map((u) => {
        const hasZeroShot = u.zero_shot_output != null;
        const hasMemory = u.memory_injected_output != null;
        const ai = hasMemory ? u.memory_injected_output : u.zero_shot_output;
        const page = (u.file_name || "").replace(/\.[^.]+$/, "") || `page #${u.page_id}`;
        const bubble = u.bubble_no ? ` &middot; #${u.bubble_no}` : ' &middot; <span title="Deleted, merged or split since">bubble gone</span>';
        const href = `editor.html?project=${encodeURIComponent(projectId)}&page=${u.page_id}${u.bubble_no ? `&bubble=${u.bubble_no - 1}` : ""}`;
        let now;
        if (u.current_translation == null) now = `<span class="text-muted">bubble no longer on the page</span>`;
        else if ((u.current_translation || "").trim() === (ai || "").trim()) now = `${markTerm(u.current_translation, term.target_term)} <span class="text-muted">(same as the AI)</span>`;
        else now = `<span title="Changes from the AI text to what the bubble says now">${diffHtml(ai || "", u.current_translation, escapeHtml)}</span>`;
        return `
        <div class="usage-row">
            <div class="usage-where"><b>${escapeHtml(u.chapter || "?")}</b> <span>${escapeHtml(page)}${bubble}</span> <span>${formatDate(u.created_at)}</span>
                <a href="${href}" title="Open this bubble in the editor">Open &rarr;</a></div>
            <div class="usage-source">${markTerm(u.source_text, term.source_term)}</div>
            ${hasZeroShot ? `<div class="usage-variant"><span class="usage-variant-label">${hasMemory ? "Zero-shot (no memory)" : "AI translation"}:</span><span>${markTerm(u.zero_shot_output, term.target_term)}</span></div>` : ""}
            ${hasMemory ? `<div class="usage-variant"><span class="usage-variant-label">Memory-injected:</span><span>${markTerm(u.memory_injected_output, term.target_term)}</span></div>` : ""}
            <div class="usage-variant"><span class="usage-variant-label">In the bubble now:</span><span>${now}</span></div>
        </div>`;
    }).join("");
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

async function runSuggestionScan(fetchSuggestions, busyText, emptyText) {
    const block = document.getElementById("suggestionsBlock");
    const list = document.getElementById("suggestionsList");
    const status = document.getElementById("glossaryStatus");

    status.textContent = busyText;
    let suggestions;
    try {
        suggestions = await fetchSuggestions();
    } catch (e) {
        status.textContent = `Scan failed: ${e.message}`;
        return;
    }
    status.textContent = "";

    if (!suggestions.length) {
        block.classList.add("hidden");
        alert(emptyText);
        return;
    }

    block.classList.remove("hidden");
    list.innerHTML = "";
    suggestions.forEach((s) => list.appendChild(suggestionRow(s)));
}

document.getElementById("scanSuggestionsBtn")?.addEventListener("click", () => runSuggestionScan(
    () => api.glossary.suggestions(projectId),
    "Scanning corrections...",
    "No repeat-fix patterns found in the stored corrections yet.",
));

document.getElementById("scanSuggestionsLlmBtn")?.addEventListener("click", () => runSuggestionScan(
    () => api.glossary.suggestionsLlm(projectId),
    "Asking the translation model to extract terms from your corrections...",
    "The model found no terminology changes in your corrections (only grammar / style edits).",
));

function suggestionRow(s) {
    const row = document.createElement("div");
    row.className = "suggestion-row";
    row.innerHTML = `
        <span><strong>${escapeHtml(s.source_term)}</strong></span>
        <span class="suggestion-arrow">${escapeHtml(s.old_translation || "-")} &rarr;</span>
        <input type="text" data-target value="${escapeHtml(s.new_translation)}" style="max-width:180px;"
               title="Polish term - use the base (dictionary) form, the model inflects it">
        <span class="badge" title="How many corrections show this pattern">${s.occurrences}×</span>
        ${s.method === "llm" ? '<span class="badge" title="Extracted by the LLM">AI</span>' : ""}
        <span class="suggestion-example" title="${escapeHtml(s.example_source_text)}">${escapeHtml(s.example_source_text)}</span>
        <button class="primary" data-accept>Accept</button>
        <button class="copy-btn" data-reject>Dismiss</button>
    `;
    row.querySelector("[data-accept]").addEventListener("click", async () => {
        try {
            const target = row.querySelector("[data-target]").value.trim() || s.new_translation;
            await api.glossary.add(projectId, {
                source_term: s.source_term, target_term: target,
                auto_detected: true, occurrences: s.occurrences,
            });
            row.remove();
            loadGlossary();
            offerMemoryCleanup(projectId, s.old_translation, target);
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