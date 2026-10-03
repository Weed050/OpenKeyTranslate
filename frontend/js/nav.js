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
 
/**
 * Small fixed banner for config/connectivity problems reported by GET /system/health
 * (missing API key, API host unreachable, ...). Dismissal is remembered per session.
 */
async function renderHealthBanner() {
    if (document.getElementById("healthBanner")) return;
    let issues;
    try {
        issues = (await api.system.health()).issues.filter((i) => i.level === "error" || i.level === "warn");
    } catch {
        return; // backend down: the rest of the page already shows its own errors
    }
    const dismissed = new Set(JSON.parse(sessionStorage.getItem("okt-health-dismissed") || "[]"));
    issues = issues.filter((i) => !dismissed.has(i.code));
    if (!issues.length) return;

    const banner = document.createElement("div");
    banner.id = "healthBanner";
    banner.className = "health-banner";
    issues.forEach((i) => {
        const row = document.createElement("div");
        row.className = `health-row ${i.level}`;
        const text = document.createElement("span");
        text.textContent = i.message;
        const close = document.createElement("button");
        close.className = "icon-btn";
        close.textContent = "\u00d7";
        close.title = "Dismiss for this session";
        close.addEventListener("click", () => {
            dismissed.add(i.code);
            sessionStorage.setItem("okt-health-dismissed", JSON.stringify([...dismissed]));
            row.remove();
            if (!banner.children.length) banner.remove();
        });
        row.append(text, close);
        banner.appendChild(row);
    });
    document.body.appendChild(banner);
}

export async function renderSidebar(currentProjectId) {
    const activeEl = document.getElementById("sidebarActive");
    if (!activeEl) return;
    renderHealthBanner();
 
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