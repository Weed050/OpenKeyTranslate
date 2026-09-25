/**
 * @file frontend/js/settings.js
 * @description Workspace path viewing/changing/resetting, moved out of the
 * dashboard into its own page. Behavior unchanged from the original
 * app.js - just centralized through api.js and restyled.
 */

import { api } from "./api.js";
import { renderSidebar } from "./nav.js";

let appSettings = {};

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(null);
    loadSettings();
});

async function loadSettings() {
    try {
        appSettings = await api.settings.get();
        document.getElementById("currentWorkspacePath").textContent = appSettings.app_root_dir;
        document.getElementById("memoryMinWordsInput").value = appSettings.memory_min_words ?? 3;
    } catch (e) {
        document.getElementById("currentWorkspacePath").textContent = `Error: ${e.message}`;
    }
}

document.getElementById("copyWorkspaceBtn").addEventListener("click", async () => {
    try {
        await navigator.clipboard.writeText(appSettings.app_root_dir || "");
        flashStatus("Path copied.");
    } catch {
        flashStatus("Couldn't copy - copy it manually.");
    }
});

document.getElementById("changeWorkspaceBtn").addEventListener("click", async () => {
    const data = await api.projects.selectFolder();
    if (!data.path) return;

    const shouldMigrate = confirm("Do you want to MIGRATE existing project data to the new workspace location?");
    const payload = { ...appSettings, app_root_dir: data.path, migrate_data: shouldMigrate };

    try {
        const result = await api.settings.update(payload);
        flashStatus(result.message);
        loadSettings();
    } catch (e) {
        flashStatus(`Failed: ${e.message}`);
    }
});

document.getElementById("resetWorkspaceBtn").addEventListener("click", async () => {
    if (!confirm("Reset to the default AppData location? All project data will be moved there.")) return;

    try {
        const result = await api.settings.resetToDefault();
        flashStatus(result.message);
        loadSettings();
    } catch (e) {
        // NOTE: /settings/reset-to-default is commented out in the backend
        // (routers/settings.py) as of this build - this will fail until
        // that endpoint is uncommented server-side.
        flashStatus(`Failed: ${e.message}`);
    }
});

function flashStatus(message) {
    document.getElementById("settingsStatus").textContent = message;
}

document.getElementById("saveMemorySettingsBtn").addEventListener("click", async () => {
    const value = parseInt(document.getElementById("memoryMinWordsInput").value, 10);
    if (!Number.isFinite(value) || value < 0) {
        document.getElementById("memorySettingsStatus").textContent = "Enter a number >= 0.";
        return;
    }
    try {
        const result = await api.settings.updateMemory({ memory_min_words: value });
        document.getElementById("memorySettingsStatus").textContent = result.message;
    } catch (e) {
        document.getElementById("memorySettingsStatus").textContent = `Failed: ${e.message}`;
    }
});
