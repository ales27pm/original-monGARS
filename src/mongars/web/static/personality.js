(() => {
  "use strict";

  const TOKEN_KEY = "mongars.session.token";
  const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
  const state = { token: readToken(), profile: null };

  const dom = {
    actionMessage: document.querySelector("#action-message"),
    apiToken: document.querySelector("#api-token"),
    connectionMessage: document.querySelector("#connection-message"),
    connectionStatus: document.querySelector("#connection-status"),
    deleteProfile: document.querySelector("#delete-profile"),
    exportProfile: document.querySelector("#export-profile"),
    forgetToken: document.querySelector("#forget-token"),
    lifecycleList: document.querySelector("#lifecycle-list"),
    preferenceList: document.querySelector("#preference-list"),
    profileCount: document.querySelector("#profile-count"),
    profileDigest: document.querySelector("#profile-digest"),
    profileRevision: document.querySelector("#profile-revision"),
    profileSource: document.querySelector("#profile-source"),
    refreshProfile: document.querySelector("#refresh-profile"),
    resetProfile: document.querySelector("#reset-profile"),
    revisionList: document.querySelector("#revision-list"),
    taskId: document.querySelector("#task-id"),
    taskMessage: document.querySelector("#task-message"),
    taskResult: document.querySelector("#task-result"),
    tokenForm: document.querySelector("#token-form"),
    transportWarning: document.querySelector("#transport-warning"),
  };

  class ApiError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  function readToken() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch {
      return "";
    }
  }

  function writeToken(token) {
    try {
      if (token) window.sessionStorage.setItem(TOKEN_KEY, token);
      else window.sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // The in-memory credential remains available for this page lifecycle.
    }
  }

  function secureTransport() {
    return window.location.protocol === "https:" || LOCAL_HOSTS.has(window.location.hostname);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function humanize(value) {
    return String(value || "unknown").replaceAll("_", " ");
  }

  function formatDate(value) {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
  }

  function apiMessage(payload, fallback) {
    if (payload && typeof payload.detail === "string") return payload.detail;
    if (payload && typeof payload.message === "string") return payload.message;
    return fallback;
  }

  async function apiFetch(path, options = {}) {
    if (!state.token) throw new ApiError("Connect with the owner API token.", 401);
    if (!secureTransport()) throw new ApiError("Credential transport is blocked on this origin.");
    const headers = new Headers(options.headers || {});
    headers.set("Accept", "application/json");
    headers.set("Authorization", `Bearer ${state.token}`);
    if (typeof options.body === "string") headers.set("Content-Type", "application/json");

    let response;
    try {
      response = await fetch(path, { ...options, headers });
    } catch {
      throw new ApiError("Could not reach the monGARS API.");
    }
    let payload = null;
    if (response.status !== 204) {
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      if (response.status === 401) forgetToken(false);
      throw new ApiError(
        apiMessage(payload, `Request failed with HTTP ${response.status}.`),
        response.status,
      );
    }
    return payload;
  }

  function setConnected(connected) {
    dom.connectionStatus.textContent = connected ? "Connected" : "Disconnected";
    dom.connectionStatus.classList.toggle("is-connected", connected);
    dom.forgetToken.hidden = !connected;
  }

  function forgetToken(showMessage = true) {
    state.token = "";
    state.profile = null;
    writeToken("");
    setConnected(false);
    renderDisconnected();
    if (showMessage) dom.connectionMessage.textContent = "Token cleared from this tab.";
  }

  function renderDisconnected() {
    dom.profileRevision.textContent = "—";
    dom.profileSource.textContent = "—";
    dom.profileCount.textContent = "—";
    dom.profileDigest.textContent = "Connect to inspect the profile.";
    dom.preferenceList.replaceChildren();
    dom.revisionList.replaceChildren();
    dom.lifecycleList.replaceChildren();
    dom.taskResult.hidden = true;
  }

  function renderProfile(profile) {
    state.profile = profile;
    const preferences = Array.isArray(profile.preferences) ? profile.preferences : [];
    dom.profileRevision.textContent = String(profile.revision);
    dom.profileSource.textContent = humanize(profile.source);
    dom.profileCount.textContent = String(preferences.length);
    dom.profileDigest.textContent = profile.profile_digest || "Default profile has no persisted digest";
    dom.preferenceList.replaceChildren();
    if (!preferences.length) {
      dom.preferenceList.append(
        element("p", "empty", "No active style preferences. Cortex uses its neutral defaults."),
      );
      return;
    }
    for (const preference of preferences) {
      const card = element("article", "preference");
      const head = element("div", "preference-head");
      head.append(
        element("strong", "", humanize(preference.dimension)),
        element("span", "score", Number(preference.value).toFixed(2)),
      );
      const meter = element("div", "meter");
      const fill = element("span", "meter-fill");
      fill.style.width = `${Math.max(0, Math.min(100, Number(preference.value) * 100))}%`;
      meter.append(fill);
      card.append(
        head,
        meter,
        element(
          "small",
          "",
          `confidence ${Number(preference.confidence).toFixed(2)} · ${preference.evidence_count} evidence`,
        ),
      );
      dom.preferenceList.append(card);
    }
  }

  function renderRevisions(revisions) {
    dom.revisionList.replaceChildren();
    if (!revisions.length) {
      dom.revisionList.append(element("p", "empty", "No applied preference revisions."));
      return;
    }
    for (const revision of revisions) {
      const row = element("article", "history-row");
      row.append(
        element("strong", "", `Revision ${revision.profile.revision}`),
        element(
          "span",
          "",
          `${humanize(revision.changed_dimension)} · ${formatDate(revision.created_at)}`,
        ),
      );
      dom.revisionList.append(row);
    }
  }

  function renderLifecycle(events) {
    dom.lifecycleList.replaceChildren();
    if (!events.length) {
      dom.lifecycleList.append(element("p", "empty", "No reset or deletion receipts."));
      return;
    }
    for (const event of events) {
      const row = element("article", "history-row");
      row.append(
        element("strong", "", humanize(event.operation)),
        element(
          "span",
          "",
          `revision ${event.expected_revision} → ${event.target_revision} · ${formatDate(event.created_at)}`,
        ),
      );
      dom.lifecycleList.append(row);
    }
  }

  async function loadProfile() {
    dom.actionMessage.textContent = "Loading personality data…";
    const [profile, revisions, lifecycle] = await Promise.all([
      apiFetch("/v1/adaptation/profile"),
      apiFetch("/v1/adaptation/profile/revisions?limit=100"),
      apiFetch("/v1/adaptation/profile/lifecycle?limit=100"),
    ]);
    renderProfile(profile);
    renderRevisions(Array.isArray(revisions) ? revisions : []);
    renderLifecycle(Array.isArray(lifecycle) ? lifecycle : []);
    dom.actionMessage.textContent = "Profile data is current.";
    setConnected(true);
  }

  async function exportProfile() {
    dom.actionMessage.textContent = "Preparing private export…";
    const payload = await apiFetch("/v1/adaptation/profile/export");
    const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    const revision = Number(payload?.profile?.revision || 0);
    link.href = url;
    link.download = `mongars-personality-r${revision}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    dom.actionMessage.textContent = "Private personality export created.";
  }

  function showTask(task, action) {
    dom.taskMessage.textContent = `${humanize(action)} is waiting for exact-payload approval.`;
    dom.taskId.textContent = task.id;
    dom.taskResult.hidden = false;
    dom.actionMessage.textContent = "No profile data changed yet.";
  }

  async function requestAction(action) {
    if (action === "delete") {
      const confirmed = window.confirm(
        "Prepare deletion of profile, feedback, history, and stored personality task payloads? " +
          "Execution still requires approval in Tasks.",
      );
      if (!confirmed) return;
    }
    dom.actionMessage.textContent = `Creating protected ${action} task…`;
    const task = await apiFetch(`/v1/adaptation/profile/${action}`, { method: "POST" });
    showTask(task, action);
  }

  function bind() {
    dom.tokenForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!secureTransport()) return;
      const token = dom.apiToken.value.trim();
      if (!token) return;
      state.token = token;
      try {
        await loadProfile();
        writeToken(token);
        dom.apiToken.value = "";
        dom.connectionMessage.textContent = "Connected securely.";
      } catch (error) {
        state.token = "";
        setConnected(false);
        dom.connectionMessage.textContent = error instanceof Error ? error.message : "Connection failed.";
      }
    });
    dom.forgetToken.addEventListener("click", () => forgetToken());
    dom.refreshProfile.addEventListener("click", () => {
      loadProfile().catch((error) => {
        dom.actionMessage.textContent = error instanceof Error ? error.message : "Refresh failed.";
      });
    });
    dom.exportProfile.addEventListener("click", () => {
      exportProfile().catch((error) => {
        dom.actionMessage.textContent = error instanceof Error ? error.message : "Export failed.";
      });
    });
    dom.resetProfile.addEventListener("click", () => {
      requestAction("reset").catch((error) => {
        dom.actionMessage.textContent = error instanceof Error ? error.message : "Reset request failed.";
      });
    });
    dom.deleteProfile.addEventListener("click", () => {
      requestAction("delete").catch((error) => {
        dom.actionMessage.textContent = error instanceof Error ? error.message : "Deletion request failed.";
      });
    });
  }

  async function initialize() {
    const secure = secureTransport();
    dom.transportWarning.hidden = secure;
    dom.apiToken.disabled = !secure;
    dom.tokenForm.querySelector("button[type='submit']").disabled = !secure;
    bind();
    if (!state.token || !secure) {
      if (!secure) forgetToken(false);
      renderDisconnected();
      return;
    }
    try {
      await loadProfile();
      setConnected(true);
    } catch (error) {
      forgetToken(false);
      dom.connectionMessage.textContent = error instanceof Error ? error.message : "Reconnect required.";
    }
  }

  initialize();
})();
