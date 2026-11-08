/**
 * @file frontend/js/nav.js
 * @description Shared sidebar: project switcher (grouped Active/Archived,
 * currently-open project highlighted). Every page calls renderSidebar()
 * once on load, passing the project id it's currently showing (if any).
 *
 * Archiving isn't wired up yet (see models.models.Project.status) - every
 * project defaults to "active", so the Archived section stays empty and
 * hidden until that workflow exists. The grouping is already in place so
 * nothing else needs to change when it does.
 */
 
import { api } from "./api.js";

/**
 * Keep the place in the page over a reload (F5) and Back / Forward. The pages scroll inside <main class="main-content">,
 * not the window, so the browser's own scroll restoration does nothing - and their lists are built after a fetch, so
 * the old position does not exist yet when the page appears. The position is saved per page (path + query) in
 * sessionStorage when the page is left and put back while the page builds itself; any scrolling by the user (wheel,
 * keys, touch, the scroll bar) or 6 seconds stop the attempt. A normal link click opens a page at the top, and an
 * address with #section opened from a link goes to that section instead (F5 / Back keep the place even with one).
 *
 * The place is remembered as an ANCHOR - the element with an id nearest to the top of the view and how far it was from
 * the top - not only as pixels: on Settings the cards above fill in one after another (key list, OCR runtime, fonts),
 * so a pixel position put back early drifted down card by card (Translation -> OCR engine -> bubble handling). While
 * the page is still growing, the anchor is put back after every change of size.
 */
function keepScrollOnReload() {
    const scroller = document.querySelector(".main-content:not(.no-scroll)");
    if (!scroller) return;
    const key = `okt-scroll:${location.pathname}${location.search}`;
    const viewTop = () => scroller.getBoundingClientRect().top;

    // a sticky / fixed element (Settings' "On this page" list) is always at the top of the view - as an anchor it
    // meant "scroll so that it is at the top", which is true at scroll 0: the page went back to the top
    const pinned = (el) => {
        for (let e = el; e && e !== scroller; e = e.parentElement) {
            const pos = getComputedStyle(e).position;
            if (pos === "sticky" || pos === "fixed") return true;
        }
        return false;
    };

    /** The element under the top edge of the view (the smallest one that spans it), else the one nearest to it. */
    function currentAnchor() {
        const top = viewTop() + 1;
        let covering = null;
        let nearest = null;
        let nearestDist = Infinity;
        for (const el of scroller.querySelectorAll("[id]")) {
            const r = el.getBoundingClientRect();
            if (!r.height || pinned(el)) continue;
            if (r.top <= top && r.bottom > top) {
                if (!covering || r.height < covering.getBoundingClientRect().height) covering = el;
            } else if (Math.abs(r.top - top) < nearestDist) {
                nearest = el;
                nearestDist = Math.abs(r.top - top);
            }
        }
        const best = covering || nearest;
        return best ? { id: best.id, delta: Math.round(best.getBoundingClientRect().top - viewTop()) } : null;
    }

    window.addEventListener("pagehide", () => {
        const saved = { top: Math.round(scroller.scrollTop), anchor: scroller.scrollTop > 0 ? currentAnchor() : null };
        try { sessionStorage.setItem(key, JSON.stringify(saved)); } catch { /* private mode */ }
    });
    const nav = performance.getEntriesByType?.("navigation")?.[0];
    if (!nav || !["reload", "back_forward"].includes(nav.type)) return;
    let saved = null;
    try {
        const raw = sessionStorage.getItem(key) || "";
        saved = raw.startsWith("{") ? JSON.parse(raw) : { top: parseInt(raw, 10) || 0, anchor: null };   // older: pixels
    } catch { /* private mode */ }
    if (!saved || saved.top <= 0) return;

    let done = false;
    const stop = () => {
        done = true;
        observer.disconnect();
        ["wheel", "keydown", "touchstart", "mousedown"].forEach((t) => scroller.removeEventListener(t, stop));
    };
    const restore = () => {
        if (done) return;
        const el = saved.anchor && document.getElementById(saved.anchor.id);
        if (el && scroller.contains(el) && el.getBoundingClientRect().height && !pinned(el)) {
            scroller.scrollTop += el.getBoundingClientRect().top - viewTop() - saved.anchor.delta;
        } else if (scroller.scrollHeight - scroller.clientHeight >= saved.top) {
            scroller.scrollTop = saved.top;
        }
    };
    // put it back after every growth of the page, until the user scrolls or 6 s pass (no jump to the bottom then)
    const observer = new ResizeObserver(restore);
    const watch = () => [...scroller.querySelectorAll(":scope > *, :scope > * > *")].forEach((child) => observer.observe(child));
    watch();
    ["wheel", "keydown", "touchstart", "mousedown"].forEach((t) => scroller.addEventListener(t, stop, { passive: true }));
    setTimeout(() => { if (!done) stop(); }, 6000);
    restore();
    setTimeout(() => { if (!done) { watch(); restore(); } }, 300);    // cards / ids added by the page scripts
}
keepScrollOnReload();

/**
 * Small fixed banner for config/connectivity problems reported by GET /system/health
 * (missing API key, API host unreachable, ...). Dismissal is remembered per session.
 */
const FIX_LINKS = {
    no_api_key: { href: "settings.html#translation", label: "Open Settings > Translation & API keys" },
    empty_key_slot: { href: "settings.html#translation", label: "Open Settings > Translation & API keys" },
    no_model: { href: "settings.html#translation", label: "Open Settings > Translation & API keys" },
    unknown_provider: { href: "settings.html#translation", label: "Open Settings > Translation & API keys" },
    // opening Settings at #memory runs the test right away (js/settings.js)
    embedder_failed: { href: "settings.html#memory", label: "Check if it works now" },
};

