/**
 * @file frontend/js/dashboard.js
 * @description Project list and "create new project" flow for index.html.
 *
 * One card per project: the stage bar of its pages (same stages and colours as the chapter bars of the page list -
 * js/stages.js, GET /projects/{id}/progress), the main actions (Resume, Open, Export) and the rare ones in a "..."
 * menu (copy path, open folder, delete - kept apart so a slip of the mouse does not hit it). Sortable by last
 * activity (newest page change), name, progress or creation date; the choice is remembered.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
import { countGroups, doneScore, exportedDraftNote, stageBarHtml, stageLegendHtml } from "./stages.js";
import { askOk, notify } from "./dialogs.js";

const SORT_KEY = "okt-project-sort";          // {"field", "dir"} (older versions: a bare mode string)
const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: "base" });
/** Sort fields: ascending comparator + the natural direction picked when the field changes. */
const SORT_FIELDS = {
    updated: { dir: "desc", cmp: (a, b) => (a.progress?.last_activity || 0) - (b.progress?.last_activity || 0) },
    name: { dir: "asc", cmp: byName },
    progress: { dir: "desc", cmp: (a, b) => a.score - b.score },
    created: { dir: "desc", cmp: (a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0) },
};

function loadSort() {
    const raw = localStorage.getItem(SORT_KEY);
    let s;
    try { s = JSON.parse(raw); } catch { s = raw; }
    const legacy = { recent: ["updated", "desc"], name: ["name", "asc"], least: ["progress", "asc"], most: ["progress", "desc"], created: ["created", "desc"] };
    if (!s || typeof s !== "object") s = legacy[s] ? { field: legacy[s][0], dir: legacy[s][1] } : { field: "updated", dir: "desc" };
    if (!SORT_FIELDS[s.field]) s = { field: "updated", dir: "desc" };
    if (s.dir !== "asc" && s.dir !== "desc") s.dir = SORT_FIELDS[s.field].dir;
    return s;
}

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(null); // no project is "open" on the dashboard itself
    loadProjects();
});

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function loadProjects() {
    const list = document.getElementById("projectList");
    let projects;
    try {
        projects = await api.projects.list();
    } catch (e) {
        list.innerHTML = `<div class="card dash-empty"><p>Couldn't load projects: ${esc(e.message)}</p>
            <p class="text-muted">Is the backend running? Start it with <code>python main.py</code> in the backend folder, then reload this page.</p></div>`;
        return;
    }

    if (!projects.length) {
        list.innerHTML = `<div class="card dash-empty"><p>No projects yet.</p><p class="text-muted">Create one below: pick the folder with the scans (one sub-folder per chapter).</p></div>`;
        return;
    }

    // progress first (for sorting by it), then the cards in the chosen order
    await Promise.all(projects.map(async (p) => {
        try {
            p.progress = await api.projects.progress(p.id);
            p.score = doneScore(countGroups(p.progress.stages || {}));
        } catch {
            p.progress = null;
            p.score = 0;
        }
    }));
    const select = document.getElementById("projectSortSelect");
    const dirBtn = document.getElementById("projectSortDir");
    const render = () => {
        const { field, dir } = loadSort();
        select.value = field;
        dirBtn.textContent = dir === "asc" ? "↑" : "↓";
        dirBtn.title = `${dir === "asc" ? "Ascending" : "Descending"} - click to reverse`;
        const sign = dir === "asc" ? 1 : -1;
        list.innerHTML = "";
        for (const p of [...projects].sort((a, b) => sign * SORT_FIELDS[field].cmp(a, b) || byName(a, b))) list.appendChild(projectCard(p));
    };
    const save = (s) => { localStorage.setItem(SORT_KEY, JSON.stringify(s)); render(); };
    select.onchange = () => save({ field: select.value, dir: SORT_FIELDS[select.value].dir });
    dirBtn.onclick = () => { const s = loadSort(); save({ field: s.field, dir: s.dir === "asc" ? "desc" : "asc" }); };
    render();
    document.addEventListener("click", (e) => {        // close an open "..." menu on a click elsewhere
        document.querySelectorAll(".dash-menu[open]").forEach((m) => { if (!m.contains(e.target)) m.open = false; });
    });
}

