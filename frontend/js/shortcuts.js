/**
 * @file frontend/js/shortcuts.js
 * @description Keyboard shortcuts: one registry of actions with default keys, user overrides (Settings > Keyboard
 * shortcuts, stored in this browser: localStorage "okt-shortcuts") and the checks that keep them from colliding.
 * Pure logic - nothing touches the DOM at import time, so it is tested without a browser.
 *
 * Combos are strings like "Ctrl+Alt+K", "ArrowLeft", "?". Ctrl also covers Cmd on a Mac. For a printable symbol
 * ("?", "[") Shift is part of the character and not written; letters are upper case ("A" = the A key).
 *
 * Rules (validateBinding):
 * - two actions may not share a combo when their scopes can be active at the same time (scopeOverlap)
 * - an action that also works while typing in a text field needs Ctrl or Alt (it must not eat a typed letter);
 *   Alt alone + letter is fine - Polish AltGr letters (Ctrl+Alt+L = "ł") are a different combo
 * - browser / system combos (RESERVED) and the fixed keys (Escape, Tab, Enter, Space) cannot be taken
 */

export const SCOPES = {
    global: "Everywhere",
    editor: "Editor (both modes)",
    translate: "Editor: Translate",
    lettering: "Editor: Lettering",
};

/** Can actions of scope a and scope b fire on the same key press? */
export function scopeOverlap(a, b) {
    if (a === b || a === "global" || b === "global") return true;
    if (a === "editor" || b === "editor") return true;      // editor = translate + lettering
    return false;                                            // translate vs lettering: never at the same time
}

export const ACTIONS = [
    { id: "help", label: "Open Help", scope: "global", keys: "?" },
    { id: "prevBubble", label: "Previous bubble", scope: "editor", keys: "ArrowLeft" },
    { id: "nextBubble", label: "Next bubble", scope: "editor", keys: "ArrowRight" },
    { id: "prevPage", label: "Previous page", scope: "editor", keys: "Ctrl+ArrowLeft" },
    { id: "nextPage", label: "Next page", scope: "editor", keys: "Ctrl+ArrowRight" },
    { id: "nextToReview", label: "Next page to review", scope: "editor", keys: "Ctrl+Shift+ArrowRight" },
    { id: "toggleMode", label: "Switch Translate / Lettering", scope: "editor", keys: "Alt+L", whileTyping: true },
    { id: "cleanArea", label: "Clean area (draw)", scope: "editor", keys: "C" },
    { id: "exportPage", label: "Export this page", scope: "editor", keys: "Alt+E", whileTyping: true },
    { id: "save", label: "Save the translation", scope: "translate", keys: "Ctrl+Enter", whileTyping: true },
    { id: "addBubble", label: "Add bubble (draw)", scope: "translate", keys: "A" },
    { id: "splitBubble", label: "Split the bubble", scope: "translate", keys: "S" },
    { id: "fontBigger", label: "Lettering: bigger text", scope: "lettering", keys: "]" },
    { id: "fontSmaller", label: "Lettering: smaller text", scope: "lettering", keys: "[" },
    { id: "rotateRight", label: "Lettering: rotate +5°", scope: "lettering", keys: "." },
    { id: "rotateLeft", label: "Lettering: rotate -5°", scope: "lettering", keys: "," },
    { id: "resetLayout", label: "Lettering: back to automatic", scope: "lettering", keys: "R" },
];

/** Keys that are never bindable: the browser / OS keeps them, or the app uses them fixed (Escape = cancel). */
export const RESERVED = [
    "Ctrl+W", "Ctrl+T", "Ctrl+N", "Ctrl+Shift+N", "Ctrl+Shift+T", "Ctrl+Shift+W", "Ctrl+Tab", "Ctrl+Shift+Tab",
    "Ctrl+R", "Ctrl+Shift+R", "F5", "F11", "F12", "Ctrl+Shift+I", "Ctrl+Shift+J", "Ctrl+F", "Ctrl+P", "Ctrl+L",
    "Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+Z", "Ctrl+Y", "Ctrl+A", "Ctrl+Shift+Z", "Alt+ArrowLeft", "Alt+ArrowRight",
    "Alt+F4", "Escape", "Tab", "Shift+Tab", "Enter", " ", "Space", "Backspace", "Delete",
];

const MODIFIERS = ["Control", "Shift", "Alt", "Meta", "AltGraph", "CapsLock", "OS"];
const STORAGE_KEY = "okt-shortcuts";

/** "Ctrl+Alt+K" from a keydown event; null for a bare modifier press. */
export function comboFromEvent(e) {
    if (!e || MODIFIERS.includes(e.key)) return null;
    let key = e.key === " " ? "Space" : e.key;
    const printable = key.length === 1;
    if (printable) key = key.toUpperCase();
    const letterOrDigit = printable && /[\p{L}\p{N}]/u.test(key);
    const parts = [];
    if (e.ctrlKey || e.metaKey) parts.push("Ctrl");
    if (e.altKey) parts.push("Alt");
    if (e.shiftKey && (!printable || letterOrDigit)) parts.push("Shift");    // "?" already says Shift
    parts.push(key);
    return parts.join("+");
}

