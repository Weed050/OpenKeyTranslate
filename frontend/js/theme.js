/**
 * @file frontend/js/theme.js
 * @description Light / dark theme. Loaded as a CLASSIC script in every page's <head> (right after tokens.css), not
 * as a module: it must set <html data-theme> before the first paint, otherwise the page flashes dark first.
 *
 * Preference (localStorage "okt-theme"): "system" (default, follows the OS setting live), "light" or "dark".
 * The resolved theme is written to <html data-theme="light|dark">; tokens.css does the rest.
 * Printing always uses the light theme (beforeprint/afterprint), so screenshots / printouts stay readable.
 *
 * Public API (window.oktTheme): get() -> preference, set(pref), resolved() -> "light"|"dark", onChange(cb).
 * The sidebar gets a small cycling button (System -> Light -> Dark); Settings has the same choice as a <select>.
 */
(function () {
    const KEY = "okt-theme";
    const PREFS = ["system", "light", "dark"];
    const LABELS = { system: "Theme: System", light: "Theme: Light", dark: "Theme: Dark" };
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const listeners = [];
    let printing = false;

    function get() {
        let pref = null;
        try { pref = localStorage.getItem(KEY); } catch { /* storage blocked: fall back to system */ }
        return PREFS.includes(pref) ? pref : "system";
    }

    function resolved() {
        const pref = get();
        if (pref === "system") return media.matches ? "light" : "dark";
        return pref;
    }

    function apply() {
        document.documentElement.dataset.theme = printing ? "light" : resolved();
        listeners.forEach((cb) => cb(get(), resolved()));
    }

    function set(pref) {
        if (!PREFS.includes(pref)) pref = "system";
        try { localStorage.setItem(KEY, pref); } catch { /* keep it for this page only */ }
        apply();
    }

    function onChange(cb) { listeners.push(cb); }

    media.addEventListener("change", () => { if (get() === "system") apply(); });
    // another tab changed the preference
    window.addEventListener("storage", (e) => { if (e.key === KEY) apply(); });
    window.addEventListener("beforeprint", () => { printing = true; apply(); });
    window.addEventListener("afterprint", () => { printing = false; apply(); });

    window.oktTheme = { get, set, resolved, onChange };
    apply();

    function addSidebarToggle() {
        const footer = document.querySelector(".sidebar-footer");
        if (!footer || document.getElementById("themeToggleBtn")) return;
        const btn = document.createElement("button");
        btn.id = "themeToggleBtn";
        btn.type = "button";
        btn.className = "sidebar-link theme-toggle";
        btn.title = "Switch color theme (System follows your OS setting)";
        const label = () => { btn.textContent = LABELS[get()]; };
        btn.addEventListener("click", () => set(PREFS[(PREFS.indexOf(get()) + 1) % PREFS.length]));
        onChange(label);
        label();
        footer.appendChild(btn);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addSidebarToggle);
    else addSidebarToggle();
})();
