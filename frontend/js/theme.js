/**
 * @file frontend/js/theme.js
 * @description Color scheme (light / dark), a theme palette PER SCHEME and the surface tint strength. Loaded as a
 * CLASSIC script in every page's <head> (right after tokens.css), not as a module: it must set the <html> attributes
 * before the first paint, otherwise the page flashes dark first.
 *
 * Browser-local preferences (localStorage), all independent of settings.json:
 * - "okt-theme":         "system" (default, follows the OS setting live), "light" or "dark" -> <html data-theme>
 * - "okt-palette-light": palette used while the page is light  \  -> <html data-palette="<id>"> for the scheme in use
 * - "okt-palette-dark":  palette used while the page is dark   /    (attribute absent for the default palette)
 *   (an old single "okt-palette" choice is taken for both until one of them is changed)
 * - "okt-tint":          "subtle" (default), "off" or "strong" -> <html data-tint> (absent for subtle): how much of
 *                        the palette's hue goes into the surfaces; the accent color is the same at every strength
 * Printing always uses the light scheme of the default palette (beforeprint/afterprint), so printouts stay neutral.
 *
 * Public API (window.oktTheme): get() -> scheme preference, set(pref), resolved() -> "light"|"dark",
 * getPalette(scheme = resolved()), setPalette(id, scheme = resolved()), palettes -> [{id, label, hint}],
 * getTint(), setTint(v), tints, onChange(cb(pref, resolved, palette)).
 * The sidebar gets a small cycling button (System -> Light -> Dark); Settings > Appearance has the full picker.
 */
(function () {
    const KEY = "okt-theme";
    const OLD_PALETTE_KEY = "okt-palette";
    const PALETTE_KEYS = { light: "okt-palette-light", dark: "okt-palette-dark" };
    const TINT_KEY = "okt-tint";
    const PREFS = ["system", "light", "dark"];
    const TINTS = ["off", "subtle", "strong"];
    const PALETTES = [
        { id: "default", label: "Slate", hint: "Neutral grey surfaces, blue accent (the original look)" },
        { id: "mono", label: "Ink", hint: "No color at all - nothing in the UI competes with the page colors" },
        { id: "paper", label: "Washi", hint: "Warm paper tones, crimson accent" },
        { id: "rust", label: "Rust", hint: "Warm surfaces, brick red accent" },
        { id: "ember", label: "Persimmon", hint: "Warm surfaces, orange accent" },
        { id: "yuzu", label: "Yuzu", hint: "Yellow-tinted surfaces, golden accent" },
        { id: "grove", label: "Matcha", hint: "Green-tinted surfaces, green accent" },
        { id: "jade", label: "Jade", hint: "Teal-tinted surfaces, teal accent" },
        { id: "ocean", label: "Tide", hint: "Blue-tinted surfaces, sky blue accent" },
        { id: "iris", label: "Wisteria", hint: "Violet-tinted surfaces, violet accent" },
        { id: "plum", label: "Plum", hint: "Magenta-tinted surfaces, plum accent" },
        { id: "sakura", label: "Sakura", hint: "Pink-tinted surfaces, cherry blossom accent" },
    ];
    const PALETTE_IDS = PALETTES.map((p) => p.id);
    const LABELS = { system: "Theme: System", light: "Theme: Light", dark: "Theme: Dark" };
    const media = window.matchMedia("(prefers-color-scheme: light)");
    const listeners = [];
    let printing = false;

    function read(key) {
        try { return localStorage.getItem(key); } catch { return null; }   // storage blocked: defaults
    }

    function write(key, value) {
        try { localStorage.setItem(key, value); } catch { /* keep it for this page only */ }
    }

    function get() {
        const pref = read(KEY);
        return PREFS.includes(pref) ? pref : "system";
    }

    function resolved() {
        const pref = get();
        if (pref === "system") return media.matches ? "light" : "dark";
        return pref;
    }

    function getPalette(scheme = resolved()) {
        const id = read(PALETTE_KEYS[scheme === "light" ? "light" : "dark"]) ?? read(OLD_PALETTE_KEY);
        return PALETTE_IDS.includes(id) ? id : "default";
    }

    function getTint() {
        const t = read(TINT_KEY);
        return TINTS.includes(t) ? t : "subtle";
    }

    function apply() {
        const root = document.documentElement;
        root.dataset.theme = printing ? "light" : resolved();
        const palette = getPalette();
        if (printing || palette === "default") delete root.dataset.palette;
        else root.dataset.palette = palette;
        const tint = getTint();
        if (printing || tint === "subtle") delete root.dataset.tint;
        else root.dataset.tint = tint;
        listeners.forEach((cb) => cb(get(), resolved(), palette));
    }

    function set(pref) {
        write(KEY, PREFS.includes(pref) ? pref : "system");
        apply();
    }

    function setPalette(id, scheme = resolved()) {
        const target = scheme === "light" ? "light" : "dark";
        const other = target === "light" ? "dark" : "light";
        // the first per-scheme choice: keep the other scheme on what the old single choice gave it
        if (read(PALETTE_KEYS[other]) === null) write(PALETTE_KEYS[other], getPalette(other));
        write(PALETTE_KEYS[target], PALETTE_IDS.includes(id) ? id : "default");
        apply();
    }

    function setTint(value) {
        write(TINT_KEY, TINTS.includes(value) ? value : "subtle");
        apply();
    }

    function onChange(cb) { listeners.push(cb); }

    media.addEventListener("change", () => { if (get() === "system") apply(); });
    // another tab changed a preference
    const OWN_KEYS = [KEY, OLD_PALETTE_KEY, PALETTE_KEYS.light, PALETTE_KEYS.dark, TINT_KEY];
    window.addEventListener("storage", (e) => { if (OWN_KEYS.includes(e.key)) apply(); });
    window.addEventListener("beforeprint", () => { printing = true; apply(); });
    window.addEventListener("afterprint", () => { printing = false; apply(); });

    window.oktTheme = { get, set, resolved, getPalette, setPalette, palettes: PALETTES, getTint, setTint, tints: TINTS, onChange };
    apply();

    function addSidebarToggle() {
        const footer = document.querySelector(".sidebar-footer");
        if (!footer || document.getElementById("themeToggleBtn")) return;
        const btn = document.createElement("button");
        btn.id = "themeToggleBtn";
        btn.type = "button";
        btn.className = "sidebar-link sidebar-button";
        btn.title = "Switch light / dark (System follows your OS setting). Colors: Settings > Appearance";
        const label = () => { btn.textContent = LABELS[get()]; };
        btn.addEventListener("click", () => set(PREFS[(PREFS.indexOf(get()) + 1) % PREFS.length]));
        onChange(label);
        label();
        footer.appendChild(btn);
    }

    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", addSidebarToggle);
    else addSidebarToggle();
})();
