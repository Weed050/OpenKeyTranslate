/**
 * @file frontend/js/settings.js
 * @description Settings page. Every section saves itself when a field is left (change event) - there is still a
 * Save button per section. A section with unsaved or invalid input stays "dirty" and leaving the page asks first
 * (an API key typed in but never saved was easy to lose).
 */
 
import { api, API_BASE } from "./api.js";
import { renderSidebar } from "./nav.js";
import { ACTIONS, SCOPES, currentBindings, loadOverrides, saveOverrides, validateBinding, comboFromEvent,
         formatCombo, normalizeCombo } from "./shortcuts.js";
 
let appSettings = {};

window.addEventListener("DOMContentLoaded", () => {
    renderSidebar(null);
    initAppearance();
    initShortcuts();
    initSettingsToc();
    loadSettings();
    wireAutosave();
});

/* ------------------------------ autosave + unsaved-changes guard ------------------------------ */

const dirtySections = new Set();
const saveTimers = {};
const SECTION_STATUS = {
    translation: "translationStatus", memory: "memorySettingsStatus", ocr: "ocrOptionsStatus", engine: "ocrEngineStatus",
    typesetting: "typesettingStatus", ignore: "ignorePatternsStatus",
};
// fields whose change saves their section (key rows of the translation section are wired in keyRowElement)
const SECTION_FIELDS = {
    translation: ["translationOnCheckbox", "providerModelGroq", "providerModelGemini", "providerModelCompat",
                  "temperatureInput", "translationMaxTokensInput", "geminiThinkingInput"],
    memory: ["memoryMinWordsInput", "memorySimilarityInput", "memoryTopKInput", "memoryShortPhraseInput",
             "memoryLexicalInput", "memoryAbRateInput", "memoryStoreApprovedCheckbox", "memoryAbCheckbox",
             "glossaryPosteditCheckbox", "glossarySuggestCheckbox", "matchCaseCheckbox"],
    ocr: ["ocrTextfixCheckbox", "ocrSkipNumericCheckbox", "ocrSkipSymbolsCheckbox", "inpaintInteriorCheckbox",
          "ocrRefineCheckbox", "ocrScanCheckbox", "ocrDetectRotationCheckbox", "inpaintModeSelect", "bubbleOrderSelect"],
    typesetting: ["typesettingFontInput", "typesettingUpperCheckbox", "typesettingShapeCheckbox"],
    engine: ["ocrDeviceSelect", "ocrDetModelSelect", "ocrThreadsInput", "ocrMkldnnCheckbox"],
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

/* ------------------------------ appearance ------------------------------ */

/**
 * Appearance card: color scheme (System / Light / Dark), the palette of the light look, the palette of the dark look
 * and the surface tint - browser-local preferences of js/theme.js (not part of settings.json) that apply instantly.
 * The previews are drawn with the real tokens: an element with data-theme / data-palette gets that scheme's colors
 * (css/tokens.css), so a light preview can sit inside a dark page without any color being repeated here.
 */
function initAppearance() {
    const theme = window.oktTheme;
    const schemeGrid = document.getElementById("schemeGrid");
    const grids = { light: document.getElementById("paletteGridLight"), dark: document.getElementById("paletteGridDark") };
    const tintSwitch = document.getElementById("tintSwitch");
    if (!theme || !schemeGrid || !grids.light || !grids.dark || !tintSwitch) return;

    const paletteAttr = (id) => (id === "default" ? "" : ` data-palette="${id}"`);
    const mock = (scheme, palette) => `
        <span class="mock" data-theme="${scheme}"${paletteAttr(palette)}>
            <span class="mock-side"><i></i><i></i><i></i></span>
            <span class="mock-main">
                <i class="mock-pill"></i><i class="mock-line"></i><i class="mock-line short"></i>
                <span class="mock-input"><i class="mock-dot"></i></span>
            </span>
            <span class="mock-card"><i></i><i></i><i></i></span>
        </span>`;

    const SCHEMES = [
        { id: "system", label: "System", hint: "Follows the light / dark setting of your OS" },
        { id: "light", label: "Light", hint: "Always light" },
        { id: "dark", label: "Dark", hint: "Always dark" },
    ];

    const TINTS = [
        { id: "off", label: "Off", hint: "Neutral grey surfaces; only the accent color changes" },
        { id: "subtle", label: "Subtle", hint: "A hint of the theme color in backgrounds and panels (default)" },
        { id: "strong", label: "Strong", hint: "Backgrounds and panels clearly in the theme color" },
    ];

    function render() {
        const scheme = theme.get();
        const light = theme.getPalette("light");
        const dark = theme.getPalette("dark");
        const tint = theme.getTint();

        schemeGrid.innerHTML = SCHEMES.map((s) => `
            <button type="button" class="scheme-card${s.id === scheme ? " selected" : ""}" role="radio"
                    aria-checked="${s.id === scheme}" data-scheme="${s.id}" title="${s.hint}">
                <span class="scheme-preview${s.id === "system" ? " split" : ""}">
                    ${s.id === "system" ? mock("light", light) + mock("dark", dark) : mock(s.id, s.id === "light" ? light : dark)}
                </span>
                <span class="scheme-label">${s.label}</span>
            </button>`).join("");

        for (const [look, grid] of Object.entries(grids)) {
            const chosen = look === "light" ? light : dark;
            grid.innerHTML = theme.palettes.map((p) => `
                <button type="button" class="palette-tile${p.id === chosen ? " selected" : ""}" role="radio"
                        aria-checked="${p.id === chosen}" data-palette-id="${p.id}" data-look="${look}"
                        title="${p.label} (${look}): ${p.hint}">
                    <span class="palette-swatch" data-theme="${look}"${paletteAttr(p.id)}></span>
                    <span class="palette-label">${p.label}</span>
                </button>`).join("");
        }

        tintSwitch.innerHTML = TINTS.map((t) => `
            <button type="button" class="${t.id === tint ? "selected" : ""}" role="radio" aria-checked="${t.id === tint}"
                    data-tint="${t.id}" title="${t.hint}">${t.label}</button>`).join("");
    }

    schemeGrid.addEventListener("click", (e) => {
        const card = e.target.closest("[data-scheme]");
        if (card) theme.set(card.dataset.scheme);
    });
    Object.values(grids).forEach((grid) => grid.addEventListener("click", (e) => {
        const tile = e.target.closest("[data-palette-id]");
        if (tile) theme.setPalette(tile.dataset.paletteId, tile.dataset.look);
    }));
    tintSwitch.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-tint]");
        if (btn) theme.setTint(btn.dataset.tint);
    });
    theme.onChange(render);   // sidebar toggle, OS switch, another tab
    render();
}