function projectCard(p) {
    const card = document.createElement("div");
    card.className = "card project-card";
    const lastEdit = getLastEdit(p.id);
    const resumeHref = lastEdit ? `editor.html?project=${p.id}&page=${lastEdit.pageId}&bubble=${lastEdit.bubbleIndex}` : null;

    card.innerHTML = `
        <div class="project-head">
            <div class="project-title">
                <h2>${esc(p.name)}</h2>
                <span class="text-muted text-mono project-path" title="${esc(p.workspace_path)}">${esc(p.workspace_path)}</span>
            </div>
            <details class="dash-menu">
                <summary class="icon-btn" title="More actions">&#8943;</summary>
                <div class="dash-menu-list">
                    <button data-act="copy">Copy folder path</button>
                    <button data-act="open">Open folder</button>
                    <button data-act="exported" title="project folder > exported">Open exported files</button>
                    <hr>
                    <button data-act="delete" class="danger">Delete project…</button>
                </div>
            </details>
        </div>
        <div class="stage-bar progress-bar" aria-hidden="true"></div>
        <div class="progress-legend text-muted">Loading…</div>
        <div class="project-actions">
            ${resumeHref ? `<a class="btn primary" href="${resumeHref}" title="Back to the bubble you edited last">Resume</a>` : ""}
            <a class="btn ${resumeHref ? "" : "primary"}" href="editor.html?project=${p.id}" title="Chapters and pages of the project">Open</a>
            <a class="btn export-link" href="editor.html?project=${p.id}&export=1" title="Export translated / clean pages of all or selected chapters">Export…</a>
        </div>`;

    card.querySelector('[data-act="copy"]').addEventListener("click", async (e) => {
        await navigator.clipboard.writeText(p.workspace_path);
        e.target.textContent = "Copied";
        setTimeout(() => { e.target.textContent = "Copy folder path"; }, 1200);
    });
    card.querySelector('[data-act="open"]').addEventListener("click", async () => {
        try {
            await api.system.openPath(p.workspace_path);
        } catch (err) {
            notify(`Couldn't open folder: ${err.message}`);
        }
    });
    card.querySelector('[data-act="exported"]').addEventListener("click", () =>
        api.projects.openExported(p.id).catch((err) => notify(`Couldn't open folder: ${err.message}`)));
    card.querySelector('[data-act="delete"]').addEventListener("click", async () => {
        if (!(await askOk(`Delete project "${p.name}"?\n\nThis removes its files on disk too (scans, cleaned pages, translations). It cannot be undone - consider Settings > Backup first.`, { ok: "Delete project", danger: true }))) return;
        try {
            await api.projects.delete(p.id);
            loadProjects();
            renderSidebar(null);
        } catch (err) {
            notify(`Delete failed: ${err.message}`);
        }
    });

    fillProgress(card, p.progress);
    return card;
}

function fillProgress(card, progress) {
    const legend = card.querySelector(".progress-legend");
    if (!progress) {
        legend.textContent = "Progress unavailable";
        return;
    }
    const counts = countGroups(progress.stages || {});
    card.querySelector(".progress-bar").innerHTML = stageBarHtml(counts);
    const when = progress.last_activity
        ? new Date(progress.last_activity * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "";
    legend.innerHTML = stageLegendHtml(counts, esc) + exportedDraftNote(progress.stages, esc)
        + `<span class="legend-total" title="Last change on any page">${when ? `last change ${esc(when)} &middot; ` : ""}${progress.total} pages</span>`;
    if (!progress.processed) {
        const exp = card.querySelector(".export-link");
        exp.classList.add("disabled");
        exp.setAttribute("aria-disabled", "true");
        exp.title = "Nothing to export yet - process (OCR) some pages first";
        exp.addEventListener("click", (e) => e.preventDefault());
    }
}

document.getElementById("selectFolderBtn").addEventListener("click", async () => {
    const data = await api.projects.selectFolder();
    if (!data.path) return;

    document.getElementById("pathPreview").textContent = data.path;

    const projectInput = document.getElementById("projectName");
    if (!projectInput.value) {
        const parts = data.path.split(/[\\/]/).filter((p) => p.length > 0);
        projectInput.value = parts.pop() || "";
    }
});

document.getElementById("importBtn").addEventListener("click", async () => {
    const path = document.getElementById("pathPreview").textContent;
    const projectName = document.getElementById("projectName").value;
    const statusMsg = document.getElementById("statusMsg");

    if (path === "None selected" || !projectName.trim()) {
        statusMsg.textContent = "Select a folder and enter a project name.";
        return;
    }

    try {
        const result = await api.projects.import(path, projectName);
        statusMsg.textContent = result.message;
        document.getElementById("projectName").value = "";
        document.getElementById("pathPreview").textContent = "None selected";
        loadProjects();
        renderSidebar(null);
    } catch (e) {
        statusMsg.textContent = `Failed: ${e.message}`;
    }
});

function getLastEdit(projectId) {
    try {
        const raw = localStorage.getItem(`okt-lastedit-project-${projectId}`);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}
