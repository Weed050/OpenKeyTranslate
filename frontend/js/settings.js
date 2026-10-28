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
        document.getElementById("memoryTopKInput").value = appSettings.memory_top_k ?? 3;
        document.getElementById("memoryShortPhraseInput").value = appSettings.memory_short_phrase_max_words ?? 2;
        document.getElementById("ignorePatternsInput").value = (appSettings.ocr_ignore_patterns || []).join("\n");
        document.getElementById("appVersionLabel").textContent = appSettings.app_version || "unknown";
        fillTranslationForm();
        document.getElementById("ocrTextfixCheckbox").checked = appSettings.ocr_textfix !== false;
        document.getElementById("ocrSkipNumericCheckbox").checked = appSettings.ocr_skip_numeric !== false;
        document.getElementById("ocrSkipSymbolsCheckbox").checked = appSettings.ocr_skip_symbols !== false;
        document.getElementById("inpaintInteriorCheckbox").checked = appSettings.inpaint_use_interior !== false;
        document.getElementById("ocrRefineCheckbox").checked = appSettings.ocr_refine !== false;
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

document.getElementById("saveIgnorePatternsBtn").addEventListener("click", async () => {
    const status = document.getElementById("ignorePatternsStatus");
    const patterns = document.getElementById("ignorePatternsInput").value
        .split("\n").map((line) => line.trim()).filter(Boolean);
    try {
        const result = await api.settings.updateIgnorePatterns({ patterns });
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

/* ----------------------------- translation (BYOK) ----------------------------- */

// working copy of the form: provider -> {model, keys:[{label, api_key}]}; api_key is either the masked string the
// server returned (= "keep the stored secret") or something the user just typed.
let formProviders = {};

function fillTranslationForm() {
    const providers = appSettings.providers || {};
    formProviders = JSON.parse(JSON.stringify(providers));
    document.getElementById("translationOnCheckbox").checked = appSettings.translation_on !== false;
    document.getElementById("providerSelect").value = appSettings.active_provider || "groq";
    document.getElementById("temperatureInput").value = appSettings.translation_temperature ?? 0.4;
    renderProviderForm();
}

function currentProviderName() {
    return document.getElementById("providerSelect").value;
}

function renderProviderForm() {
    const name = currentProviderName();
    const cfg = (formProviders[name] ||= { model: "", keys: [{ label: "default", api_key: "" }] });
    document.getElementById("providerModelInput").value = cfg.model || "";

    const list = document.getElementById("keyList");
    list.innerHTML = "";
    cfg.keys.forEach((k, i) => {
        const row = document.createElement("div");
        row.style.cssText = "display:flex; gap:6px; margin-bottom:6px;";
        const label = document.createElement("input");
        label.type = "text"; label.value = k.label; label.placeholder = "label"; label.style.maxWidth = "130px";
        label.addEventListener("input", () => { k.label = label.value; });
        const key = document.createElement("input");
        key.type = "text"; key.value = k.api_key; key.placeholder = "paste API key"; key.spellcheck = false; key.autocomplete = "off";
        key.className = "text-mono";
        key.addEventListener("input", () => { k.api_key = key.value; });
        const del = document.createElement("button");
        del.className = "danger"; del.textContent = "Remove";
        del.addEventListener("click", () => { cfg.keys.splice(i, 1); renderProviderForm(); });
        row.append(label, key, del);
        list.appendChild(row);
    });
    if (!cfg.keys.length) list.innerHTML = `<p class="text-muted">No keys. Add one below.</p>`;
}

document.getElementById("providerSelect")?.addEventListener("change", renderProviderForm);
document.getElementById("providerModelInput")?.addEventListener("input", (e) => {
    (formProviders[currentProviderName()] ||= { keys: [] }).model = e.target.value;
});
document.getElementById("addKeyBtn")?.addEventListener("click", () => {
    const cfg = (formProviders[currentProviderName()] ||= { model: "", keys: [] });
    cfg.keys.push({ label: `key${cfg.keys.length + 1}`, api_key: "" });
    renderProviderForm();
});

document.getElementById("saveTranslationBtn")?.addEventListener("click", async () => {
    const status = document.getElementById("translationStatus");
    const temperature = parseFloat(document.getElementById("temperatureInput").value);
    if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) { status.textContent = "Temperature must be 0.0-2.0."; return; }
    const name = currentProviderName();
    const cfg = formProviders[name];
    if (cfg && cfg.keys.some((k) => !k.api_key.trim())) {
        status.textContent = "Fill in or remove the empty key rows first.";
        return;
    }
    try {
        const result = await api.settings.updateTranslation({
            translation_on: document.getElementById("translationOnCheckbox").checked,
            active_provider: name,
            translation_temperature: temperature,
            providers: { [name]: { model: cfg.model, keys: cfg.keys } },
        });
        status.textContent = result.message;
        await loadSettings();      // re-reads (masked) keys, so the form shows what is really stored
        renderSidebar(null);       // health banner: a "no API key" warning disappears right away
        document.getElementById("healthBanner")?.remove();
        renderSidebar(null);
    } catch (e) {
        status.textContent = `Failed: ${e.message}`;
    }
});

document.getElementById("testTranslationBtn")?.addEventListener("click", async () => {
    const status = document.getElementById("translationStatus");
    const btn = document.getElementById("testTranslationBtn");
    btn.disabled = true;
    status.textContent = "Testing...";
    try {
        const result = await api.settings.testTranslation();
        status.textContent = (result.ok ? "\u2713 " : "\u2717 ") + result.message;
    } catch (e) {
        status.textContent = `Test failed: ${e.message}`;
    } finally {
        btn.disabled = false;
    }
});

document.getElementById("saveOcrOptionsBtn")?.addEventListener("click", async () => {
    const status = document.getElementById("ocrOptionsStatus");
    try {
        const result = await api.settings.updateOcrOptions({
            ocr_textfix: document.getElementById("ocrTextfixCheckbox").checked,
            ocr_skip_numeric: document.getElementById("ocrSkipNumericCheckbox").checked,
            ocr_skip_symbols: document.getElementById("ocrSkipSymbolsCheckbox").checked,
            inpaint_use_interior: document.getElementById("inpaintInteriorCheckbox").checked,
            ocr_refine: document.getElementById("ocrRefineCheckbox").checked,
        });
        status.textContent = result.message;
    } catch (e) {
        status.textContent = `Failed: ${e.message}`;
    }
});


/* ----------------------- backup / move to another computer ----------------------- */

const backupStatus = () => document.getElementById("backupStatus");

/** Poll a background job, driving the progress bar. Resolves with the finished job (state "done" or "error"). */
async function followJob(jobId) {
    const bar = document.getElementById("backupProgress");
    bar.classList.remove("hidden");
    for (;;) {
        const job = await api.system.job(jobId);
        const pct = job.total_bytes ? Math.round((100 * job.done_bytes) / job.total_bytes) : 0;
        bar.value = pct;
        backupStatus().textContent = `${job.kind === "export" ? "Exporting" : "Importing"}: ${job.step}${job.total_bytes ? ` (${pct}%)` : ""}`;
        if (job.state !== "running") { bar.classList.add("hidden"); return job; }
        await new Promise((r) => setTimeout(r, 700));
    }
}

function setBackupButtons(disabled) {
    document.getElementById("exportAllBtn").disabled = disabled;
    document.getElementById("importAllBtn").disabled = disabled;
}

document.getElementById("exportAllBtn")?.addEventListener("click", async () => {
    const stamp = new Date().toISOString().slice(0, 10);
    try {
        const { path } = await api.system.pickSaveFile(`OpenKeyTranslate-backup-${stamp}.zip`);
        if (!path) return;
        setBackupButtons(true);
        backupStatus().textContent = "Starting...";
        const { job_id } = await api.system.exportAll({
            path,
            include_secrets: document.getElementById("backupSecretsCheckbox").checked,
            include_models: document.getElementById("backupModelsCheckbox").checked,
            include_logs: document.getElementById("backupLogsCheckbox").checked,
        });
        const job = await followJob(job_id);
        backupStatus().textContent = job.state === "done" ? `\u2713 ${job.message}` : `\u2717 Export failed: ${job.message}`;
    } catch (e) {
        backupStatus().textContent = `\u2717 ${e.message}`;
    } finally {
        setBackupButtons(false);
    }
});

document.getElementById("importAllBtn")?.addEventListener("click", async () => {
    try {
        const { path } = await api.system.pickOpenFile();
        if (!path) return;
        const info = await api.system.inspectBackup(path);
        const m = info.manifest;
        const projects = (m.projects || []).map((p) => `  - ${p.name}: ${p.chapters} chapters, ${p.pages} pages, ${p.corrections} corrections, ${p.glossary_terms} glossary terms`).join("\n");
        const summary = `Backup from ${new Date(m.created_at).toLocaleString()} (app ${m.app_version}, ${m.source_platform}).\n`
            + `${(m.total_bytes / 1048576).toFixed(0)} MB, ${m.file_count} files. API keys: ${m.has_secrets ? "included" : "not included"}; models: ${m.has_models ? "included" : "no"}.\n\nProjects:\n${projects || "  (none)"}`;
        if (info.busy_pages) { backupStatus().textContent = `\u2717 ${info.busy_pages} page(s) are being processed - wait, then import.`; return; }

        let mode = "fresh";
        if (info.target_has_data) {
            if (!confirm(`${summary}\n\nThis installation ALREADY has projects. Importing REPLACES them.\nThe current database and workspace are moved aside (not deleted) into a pre_import_backup_... folder next to the workspace.\n\nReplace the current data?`)) return;
            mode = "replace";
        } else if (!confirm(`${summary}\n\nImport into this (empty) installation?`)) return;

        setBackupButtons(true);
        const { job_id } = await api.system.importBackup({ path, mode });
        const job = await followJob(job_id);
        if (job.state === "done") {
            const notes = (job.result.notes || []).map((n) => `\n- ${n}`).join("");
            backupStatus().textContent = `\u2713 ${job.message}${notes}\nReload the page (and restart the backend if a .env was restored).`;
        } else {
            backupStatus().textContent = `\u2717 Import failed: ${job.message}\n(Nothing was lost - the previous data is back in place.)`;
        }
    } catch (e) {
        backupStatus().textContent = `\u2717 ${e.message}`;
    } finally {
        setBackupButtons(false);
    }
});
