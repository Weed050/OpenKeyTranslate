/**
 * @file frontend/js/settings.js
 * @description Settings page. Every section saves itself when a field is left (change event) - there is still a
 * Save button per section. A section with unsaved or invalid input stays "dirty" and leaving the page asks first
 * (an API key typed in but never saved was easy to lose).
 */
 
import { api } from "./api.js";
import { renderSidebar } from "./nav.js";
 
let appSettings = {};

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(null);
    initThemeSelect();
    loadSettings();
    wireAutosave();
});

/* ------------------------------ autosave + unsaved-changes guard ------------------------------ */

const dirtySections = new Set();
const saveTimers = {};
const SECTION_STATUS = {
    translation: "translationStatus", memory: "memorySettingsStatus", ocr: "ocrOptionsStatus",
    typesetting: "typesettingStatus", ignore: "ignorePatternsStatus",
};
// fields whose change saves their section (key rows of the translation section are wired in renderKeyRows)
const SECTION_FIELDS = {
    translation: ["translationOnCheckbox", "providerSelect", "providerModelGroq", "providerModelGemini",
                  "temperatureInput", "translationMaxTokensInput", "geminiThinkingInput"],
    memory: ["memoryMinWordsInput", "memorySimilarityInput", "memoryTopKInput", "memoryShortPhraseInput",
             "memoryLexicalInput", "memoryAbRateInput", "memoryStoreApprovedCheckbox", "memoryAbCheckbox",
             "glossaryPosteditCheckbox", "glossarySuggestCheckbox", "matchCaseCheckbox"],
    ocr: ["ocrTextfixCheckbox", "ocrSkipNumericCheckbox", "ocrSkipSymbolsCheckbox", "inpaintInteriorCheckbox",
          "ocrRefineCheckbox", "ocrScanCheckbox", "inpaintModeSelect", "bubbleOrderSelect"],
    typesetting: ["typesettingFontInput", "typesettingUpperCheckbox"],
    ignore: ["ignorePatternsInput"],
};

function sectionStatus(section, text) {
    const el = document.getElementById(SECTION_STATUS[section]);
    if (el) el.textContent = text;
}

function markDirty(section) {
    if (!dirtySections.has(section)) sectionStatus(section, "Unsaved changes - saved when you leave the field.");
    dirtySections.add(section);
}

function markClean(section, message) {
    dirtySections.delete(section);
    if (message !== undefined) sectionStatus(section, message);
}

function scheduleSave(section) {
    clearTimeout(saveTimers[section]);
    saveTimers[section] = setTimeout(() => SAVERS[section]({ auto: true }), 200);
}

function wireField(section, el) {
    if (!el) return;
    el.addEventListener("input", () => markDirty(section));
    el.addEventListener("change", () => { markDirty(section); scheduleSave(section); });   // blur / tick / select
}

function wireAutosave() {
    for (const [section, ids] of Object.entries(SECTION_FIELDS)) {
        ids.forEach((id) => wireField(section, document.getElementById(id)));
    }
    window.addEventListener("beforeunload", (event) => {
        if (!dirtySections.size) return;
        event.preventDefault();
        event.returnValue = "";          // the browser shows its own "leave page?" dialog
    });
}

function savedNow() {
    return `Saved \u2713 ${new Date().toLocaleTimeString()}`;
}

/** Appearance card: browser-local preference (js/theme.js), not part of settings.json - applies instantly. */
function initThemeSelect() {
    const select = document.getElementById("themeSelect");
    if (!select || !window.oktTheme) return;
    select.value = window.oktTheme.get();
    select.addEventListener("change", () => window.oktTheme.set(select.value));
    window.oktTheme.onChange((pref) => { select.value = pref; });   // sidebar toggle / other tab
}
 
