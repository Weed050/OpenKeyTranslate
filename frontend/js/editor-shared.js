/**
 * @file frontend/js/editor-shared.js
 * @description State and small helpers shared by editor.js (the bubble editor), pagepicker.js (chapter / page list)
 * and import-dialog.js. Nothing here touches the DOM at import time.
 */

import { api } from "./api.js";

import { notify } from "./dialogs.js";
export const params = new URLSearchParams(window.location.search);
export const pageId = params.get("page");

export const state = {
    projectId: params.get("project"),
    pageId,
    bubbles: [],
    selectedIndex: null,
    unsavedId: null,          // bubble_id whose open textarea differs from its saved translation ("unsaved" marker)
    storeApproved: false,     // memory_store_approved: show the explicit "Approve to memory" button
    mergePicks: [],           // bubble ids Shift+clicked to be merged into the selected bubble
    detectRotation: false,    // this page's "whole page is rotated" OCR choice (Re-OCR dialog)
    cleanups: [],             // this page's manual "Clean area" rectangles ({box}), newest last
    translating: new Set(),   // bubble ids with a translation request running ("Translating..." in the list + panel)
    showOriginal: false,      // the page image shows the raw original ("Original" button)
    zoomFactor: 1,
    baseScale: 1,
    naturalW: 0,
    naturalH: 0,
    jsonPath: null,
    pageFolder: null,
    flatPageList: [], // all pages in the project, sorted, for prev/next-page fallthrough
};

/** DOM references of the editor page; filled once by editor.js init() when the DOM exists. */
export const el = {};

export function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
}

/**
 * A short message at the top of the editor (under the toolbar). It stays at least as long as it takes to read
 * (a longer text stays longer than `ms`), does not go away while the mouse is over it, and has a close button -
 * at the bottom, small and for 2-3 s, messages like "the OCR read the same text" were gone before they were read.
 * `action` {label, run}: one button in the message (e.g. Undo).
 */
export function showToast(message, ms = 1800, action = null) {
    const toast = el.saveToast;
    const hide = () => { clearTimeout(showToast._t); toast.classList.remove("show"); };
    toast.textContent = "";
    const text = document.createElement("span");
    text.textContent = message;
    toast.append(text);
    if (action) {
        const btn = document.createElement("button");
        btn.className = "toast-action";
        btn.textContent = action.label;
        btn.addEventListener("click", () => { hide(); action.run(); }, { once: true });
        toast.append(btn);
    }
    const close = document.createElement("button");
    close.className = "toast-close";
    close.textContent = "×";
    close.title = "Close";
    close.setAttribute("aria-label", "Close the message");
    close.addEventListener("click", hide, { once: true });
    toast.append(close);
    toast.classList.toggle("has-action", !!action);
    toast.classList.add("show");
    const readTime = 1500 + String(message).length * 45;          // ~ reading speed; "Saved" stays short
    const duration = Math.max(ms, readTime, action ? 6000 : 0);
    const schedule = (after) => { clearTimeout(showToast._t); showToast._t = setTimeout(hide, after); };
    toast.onmouseenter = () => clearTimeout(showToast._t);
    toast.onmouseleave = () => { if (toast.classList.contains("show")) schedule(1500); };
    schedule(duration);
}

export async function openOrAlert(path) {
    if (!path) return;
    try {
        await api.system.openPath(path);
    } catch (e) {
        notify(`Couldn't open: ${e.message}`);
    }
}

/* ------------------------------ chapter helpers ------------------------------ */

export function groupByChapter(pages) {
    const map = new Map();
    pages.forEach((p) => {
        if (!map.has(p.chapter)) map.set(p.chapter, []);
        map.get(p.chapter).push(p);
    });
    // Natural sort ("Chapter_2" before "Chapter_10") instead of the Map's arrival order.
    return new Map([...map.entries()].sort((a, b) => compareChapterNames(a[0], b[0])));
}

// Mirrors utils/chapter_labels.py chapter_sort_key: Chapter_13 < Chapter_13.5 < Chapter_14 < "Epilog".
export function chapterSortKey(name) {
    const m = /^Chapter_(\d+)(?:\.(\d+))?$/.exec(name);
    if (m) return [0, parseInt(m[1], 10), m[2] ? parseFloat(`0.${m[2]}`) : 0];
    return [1, 0, 0];
}

export function compareChapterNames(a, b) {
    const ka = chapterSortKey(a), kb = chapterSortKey(b);
    for (let i = 0; i < 3; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
    return a.localeCompare(b, undefined, { numeric: true });
}
