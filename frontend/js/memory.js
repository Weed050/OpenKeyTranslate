/**
 * @file frontend/js/memory.js
 * @description Correction-memory browser (the "TM analytics" panel):
 * lists every stored Correction for a project; an entry can be edited in place or deleted, and a find & replace
 * fixes a term across many entries at once (WIWERN -> WYWERN). Also owns the Corrections/Glossary tab switcher
 * shared with glossary.js.
 *
 * URL parameters (the editor opens this page in a new tab):
 *   ?project=1&correction=12                              open, highlight and scroll to entry 12
 *   ?project=1&find=WIWERN&replace=WYWERN&mode=prefix     prefill the find & replace and show its preview
 */
 
import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { diffHtml } from "./diff.js";
 
const params = new URLSearchParams(window.location.search);
const projectId = params.get("project");
 
window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(projectId);
    setupTabs();
    if (!projectId) {
        document.getElementById("noProject").classList.remove("hidden");
        return;
    }
    loadCorrections().then(applyUrlTargets);
});
 
function setupTabs() {
    const buttons = document.querySelectorAll(".tab-btn");
    buttons.forEach((btn) => {
        btn.addEventListener("click", () => {
            buttons.forEach((b) => b.classList.remove("active"));
            btn.classList.add("active");
            document.getElementById("correctionsTab").classList.toggle("hidden", btn.dataset.tab !== "corrections");
            document.getElementById("glossaryTab").classList.toggle("hidden", btn.dataset.tab !== "glossary");
        });
    });
}
 
let allCorrections = [];
const usageCache = new Map();
 
async function loadCorrections() {
    const listEl = document.getElementById("correctionList");
    try {
        allCorrections = await api.corrections.listByProject(projectId);
    } catch (e) {
        listEl.innerHTML = `<p class="text-muted">Couldn't load corrections: ${e.message}</p>`;
        return;
    }
 
    updateCountBadge();
 
    if (!allCorrections.length) {
        listEl.innerHTML = `<p class="text-muted">No corrections stored yet for this project.</p>`;
        return;
    }
 
    renderList();
}
 
function updateCountBadge() {
    const approvedCount = allCorrections.filter((c) => c.kind === "approved").length;
    document.getElementById("correctionsCountBadge").textContent =
        `${allCorrections.length - approvedCount} corrected, ${approvedCount} approved`;
}

