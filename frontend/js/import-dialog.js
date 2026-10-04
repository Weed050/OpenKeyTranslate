/**
 * @file frontend/js/import-dialog.js
 * @description "Import new chapters..." flow: pick a folder -> server previews what it would import (labels, page
 * counts, duplicates, page-order method, ignored files) -> user adjusts and confirms. onImported() is called after a
 * successful import (the page list passes its own refresh function, so this module needs no knowledge of it).
 */

import { api } from "./api.js";
import { state, showToast, escapeHtml } from "./editor-shared.js";

export async function openImportDialog(onImported) {
    const data = await api.projects.selectFolder();
    if (!data.path) return;
    await loadImportPreview(data.path, "auto", onImported);
}

async function loadImportPreview(path, orderMode, onImported) {
    let preview;
    try {
        preview = await api.projects.previewChapters(state.projectId, path, orderMode);
    } catch (e) {
        alert(`Scan failed: ${e.message}`);
        return;
    }

    if (!preview.chapters.length) {
        const skipped = preview.skipped_folders.map((s) => `- ${s.folder}: ${s.extensions_found.join(", ")}`).join("\n");
        alert("No supported images found in that folder." + (skipped ? `\n\nSkipped (unsupported format):\n${skipped}` : ""));
        return;
    }
    showImportDialog(preview, path, orderMode, onImported);
}

