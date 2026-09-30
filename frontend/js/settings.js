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
        document.getElementById("memorySimilarityInput").value = appSettings.memory_similarity_threshold ?? 0.80;
        document.getElementById("appVersionLabel").textContent = appSettings.app_version || "unknown";
        document.getElementById("memoryTopKInput").value = appSettings.memory_top_k ?? 3;
        document.getElementById("memoryShortPhraseInput").value = appSettings.memory_short_phrase_max_words ?? 2;
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
        flashStatus(`Failed: ${e.message}`);
    }
});
 
function flashStatus(message) {
    document.getElementById("settingsStatus").textContent = message;
}
 
document.getElementById("saveMemorySettingsBtn").addEventListener("click", async () => {
    const status = document.getElementById("memorySettingsStatus");
    const words = parseInt(document.getElementById("memoryMinWordsInput").value, 10);
    const threshold = parseFloat(document.getElementById("memorySimilarityInput").value);
    const topK = parseInt(document.getElementById("memoryTopKInput").value, 10);
    const shortMax = parseInt(document.getElementById("memoryShortPhraseInput").value, 10);
    if (!Number.isFinite(words) || words < 0) { status.textContent = "Min words must be >= 0."; return; }
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) { status.textContent = "Threshold must be 0.0-1.0."; return; }
    if (!Number.isFinite(topK) || topK < 1) { status.textContent = "Top-K must be >= 1."; return; }
    if (!Number.isFinite(shortMax) || shortMax < 0) { status.textContent = "Short phrase limit must be >= 0."; return; }
    try {
        const result = await api.settings.updateMemory({
            memory_min_words: words,
            memory_similarity_threshold: threshold,
            memory_top_k: topK,
            memory_short_phrase_max_words: shortMax,
        });
        status.textContent = result.message;
    } catch (e) {
        status.textContent = `Failed: ${e.message}`;
    }
});
 
document.getElementById("openLogFolderBtn").addEventListener("click", async () => {
    if (!appSettings.log_file_path) return;
    try {
        await api.system.openPath(appSettings.log_file_path);
    } catch (e) {
        alert(`Couldn't open log file: ${e.message}`);
    }
});
 
document.getElementById("openDbFolderBtn").addEventListener("click", async () => {
    if (!appSettings.database_path) return;
    try {
        await api.system.openPath(appSettings.database_path);
    } catch (e) {
        alert(`Couldn't open database file: ${e.message}`);
    }
});
 
 
document.getElementById("viewLogBtn").addEventListener("click", async () => {
    const block = document.getElementById("logViewerBlock");
    block.classList.toggle("hidden");
    if (!block.classList.contains("hidden")) await refreshLogViewer();
});
document.getElementById("logErrorsOnlyCheckbox").addEventListener("change", refreshLogViewer);
 
async function refreshLogViewer() {
    const errorsOnly = document.getElementById("logErrorsOnlyCheckbox").checked;
    const content = document.getElementById("logViewerContent");
    content.textContent = "Loading...";
    try {
        const result = await api.system.tailLog(200, errorsOnly);
        content.textContent = result.lines.length ? result.lines.join("\n") : "(no matching log lines)";
    } catch (e) {
        content.textContent = `Couldn't load log: ${e.message}`;
    }
}
 
const SPELLCHECK_STORAGE_KEY = "okt-spellcheck-enabled";
const spellcheckCheckbox = document.getElementById("spellcheckSettingCheckbox");
if (spellcheckCheckbox) {
    spellcheckCheckbox.checked = localStorage.getItem(SPELLCHECK_STORAGE_KEY) === "1";
    spellcheckCheckbox.addEventListener("change", () => {
        localStorage.setItem(SPELLCHECK_STORAGE_KEY, spellcheckCheckbox.checked ? "1" : "0");
    });
}