/**
 * @file frontend/js/diff.js
 * @description Shared word-level diff (LCS-based) and its HTML rendering,
 * used by logs.js, memory.js and glossary.js wherever two translations of
 * the same source need a visual diff instead of two flat strings.
 */

export function wordDiff(a, b) {
    const aw = a.split(/(\s+)/);
    const bw = b.split(/(\s+)/);
    const m = aw.length, n = bw.length;
    const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) {
        for (let j = n - 1; j >= 0; j--) {
            dp[i][j] = aw[i] === bw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
        }
    }
    let i = 0, j = 0;
    const parts = [];
    while (i < m && j < n) {
        if (aw[i] === bw[j]) { parts.push({ t: aw[i], type: "same" }); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { parts.push({ t: aw[i], type: "removed" }); i++; }
        else { parts.push({ t: bw[j], type: "added" }); j++; }
    }
    while (i < m) { parts.push({ t: aw[i], type: "removed" }); i++; }
    while (j < n) { parts.push({ t: bw[j], type: "added" }); j++; }
    return parts;
}

export function diffHtml(a, b, escapeHtml) {
    if (a.trim() === b.trim()) return `<span class="text-muted">identical</span>`;
    return wordDiff(a, b)
        .map((p) => {
            if (p.type === "same") return escapeHtml(p.t);
            const cls = p.type === "removed" ? "diff-removed" : "diff-added";
            return `<span class="${cls}">${escapeHtml(p.t)}</span>`;
        })
        .join("");
}