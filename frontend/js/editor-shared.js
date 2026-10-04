/**
 * @file frontend/js/editor-shared.js
 * @description State and small helpers shared by editor.js (the bubble editor), pagepicker.js (chapter / page list)
 * and import-dialog.js. Nothing here touches the DOM at import time.
 */

import { api } from "./api.js";

export const params = new URLSearchParams(window.location.search);
export const pageId = params.get("page");

export const state = {
    projectId: params.get("project"),
    pageId,
    bubbles: [],
    selectedIndex: null,
    savedThisSession: new Set(),
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

export function showToast(message, ms = 1800) {
    el.saveToast.textContent = message;
    el.saveToast.classList.add("show");
    clearTimeout(showToast._t);
    showToast._t = setTimeout(() => el.saveToast.classList.remove("show"), ms);
}

export async function openOrAlert(path) {
    if (!path) return;
    try {
        await api.system.openPath(path);
    } catch (e) {
        alert(`Couldn't open: ${e.message}`);
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
