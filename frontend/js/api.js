/**
 * @file frontend/js/api.js
 * @description Single source of truth for talking to the FastAPI backend.
 * Every fetch() call in the app goes through here - if the host/port or an
 * endpoint path ever changes, this is the one file to edit.
 */

export const API_BASE = "http://127.0.0.1:8000";

async function request(path, options = {}) {
    const hasBody = options.body !== undefined;
    const response = await fetch(`${API_BASE}${path}`, {
        ...options,
        headers: hasBody ? { "Content-Type": "application/json", ...(options.headers || {}) } : options.headers,
    });

    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
            const err = await response.json();
            if (err.detail) detail = err.detail;
        } catch {
            /* body wasn't JSON - keep the generic HTTP status message */
        }
        throw new Error(detail);
    }

    if (response.status === 204) return null;
    return response.json();
}

export const api = {
    projects: {
        list: () => request("/projects/"),
        selectFolder: () => request("/projects/select-folder"),
        import: (path, projectName) =>
            request("/projects/import", { method: "POST", body: JSON.stringify({ path, projectName }) }),
    },

    settings: {
        get: () => request("/settings/"),
        update: (payload) => request("/settings/", { method: "POST", body: JSON.stringify(payload) }),
        resetToDefault: () => request("/settings/reset-to-default", { method: "POST" }),
    },

    pages: {
        listByProject: (projectId) => request(`/pages/by-project/${projectId}`),
        get: (pageId) => request(`/pages/${pageId}`),
        imageUrl: (pageId) => `${API_BASE}/pages/${pageId}/image`,
        process: (pageId) => request(`/pages/${pageId}/process`, { method: "POST" }),
        retranslate: (pageId) => request(`/pages/${pageId}/retranslate`, { method: "POST" }),
        deleteBubble: (pageId, bubbleId) =>
            request(`/pages/${pageId}/bubbles/${bubbleId}`, { method: "DELETE" }),
    },

    system: {
        openPath: (path) => request("/system/open-path", { method: "POST", body: JSON.stringify({ path }) }),
    },

    corrections: {
        save: (pageId, bubbleId, payload) =>
            request(`/corrections/pages/${pageId}/bubbles/${bubbleId}`, {
                method: "POST",
                body: JSON.stringify(payload),
            }),
        listByProject: (projectId) => request(`/corrections/by-project/${projectId}`),
        delete: (correctionId) => request(`/corrections/${correctionId}`, { method: "DELETE" }),
    },

    logs: {
        listByProject: (projectId) => request(`/logs/by-project/${projectId}`),
    },
};