async function renderHealthBanner() {
    if (document.getElementById("healthBanner")) return;
    let issues;
    try {
        issues = (await api.system.health()).issues.filter((i) => i.level === "error" || i.level === "warn");
    } catch {
        return; // backend down: the rest of the page already shows its own errors
    }
    const dismissed = new Set(JSON.parse(sessionStorage.getItem("okt-health-dismissed") || "[]"));
    issues = issues.filter((i) => !dismissed.has(i.code));
    if (!issues.length) return;

    const banner = document.createElement("div");
    banner.id = "healthBanner";
    banner.className = "health-banner";
    issues.forEach((i) => {
        const row = document.createElement("div");
        row.className = `health-row ${i.level}`;
        row.dataset.code = i.code;   // a passing check can remove its own row (Settings > Correction memory > Test)
        const text = document.createElement("span");
        text.textContent = i.message;
        const fix = FIX_LINKS[i.code];
        if (fix && !location.pathname.endsWith("settings.html")) {   // straight to the field that fixes it
            const a = document.createElement("a");
            a.href = fix.href;
            a.className = "health-fix";
            a.textContent = fix.label;
            text.append(" ", a);
        }
        const close = document.createElement("button");
        close.className = "icon-btn";
        close.textContent = "\u00d7";
        close.title = "Dismiss for this session";
        close.addEventListener("click", () => {
            dismissed.add(i.code);
            sessionStorage.setItem("okt-health-dismissed", JSON.stringify([...dismissed]));
            row.remove();
            if (!banner.querySelector(".health-row")) banner.remove();
        });
        row.append(text, close);
        banner.appendChild(row);
    });
    if (issues.length >= 3) {                       // many notices at once: one click hides them all (for this session)
        const all = document.createElement("div");
        all.className = "health-all";
        all.innerHTML = `<span>${issues.length} notices</span>`;
        const btn = document.createElement("button");
        btn.className = "link-btn";
        btn.textContent = "Dismiss all";
        btn.addEventListener("click", () => {
            issues.forEach((i) => dismissed.add(i.code));
            sessionStorage.setItem("okt-health-dismissed", JSON.stringify([...dismissed]));
            banner.remove();
        });
        all.appendChild(btn);
        banner.prepend(all);
    }
    document.body.appendChild(banner);
}

export async function renderSidebar(currentProjectId) {
    const activeEl = document.getElementById("sidebarActive");
    if (!activeEl) return;
    renderHealthBanner();
 
    const archivedSection = document.getElementById("sidebarArchivedSection");
    const archivedEl = document.getElementById("sidebarArchived");
    const projectLinksEl = document.getElementById("sidebarProjectLinks");
 
    if (projectLinksEl) {
        if (currentProjectId) {
            projectLinksEl.classList.remove("hidden");
            projectLinksEl.innerHTML = `
                <a class="sidebar-link" href="memory.html?project=${currentProjectId}">Memory</a>
                <a class="sidebar-link" href="logs.html?project=${currentProjectId}" title="What the AI translated, where and with which hints">History</a>
            `;
        } else {
            projectLinksEl.classList.add("hidden");
        }
    }
 
    let projects;
    try {
        projects = await api.projects.list();
    } catch (e) {
        activeEl.innerHTML = `<p class="sidebar-error">Couldn't load projects.</p>`;
        console.error("[NAV] Failed to load projects:", e);
        return;
    }
 
    const active = projects.filter((p) => p.status !== "archived");
    const archived = projects.filter((p) => p.status === "archived");
 
    activeEl.innerHTML = "";
    if (!active.length) {
        activeEl.innerHTML = `<p class="sidebar-error">No projects yet.</p>`;
    } else {
        active.forEach((p) => activeEl.appendChild(projectRow(p, currentProjectId)));
        addProjectFilter(activeEl, active.length);
        activeEl.querySelector(".sidebar-project.selected")?.scrollIntoView({ block: "nearest" });
    }
 
    if (archivedEl && archivedSection) {
        archivedEl.innerHTML = "";
        archived.forEach((p) => archivedEl.appendChild(projectRow(p, currentProjectId)));
        archivedSection.classList.toggle("hidden", archived.length === 0);
    }
}
 
/** From this many projects on, a "Find a project" field above the list (the list itself scrolls - css/layout.css). */
const SIDEBAR_FILTER_FROM = 9;

function addProjectFilter(listEl, count) {
    document.getElementById("sidebarProjectFilter")?.remove();
    if (count < SIDEBAR_FILTER_FROM) return;
    const input = document.createElement("input");
    input.type = "search";
    input.id = "sidebarProjectFilter";
    input.className = "sidebar-filter";
    input.placeholder = `Find a project (${count})`;
    input.addEventListener("input", () => {
        const q = input.value.trim().toLowerCase();
        listEl.querySelectorAll(".sidebar-project").forEach((row) => {
            row.classList.toggle("hidden", !!q && !row.textContent.toLowerCase().includes(q));
        });
    });
    listEl.before(input);
}

function projectRow(project, currentProjectId) {
    const row = document.createElement("a");
    row.href = `editor.html?project=${project.id}`;
    row.className = "sidebar-project" + (String(project.id) === String(currentProjectId) ? " selected" : "");
    row.textContent = project.name;
    row.title = project.workspace_path;
    return row;
}