/**
 * @file frontend/js/memory-links.js
 * @description Links into the Memory page (memory.html) shared by the editor and the glossary:
 *  - the editor's "Used from memory" panel opens an entry in a NEW TAB, where it is edited (the editor itself never
 *    edits memory - one place to edit = fewer ways to get it wrong);
 *  - after a glossary term is accepted, old memory entries that still use the previous wording (WIWERN while the
 *    glossary now says WYWERNA) are offered for a find & replace, so memory examples stop contradicting the glossary.
 */

import { api } from "./api.js";

export function memoryEntryUrl(projectId, correctionId) {
    return `memory.html?project=${encodeURIComponent(projectId)}&correction=${encodeURIComponent(correctionId)}`;
}

export function memoryReplaceUrl(projectId, find, replace, mode = "prefix") {
    return `memory.html?project=${encodeURIComponent(projectId)}&find=${encodeURIComponent(find)}`
        + `&replace=${encodeURIComponent(replace)}&mode=${mode}`;
}

/** Polish base form -> stem shared by its inflected forms ("WIWERNA" -> "WIWERN"; "RYCERZ" stays). */
export function termStem(term) {
    return (term || "").trim().replace(/[aeiouyąęó]$/i, "");
}

/**
 * After a glossary term was accepted: if memory entries still contain the OLD wording, offer to replace it.
 * Nothing changes without the preview + confirmation of the find & replace on the Memory page.
 */
export async function offerMemoryCleanup(projectId, oldTerm, newTerm) {
    const find = termStem(oldTerm);
    const replace = termStem(newTerm);
    if (!projectId || find.length < 3 || !replace || find.toLowerCase() === replace.toLowerCase()) return;
    let preview;
    try {
        preview = await api.corrections.replace(projectId, { find, replace, mode: "prefix", apply: false });
    } catch (e) {
        console.warn("[MEMORY] cleanup preview failed:", e);
        return;
    }
    if (!preview.count) return;
    const plural = preview.count === 1 ? "entry still uses" : "entries still use";
    if (!confirm(`${preview.count} memory ${plural} "${find}..." - the glossary now says "${newTerm}".\n\n`
        + `Open find & replace "${find}..." -> "${replace}..." to fix them (you see a preview first)?`)) return;

    const box = document.getElementById("memoryReplaceBox");
    if (box) {          // already on the Memory page (glossary tab): fill the form here instead of a new tab
        document.getElementById("tabBtnCorrections")?.click();
        document.getElementById("replaceFindInput").value = find;
        document.getElementById("replaceWithInput").value = replace;
        document.getElementById("replaceModeSelect").value = "prefix";
        box.open = true;
        box.scrollIntoView({ block: "start" });
        document.getElementById("replacePreviewBtn")?.click();
        return;
    }
    window.open(memoryReplaceUrl(projectId, find, replace), "_blank");
}
