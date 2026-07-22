(() => {
  "use strict";

  const TOKEN_KEY = "mongars.session.token";
  const POLL_INTERVAL_MS = 5_000;
  const ACTIVE_REINDEX_STATUSES = new Set(["waiting_approval", "queued", "running"]);

  const state = {
    activeTaskId: "",
    queueing: false,
    readiness: null,
    summaryText: "",
  };

  function apiToken() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch {
      return "";
    }
  }

  function isSecureTransport() {
    return window.isSecureContext
      || window.location.protocol === "https:"
      || ["localhost", "127.0.0.1", "::1"].includes(window.location.hostname);
  }

  async function parseResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    const payload = contentType.includes("application/json")
      ? await response.json()
      : await response.text();
    if (response.ok || response.status === 503) return payload;
    const detail = payload && typeof payload === "object" ? payload.detail : null;
    const message = typeof detail === "string"
      ? detail
      : typeof detail?.code === "string"
        ? detail.code.replaceAll("_", " ")
        : `Request failed with HTTP ${response.status}.`;
    throw new Error(message);
  }

  async function request(path, options = {}) {
    const token = apiToken();
    if (!token) throw new Error("Connect with the API token first.");
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${token}`);
    if (typeof options.body === "string" && !headers.has("Content-Type")) {
      headers.set("Content-Type", "application/json");
    }
    const response = await fetch(path, { ...options, headers });
    if (response.status === 401) throw new Error("The API token was rejected.");
    return parseResponse(response);
  }

  function openTasks() {
    const taskLink = document.querySelector('[data-view-link="tasks"]');
    if (taskLink instanceof HTMLElement) taskLink.click();
    window.location.hash = "#tasks";
  }

  function recoveryButton() {
    let button = document.getElementById("memory-reindex-recovery");
    if (button instanceof HTMLButtonElement) return button;

    const actions = document.querySelector("#memory-view .view-header-actions");
    if (!(actions instanceof HTMLElement)) return null;

    button = document.createElement("button");
    button.id = "memory-reindex-recovery";
    button.className = "secondary-button compact";
    button.type = "button";
    button.hidden = true;
    button.textContent = "Queue memory reindex";
    button.addEventListener("click", () => {
      if (state.activeTaskId) {
        openTasks();
        return;
      }
      void queueReindex();
    });
    actions.prepend(button);
    return button;
  }

  function setManagedSummary(text) {
    const summary = document.getElementById("memory-summary");
    if (!(summary instanceof HTMLElement)) return;
    if (text) {
      summary.textContent = text;
      state.summaryText = text;
    } else if (state.summaryText && summary.textContent === state.summaryText) {
      summary.textContent = "Enter a query to search embedded notes and documents.";
      state.summaryText = "";
    }
  }

  function setStatusLabels(label) {
    for (const id of ["global-status-label", "sidebar-status-label"]) {
      const element = document.getElementById(id);
      if (element instanceof HTMLElement && element.textContent !== label) {
        element.textContent = label;
      }
    }
  }

  function embeddingDependency(readiness) {
    return readiness?.dependencies?.embedding_space || null;
  }

  function parserDependency(readiness) {
    return readiness?.dependencies?.parser || null;
  }

  function reindexRequired(readiness) {
    const dependency = embeddingDependency(readiness);
    return Boolean(
      dependency
      && (dependency.reindex_required === true
        || dependency.status === "reindex_required"
        || dependency.error_code === "embedding_reindex_required"),
    );
  }

  function updateRecoverySurface() {
    const button = recoveryButton();
    const embedding = embeddingDependency(state.readiness);
    const parser = parserDependency(state.readiness);

    if (reindexRequired(state.readiness)) {
      const legacyCount = Number.isSafeInteger(embedding?.legacy_chunk_count)
        ? embedding.legacy_chunk_count
        : null;
      const countText = legacyCount === null
        ? "Legacy memory chunks"
        : `${legacyCount.toLocaleString()} legacy memory ${legacyCount === 1 ? "chunk" : "chunks"}`;
      setStatusLabels("Memory reindex required");
      setManagedSummary(
        state.activeTaskId
          ? `${countText} need the active embedding space. Review the queued memory reindex task.`
          : `${countText} need the active embedding space. Queue a protected reindex, then review it in Tasks.`,
      );
      if (button) {
        button.hidden = false;
        button.disabled = state.queueing;
        button.textContent = state.queueing
          ? "Queueing reindex…"
          : state.activeTaskId
            ? "Review memory reindex"
            : "Queue memory reindex";
      }
      return;
    }

    state.activeTaskId = "";
    if (button) button.hidden = true;
    setManagedSummary("");
    if (parser && parser.healthy === false) setStatusLabels("Parser unavailable");
  }

  async function findActiveReindexTask() {
    const tasks = await request("/v1/tasks?limit=50");
    if (!Array.isArray(tasks)) return "";
    const task = tasks.find(
      (candidate) => candidate?.kind === "memory.reindex"
        && ACTIVE_REINDEX_STATUSES.has(candidate.status),
    );
    return typeof task?.id === "string" ? task.id : "";
  }

  async function refreshRecoveryState() {
    if (!apiToken() || !isSecureTransport()) {
      state.activeTaskId = "";
      state.readiness = null;
      updateRecoverySurface();
      return;
    }
    if (document.visibilityState === "hidden") return;
    try {
      state.readiness = await request("/v1/readyz");
      if (reindexRequired(state.readiness)) {
        state.activeTaskId = await findActiveReindexTask();
      } else {
        state.activeTaskId = "";
      }
      updateRecoverySurface();
    } catch {
      // The primary application owns authentication and generic connection errors.
    }
  }

  async function queueReindex() {
    if (state.queueing) return;
    state.queueing = true;
    updateRecoverySurface();
    try {
      const existingTaskId = await findActiveReindexTask();
      if (existingTaskId) {
        state.activeTaskId = existingTaskId;
        openTasks();
        return;
      }
      const task = await request("/v1/memory/reindex", {
        method: "POST",
        body: JSON.stringify({ batch_size: 32 }),
      });
      if (!task || task.kind !== "memory.reindex" || typeof task.id !== "string") {
        throw new Error("The server returned an invalid reindex task.");
      }
      state.activeTaskId = task.id;
      updateRecoverySurface();
      openTasks();
    } catch (error) {
      const summary = document.getElementById("memory-summary");
      if (summary instanceof HTMLElement) {
        summary.textContent = error instanceof Error
          ? error.message
          : "Could not queue the memory reindex task.";
        state.summaryText = summary.textContent;
      }
    } finally {
      state.queueing = false;
      updateRecoverySurface();
    }
  }

  function start() {
    recoveryButton();
    void refreshRecoveryState();
    window.setInterval(() => void refreshRecoveryState(), POLL_INTERVAL_MS);
    window.addEventListener("hashchange", () => void refreshRecoveryState());
    document.addEventListener("visibilitychange", () => void refreshRecoveryState());
    document.getElementById("refresh-status")?.addEventListener(
      "click",
      () => window.setTimeout(() => void refreshRecoveryState(), 0),
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
})();