function renderList() {
    const listEl = document.getElementById("correctionList");
    const query = (document.getElementById("memorySearchInput").value || "").toLowerCase();
    const sortMode = document.getElementById("memorySortSelect").value;
    const kindFilter = document.getElementById("memoryKindSelect")?.value || "all";
 
    let items = allCorrections.filter((c) =>
        (kindFilter === "all" || (c.kind || "corrected") === kindFilter) && (
        !query ||
        c.source_text.toLowerCase().includes(query) ||
        c.final_translation.toLowerCase().includes(query))
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
    details.className = "item-row";
    details.dataset.correctionId = c.id;
    details.innerHTML = `
        <summary>
            <span class="item-summary-text">${escapeHtml(c.final_translation)}</span>
            ${(c.kind || "corrected") === "approved"
                ? '<span class="badge" title="Accepted unchanged - a plain translation-memory entry">approved</span>'
                : '<span class="badge" title="You changed the AI text - ranked first, the AI text is shown to the model as rejected">corrected</span>'}
            ${c.edited_at ? `<span class="badge" title="Edited on the Memory page ${escapeHtml(formatDate(c.edited_at))}. First saved as: ${escapeHtml(c.final_before_edit || "")}">edited</span>` : ""}
            <span class="badge" title="Times used as a hint">used ${c.reuse_count || 0}×</span>
        </summary>
        <div class="item-body">
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
                <div class="field-value" data-final>${escapeHtml(c.final_translation)}</div>
                <div class="hidden" data-edit-box>
                    <textarea rows="2" lang="pl" data-edit-input style="width:100%;">${escapeHtml(c.final_translation)}</textarea>
                    <div style="display:flex; gap:8px; margin-top:6px;">
                        <button class="primary" data-edit-save>Save</button>
                        <button data-edit-cancel>Cancel</button>
                    </div>
                </div>
            </div>
            ${c.final_before_edit ? `<div class="text-muted" style="font-size:11px;">First saved as: ${escapeHtml(c.final_before_edit)}</div>` : ""}
            <div class="text-muted" style="font-size:11px;">Saved ${formatDate(c.created_at)}</div>
            <div style="display:flex; gap:8px;">
                <button class="copy-btn usage-toggle" data-usage="${c.id}">Show usage (${c.reuse_count || 0})</button>
                <button data-edit title="Change the stored final translation (the English source and its search vector stay)">Edit</button>
                <button class="danger" data-id="${c.id}">Delete from memory</button>
            </div>
            <div class="usage-panel hidden" id="usage-${c.id}"></div>
        </div>
    `;
    details.querySelector("button.danger").addEventListener("click", (event) => {
        event.preventDefault();
        deleteCorrection(c.id, details);
    });
    details.querySelector("[data-usage]").addEventListener("click", (event) => {
        event.preventDefault();
        toggleUsage(c.id);
    });
    setupInlineEdit(details, c);
    return details;
}

function setupInlineEdit(details, c) {
    const view = details.querySelector("[data-final]");
    const box = details.querySelector("[data-edit-box]");
    const input = details.querySelector("[data-edit-input]");
    const toggle = (editing) => {
        view.classList.toggle("hidden", editing);
        box.classList.toggle("hidden", !editing);
        if (editing) input.focus();
    };
    details.querySelector("[data-edit]").addEventListener("click", (event) => {
        event.preventDefault();
        input.value = c.final_translation;
        toggle(true);
    });
    details.querySelector("[data-edit-cancel]").addEventListener("click", (event) => {
        event.preventDefault();
        toggle(false);
    });
    details.querySelector("[data-edit-save]").addEventListener("click", async (event) => {
        event.preventDefault();
        const value = input.value.trim();
        if (!value) {
            alert("The translation must not be empty - use 'Delete from memory' instead.");
            return;
        }
        try {
            const result = await api.corrections.edit(c.id, value);
            if (c.final_before_edit == null && value !== c.final_translation) c.final_before_edit = c.final_translation;
            c.final_translation = result.final_translation;
            c.kind = result.kind;
            c.edited_at = result.edited_at;
            const fresh = correctionRow(c);
            fresh.open = true;
            details.replaceWith(fresh);
            updateCountBadge();
        } catch (e) {
            alert(`Save failed: ${e.message}`);
        }
    });
}

/* -------- deep links from the editor: ?correction=ID, ?find=..&replace=..&mode=.. -------- */

function applyUrlTargets() {
    const targetId = params.get("correction");
    if (targetId) {
        const row = document.querySelector(`[data-correction-id="${CSS.escape(targetId)}"]`);
        if (row) {
            row.open = true;
            row.style.outline = "2px solid var(--accent, #6c8cff)";
            row.scrollIntoView({ block: "center" });
        } else {
            alert(`Memory entry #${targetId} no longer exists (it was deleted).`);
        }
    }
    if (params.get("find")) {
        document.getElementById("replaceFindInput").value = params.get("find");
        document.getElementById("replaceWithInput").value = params.get("replace") || "";
        document.getElementById("replaceModeSelect").value = params.get("mode") === "word" ? "word" : "prefix";
        const box = document.getElementById("memoryReplaceBox");
        box.open = true;
        box.scrollIntoView({ block: "start" });
        previewReplace();
    }
}

/* ---------------------------- find & replace ---------------------------- */

function replacePayload() {
    return {
        find: document.getElementById("replaceFindInput").value.trim(),
        replace: document.getElementById("replaceWithInput").value.trim(),
        mode: document.getElementById("replaceModeSelect").value,
    };
}

async function previewReplace() {
    const out = document.getElementById("replacePreview");
    const payload = replacePayload();
    if (payload.find.length < 2) {
        out.innerHTML = `<p class="text-muted">Type at least 2 characters to search for.</p>`;
        return;
    }
    let result;
    try {
        result = await api.corrections.replace(projectId, { ...payload, apply: false });
    } catch (e) {
        out.innerHTML = `<p class="text-muted">Preview failed: ${escapeHtml(e.message)}</p>`;
        return;
    }
    if (!result.count) {
        out.innerHTML = `<p class="text-muted">No stored translation contains "${escapeHtml(payload.find)}".</p>`;
        return;
    }
    const plural = result.count === 1 ? "entry" : "entries";
    out.innerHTML = `
        <div class="text-muted" style="margin:6px 0;">${result.count} ${plural} would change:</div>
        ${result.changes.map((ch) => `
            <label class="usage-row" style="display:flex; gap:8px; align-items:flex-start;">
                <input type="checkbox" data-replace-id="${ch.id}" checked>
                <span>
                    <span class="text-muted" style="font-size:11px;">${escapeHtml(ch.source_text)}</span><br>
                    ${diffHtml(ch.before, ch.after, escapeHtml)}
                </span>
            </label>`).join("")}
        <button class="primary" id="replaceApplyBtn" style="margin-top:8px;">Replace in selected entries</button>`;
    document.getElementById("replaceApplyBtn").addEventListener("click", () => applyReplace(payload));
}

async function applyReplace(payload) {
    const ids = [...document.querySelectorAll("[data-replace-id]")]
        .filter((box) => box.checked).map((box) => Number(box.dataset.replaceId));
    if (!ids.length) {
        alert("Nothing selected.");
        return;
    }
    const plural = ids.length === 1 ? "entry" : "entries";
    if (!confirm(`Change ${ids.length} memory ${plural}: "${payload.find}" -> "${payload.replace}"?`)) return;
    try {
        const result = await api.corrections.replace(projectId, { ...payload, ids, apply: true });
        document.getElementById("replacePreview").innerHTML =
            `<p class="text-muted">Changed ${result.count} ${result.count === 1 ? "entry" : "entries"}.</p>`;
        await loadCorrections();
    } catch (e) {
        alert(`Replace failed: ${e.message}`);
    }
}

document.getElementById("replacePreviewBtn")?.addEventListener("click", previewReplace);
document.getElementById("replaceFindInput")?.addEventListener("keydown", (e) => { if (e.key === "Enter") previewReplace(); });
document.getElementById("replaceWithInput")?.addEventListener("keydown", (e) => { if (e.key === "Enter") previewReplace(); });
 
async function toggleUsage(correctionId) {
    const panel = document.getElementById(`usage-${correctionId}`);
    if (!panel) return;
 
    if (!panel.classList.contains("hidden")) {
        panel.classList.add("hidden");
        return;
    }
 
    panel.classList.remove("hidden");
    if (usageCache.has(correctionId)) {
        renderUsage(panel, usageCache.get(correctionId));
        return;
    }
 
    panel.innerHTML = `<p class="usage-empty">Loading...</p>`;
    try {
        const usage = await api.corrections.usage(correctionId);
        usageCache.set(correctionId, usage);
        renderUsage(panel, usage);
    } catch (e) {
        panel.innerHTML = `<p class="usage-empty">Couldn't load usage: ${e.message}</p>`;
    }
}
 
function renderUsage(panel, usage) {
    if (!usage.length) {
        panel.innerHTML = `<p class="usage-empty">Never used as a hint yet.</p>`;
        return;
    }
    panel.innerHTML = usage.map((u) => {
        const hasZeroShot = u.zero_shot_output != null;
        const hasFinal = u.final_correction != null;
        return `
        <div class="usage-row">
            <div class="usage-source"><strong>${escapeHtml(u.source_text)}</strong> <span class="badge" title="Similarity to this correction's source text">${Math.round((u.similarity_score || 0) * 100)}% match</span></div>
 
            ${hasZeroShot ? `
            <div class="usage-variant">
                <span class="usage-variant-label">Without hint (zero-shot):</span>
                <span>${escapeHtml(u.zero_shot_output)}</span>
            </div>` : ""}
 
            <div class="usage-variant">
                <span class="usage-variant-label">With this hint (shown to user):</span>
                <span>${escapeHtml(u.memory_injected_output)}</span>
            </div>
 
            ${hasFinal ? `
            <div class="usage-variant">
                <span class="usage-variant-label">User's final edit for this line:</span>
                <span>${diffHtml(u.memory_injected_output, u.final_correction, escapeHtml)}</span>
            </div>` : `<div class="usage-variant text-muted">Not corrected yet - hint was accepted as-is (or the line hasn't been saved).</div>`}
 
            <div class="text-muted" style="font-size:10px;">Page #${u.page_id} · ${formatDate(u.created_at)}</div>
        </div>
    `}).join("");
}
 
async function deleteCorrection(id, rowEl) {
    if (!confirm("Remove this correction from memory? It will no longer be suggested as a hint.")) return;
    try {
        await api.corrections.delete(id);
        rowEl.remove();
        allCorrections = allCorrections.filter((c) => c.id !== id);
        usageCache.delete(id);
        updateCountBadge();
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

document.getElementById("memoryKindSelect")?.addEventListener("change", renderList);

document.getElementById("reembedMemoryBtn")?.addEventListener("click", async (event) => {
    const btn = event.currentTarget;
    btn.disabled = true;
    try {
        const result = await api.corrections.reembed(projectId);
        alert(result.message);
    } catch (e) {
        alert(`Re-embed failed: ${e.message}`);
    } finally {
        btn.disabled = false;
    }
});
