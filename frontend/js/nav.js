/**
 * @file frontend/js/nav.js
 * @description Shared sidebar: project switcher (grouped Active/Archived,
 * currently-open project highlighted). Every page calls renderSidebar()
 * once on load, passing the project id it's currently showing (if any).
 *
 * Archiving isn't wired up yet (see models.models.Project.status) - every
 * project defaults to "active", so the Archived section stays empty and
 * hidden until that workflow exists. The grouping is already in place so
 * nothing else needs to change when it does.
 */

import { api } from "./api.js";

export async function renderSidebar(currentProjectId) {
    const activeEl = document.getElementById("sidebarActive");
    if (!activeEl) return;

    const archivedSection = document.getElementById("sidebarArchivedSection");
    const archivedEl = document.getElementById("sidebarArchived");
    const projectLinksEl = document.getElementById("sidebarProjectLinks");

    if (projectLinksEl) {
        if (currentProjectId) {
            projectLinksEl.classList.remove("hidden");
            projectLinksEl.innerHTML = `
                <a class="sidebar-link" href="memory.html?project=${currentProjectId}">Memory</a>
                <a class="sidebar-link" href="logs.html?project=${currentProjectId}">Logs</a>
            `;
        } else {
            projectLinksEl.classList.add("hidden");
        }
    }

    let projects;
    try {
        projects = await api.projects.list();
    } catch (e) {
        activeEl.innerHTML = `<p class="sidebar-error">Couldn't load projects.</p>`;
        console.error("[NAV] Failed to load projects:", e);
        return;
    }

    const active = projects.filter((p) => p.status !== "archived");
    const archived = projects.filter((p) => p.status === "archived");

    activeEl.innerHTML = "";
    if (!active.length) {
        activeEl.innerHTML = `<p class="sidebar-error">No projects yet.</p>`;
    } else {
        active.forEach((p) => activeEl.appendChild(projectRow(p, currentProjectId)));
    }

    if (archivedEl && archivedSection) {
        archivedEl.innerHTML = "";
        archived.forEach((p) => archivedEl.appendChild(projectRow(p, currentProjectId)));
        archivedSection.classList.toggle("hidden", archived.length === 0);
    }
}

function projectRow(project, currentProjectId) {
    const row = document.createElement("a");
    row.href = `editor.html?project=${project.id}`;
    row.className = "sidebar-project" + (String(project.id) === String(currentProjectId) ? " selected" : "");
    row.textContent = project.name;
    row.title = project.workspace_path;
    return row;
}
