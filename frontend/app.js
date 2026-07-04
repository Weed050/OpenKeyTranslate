/**
 * @file frontend/app.js
 * @description Core frontend logic for the OpenKeyTranslate Dashboard.
 * Handles project initialization, workspace configuration, and REST API
 * communication with the local FastAPI backend.
 */

let appSettings = {};

window.addEventListener('DOMContentLoaded', () => {
    loadProjects();
    loadSettings();
});

/**
 * Initializes the workspace for a specific project.
 * @param {number|string} id - The unique identifier of the target project.
 */
window.openProject = async function(id) {
    console.log("[WORKSPACE] Opening project with ID:", id);
    // TODO: Implement redirect or UI state change for the project editor
};

/**
 * Fetches global application settings from the backend and updates the UI.
 */
async function loadSettings() {
    // NOTE: The trailing slash is strictly required here to match FastAPI's
    // exact routing definition (@router.get("/")).
    const response = await fetch("http://127.0.0.1:8000/settings/");
    appSettings = await response.json();
    document.getElementById('currentWorkspacePath').innerText = appSettings.app_root_dir;
}

document.getElementById('changeWorkspaceBtn').addEventListener('click', async () => {
    const response = await fetch("http://127.0.0.1:8000/projects/select-folder");
    const data = await response.json();

    if(data.path) {
        // Prompt user to decide if existing projects should be migrated to the new path
        const shouldMigrate = confirm("Do you want to MIGRATE existing project data to the new workspace location?");

        const payload = {
            ...appSettings,
            app_root_dir: data.path,
            migrate_data: shouldMigrate
        };

        const saveRes = await fetch("http://127.0.0.1:8000/settings/", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify(payload)
        });

        const saveResult = await saveRes.json();
        alert(saveResult.message);
        loadSettings();
    }
});

/**
 * Fetches and renders the list of available projects from the database.
 */
async function loadProjects() {
    const response = await fetch("http://127.0.0.1:8000/projects");
    const projects = await response.json();

    const list = document.getElementById('projectList');
    list.innerHTML = "";

    if (projects.length === 0) {
        list.innerHTML = "<p>No projects found. Create a new one below.</p>";
        return;
    }

    projects.forEach(p => {
        const div = document.createElement('div');
        div.className = "project-card";
        div.innerHTML = `
            <strong>${p.name}</strong> 
            <span style="color: gray; font-size: 0.9em;">(${p.workspace_path})</span>
            <button onclick="openProject(${p.id})">Open</button>
        `;
        list.appendChild(div);
    });
}

document.getElementById('selectFolderBtn').addEventListener('click', async () => {
    const response = await fetch("http://127.0.0.1:8000/projects/select-folder");
    const data = await response.json();

    if (data.path) {
        document.getElementById('pathPreview').innerText = data.path;

        // Auto-fill project name based on the selected folder's name if the input is empty
        const projectInput = document.getElementById('projectName');
        if (!projectInput.value) {
            const pathParts = data.path.split(/[\\/]/).filter(p => p.length > 0);
            projectInput.value = pathParts.pop() || "";
        }
    }
});

document.getElementById('importBtn').addEventListener('click', async () => {
    const path = document.getElementById('pathPreview').innerText;
    const projectName = document.getElementById('projectName').value;

    if (path === "None selected" || !projectName.trim()) {
        return alert("Please select a folder and enter a project name!");
    }

    // Execute project creation/import
    const response = await fetch("http://127.0.0.1:8000/projects/import", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ path: path, projectName: projectName })
    });

    const result = await response.json();
    alert(result.message);

    // Refresh the project list on the dashboard
    loadProjects();

    // FIX: Clear form inputs to prevent namespace collisions on subsequent creations
    if (response.ok) {
        document.getElementById('projectName').value = "";
        document.getElementById('pathPreview').innerText = "None selected";
    }
});

document.getElementById('resetWorkspaceBtn').addEventListener('click', async () => {
    // Failsafe confirmation for destructive actions
    if (!confirm("Are you sure you want to reset to the default AppData location? All project data will be moved there.")) {
        return;
    }

    try {
        const response = await fetch("http://127.0.0.1:8000/settings/reset-to-default", {
            method: "POST"
        });

        const result = await response.json();
        alert(result.message);
        loadSettings();
    } catch (e) {
        console.error("[WORKSPACE] Failed to reset workspace:", e);
        alert("An error occurred while resetting the workspace configuration.");
    }
});