function showImportDialog(preview, sourcePath, orderMode = "auto", onImported = () => {}) {
    document.getElementById("importDialog")?.remove();
    const dlg = document.createElement("dialog");
    dlg.id = "importDialog";
    dlg.className = "import-dialog";

    const statusText = (c) => {
        if (c.status === "duplicate") return `same content as ${c.conflict_with}`;
        if (c.status === "label_conflict") return `label in use (${c.conflict_with}) - rename`;
        return "new";
    };

    const head = document.createElement("div");
    head.innerHTML = `<h2>Add chapters</h2>
        <p class="text-muted">Labels come from the folder names - edit them if needed. Duplicates and taken labels are unchecked.</p>
        <label style="display:flex; align-items:center; gap:8px;">Page order
            <select id="orderModeSelect" style="width:auto;">
                <option value="auto">Auto (file name when names are numbered, else file date)</option>
                <option value="name">Always by file name</option>
                <option value="mtime">Always by file date</option>
            </select>
        </label>`;
    head.querySelector("#orderModeSelect").value = orderMode;
    head.querySelector("#orderModeSelect").addEventListener("change", (e) => { dlg.close(); loadImportPreview(sourcePath, e.target.value, onImported); });

    const table = document.createElement("table");
    table.className = "import-table";
    table.innerHTML = `<thead><tr><th></th><th>Source folder</th><th>Label</th><th>Pages</th><th>Order</th><th>Status</th></tr></thead>`;
    const tbody = document.createElement("tbody");
    const rows = preview.chapters.map((c) => {
        const tr = document.createElement("tr");
        const tdCheck = document.createElement("td");
        const check = document.createElement("input");
        check.type = "checkbox";
        check.checked = c.selected;
        tdCheck.appendChild(check);

        const tdSrc = document.createElement("td");
        tdSrc.textContent = c.rel === "." ? c.source_name : c.rel;
        tdSrc.title = c.folder;
        tdSrc.className = "text-mono";

        const tdLabel = document.createElement("td");
        const labelInput = document.createElement("input");
        labelInput.type = "text";
        labelInput.value = c.label;
        tdLabel.appendChild(labelInput);

        const tdPages = document.createElement("td");
        tdPages.textContent = c.pages;
        if (c.ignored_files && c.ignored_files.length) {   // files that will NOT be imported (unsupported format) - used to vanish silently
            const warn = document.createElement("span");
            warn.className = "badge";
            warn.style.cssText = "margin-left:6px; color:var(--warn);";
            warn.textContent = `+${c.ignored_files.length} ignored`;
            warn.title = `Not imported (unsupported format): ${c.ignored_files.join(", ")}`;
            tdPages.appendChild(warn);
        }

        const tdOrder = document.createElement("td");
        tdOrder.textContent = c.order_method === "name" ? "by name" : "by date";
        tdOrder.title = `First pages: ${(c.first_pages || []).join(", ")}`;
        if (c.order_disagree > 0) {
            const warn = document.createElement("span");
            warn.className = "badge";
            warn.style.cssText = "margin-left:6px; color:var(--warn);";
            warn.textContent = `${c.order_disagree} differ`;
            warn.title = `File-name order and file-date order disagree in ${c.order_disagree} position(s). Check the first pages (${(c.first_pages || []).join(", ")}) and change "Page order" above if they look wrong.`;
            tdOrder.appendChild(warn);
        }

        const tdStatus = document.createElement("td");
        const badge = document.createElement("span");
        badge.className = "badge";
        badge.dataset.status = c.status === "new" ? "processed" : "failed";
        badge.textContent = statusText(c);
        tdStatus.appendChild(badge);

        tr.append(tdCheck, tdSrc, tdLabel, tdPages, tdOrder, tdStatus);
        tbody.appendChild(tr);
        return { c, check, labelInput };
    });
    table.appendChild(tbody);

    const tools = document.createElement("div");
    tools.className = "import-tools";
    const mkBtn = (text, fn) => { const b = document.createElement("button"); b.textContent = text; b.addEventListener("click", fn); return b; };
    const allowDup = document.createElement("input");
    allowDup.type = "checkbox";
    const allowLabel = document.createElement("label");
    allowLabel.style.cssText = "display:flex; align-items:center; gap:4px; margin:0;";
    allowLabel.append(allowDup, "allow duplicates");
    tools.append(
        mkBtn("Select new only", () => rows.forEach((r) => { r.check.checked = r.c.status === "new"; refresh(); })),
        mkBtn("Select all", () => rows.forEach((r) => { r.check.checked = true; refresh(); })),
        mkBtn("Select none", () => rows.forEach((r) => { r.check.checked = false; refresh(); })),
        allowLabel,
    );

    const error = document.createElement("pre");
    error.className = "import-error hidden";

    const footer = document.createElement("div");
    footer.className = "import-footer";
    const cancelBtn = mkBtn("Cancel", () => dlg.close());
    const importBtn = document.createElement("button");
    importBtn.className = "primary";
    footer.append(cancelBtn, importBtn);

    function refresh() {
        const n = rows.filter((r) => r.check.checked).length;
        importBtn.textContent = `Import selected (${n})`;
        importBtn.disabled = n === 0;
    }
    rows.forEach((r) => r.check.addEventListener("change", refresh));
    refresh();

    importBtn.addEventListener("click", async () => {
        const chapters = rows.filter((r) => r.check.checked)
            .map((r) => ({ folder: r.c.folder, label: r.labelInput.value.trim() }));
        if (chapters.some((c) => !c.label)) {
            error.textContent = "Every selected chapter needs a label.";
            error.classList.remove("hidden");
            return;
        }
        importBtn.disabled = true;
        importBtn.textContent = "Importing...";
        try {
            const result = await api.projects.importChapterSelection(state.projectId, {
                chapters, allow_duplicates: allowDup.checked, order_mode: orderMode,
            });
            dlg.close();
            showToast(result.message, 3000);
            await onImported();
        } catch (e) {
            error.textContent = e.message; // 409 lists every conflict - fix labels / selection and retry
            error.classList.remove("hidden");
            refresh();
        }
    });

    const skipped = preview.skipped_folders.length
        ? `<p class="text-muted">Skipped (unsupported format): ${preview.skipped_folders.map((s) => escapeHtml(`${s.folder} [${s.extensions_found.join(", ")}]`)).join("; ")}</p>`
        : "";
    const skippedEl = document.createElement("div");
    skippedEl.innerHTML = skipped;

    const scroll = document.createElement("div");
    scroll.className = "import-scroll";
    scroll.appendChild(table);

    dlg.append(head, tools, scroll, skippedEl, error, footer);
    dlg.addEventListener("close", () => dlg.remove());
    document.body.appendChild(dlg);
    dlg.showModal();
}