/* ------------------------------ keyboard shortcuts ------------------------------ */

/**
 * Keyboard shortcuts card: every action of js/shortcuts.js with its key. "Change" listens for the next key press
 * (Esc = cancel, Backspace = no key); the combo is checked by validateBinding (collisions where both actions could
 * fire, browser keys, the while-typing rule) before it is stored. Browser-local, applies to pages opened afterwards.
 */
function initShortcuts() {
    const list = document.getElementById("shortcutList");
    const status = document.getElementById("shortcutStatus");
    if (!list) return;
    let capturing = null;          // action id waiting for a key press

    function render(errorFor = null, error = "") {
        const bindings = currentBindings();
        const overrides = loadOverrides();
        list.innerHTML = ACTIONS.map((a) => {
            const combo = bindings[a.id];
            const custom = Object.prototype.hasOwnProperty.call(overrides, a.id);
            return `
            <div class="shortcut-row${capturing === a.id ? " capturing" : ""}">
                <span class="shortcut-label">${a.label}</span>
                <span class="shortcut-scope text-muted">${SCOPES[a.scope]}${a.whileTyping ? ", also while typing" : ""}</span>
                <span class="shortcut-key">${capturing === a.id ? "Press keys... (Esc cancels, Backspace = no key)" : combo ? `<kbd>${formatCombo(combo)}</kbd>` : '<span class="text-muted">no key</span>'}${custom ? ' <span class="badge" title="Changed from the default">custom</span>' : ""}</span>
                <span class="shortcut-actions">
                    <button type="button" data-change="${a.id}">${capturing === a.id ? "Cancel" : "Change"}</button>
                    <button type="button" data-default="${a.id}" ${custom ? "" : "disabled"} title="Back to ${formatCombo(a.keys)}">Default</button>
                </span>
                ${errorFor === a.id ? `<span class="shortcut-error">${error}</span>` : ""}
            </div>`;
        }).join("");
    }

    function store(id, combo) {
        const overrides = loadOverrides();
        const action = ACTIONS.find((a) => a.id === id);
        if (normalizeCombo(combo) === normalizeCombo(action.keys)) delete overrides[id];
        else overrides[id] = combo;
        saveOverrides(overrides);
    }

    window.addEventListener("keydown", (e) => {
        if (!capturing) return;
        e.preventDefault();
        e.stopPropagation();
        const id = capturing;
        if (e.key === "Escape") { capturing = null; render(); return; }
        const combo = e.key === "Backspace" ? "" : comboFromEvent(e);
        if (combo === null) return;                                  // a modifier alone: wait for the key
        const check = validateBinding(id, combo);
        if (!check.ok) { render(id, check.error); return; }
        capturing = null;
        store(id, combo);
        render();
        status.textContent = `${savedNow()} - applies to pages opened from now on`;
    }, true);

    list.addEventListener("click", (e) => {
        const change = e.target.closest("[data-change]");
        const def = e.target.closest("[data-default]");
        if (change) {
            capturing = capturing === change.dataset.change ? null : change.dataset.change;
            render();
        } else if (def) {
            const id = def.dataset.default;
            const action = ACTIONS.find((a) => a.id === id);
            const check = validateBinding(id, action.keys, { ...currentBindings(), [id]: "" });
            if (!check.ok) { render(id, `${check.error} - change that one first`); return; }
            store(id, action.keys);
            render();
        }
    });
    document.getElementById("resetShortcutsBtn").addEventListener("click", () => {
        if (!confirm("Put every keyboard shortcut back to its default?")) return;
        saveOverrides({});
        capturing = null;
        render();
        status.textContent = "All shortcuts are back to their defaults";
    });
    render();
}

/* ------------------------------ section list (right side) ------------------------------ */

/**
 * The sticky list of settings sections on the right (css/settings.css): one link per card, from the card's heading,
 * so a new card shows up by itself. A click scrolls the card into view; while scrolling, the card at the top of the
 * view is highlighted. The scrolling element is <main class="main-content">, not the window.
 */
