/**
 * @file frontend/js/dashboard.js
 * @description Project list and "create new project" flow for index.html.
 *
 * One card per project: a three-colour progress bar (reviewed by you / processed by the system only / not processed
 * yet - GET /projects/{id}/progress), the main actions (Resume, Open, Export) and the rare ones in a "..." menu
 * (copy path, open folder, delete - kept apart so a slip of the mouse does not hit it).
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";

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

    list.innerHTML = "";
    for (const p of projects) list.appendChild(projectCard(p));
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
                    <hr>
                    <button data-act="delete" class="danger">Delete project…</button>
                </div>
            </details>
        </div>
        <div class="progress-bar" aria-hidden="true">
            <span class="seg reviewed" style="width:0%"></span><span class="seg processed" style="width:0%"></span>
        </div>
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
            alert(`Couldn't open folder: ${err.message}`);
        }
    });
    card.querySelector('[data-act="delete"]').addEventListener("click", async () => {
        if (!confirm(`Delete project "${p.name}"?\n\nThis removes its files on disk too (scans, cleaned pages, translations). It cannot be undone - consider Settings > Backup first.`)) return;
        try {
            await api.projects.delete(p.id);
            loadProjects();
            renderSidebar(null);
        } catch (err) {
            alert(`Delete failed: ${err.message}`);
        }
    });

    fillProgress(card, p.id);
    return card;
}

async function fillProgress(card, projectId) {
    const legend = card.querySelector(".progress-legend");
    try {
        const { total, processed, reviewed } = await api.projects.progress(projectId);
        const pct = (n) => (total ? (100 * n) / total : 0);
        card.querySelector(".seg.reviewed").style.width = `${pct(reviewed)}%`;
        card.querySelector(".seg.processed").style.width = `${pct(processed - reviewed)}%`;
        legend.innerHTML = `
            <span><i class="dot reviewed"></i>${reviewed} reviewed by you</span>
            <span><i class="dot processed"></i>${processed - reviewed} processed, not reviewed</span>
            <span><i class="dot todo"></i>${total - processed} not processed</span>
            <span class="legend-total">${total} pages</span>`;
        if (!processed) {
            const exp = card.querySelector(".export-link");
            exp.classList.add("disabled");
            exp.setAttribute("aria-disabled", "true");
            exp.title = "Nothing to export yet - process (OCR) some pages first";
            exp.addEventListener("click", (e) => e.preventDefault());
        }
    } catch {
        legend.textContent = "Progress unavailable";
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