/** Same combo written two ways ("ctrl+alt+k", "Alt+Ctrl+K") -> one canonical form. */
export function normalizeCombo(combo) {
    if (!combo) return "";
    const raw = String(combo).trim();
    if (raw === "+" ) return "+";
    const parts = raw.split("+").map((p) => p.trim()).filter(Boolean);
    let key = parts.pop() || "";
    const mods = new Set(parts.map((p) => p.toLowerCase()));
    if (key.length === 1) key = key.toUpperCase();
    const out = [];
    if (mods.has("ctrl") || mods.has("cmd") || mods.has("meta")) out.push("Ctrl");
    if (mods.has("alt")) out.push("Alt");
    if (mods.has("shift")) out.push("Shift");
    out.push(key);
    return out.join("+");
}

export function hasCommandModifier(combo) {
    const parts = normalizeCombo(combo).split("+");
    return parts.length > 1 && (parts.includes("Ctrl") || parts.includes("Alt"));
}

export function isReserved(combo) {
    const c = normalizeCombo(combo);
    return RESERVED.some((r) => normalizeCombo(r) === c);
}

/** Readable label: "Ctrl+ArrowLeft" -> "Ctrl + ←". */
export function formatCombo(combo) {
    const names = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓", Space: "Space" };
    return normalizeCombo(combo).split("+").map((p) => names[p] || p).join(" + ");
}

export function actionById(id) {
    return ACTIONS.find((a) => a.id === id) || null;
}

function storage() {
    try { return typeof localStorage !== "undefined" ? localStorage : null; } catch { return null; }
}

/** The user's overrides {actionId: combo}; "" = the action has no key. */
export function loadOverrides(store = storage()) {
    try {
        const parsed = JSON.parse((store && store.getItem(STORAGE_KEY)) || "{}");
        return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
        return {};
    }
}

export function saveOverrides(overrides, store = storage()) {
    if (!store) return;
    const clean = {};
    for (const [id, combo] of Object.entries(overrides || {})) {
        const action = actionById(id);
        if (action && normalizeCombo(combo) !== normalizeCombo(action.keys)) clean[id] = combo === "" ? "" : normalizeCombo(combo);
    }
    store.setItem(STORAGE_KEY, JSON.stringify(clean));
}

/** {actionId: combo} = defaults + overrides. */
export function currentBindings(overrides = loadOverrides()) {
    const out = {};
    for (const a of ACTIONS) out[a.id] = normalizeCombo(Object.prototype.hasOwnProperty.call(overrides, a.id) ? overrides[a.id] : a.keys);
    return out;
}

/** Every pair of actions that would fire on the same key press: [{a, b, combo}]. */
export function findConflicts(bindings = currentBindings()) {
    const out = [];
    for (let i = 0; i < ACTIONS.length; i++) {
        for (let j = i + 1; j < ACTIONS.length; j++) {
            const a = ACTIONS[i], b = ACTIONS[j];
            const ca = bindings[a.id], cb = bindings[b.id];
            if (ca && ca === cb && scopeOverlap(a.scope, b.scope)) out.push({ a: a.id, b: b.id, combo: ca });
        }
    }
    return out;
}

/** Can `combo` be given to action `id`? {ok: true} or {ok: false, error}. "" (no key) is always allowed. */
export function validateBinding(id, combo, bindings = currentBindings()) {
    const action = actionById(id);
    if (!action) return { ok: false, error: "Unknown action" };
    const c = normalizeCombo(combo);
    if (!c) return { ok: true };
    if (isReserved(c)) return { ok: false, error: `${formatCombo(c)} is kept by the browser or the app - choose another key` };
    if (action.whileTyping && !hasCommandModifier(c)) {
        return { ok: false, error: "This action also works while typing - it needs Ctrl or Alt, otherwise it would eat a typed letter" };
    }
    for (const other of ACTIONS) {
        if (other.id !== id && bindings[other.id] === c && scopeOverlap(action.scope, other.scope)) {
            return { ok: false, error: `${formatCombo(c)} is already "${other.label}"` };
        }
    }
    return { ok: true };
}

/**
 * Which action a key press means: {typing: true when focus is in a text field, scopes: the active scopes}.
 * While typing only whileTyping actions fire.
 */
export function actionFor(event, { typing = false, scopes = ["global"] } = {}, bindings = currentBindings()) {
    const combo = comboFromEvent(event);
    if (!combo) return null;
    for (const a of ACTIONS) {
        if (bindings[a.id] !== combo || !scopes.includes(a.scope)) continue;
        if (typing && !a.whileTyping) continue;
        return a.id;
    }
    return null;
}
