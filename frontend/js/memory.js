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

async function loadCorrections() {
    const listEl = document.getElementById("correctionList");
    let corrections;
    try {
        corrections = await api.corrections.listByProject(projectId);
    } catch (e) {
        listEl.innerHTML = `<p class="text-muted">Couldn't load corrections: ${e.message}</p>`;
        return;
    }

    document.getElementById("countBadge").textContent = corrections.length;

    if (!corrections.length) {
        listEl.innerHTML = `<p class="text-muted">No corrections stored yet for this project.</p>`;
        return;
    }

    listEl.innerHTML = "";
    corrections.forEach((c) => listEl.appendChild(correctionRow(c)));
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