function initSettingsToc() {
    const toc = document.getElementById("settingsToc");
    const scroller = document.querySelector(".main-content");
    const cards = [...document.querySelectorAll(".settings-cards > .card")].filter((c) => c.querySelector("h2"));
    if (!toc || !scroller || !cards.length) return;

    const slug = (text) => text.toLowerCase().replace(/&/g, "and").replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    toc.innerHTML = `<div class="settings-toc-title">On this page</div>`;
    const links = cards.map((card) => {
        const title = card.querySelector("h2").textContent.trim();
        card.id = card.id || `section-${slug(title)}`;
        const a = document.createElement("a");
        a.href = `#${card.id}`;
        // short label: <h2 data-toc="..."> or the heading without "(...)": "Text on the page (preview and export)"
        a.textContent = card.querySelector("h2").dataset.toc || title.replace(/\s*\(.*\)$/, "");
        a.title = title;
        a.addEventListener("click", (e) => {
            e.preventDefault();
            card.scrollIntoView({ behavior: "smooth", block: "start" });
            history.replaceState(null, "", a.href);
            setActive(a);
        });
        toc.appendChild(a);
        return a;
    });

    function setActive(link) {
        links.forEach((a) => a.classList.toggle("active", a === link));
    }

    let ticking = false;
    function update() {
        ticking = false;
        const top = scroller.getBoundingClientRect().top + 80;
        let current = 0;
        cards.forEach((card, i) => { if (card.getBoundingClientRect().top <= top) current = i; });
        if (scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4) current = cards.length - 1;   // bottom reached
        setActive(links[current]);
    }
    scroller.addEventListener("scroll", () => {
        if (!ticking) { ticking = true; requestAnimationFrame(update); }
    }, { passive: true });

    const fromHash = cards.findIndex((c) => `#${c.id}` === location.hash);
    if (fromHash > 0) cards[fromHash].scrollIntoView({ block: "start" });
    update();
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
        document.getElementById("ocrDetectRotationCheckbox").checked = appSettings.ocr_detect_page_rotation === true;
        await initFonts(appSettings.typesetting_font || "");
        document.getElementById("typesettingUpperCheckbox").checked = appSettings.typesetting_uppercase !== false;
        document.getElementById("typesettingShapeCheckbox").checked = appSettings.typesetting_shape_fit !== false;
        document.getElementById("ocrDeviceSelect").value = appSettings.ocr_device || "auto";
        document.getElementById("ocrDetModelSelect").value = appSettings.ocr_det_model || "auto";
        document.getElementById("ocrThreadsInput").value = appSettings.ocr_cpu_threads || 0;
        document.getElementById("ocrMkldnnCheckbox").checked = appSettings.ocr_mkldnn === true;
        loadOcrRuntime();
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

/*
 * ONE pool of keys (backend: services/providers - KeyPool): rows in order of use, providers may be mixed, each row
 * switched on / off. A row: {provider, label, api_key, model, base_url, enabled, from_provider, from_label}.
 * api_key is the masked string the server returned (= "keep the stored secret") or what the user typed; from_provider /
 * from_label say where that stored secret lives. The server keeps a stored secret only while the row stays with the
 * same provider and (OpenAI-compatible) the same server address - so moving a row there clears its key field here.
 */
const PROVIDER_ORDER = ["groq", "gemini", "openai_compat"];
const PROVIDER_MODEL_INPUT = { groq: "providerModelGroq", gemini: "providerModelGemini", openai_compat: "providerModelCompat" };
const GITHUB_ISSUES = "https://github.com/Weed050/OpenKeyTranslate/issues";
const isMasked = (value) => String(value || "").startsWith("•");

let keyRows = [];
let providerInfo = {};
const fetchedModels = {};            // key id -> model ids from the server (Models button)

function providerLabel(name) {
    return providerInfo[name]?.label || name;
}

function defaultModel(provider) {
    return document.getElementById(PROVIDER_MODEL_INPUT[provider])?.value.trim() || "";
}

function keyId(row) {
    return `${row.provider}/${row.label.trim() || "default"}`;
}

/** Why a row cannot be used ("" = fine) - the same rules as services/providers.unusable_reason. */
function rowProblem(row) {
    if (!(row.model.trim() || defaultModel(row.provider))) return "no model";
    if (row.provider === "openai_compat") return row.base_url.trim() ? "" : "no server address";
    return row.api_key.trim() ? "" : "no key";
}

function fillTranslationForm() {
    providerInfo = appSettings.known_providers || {};
    const providers = appSettings.providers || {};
    for (const name of PROVIDER_ORDER) {
        const input = document.getElementById(PROVIDER_MODEL_INPUT[name]);
        if (input) input.value = providers[name]?.model || "";
    }
    fillDatalist("modelsGroq", providerInfo.groq?.models || []);
    fillDatalist("modelsGemini", providerInfo.gemini?.models || []);
    const presets = document.getElementById("compatPresets");
    presets.innerHTML = "";
    (providerInfo.openai_compat?.presets || []).forEach((p) => {
        const o = document.createElement("option");
        o.value = p.base_url; o.label = p.name;
        presets.appendChild(o);
    });
    keyRows = (appSettings.key_pool || [])
        // the empty "default" slot of a provider nobody uses (fresh settings) is not worth a row
        .filter((k) => !(k.label === "default" && !k.api_key && !k.enabled && k.provider !== "openai_compat"))
        .map((k) => ({
            provider: k.provider, label: k.label, api_key: k.api_key || "", model: k.model || "", base_url: k.base_url || "",
            enabled: k.enabled !== false, from_provider: k.provider, from_label: k.label,
        }));
    document.getElementById("translationOnCheckbox").checked = appSettings.translation_on !== false;
    document.getElementById("temperatureInput").value = appSettings.translation_temperature ?? 0.4;
    document.getElementById("translationMaxTokensInput").value = appSettings.translation_max_tokens ?? 4096;
    document.getElementById("geminiThinkingInput").value = appSettings.gemini_thinking_budget ?? 0;
    renderKeyRows();
}

function fillDatalist(id, values) {
    const list = document.getElementById(id);
    if (!list) return;
    list.innerHTML = "";
    [...new Set(values)].forEach((v) => {
        const o = document.createElement("option");
        o.value = v;
        list.appendChild(o);
    });
}

function keyChanged() {
    markDirty("translation");
    scheduleSave("translation");
}

function renderKeyRows() {
    const list = document.getElementById("keyList");
    list.innerHTML = "";
    if (!keyRows.length) {
        list.innerHTML = `<p class="text-muted">No keys yet - <b>+ Add key</b> below.</p>`;
        return;
    }
    keyRows.forEach((row, i) => list.appendChild(keyRowElement(row, i)));
}

function keyRowElement(row, i) {
    const wrap = document.createElement("div");
    wrap.className = "key-row" + (row.enabled ? "" : " off");
    row._el = wrap;

    const main = document.createElement("div");
    main.className = "key-main";

    const on = document.createElement("input");
    on.type = "checkbox"; on.checked = row.enabled; on.className = "key-on";
    on.title = "Use this key for translation (off = kept, but skipped)";
    on.addEventListener("change", () => { row.enabled = on.checked; wrap.classList.toggle("off", !on.checked); keyChanged(); });

    const order = document.createElement("span");
    order.className = "key-order";
    const up = document.createElement("button");
    up.type = "button"; up.textContent = "▲"; up.title = "Use earlier"; up.disabled = i === 0;
    const down = document.createElement("button");
    down.type = "button"; down.textContent = "▼"; down.title = "Use later"; down.disabled = i === keyRows.length - 1;
    const move = (delta) => {
        const j = i + delta;
        [keyRows[i], keyRows[j]] = [keyRows[j], keyRows[i]];
        renderKeyRows();
        keyChanged();
    };
    up.addEventListener("click", () => move(-1));
    down.addEventListener("click", () => move(1));
    order.append(up, down);

    const label = document.createElement("input");
    label.type = "text"; label.value = row.label; label.placeholder = "name"; label.className = "key-label";
    label.title = "Name of the key - shown in the translation history (which key answered)";
    label.addEventListener("input", () => { row.label = label.value; });

    const provider = document.createElement("select");
    provider.className = "key-provider";
    PROVIDER_ORDER.forEach((name) => provider.add(new Option(providerLabel(name) + (providerInfo[name]?.beta ? " (beta)" : ""), name)));
    provider.value = row.provider;
    provider.addEventListener("change", () => {
        row.provider = provider.value;
        if (isMasked(row.api_key) && row.from_provider !== row.provider) {
            row.api_key = "";                 // the server would not move a stored key to another provider anyway
            row._moved = true;
        }
        renderKeyRows();
        keyChanged();
    });

    const modelBox = document.createElement("span");
    modelBox.className = "key-model-box";
    const model = document.createElement("input");
    model.type = "text"; model.value = row.model; model.spellcheck = false; model.className = "key-model";
    const listId = `keyModels${i}`;
    model.setAttribute("list", listId);
    model.placeholder = `default: ${defaultModel(row.provider) || "?"}`;
    model.title = "Model for this key only - empty = the provider's default model below. Click to pick from the list, or type any name.";
    model.addEventListener("input", () => { row.model = model.value; });
    const models = document.createElement("datalist");
    models.id = listId;
    const known = [...(fetchedModels[keyId(row)] || []), ...(providerInfo[row.provider]?.models || [])];
    [...new Set(known)].forEach((v) => { const o = document.createElement("option"); o.value = v; models.appendChild(o); });
    const modelsBtn = document.createElement("button");
    modelsBtn.type = "button"; modelsBtn.textContent = "Models"; modelsBtn.className = "key-small";
    modelsBtn.title = "Ask the server which models this key can use (fills the Model list)";
    modelsBtn.addEventListener("click", () => fetchKeyModels(row));
    modelBox.append(model, models, modelsBtn);

    const key = document.createElement("input");
    key.type = "text"; key.value = row.api_key; key.spellcheck = false; key.autocomplete = "off";
    key.className = "text-mono key-secret";
    key.placeholder = row._moved ? "paste the key again (moved)"
        : row.provider === "openai_compat" ? "API key (empty for Ollama / LM Studio)" : "paste API key";
    key.addEventListener("input", () => { row.api_key = key.value; row._moved = false; });
    row._keyInput = key;

    const test = document.createElement("button");
    test.type = "button"; test.textContent = "Test"; test.className = "key-small";
    test.title = "One tiny translation through this key only (also when it is switched off)";
    test.addEventListener("click", () => testOneKey(row));

    const del = document.createElement("button");
    del.type = "button"; del.className = "danger key-small"; del.textContent = "Remove";
    del.addEventListener("click", () => {
        if (row.api_key && !confirm(`Remove the key "${row.label}"? Its secret is deleted from the settings.`)) return;
        keyRows.splice(i, 1);
        renderKeyRows();
        keyChanged();
    });

    [label, model, key].forEach((el) => wireField("translation", el));
    main.append(on, order, label, provider, modelBox, key, test, del);
    wrap.appendChild(main);

    if (row.provider === "openai_compat") {
        const extra = document.createElement("div");
        extra.className = "key-extra";
        const base = document.createElement("input");
        base.type = "text"; base.value = row.base_url; base.spellcheck = false; base.className = "text-mono key-base";
        base.setAttribute("list", "compatPresets");
        base.placeholder = "server address, e.g. https://openrouter.ai/api/v1 or http://localhost:11434/v1";
        base.title = "Base URL of the OpenAI-compatible API (ends with /v1). Pick a preset or type it. Changing it for a saved key means pasting the key again - a key is never sent to a new address silently.";
        base.addEventListener("input", () => { row.base_url = base.value; });
        base.addEventListener("change", () => {
            if (isMasked(row.api_key)) {
                row.api_key = ""; row._moved = true;
                key.value = ""; key.placeholder = "paste the key again (new address)";
            }
        });
        wireField("translation", base);
        const note = document.createElement("span");
        note.className = "key-beta text-muted";
        note.innerHTML = `Server address <span class="key-beta-tag">beta</span> - not tested with every server; if one misbehaves, <a href="${GITHUB_ISSUES}" target="_blank" rel="noopener">report it on GitHub</a>.`;
        extra.append(note, base);
        wrap.appendChild(extra);
    }

    const problem = rowProblem(row);
    if (problem) {
        const warn = document.createElement("div");
        warn.className = "key-problem";
        warn.textContent = row.enabled ? `Not usable yet: ${problem}.` : `Switched off - and ${problem}.`;
        wrap.appendChild(warn);
    }

    const result = document.createElement("div");
    result.className = "key-result hidden";
    result.setAttribute("role", "status");
    wrap.appendChild(result);
    row._result = result;
    if (row._lastResult) showKeyResult(result, row._lastResult);
    return wrap;
}

for (const name of PROVIDER_ORDER) {
    document.getElementById(PROVIDER_MODEL_INPUT[name])?.addEventListener("input", () => {
        // the "default: <model>" hints follow the provider default models without re-rendering (keeps focus)
        keyRows.forEach((row) => {
            const input = row._el?.querySelector(".key-model");
            if (input && row.provider === name) input.placeholder = `default: ${defaultModel(name) || "?"}`;
        });
    });
}

document.getElementById("addKeyBtn")?.addEventListener("click", () => {
    const provider = keyRows.length ? keyRows[keyRows.length - 1].provider : "groq";
    const used = new Set(keyRows.filter((r) => r.provider === provider).map((r) => r.label));
    let n = keyRows.length + 1;
    while (used.has(`key${n}`)) n += 1;
    keyRows.push({ provider, label: `key${n}`, api_key: "", model: "", base_url: "", enabled: true, from_provider: null, from_label: null });
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
    const waiting = keyRows.find((r) => r.enabled && r.provider !== "openai_compat" && !r.api_key.trim());
    if (waiting) {
        status(`Not saved yet: paste the key into "${waiting.label}" (or switch it off / remove it). Leaving the page will ask first.`);
        return false;
    }
    for (const name of PROVIDER_ORDER) {
        const labels = keyRows.filter((r) => r.provider === name).map((r) => r.label.trim() || "default");
        const dup = labels.find((l, i) => labels.indexOf(l) !== i);
        if (dup) { status(`Not saved: two ${providerLabel(name)} keys are named "${dup}" - names must differ.`); return false; }
    }

    const sent = keyRows.slice();
    try {
        const result = await api.settings.updateTranslation({
            translation_on: document.getElementById("translationOnCheckbox").checked,
            translation_temperature: temperature,
            translation_max_tokens: maxTokens,
            gemini_thinking_budget: thinking,
            provider_models: Object.fromEntries(PROVIDER_ORDER.map((n) => [n, defaultModel(n)])),
            pool: sent.map((r) => ({
                provider: r.provider, label: r.label.trim() || "default", api_key: r.api_key.trim(), model: r.model.trim(),
                base_url: r.provider === "openai_compat" ? r.base_url.trim() : "", enabled: r.enabled,
                from_provider: r.from_provider, from_label: r.from_label,
            })),
        });
        // the stored state now, in the same order: masked keys, and every row's secret lives under its current name
        (result.key_pool || []).forEach((stored, i) => {
            const row = sent[i];
            if (!row) return;
            row.label = stored.label;
            row.from_provider = stored.provider;
            row.from_label = stored.label;
            if (!isMasked(row.api_key) || !row.api_key.trim()) {
                row.api_key = stored.api_key;
                if (row._keyInput && document.activeElement !== row._keyInput) row._keyInput.value = stored.api_key;
            }
        });
        const notes = (result.notes || []).join(" ");
        markClean("translation", `${savedNow()} - ${result.message}${notes ? ` ${notes}` : ""}`);
        if (!document.activeElement || !document.getElementById("keyList").contains(document.activeElement)) renderKeyRows();
        document.getElementById("healthBanner")?.remove();
        renderSidebar(null);       // health banner: a "no API key" warning disappears right away
        return true;
    } catch (e) {
        status(`Not saved: ${e.message}`);
        return false;
    }
}

/** Save first when the section has unsaved input: tests and model lists use the STORED key. */
async function savedForServer() {
    if (!dirtySections.has("translation")) return true;
    clearTimeout(saveTimers.translation);
    return saveTranslation();
}

const RESULT_KIND = {
    RateLimited: ["Rate limit", "The key works, but its limit is used up for now - wait a minute, or add another key below it."],
    KeyRejected: ["Rejected", "Check the key (copy it again), the model name (Models lists what this key can use) and the server address."],
    Unreachable: ["No connection", "The server did not answer."],
    Transient: ["Server error", "The provider has a problem right now - try again in a moment."],
    unusable: ["Not ready", ""],
    error: ["Error", ""],
};

function showKeyResult(box, r) {
    box.classList.remove("hidden", "ok", "err", "busy", "info");
    if (r.busy) {
        box.classList.add("busy");
        box.textContent = r.busy;
        return;
    }
    if (r.info) {
        box.classList.add("info");
        box.innerHTML = r.info;
        return;
    }
    box.classList.add(r.ok ? "ok" : "err");
    if (r.ok) {
        box.innerHTML = `<b>✓ Works</b> - ${escapeHtml(r.message)}`
            + (r.sample ? `<div class="key-sample">"${escapeHtml(r.source || "Hello! Where are you going?")}" &rarr; "${escapeHtml(r.sample)}"</div>` : "");
        return;
    }
    const [title, hint] = RESULT_KIND[r.kind] || RESULT_KIND.error;
    box.innerHTML = `<b>✗ ${title}</b> - ${escapeHtml(r.message)}` + (hint ? `<div class="key-hint">${hint}</div>` : "");
}

async function testOneKey(row) {
    const box = row._result;
    if (!(await savedForServer())) {
        showKeyResult(box, { ok: false, kind: "unusable", message: "Save the key first (see the message under the section)." });
        return;
    }
    const id = keyId(row);
    row._lastResult = { busy: `Testing ${row.label} (${providerLabel(row.provider)}, ${row.model.trim() || defaultModel(row.provider) || "no model"})…` };
    showKeyResult(box, row._lastResult);
    try {
        row._lastResult = await api.settings.testKey(id);
    } catch (e) {
        row._lastResult = { ok: false, kind: "error", message: e.message };
    }
    showKeyResult(row._result, row._lastResult);
}

async function fetchKeyModels(row) {
    const box = row._result;
    if (!(await savedForServer())) {
        showKeyResult(box, { ok: false, kind: "unusable", message: "Save the key first." });
        return;
    }
    const id = keyId(row);
    row._lastResult = { busy: `Asking ${providerLabel(row.provider)} which models this key can use…` };
    showKeyResult(box, row._lastResult);
    let res;
    try {
        res = await api.settings.keyModels(id);
    } catch (e) {
        res = { error: e.message };
    }
    if (res.error) {
        row._lastResult = { ok: false, kind: "error", message: res.error };
    } else {
        fetchedModels[id] = res.models;
        const current = row.model.trim() || defaultModel(row.provider);
        const missing = current && res.models.length && !res.models.includes(current);
        row._lastResult = { info: `<b>${res.models.length} model(s)</b> available - click the Model field to pick one (or type to filter).`
            + (missing ? ` <span class="key-warn">The model now set ("${escapeHtml(current)}") is not in this list.</span>` : "") };
        renderKeyRows();
    }
    showKeyResult(row._result, row._lastResult);
}

document.getElementById("testTranslationBtn")?.addEventListener("click", async () => {
    const box = document.getElementById("poolTestResult");
    const btn = document.getElementById("testTranslationBtn");
    btn.disabled = true;
    try {
        if (!(await savedForServer())) {
            showKeyResult(box, { ok: false, kind: "unusable", message: "Save the keys first." });
            return;
        }
        showKeyResult(box, { busy: "Translating one line through the pool…" });
        const result = await api.settings.testTranslation();
        showKeyResult(box, result.ok
            ? { ok: true, sample: result.sample, message: `key "${result.key}" (${result.model}) answered in ${(result.ms / 1000).toFixed(1)} s` }
            : { ok: false, kind: "error", message: result.message });
    } catch (e) {
        showKeyResult(box, { ok: false, kind: "error", message: e.message });
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
            ocr_detect_page_rotation: document.getElementById("ocrDetectRotationCheckbox").checked,
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

/* ------------------------------ fonts (Settings > Text & fonts) ------------------------------ */

const fontEsc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
let fontFolder = "";

/** Default-font select (families; Polish-capable first) + the user's font files. `current`: the saved setting. */
async function initFonts(current, refresh = false) {
    const select = document.getElementById("typesettingFontInput");
    const keep = current ?? select.value;
    try {
        const [{ fonts, folder }, { files }] = await Promise.all([api.fonts.list(refresh), api.fonts.user()]);
        fontFolder = folder;
        const opt = (f) => `<option value="${fontEsc(f.family)}">${fontEsc(f.family)}${f.polish ? "" : " (no Polish letters)"}</option>`;
        const mine = fonts.filter((f) => f.source === "user");
        const sys = fonts.filter((f) => f.source === "system");
        select.innerHTML = `<option value="">Automatic (Arial / DejaVu / Liberation)</option>`
            + (mine.length ? `<optgroup label="Your fonts">${mine.map(opt).join("")}</optgroup>` : "")
            + `<optgroup label="System fonts">${sys.map(opt).join("")}</optgroup>`;
        if (keep && ![...select.options].some((o) => o.value === keep)) {
            select.insertAdjacentHTML("beforeend", `<option value="${fontEsc(keep)}">File: ${fontEsc(keep)}</option>`);
        }
        select.value = keep;
        renderUserFonts(files);
    } catch (e) {
        document.getElementById("userFontList").innerHTML = `<span class="text-muted">Fonts unavailable: ${fontEsc(e.message)}</span>`;
        if (keep) {
            select.insertAdjacentHTML("beforeend", `<option value="${fontEsc(keep)}">${fontEsc(keep)}</option>`);
            select.value = keep;
        }
    }
}

function renderUserFonts(files) {
    const list = document.getElementById("userFontList");
    if (!files.length) {
        list.innerHTML = '<span class="text-muted">None yet - the system fonts are used.</span>';
        return;
    }
    list.innerHTML = files.map((f) => `
        <div class="user-font">
            <span class="font-sample" style="font-family:'${fontEsc(f.family).replace(/'/g, "")}'">Zażółć gęślą</span>
            <span class="font-meta">${fontEsc(f.family)} <span class="text-muted">${fontEsc(f.style)} &middot; ${fontEsc(f.file)}</span>
                ${f.polish ? "" : '<span class="font-warn" title="The font has no glyphs for some Polish letters">no Polish</span>'}</span>
            <button class="copy-btn danger" data-font-file="${fontEsc(f.file)}">Remove</button>
        </div>`).join("");
    files.forEach((f, i) => {                     // the sample line in the font itself (it is not installed in the system)
        const face = new FontFace(`okt-user-font-${i}`, `url(${API_BASE}/fonts/user/${encodeURIComponent(f.file)}/data)`);
        face.load().then((loaded) => {
            document.fonts.add(loaded);
            const sample = list.querySelectorAll(".font-sample")[i];
            if (sample) sample.style.fontFamily = `okt-user-font-${i}`;
        }).catch(() => { /* keep the fallback font */ });
    });
    list.querySelectorAll("[data-font-file]").forEach((btn) => btn.addEventListener("click", async () => {
        if (!confirm(`Remove the font file ${btn.dataset.fontFile} from the app's font folder?\n\nBubbles that use this font fall back to the default font.`)) return;
        try {
            await api.fonts.remove(btn.dataset.fontFile);
            await initFonts(null, true);
        } catch (e) {
            alert(`Couldn't remove: ${e.message}`);
        }
    }));
}

function fileToBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(file);
    });
}

document.getElementById("addFontBtn")?.addEventListener("click", () => document.getElementById("fontFileInput").click());
document.getElementById("fontFileInput")?.addEventListener("change", async (e) => {
    const status = document.getElementById("fontStatus");
    const added = [];
    for (const file of e.target.files) {
        try {
            const info = await api.fonts.upload(file.name, await fileToBase64(file));
            added.push(`${info.family} ${info.style}${info.polish ? "" : " (no Polish letters!)"}`);
        } catch (err) {
            added.push(`${file.name}: ${err.message}`);
        }
    }
    e.target.value = "";
    status.textContent = added.join("; ");
    await initFonts(null, true);
});
document.getElementById("openFontFolderBtn")?.addEventListener("click", async () => {
    try {
        await api.system.openPath(fontFolder);
    } catch (e) {
        alert(`Couldn't open: ${e.message}`);
    }
});
document.getElementById("refreshFontsBtn")?.addEventListener("click", () => initFonts(null, true));

async function saveTypesetting() {
    try {
        await api.settings.updateOcrOptions({
            typesetting_font: document.getElementById("typesettingFontInput").value.trim(),
            typesetting_uppercase: document.getElementById("typesettingUpperCheckbox").checked,
            typesetting_shape_fit: document.getElementById("typesettingShapeCheckbox").checked,
        });
        markClean("typesetting", `${savedNow()} - the next preview / export uses it.`);
        return true;
    } catch (e) {
        sectionStatus("typesetting", `Not saved: ${e.message}`);
        return false;
    }
}

/* ----------------------- OCR engine (core/ocr_runtime.py) ----------------------- */

document.getElementById("saveOcrEngineBtn")?.addEventListener("click", () => saveOcrEngine());

async function saveOcrEngine() {
    try {
        const result = await api.settings.updateOcrEngine({
            ocr_device: document.getElementById("ocrDeviceSelect").value,
            ocr_det_model: document.getElementById("ocrDetModelSelect").value,
            ocr_cpu_threads: document.getElementById("ocrThreadsInput").value.trim() || 0,
            ocr_mkldnn: document.getElementById("ocrMkldnnCheckbox").checked,
        });
        markClean("engine", `${savedNow()} - ${result.message}`);
        return true;
    } catch (e) {
        sectionStatus("engine", `Not saved: ${e.message}`);
        return false;
    }
}

const escapeHtml = (text) => String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

/** What the running OCR uses + the last self-test report. */
async function loadOcrRuntime() {
    const now = document.getElementById("ocrRuntimeNow");
    const box = document.getElementById("ocrSelftestReport");
    let info;
    try {
        info = await api.system.ocrRuntime();
    } catch (e) {
        now.textContent = `Couldn't ask the backend: ${e.message}`;
        return;
    }
    const rt = info.runtime;
    now.innerHTML = rt
        ? `Now: <b>${escapeHtml(rt.device.toUpperCase())}</b> (${escapeHtml(rt.reason)}), detection <b>${escapeHtml(rt.det_model)}</b>,
           oneDNN ${rt.mkldnn ? "on" : "off"}, threads ${rt.threads || "automatic"} - ${escapeHtml(info.paddle)}`
        : `The OCR is not loaded in this run yet - ${escapeHtml(info.paddle)}`;
    const r = info.selftest;
    if (!r) return;
    const mark = (s) => (s.ok ? "OK" : s.crash ? "CRASH" : "FAILED");
    box.innerHTML = `${escapeHtml(r.time || "")} - ${escapeHtml(r.cpu || "")}, RAM ${r.ram_total_gb ?? "?"} GB<br>`
        + (r.steps || []).map((s) => `${escapeHtml(s.step)}: <b>${mark(s)}</b> (${s.seconds}s${s.peak_mb ? `, ${s.peak_mb} MB` : ""})`).join(" &middot; ")
        + ((r.advice || []).length ? `<ul style="margin:6px 0 0 18px; padding:0;">${r.advice.map((a) => `<li>${escapeHtml(a)}</li>`).join("")}</ul>` : "");
}

/*
 * Settings > Correction memory: is the local embedding model really working? The health banner's "Check if it works
 * now" link opens Settings at #memory, which runs the test at once. The test loads the model if needed, so a load
 * that failed at start (e.g. no network for a moment) is retried; a pass removes the banner's now outdated row.
 */
const EMBEDDER_STATES = { idle: "not loaded yet (loads at the first translation)", loading: "loading…", ready: "ready", error: "failed to load" };

function describeEmbedder(s) {
    const where = s.source === "disk" ? "from disk" : s.source === "internet" ? "downloaded" : "";
    const time = s.seconds != null ? ` in ${s.seconds} s` : "";
    return `Memory model <b>${escapeHtml(s.model)}</b>: ${EMBEDDER_STATES[s.state] || escapeHtml(s.state)}`
        + (s.state === "ready" && where ? ` (${where}${time})` : "");
}

async function loadEmbedderState() {
    const el = document.getElementById("embedderState");
    try {
        el.innerHTML = describeEmbedder(await api.system.embedder());
    } catch (e) {
        el.textContent = `Memory model: couldn't ask the backend (${e.message})`;
    }
}

async function testEmbedder() {
    const btn = document.getElementById("embedderTestBtn");
    const box = document.getElementById("embedderTestResult");
    btn.disabled = true;
    showKeyResult(box, { busy: "Testing the memory model (the first load can take up to half a minute)…" });
    try {
        const r = await api.system.embedderTest();
        box.classList.remove("busy");
        box.classList.add(r.ok ? "ok" : "err");
        if (r.ok) {
            box.innerHTML = `<b>✓ Works</b> - memory hints and saving corrections are available.`
                + `<div class="key-hint">Similar lines ${r.similar} vs unrelated ${r.unrelated} (cosine), ${r.dim}-dim vectors, test ${r.test_seconds} s.</div>`;
            document.querySelector('#healthBanner [data-code="embedder_failed"]')?.remove();
            const banner = document.getElementById("healthBanner");
            if (banner && !banner.children.length) banner.remove();
        } else {
            const step = { load: "Model doesn't load", embed: "Model loaded, but can't embed text", compare: "Model gives wrong results" }[r.step] || "Failed";
            box.innerHTML = `<b>✗ ${step}</b> - ${escapeHtml(r.message)}`
                + `<div class="key-hint">${r.step === "load"
                    ? "Without the model on disk it has to be downloaded once (huggingface.co) - check the internet / VPN / firewall and press Test again."
                    : "Delete the model cache in Storage &amp; uninstall and restart the backend."}</div>`;
        }
    } catch (e) {
        showKeyResult(box, { ok: false, kind: "error", message: e.message });
    } finally {
        btn.disabled = false;
        loadEmbedderState();
    }
}

document.getElementById("embedderTestBtn")?.addEventListener("click", testEmbedder);
loadEmbedderState();
if (location.hash === "#memory") testEmbedder();

// section -> save function (autosave calls them with {auto: true}; the Save buttons without arguments)
const SAVERS = {
    translation: saveTranslation, memory: saveMemorySettings, ocr: saveOcrOptions, engine: saveOcrEngine,
    typesetting: saveTypesetting, ignore: saveIgnorePatterns,
};

/* ----------------------------- storage & uninstall ----------------------------- */

/*
 * Settings > Storage & uninstall (backend: services/storage_service.py). Counting the folders takes a few seconds (the
 * program folder holds the Python environment), so it runs on "Check sizes" or when the page is opened at #storage -
 * not on every visit of Settings. Only caches / logs / spare copies have a Delete button; the work is shown, never deleted.
 */
const STORAGE_GROUPS = [
    ["work", "Your work", "never deleted from the app"],
    ["cache", "Caches, logs and spare copies", "safe to delete - they come back by themselves"],
    ["program", "The program", ""],
];

function formatBytes(n) {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let v = n;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
    return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

async function loadStorage() {
    const status = document.getElementById("storageStatus");
    const btn = document.getElementById("storageScanBtn");
    btn.disabled = true;
    status.textContent = "Counting… (the program folder can take a few seconds)";
    try {
        const report = await api.system.storage();
        renderStorage(report);
        const total = report.items.reduce((sum, i) => sum + (i.bytes || 0), 0);
        status.textContent = `${formatBytes(total)} in all - checked ${new Date().toLocaleTimeString()}`;
    } catch (e) {
        status.textContent = `Couldn't check: ${e.message}`;
    } finally {
        btn.disabled = false;
    }
}

function renderStorage(report) {
    const list = document.getElementById("storageList");
    list.innerHTML = "";
    for (const [group, title, sub] of STORAGE_GROUPS) {
        const items = report.items.filter((i) => i.group === group);
        if (!items.length) continue;
        const head = document.createElement("div");
        head.className = "storage-group";
        head.innerHTML = `${escapeHtml(title)}${sub ? ` <span class="text-muted">- ${escapeHtml(sub)}</span>` : ""}`;
        list.appendChild(head);
        items.forEach((item) => list.appendChild(storageRow(item)));
    }
    const folders = document.getElementById("uninstallFolders");
    folders.innerHTML = "";
    for (const f of report.uninstall_folders || []) {
        const line = document.createElement("div");
        line.className = "uninstall-folder";
        line.innerHTML = `<span>${escapeHtml(f.title)}</span><code class="text-mono">${escapeHtml(f.path)}</code>`;
        const open = document.createElement("button");
        open.className = "copy-btn"; open.textContent = "Open";
        open.addEventListener("click", () => openStoragePath(f.path));
        line.appendChild(open);
        folders.appendChild(line);
    }
}

function storageRow(item) {
    const row = document.createElement("div");
    row.className = `storage-row group-${item.group}` + (item.exists ? "" : " missing");
    const size = item.exists
        ? `${item.approx ? "≥ " : ""}${formatBytes(item.bytes)}<small>${item.files} file${item.files === 1 ? "" : "s"}</small>`
        : `<small>not there</small>`;
    const notes = [item.extra_bytes ? `${formatBytes(item.extra_bytes)} ${escapeHtml(item.extra_label || "")}` : "",
                   item.shared ? escapeHtml(item.shared_note || "") : "", escapeHtml(item.note || ""),
                   item.deletable ? "" : escapeHtml(item.keep || "")].filter(Boolean);
    row.innerHTML = `
        <div class="storage-main">
            <div class="storage-title">${escapeHtml(item.title)}</div>
            <div class="text-muted storage-what">${escapeHtml(item.what)}</div>
            <code class="storage-path text-mono">${escapeHtml(item.folder)}${item.count > 1 ? ` <span class="text-muted">(${item.count} items)</span>` : ""}</code>
            ${notes.map((n) => `<div class="storage-note">${n}</div>`).join("")}
        </div>
        <div class="storage-size">${size}</div>
        <div class="storage-actions"></div>`;
    const actions = row.querySelector(".storage-actions");
    if (item.exists) {
        const open = document.createElement("button");
        open.className = "copy-btn"; open.textContent = "Open";
        open.title = "Show in the file explorer";
        open.addEventListener("click", () => openStoragePath(item.folder));
        actions.appendChild(open);
    }
    if (item.deletable && item.exists && item.bytes > 0) {
        const del = document.createElement("button");
        del.className = "danger"; del.textContent = "Delete…";
        del.addEventListener("click", async () => {
            const text = `Delete "${item.title}" (${formatBytes(item.bytes)})?\n\n${item.what}`
                + (item.shared ? `\n\n${item.shared_note}` : "") + (item.note ? `\n\n${item.note}` : "");
            if (!confirm(text)) return;
            del.disabled = true;
            try {
                const res = await api.system.storageDelete(item.id);
                document.getElementById("storageStatus").textContent = `${item.title}: ${res.message}`;
            } catch (e) {
                document.getElementById("storageStatus").textContent = `Not deleted: ${e.message}`;
            }
            const message = document.getElementById("storageStatus").textContent;
            await loadStorage();
            document.getElementById("storageStatus").textContent = message;
        });
        actions.appendChild(del);
    }
    return row;
}

async function openStoragePath(path) {
    try {
        await api.system.openPath(path);
    } catch (e) {
        alert(`Couldn't open: ${e.message}`);
    }
}

document.getElementById("storageScanBtn")?.addEventListener("click", loadStorage);
if (location.hash === "#storage") loadStorage();
document.getElementById("uninstallBox")?.addEventListener("toggle", (e) => {
    if (e.target.open && !document.getElementById("storageList").children.length) loadStorage();
});

/* OCR debug view in the editor (js/editor-ocrdebug.js) - a browser-local preference like the spellcheck switch */
const OCR_DEBUG_KEY = "okt-ocr-debug";
const ocrDebugCheckbox = document.getElementById("ocrDebugSettingCheckbox");
if (ocrDebugCheckbox) {
    ocrDebugCheckbox.checked = localStorage.getItem(OCR_DEBUG_KEY) === "1";
    ocrDebugCheckbox.addEventListener("change", () => {
        localStorage.setItem(OCR_DEBUG_KEY, ocrDebugCheckbox.checked ? "1" : "0");
    });
}
