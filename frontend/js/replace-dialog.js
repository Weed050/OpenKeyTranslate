/**
 * @file frontend/js/replace-dialog.js
 * @description Find & replace in the TRANSLATIONS of the whole project (editor > More, project page): a term the
 * user changed their mind about ("ŻELAZNY SMOK" -> "STALOWY SMOK") after it was accepted on many pages.
 * Preview first - every bubble that would change, with a diff and a tick box - then "Replace in N bubbles".
 * The same words can be replaced in the correction memory too (its own find & replace, /corrections/replace), so new
 * translations stop getting the old term as an example. Backend: services/translation_replace.py.
 */

import { api } from "./api.js";
import { diffHtml } from "./diff.js";

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text ?? "";
    return div.innerHTML;
}

/** onDone(changedKeys) runs after a replacement (the editor reloads its page). */
export function openReplaceDialog(projectId, { onDone = null, initialFind = "" } = {}) {
    const dlg = document.createElement("dialog");
    dlg.className = "import-dialog replace-dialog";
    dlg.addEventListener("close", () => dlg.remove());
    dlg.innerHTML = `
        <h2 style="margin:0;">Find &amp; replace in translations</h2>
        <p class="text-muted" style="margin:0;">Every page of the project. The AI drafts stay as they were (the History still compares against them); replaced bubbles count as edited by you.</p>
        <div class="replace-form">
            <input type="text" id="rpFind" placeholder="Find (e.g. ŻELAZNY SMOK)" spellcheck="false" value="${escapeHtml(initialFind)}">
            <span>&rarr;</span>
            <input type="text" id="rpReplace" placeholder="Replace with (e.g. STALOWY SMOK)" spellcheck="false">
            <select id="rpMode" title="Whole words: only exactly these words. Word + its forms: also the word with Polish endings (WIWERN, WIWERNY, WIWERNAMI - each keeps its ending)">
                <option value="word">Whole words</option>
                <option value="prefix">Word + its inflected forms</option>
            </select>
            <button type="button" id="rpPreview" class="primary">Preview</button>
        </div>
        <div id="rpBody" class="replace-body"><p class="text-muted">Type what to find and press Preview (Enter).</p></div>
        <label class="checkbox-label hidden" id="rpMemoryRow"><input type="checkbox" id="rpMemory" checked> <span id="rpMemoryText"></span></label>
        <div class="import-footer">
            <button type="button" data-act="close">Close</button>
            <button type="button" class="primary" id="rpApply" disabled>Replace</button>
        </div>`;
    document.body.appendChild(dlg);
    const $ = (sel) => dlg.querySelector(sel);
    let changes = [];
    let memoryCount = 0;

    const payload = () => ({ find: $("#rpFind").value.trim(), replace: $("#rpReplace").value, mode: $("#rpMode").value });

    function picked() {
        return [...dlg.querySelectorAll(".rp-row input:checked")].map((c) => c.dataset.key);
    }

    function updateApply() {
        const n = picked().length;
        $("#rpApply").disabled = !n && !(memoryCount && $("#rpMemory").checked);
        $("#rpApply").textContent = n ? `Replace in ${n} bubble${n === 1 ? "" : "s"}` : memoryCount ? "Replace in memory only" : "Replace";
        const unreviewed = changes.filter((c) => !c.reviewed && picked().includes(c.key)).length;
        $("#rpUnreviewed")?.classList.toggle("hidden", !unreviewed);
        if ($("#rpUnreviewedN")) $("#rpUnreviewedN").textContent = unreviewed;
    }

    async function preview() {
        const p = payload();
        if (p.find.length < 2) { $("#rpBody").innerHTML = '<p class="import-error">Type at least 2 characters to find.</p>'; return; }
        $("#rpBody").innerHTML = '<p class="text-muted">Searching every page...</p>';
        $("#rpApply").disabled = true;
        let mem = { count: 0 };
        try {
            [{ changes }, mem] = await Promise.all([
                api.projects.replaceTranslations(projectId, { ...p, apply: false }),
                api.corrections.replace(projectId, { ...p, apply: false }).catch(() => ({ count: 0 })),
            ]);
        } catch (e) {
            $("#rpBody").innerHTML = `<p class="import-error">${escapeHtml(e.message)}</p>`;
            return;
        }
        memoryCount = mem.count || 0;
        $("#rpMemoryRow").classList.toggle("hidden", !memoryCount);
        $("#rpMemoryText").textContent = `Also in the correction memory (${memoryCount} entr${memoryCount === 1 ? "y" : "ies"}) - so new translations do not get the old words as an example`;
        if (!changes.length) {
            $("#rpBody").innerHTML = `<p class="text-muted">No translation contains "${escapeHtml(p.find)}"${p.mode === "word" ? " as a whole word - try \"Word + its inflected forms\"" : ""}.</p>`;
            updateApply();
            return;
        }
        $("#rpBody").innerHTML = `
            <div class="rp-head"><label class="checkbox-label"><input type="checkbox" id="rpAll" checked> ${changes.length} bubble${changes.length === 1 ? "" : "s"}</label>
                <span class="import-error hidden" id="rpUnreviewed"><span id="rpUnreviewedN"></span> not reviewed yet - after the replacement they show the "edited" marker</span></div>
            <div class="rp-list">${changes.map((c) => `
                <label class="rp-row">
                    <input type="checkbox" data-key="${escapeHtml(c.key)}" checked>
                    <span class="rp-where">${escapeHtml(c.chapter)} &middot; ${escapeHtml(c.file_name.replace(/\.[^.]+$/, ""))} &middot; #${c.bubble_no}${c.reviewed ? "" : ' <span class="badge" title="Not reviewed in the editor yet">not reviewed</span>'}</span>
                    <span class="rp-text">${diffHtml(c.before, c.after, escapeHtml)}</span>
                </label>`).join("")}</div>`;
        $("#rpAll").addEventListener("change", (e) => {
            dlg.querySelectorAll(".rp-row input").forEach((c) => { c.checked = e.target.checked; });
            updateApply();
        });
        dlg.querySelectorAll(".rp-row input").forEach((c) => c.addEventListener("change", updateApply));
        updateApply();
    }

    async function apply() {
        const p = payload();
        const keys = picked();
        $("#rpApply").disabled = true;
        try {
            let done = 0, mem = 0;
            if (keys.length) done = (await api.projects.replaceTranslations(projectId, { ...p, keys, apply: true })).count;
            if (memoryCount && $("#rpMemory").checked) mem = (await api.corrections.replace(projectId, { ...p, apply: true })).count;
            $("#rpBody").innerHTML = `<p>Replaced in ${keys.length} bubble${keys.length === 1 ? "" : "s"}${mem ? ` and ${mem} memory entr${mem === 1 ? "y" : "ies"}` : ""}.</p>`;
            $("#rpMemoryRow").classList.add("hidden");
            memoryCount = 0;
            changes = [];
            $("#rpApply").textContent = "Replace";
            onDone?.(keys, done);
        } catch (e) {
            $("#rpBody").insertAdjacentHTML("afterbegin", `<p class="import-error">Replace failed: ${escapeHtml(e.message)}</p>`);
            updateApply();
        }
    }

    $("#rpPreview").addEventListener("click", preview);
    ["#rpFind", "#rpReplace"].forEach((s) => $(s).addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); preview(); } }));
    $("#rpMode").addEventListener("change", () => { if (changes.length || $("#rpFind").value.trim()) preview(); });
    $("#rpMemory").addEventListener("change", updateApply);
    $("#rpApply").addEventListener("click", apply);
    dlg.querySelector('[data-act="close"]').addEventListener("click", () => dlg.close());
    dlg.showModal();
    $("#rpFind").focus();
}
