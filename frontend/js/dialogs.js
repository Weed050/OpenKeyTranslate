/**
 * @file frontend/js/dialogs.js
 * @description The app's own question dialog, instead of the browser's confirm().
 *
 * Why: confirm() only has OK / Cancel, so a question with three answers ("translate again", "keep the translation",
 * "undo the re-read") had to be squeezed into two - and "Cancel" read as "cancel the whole operation" although the
 * re-read was already done. Here every answer is a button with its own words, the dialog can show what changed
 * (before / after), it follows the app theme, and Esc / a click outside always means "close without choosing".
 *
 * Uses the dialog styles of css/dialogs.css (class import-dialog); a page that does not load that file gets it added
 * on the first dialog (ensureStyles).
 *
 * askOk() / notify() are the drop-in replacements of confirm() / alert() used everywhere in the app (since 0.4.1):
 * the same text, but in the app's own window - themed, with words on the buttons, Esc / click outside = Cancel.
 */

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function ensureStyles() {
    if (document.querySelector('link[href$="css/dialogs.css"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "css/dialogs.css";
    document.head.appendChild(link);
}

/**
 * A plain message (the old confirm / alert text) as title + body: a short first paragraph becomes the title, the rest
 * the body; blank lines = paragraphs, single line breaks stay.
 */
function splitMessage(message, fallbackTitle) {
    const parts = String(message ?? "").trim().split(/\n\s*\n/);
    let title = fallbackTitle;
    if (parts.length && parts[0].length <= 90 && !parts[0].includes("\n")) title = parts.shift();
    const html = parts.map((p) => `<p>${esc(p).replace(/\n/g, "<br>")}</p>`).join("");
    return { title, html };
}

/**
 * confirm() replacement: resolves to true only for the OK button.
 * @param {string} message  the question (first short paragraph = the title)
 * @param {{ok?: string, cancel?: string, danger?: boolean, title?: string}} [o]
 */
export async function askOk(message, { ok = "OK", cancel = "Cancel", danger = false, title = "" } = {}) {
    const m = splitMessage(message, "Please confirm");
    const { choice } = await choose({
        title: title || m.title,
        html: m.html,
        buttons: [{ id: "cancel", label: cancel }, { id: "ok", label: ok, primary: !danger, danger }],
    });
    return choice === "ok";
}

/** alert() replacement: shows the message with one OK button; resolves when it is closed. */
export async function notify(message, { title = "" } = {}) {
    const m = splitMessage(message, "Message");
    await choose({ title: title || m.title, html: m.html, buttons: [{ id: "ok", label: "OK", primary: true }] });
}

/**
 * Show a modal question. Resolves to { choice, remember } - choice = the id of the pressed button, or null when the
 * dialog was closed (Esc, x, click outside); remember = the "remember" checkbox (false without one).
 * @param {object} o
 * @param {string} o.title
 * @param {string} [o.html]      body, already escaped HTML
 * @param {{id: string, label: string, primary?: boolean, danger?: boolean, title?: string, left?: boolean}[]} o.buttons
 *                               left: shown on the left side of the footer (a "go back" kind of answer)
 * @param {string} [o.remember]  label of a "remember my choice" checkbox
 * @param {string} [o.width]     CSS width
 */
export function choose({ title, html = "", buttons, remember = "", width = "min(600px, 94vw)" }) {
    ensureStyles();
    return new Promise((resolve) => {
        const dlg = document.createElement("dialog");
        dlg.className = "import-dialog choice-dialog";
        dlg.style.width = width;
        const button = (b) => `<button type="button" data-choice="${esc(b.id)}" class="${b.primary ? "primary" : ""}${b.danger ? " danger" : ""}"${b.title ? ` title="${esc(b.title)}"` : ""}>${esc(b.label)}</button>`;
        dlg.innerHTML = `
            <div class="choice-head">
                <h2>${esc(title)}</h2>
                <button type="button" class="icon-btn choice-close" aria-label="Close" title="Close without choosing (Esc)">&times;</button>
            </div>
            <div class="choice-body">${html}</div>
            ${remember ? `<label class="checkbox-label"><input type="checkbox" data-remember> ${esc(remember)}</label>` : ""}
            <div class="import-footer choice-footer">
                <span class="choice-left">${buttons.filter((b) => b.left).map(button).join("")}</span>
                ${buttons.filter((b) => !b.left).map(button).join("")}
            </div>`;
        let answer = null;
        dlg.addEventListener("click", (e) => {
            const btn = e.target.closest("[data-choice]");
            if (btn) {
                answer = btn.dataset.choice;
                dlg.close();
            } else if (e.target === dlg || e.target.closest(".choice-close")) {
                dlg.close();
            }
        });
        dlg.addEventListener("close", () => {
            const remembered = !!dlg.querySelector("[data-remember]")?.checked;
            dlg.remove();
            resolve({ choice: answer, remember: answer !== null && remembered });
        });
        document.body.appendChild(dlg);
        dlg.showModal();
        (dlg.querySelector(".choice-footer .primary") || dlg.querySelector("[data-choice]"))?.focus();
    });
}

/**
 * Word-level difference of two short texts (bubble sources): { before, after } as HTML, words only in `before`
 * wrapped in <del>, words only in `after` in <ins>. Longest common subsequence over words - bubbles are short.
 */
export function wordDiff(before, after) {
    const a = String(before || "").split(/(\s+)/).filter((w) => w !== "");
    const b = String(after || "").split(/(\s+)/).filter((w) => w !== "");
    const n = a.length;
    const m = b.length;
    const lcs = Array.from({ length: n + 1 }, () => new Array(m + 1).fill(0));
    for (let i = n - 1; i >= 0; i -= 1) {
        for (let j = m - 1; j >= 0; j -= 1) {
            lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
        }
    }
    const left = [];
    const right = [];
    let i = 0;
    let j = 0;
    const mark = (tag, w) => (/^\s+$/.test(w) ? w : `<${tag}>${esc(w)}</${tag}>`);
    while (i < n && j < m) {
        if (a[i] === b[j]) {
            left.push(esc(a[i]));
            right.push(esc(b[j]));
            i += 1;
            j += 1;
        } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
            left.push(mark("del", a[i]));
            i += 1;
        } else {
            right.push(mark("ins", b[j]));
            j += 1;
        }
    }
    while (i < n) left.push(mark("del", a[i++]));
    while (j < m) right.push(mark("ins", b[j++]));
    return { before: left.join(""), after: right.join("") };
}