async function loadSettings() {
    try {
        appSettings = await api.settings.get();
        document.getElementById("currentWorkspacePath").textContent = appSettings.app_root_dir;
        document.getElementById("memoryMinWordsInput").value = appSettings.memory_min_words ?? 3;
        document.getElementById("memorySimilarityInput").value = appSettings.memory_similarity_threshold ?? 0.67;
        document.getElementById("memoryTopKInput").value = appSettings.memory_top_k ?? 3;
        document.getElementById("memoryShortPhraseInput").value = appSettings.memory_short_phrase_max_words ?? 2;
        document.getElementById("memoryLexicalInput").value = appSettings.memory_lexical_min ?? 25;
        document.getElementById("memoryAbRateInput").value = appSettings.memory_ab_sample_rate ?? 1.0;
        document.getElementById("memoryStoreApprovedCheckbox").checked = appSettings.memory_store_approved === true;
        document.getElementById("memoryAbCheckbox").checked = appSettings.memory_ab_test_logging !== false;
        document.getElementById("glossaryPosteditCheckbox").checked = appSettings.glossary_postedit !== false;
        document.getElementById("glossarySuggestCheckbox").checked = appSettings.glossary_suggest_on_save !== false;
        document.getElementById("matchCaseCheckbox").checked = appSettings.translation_match_case !== false;
        document.getElementById("ignorePatternsInput").value = (appSettings.ocr_ignore_patterns || []).join("\n");
        document.getElementById("appVersionLabel").textContent = appSettings.app_version || "unknown";
        document.getElementById("settingsPathLabel").textContent = appSettings.settings_path || "unknown";
        fillTranslationForm();
        document.getElementById("ocrTextfixCheckbox").checked = appSettings.ocr_textfix !== false;
        document.getElementById("ocrSkipNumericCheckbox").checked = appSettings.ocr_skip_numeric !== false;
        document.getElementById("ocrSkipSymbolsCheckbox").checked = appSettings.ocr_skip_symbols !== false;
        document.getElementById("inpaintInteriorCheckbox").checked = appSettings.inpaint_use_interior !== false;
        document.getElementById("ocrRefineCheckbox").checked = appSettings.ocr_refine !== false;
        document.getElementById("ocrScanCheckbox").checked = appSettings.ocr_scan_missed_bubbles === true;
        document.getElementById("typesettingFontInput").value = appSettings.typesetting_font || "";
        document.getElementById("typesettingUpperCheckbox").checked = appSettings.typesetting_uppercase !== false;
        document.getElementById("inpaintModeSelect").value = appSettings.inpaint_mode || "glyph";
        document.getElementById("bubbleOrderSelect").value = appSettings.bubble_order || "ttb";
        Object.keys(SECTION_STATUS).forEach((section) => markClean(section));
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
 
document.getElementById("saveMemorySettingsBtn").addEventListener("click", () => saveMemorySettings());

async function saveMemorySettings() {
    const status = { set textContent(t) { sectionStatus("memory", t); } };
    const words = parseInt(document.getElementById("memoryMinWordsInput").value, 10);
    const threshold = parseFloat(document.getElementById("memorySimilarityInput").value);
    const topK = parseInt(document.getElementById("memoryTopKInput").value, 10);
    const shortMax = parseInt(document.getElementById("memoryShortPhraseInput").value, 10);
    if (!Number.isFinite(words) || words < 0) { status.textContent = "Not saved: min words must be >= 0."; return false; }
    if (!Number.isFinite(threshold) || threshold < 0 || threshold > 1) { status.textContent = "Not saved: threshold must be 0.0-1.0."; return false; }
    if (!Number.isFinite(topK) || topK < 1) { status.textContent = "Not saved: Top-K must be >= 1."; return false; }
    if (!Number.isFinite(shortMax) || shortMax < 0) { status.textContent = "Not saved: short phrase limit must be >= 0."; return false; }
    const lexical = parseInt(document.getElementById("memoryLexicalInput").value, 10);
    const abRate = parseFloat(document.getElementById("memoryAbRateInput").value);
    if (!Number.isFinite(lexical) || lexical < 0 || lexical > 100) { status.textContent = "Not saved: word overlap must be 0-100."; return false; }
    if (!Number.isFinite(abRate) || abRate < 0 || abRate > 1) { status.textContent = "Not saved: A/B sample rate must be 0.0-1.0."; return false; }
    try {
        const result = await api.settings.updateMemory({
            memory_min_words: words,
            memory_similarity_threshold: threshold,
            memory_top_k: topK,
            memory_short_phrase_max_words: shortMax,
            memory_lexical_min: lexical,
            memory_ab_sample_rate: abRate,
            memory_store_approved: document.getElementById("memoryStoreApprovedCheckbox").checked,
            memory_ab_test_logging: document.getElementById("memoryAbCheckbox").checked,
            glossary_postedit: document.getElementById("glossaryPosteditCheckbox").checked,
            glossary_suggest_on_save: document.getElementById("glossarySuggestCheckbox").checked,
            translation_match_case: document.getElementById("matchCaseCheckbox").checked,
        });
        markClean("memory", `${savedNow()} - ${result.message}`);
        return true;
    } catch (e) {
        status.textContent = `Not saved: ${e.message}`;
        return false;
    }
}

document.getElementById("saveIgnorePatternsBtn").addEventListener("click", () => saveIgnorePatterns());

async function saveIgnorePatterns() {
    const patterns = document.getElementById("ignorePatternsInput").value
        .split("\n").map((line) => line.trim()).filter(Boolean);
    try {
        const result = await api.settings.updateIgnorePatterns({ patterns });
        markClean("ignore", `${savedNow()} - ${result.message}`);
        return true;
    } catch (e) {
        sectionStatus("ignore", `Not saved: ${e.message}`);
        return false;
    }
}
 
 
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

const KNOWN_PROVIDERS = ["groq", "gemini"];
const PROVIDER_MODEL_INPUT = { groq: "providerModelGroq", gemini: "providerModelGemini" };

// working copy: ONE ROW PER KEY across providers - {provider, label, api_key, model, from_provider, from_label}.
// api_key is either the masked string the server returned (= "keep the stored secret") or what the user typed;
// from_provider / from_label say where that stored secret lives, so a row moved to another provider keeps its key.
let keyRows = [];

function fillTranslationForm() {
    const providers = appSettings.providers || {};
    keyRows = [];
    for (const name of KNOWN_PROVIDERS) {
        const cfg = providers[name] || {};
        document.getElementById(PROVIDER_MODEL_INPUT[name]).value = cfg.model || "";
        const keys = cfg.keys || [];
        for (const k of keys) {
            if (!k.api_key && keys.length === 1) continue;          // the empty placeholder slot of an unused provider
            keyRows.push({ provider: name, label: k.label || "default", api_key: k.api_key || "", model: k.model || "",
                           from_provider: name, from_label: k.label || "default" });
        }
    }
    document.getElementById("translationOnCheckbox").checked = appSettings.translation_on !== false;
    document.getElementById("providerSelect").value = appSettings.active_provider || "groq";
    document.getElementById("temperatureInput").value = appSettings.translation_temperature ?? 0.4;
    document.getElementById("translationMaxTokensInput").value = appSettings.translation_max_tokens ?? 4096;
    document.getElementById("geminiThinkingInput").value = appSettings.gemini_thinking_budget ?? 0;
    renderKeyRows();
}

function currentProviderName() {
    return document.getElementById("providerSelect").value;
}

function renderKeyRows() {
    const list = document.getElementById("keyList");
    list.innerHTML = "";
    const active = currentProviderName();
    keyRows.forEach((row, i) => {
        const line = document.createElement("div");
        line.style.cssText = "display:flex; gap:6px; margin-bottom:6px; align-items:center; flex-wrap:wrap;";
        line.style.opacity = row.provider === active ? "1" : "0.6";
        line.title = row.provider === active ? "Used (active provider)" : "Not used until you switch the active provider";

        const label = document.createElement("input");
        label.type = "text"; label.value = row.label; label.placeholder = "name"; label.style.maxWidth = "110px";
        label.addEventListener("input", () => { row.label = label.value; });

        const provider = document.createElement("select");
        provider.style.maxWidth = "110px";
        KNOWN_PROVIDERS.forEach((name) => provider.add(new Option(name[0].toUpperCase() + name.slice(1), name)));
        provider.value = row.provider;
        provider.addEventListener("change", () => { row.provider = provider.value; renderKeyRows(); });

        const model = document.createElement("input");
        model.type = "text"; model.value = row.model; model.spellcheck = false; model.style.maxWidth = "190px";
        model.placeholder = `default: ${document.getElementById(PROVIDER_MODEL_INPUT[row.provider]).value || "?"}`;
        model.title = "Model for this key only - empty = the provider's default model";
        model.addEventListener("input", () => { row.model = model.value; });

        const key = document.createElement("input");
        key.type = "text"; key.value = row.api_key; key.placeholder = "paste API key"; key.spellcheck = false;
        key.autocomplete = "off"; key.className = "text-mono"; key.style.flex = "1"; key.style.minWidth = "160px";
        key.addEventListener("input", () => { row.api_key = key.value; });
        row._keyInput = key;

        const del = document.createElement("button");
        del.className = "danger"; del.textContent = "Remove";
        del.addEventListener("click", () => {
            keyRows.splice(i, 1);
            renderKeyRows();
            markDirty("translation");
            scheduleSave("translation");
        });

        [label, provider, model, key].forEach((el) => wireField("translation", el));
        line.append(label, provider, model, key, del);
        list.appendChild(line);
    });
    if (!keyRows.length) list.innerHTML = `<p class="text-muted">No keys. Add one below.</p>`;
}

document.getElementById("providerSelect")?.addEventListener("change", renderKeyRows);
for (const name of KNOWN_PROVIDERS) {
    document.getElementById(PROVIDER_MODEL_INPUT[name])?.addEventListener("input", renderKeyRowPlaceholders);
}

function renderKeyRowPlaceholders() {
    // the "default: <model>" hints follow the provider default models without re-rendering (keeps focus)
    document.querySelectorAll("#keyList select").forEach((sel) => {
        const modelInput = sel.nextElementSibling;
        if (modelInput) modelInput.placeholder = `default: ${document.getElementById(PROVIDER_MODEL_INPUT[sel.value]).value || "?"}`;
    });
}

document.getElementById("addKeyBtn")?.addEventListener("click", () => {
    const provider = currentProviderName();
    const used = new Set(keyRows.filter((r) => r.provider === provider).map((r) => r.label));
    let n = keyRows.length + 1;
    while (used.has(`key${n}`)) n += 1;
    keyRows.push({ provider, label: `key${n}`, api_key: "", model: "", from_provider: null, from_label: null });
    renderKeyRows();
    markDirty("translation");
    keyRows[keyRows.length - 1]._keyInput?.focus();
});

document.getElementById("saveTranslationBtn")?.addEventListener("click", () => saveTranslation());

async function saveTranslation() {
    const status = (t) => sectionStatus("translation", t);
    const temperature = parseFloat(document.getElementById("temperatureInput").value);
    if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) { status("Not saved: temperature must be 0.0-2.0."); return false; }
    const thinking = parseInt(document.getElementById("geminiThinkingInput").value, 10);
    if (!Number.isFinite(thinking) || thinking < -1) { status("Not saved: thinking budget must be -1 or more."); return false; }
    const maxTokens = parseInt(document.getElementById("translationMaxTokensInput").value, 10);
    if (!Number.isFinite(maxTokens) || maxTokens < 256 || maxTokens > 65536) { status("Not saved: max output tokens must be 256-65536."); return false; }
    if (keyRows.some((r) => !r.api_key.trim())) {
        status("Not saved yet: paste the key into the empty row (or remove the row). Leaving the page will ask first.");
        return false;
    }
    for (const name of KNOWN_PROVIDERS) {
        const labels = keyRows.filter((r) => r.provider === name).map((r) => r.label.trim() || "default");
        const dup = labels.find((l, i) => labels.indexOf(l) !== i);
        if (dup) { status(`Not saved: two ${name} keys are named "${dup}" - names must differ.`); return false; }
    }

    const providers = {};
    for (const name of KNOWN_PROVIDERS) {
        providers[name] = {
            model: document.getElementById(PROVIDER_MODEL_INPUT[name]).value.trim(),
            keys: keyRows.filter((r) => r.provider === name).map((r) => ({
                label: r.label.trim() || "default", api_key: r.api_key.trim(), model: r.model.trim(),
                from_provider: r.from_provider, from_label: r.from_label,
            })),
        };
    }
    try {
        const result = await api.settings.updateTranslation({
            translation_on: document.getElementById("translationOnCheckbox").checked,
            active_provider: currentProviderName(),
            translation_temperature: temperature,
            translation_max_tokens: maxTokens,
            gemini_thinking_budget: thinking,
            providers,
        });
        // the stored state now: masked keys, and every row's secret lives under its current provider + name
        appSettings.providers = result.providers;
        for (const row of keyRows) {
            row.label = row.label.trim() || "default";
            const stored = (result.providers[row.provider]?.keys || []).find((k) => k.label === row.label);
            if (stored && !row.api_key.startsWith("\u2022")) {
                row.api_key = stored.api_key;
                if (row._keyInput && document.activeElement !== row._keyInput) row._keyInput.value = stored.api_key;
            }
            row.from_provider = row.provider;
            row.from_label = row.label;
        }
        markClean("translation", `${savedNow()} - ${result.message}`);
        document.getElementById("healthBanner")?.remove();
        renderSidebar(null);       // health banner: a "no API key" warning disappears right away
        return true;
    } catch (e) {
        status(`Not saved: ${e.message}`);
        return false;
    }
}

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

document.getElementById("saveOcrOptionsBtn")?.addEventListener("click", () => saveOcrOptions());

async function saveOcrOptions() {
    try {
        const result = await api.settings.updateOcrOptions({
            ocr_textfix: document.getElementById("ocrTextfixCheckbox").checked,
            ocr_skip_numeric: document.getElementById("ocrSkipNumericCheckbox").checked,
            ocr_skip_symbols: document.getElementById("ocrSkipSymbolsCheckbox").checked,
            inpaint_use_interior: document.getElementById("inpaintInteriorCheckbox").checked,
            ocr_refine: document.getElementById("ocrRefineCheckbox").checked,
            ocr_scan_missed_bubbles: document.getElementById("ocrScanCheckbox").checked,
            inpaint_mode: document.getElementById("inpaintModeSelect").value,
            bubble_order: document.getElementById("bubbleOrderSelect").value,
        });
        markClean("ocr", `${savedNow()} - ${result.message}`);
        return true;
    } catch (e) {
        sectionStatus("ocr", `Not saved: ${e.message}`);
        return false;
    }
}


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


document.getElementById("saveTypesettingBtn")?.addEventListener("click", () => saveTypesetting());

async function saveTypesetting() {
    try {
        await api.settings.updateOcrOptions({
            typesetting_font: document.getElementById("typesettingFontInput").value.trim(),
            typesetting_uppercase: document.getElementById("typesettingUpperCheckbox").checked,
        });
        markClean("typesetting", `${savedNow()} - the next preview / export uses it.`);
        return true;
    } catch (e) {
        sectionStatus("typesetting", `Not saved: ${e.message}`);
        return false;
    }
}

// section -> save function (autosave calls them with {auto: true}; the Save buttons without arguments)
const SAVERS = {
    translation: saveTranslation, memory: saveMemorySettings, ocr: saveOcrOptions,
    typesetting: saveTypesetting, ignore: saveIgnorePatterns,
};
