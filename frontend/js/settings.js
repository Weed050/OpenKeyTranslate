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
          "ocrRefineCheckbox", "ocrScanCheckbox", "ocrDetectRotationCheckbox", "inpaintModeSelect", "bubbleOrderSelect"],
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
