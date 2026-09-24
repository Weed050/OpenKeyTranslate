/**
 * @file frontend/js/dashboard.js
 * @description Project list and "create new project" flow for index.html.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(null); // no project is "open" on the dashboard itself
    loadProjects();
});

async function loadProjects() {
    const list = document.getElementById("projectList");
    let projects;
    try {
        projects = await api.projects.list();
    } catch (e) {
        list.innerHTML = `<p class="text-muted">Couldn't load projects: ${e.message}</p>`;
        return;
    }

    if (!projects.length) {
        list.innerHTML = `<p class="text-muted">No projects found. Create one below.</p>`;
        return;
    }

    list.innerHTML = "";
    projects.forEach(async (p) => {
        const row = document.createElement("div");
        row.className = "row";
        row.innerHTML = `
            <div>
                <strong>${p.name}</strong>
                <div style="display:flex; align-items:center; gap:6px;">
                    <span class="text-muted text-mono">${p.workspace_path}</span>
                    <button class="copy-btn" title="Copy path" data-copy="${p.workspace_path}">Copy</button>
                    <button class="copy-btn" title="Open in file explorer" data-open="${p.workspace_path}">Open folder</button>
                </div>
            </div>
            <div style="display:flex; align-items:center; gap:12px;">
                <span class="badge" id="progress-${p.id}">...</span>
                <a href="editor.html?project=${p.id}"><button>Open project in editor</button></a>
            </div>
        `;
        list.appendChild(row);

        row.querySelector("[data-copy]").addEventListener("click", async (e) => {
            await navigator.clipboard.writeText(e.target.dataset.copy);
            e.target.textContent = "Copied";
            setTimeout(() => { e.target.textContent = "Copy"; }, 1200);
        });
        row.querySelector("[data-open]").addEventListener("click", async (e) => {
            try {
                await api.system.openPath(e.target.dataset.open);
            } catch (err) {
                alert(`Couldn't open folder: ${err.message}`);
            }
        });

        try {
            const pages = await api.pages.listByProject(p.id);
            const done = pages.filter((pg) => pg.status === "processed").length;
            document.getElementById(`progress-${p.id}`).textContent = `${done}/${pages.length} processed`;
        } catch {
            document.getElementById(`progress-${p.id}`).textContent = "—";
        }
    });
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
    } catch (e) {
        statusMsg.textContent = `Failed: ${e.message}`;
    }
});
