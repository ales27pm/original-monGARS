(() => {
  "use strict";

  const TOKEN_KEY = "mongars.session.token";
  const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
  const TASK_POLL_MS = 8_000;
  const MAX_DOCUMENT_BYTES = 10_000_000;
  const DOCUMENT_MIME_TYPES = Object.freeze({
    txt: "text/plain",
    md: "text/markdown",
    markdown: "text/markdown",
    html: "text/html",
    htm: "text/html",
    pdf: "application/pdf",
    docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  });
  const GENERIC_DOCUMENT_MIME_TYPES = new Set(["", "application/octet-stream"]);
  const FORMAT_CONTROL_CHARACTERS = /\p{Cf}/u;
  const TASK_GROUPS = [
    { key: "waiting_approval", label: "Needs approval" },
    { key: "queued", label: "Queued" },
    { key: "running", label: "Running" },
    { key: "done", label: "Completed" },
    { key: "failed", label: "Failed" },
    { key: "cancelled", label: "Cancelled" },
  ];

  const state = {
    token: readSessionToken(),
    sessionId: null,
    lastChatTraceId: null,
    tasks: [],
    taskReviews: new Map(),
    taskFilter: "all",
    currentView: "chat",
    taskPoll: null,
    uploadedTaskId: null,
  };

  const dom = {
    authButton: document.querySelector("#auth-button"),
    authClose: document.querySelector("#auth-close"),
    authDialog: document.querySelector("#auth-dialog"),
    authError: document.querySelector("#auth-error"),
    authForm: document.querySelector("#auth-form"),
    apiToken: document.querySelector("#api-token"),
    connectButton: document.querySelector("#connect-button"),
    databaseStatus: document.querySelector("#database-status"),
    disconnectButton: document.querySelector("#disconnect-button"),
    documentClose: document.querySelector("#document-close"),
    documentDialog: document.querySelector("#document-dialog"),
    documentError: document.querySelector("#document-error"),
    documentFile: document.querySelector("#document-file"),
    documentFileSummary: document.querySelector("#document-file-summary"),
    documentForm: document.querySelector("#document-form"),
    documentRetention: document.querySelector("#document-retention"),
    documentSensitivity: document.querySelector("#document-sensitivity"),
    documentSubmit: document.querySelector("#document-submit"),
    documentTaskLink: document.querySelector("#document-task-link"),
    documentTitle: document.querySelector("#document-title-input"),
    documentUploadResult: document.querySelector("#document-upload-result"),
    documentUploadState: document.querySelector("#document-upload-state"),
    documentUploadStatus: document.querySelector("#document-upload-status"),
    documentUploadTaskId: document.querySelector("#document-upload-task-id"),
    emptyChat: document.querySelector("#empty-chat"),
    evolutionStatus: document.querySelector("#evolution-status"),
    executorStatus: document.querySelector("#executor-status"),
    globalStatusDot: document.querySelector("#global-status-dot"),
    globalStatusLabel: document.querySelector("#global-status-label"),
    governanceStatus: document.querySelector("#governance-status"),
    inferenceStatus: document.querySelector("#inference-status"),
    insecureWarning: document.querySelector("#insecure-warning"),
    localOnly: document.querySelector("#local-only"),
    memoryMode: document.querySelector("#memory-mode"),
    memoryQuery: document.querySelector("#memory-query"),
    memoryResults: document.querySelector("#memory-results"),
    memorySearchForm: document.querySelector("#memory-search-form"),
    memorySummary: document.querySelector("#memory-summary"),
    mobileTaskCount: document.querySelector("#mobile-task-count"),
    noteClose: document.querySelector("#note-close"),
    noteDialog: document.querySelector("#note-dialog"),
    noteError: document.querySelector("#note-error"),
    noteForm: document.querySelector("#note-form"),
    noteRetention: document.querySelector("#note-retention"),
    noteSensitivity: document.querySelector("#note-sensitivity"),
    noteText: document.querySelector("#note-text"),
    noteTitle: document.querySelector("#note-title-input"),
    openDocument: document.querySelector("#open-document"),
    openNote: document.querySelector("#open-note"),
    refreshStatus: document.querySelector("#refresh-status"),
    refreshTasks: document.querySelector("#refresh-tasks"),
    sidebarStatusDot: document.querySelector("#sidebar-status-dot"),
    sidebarStatusLabel: document.querySelector("#sidebar-status-label"),
    statusCallout: document.querySelector("#status-callout"),
    statusButton: document.querySelector("#status-button"),
    taskCount: document.querySelector("#task-count"),
    taskList: document.querySelector("#task-list"),
    taskStatus: document.querySelector("#tasks-status"),
    tasksEmptyActions: document.querySelector("#tasks-empty-actions"),
    tasksEmptyOpenDocument: document.querySelector("#tasks-empty-open-document"),
    tasksEmptyOpenNote: document.querySelector("#tasks-empty-open-note"),
    chatForm: document.querySelector("#chat-form"),
    chatMessage: document.querySelector("#chat-message"),
    chatSend: document.querySelector("#chat-send"),
    chatStream: document.querySelector("#chat-stream"),
    chatFeedback: document.querySelector("#chat-action-feedback"),
    chatThread: document.querySelector("#chat-thread"),
    newChat: document.querySelector("#new-chat"),
    personalityCurrent: document.querySelector("#personality-current"),
    personalityRevisions: document.querySelector("#personality-revisions"),
    personalitySummary: document.querySelector("#personality-summary"),
    refreshPersonality: document.querySelector("#refresh-personality"),
    resetPersonality: document.querySelector("#reset-personality"),
    deletePersonality: document.querySelector("#delete-personality"),
    adaptationFeedbackForm: document.querySelector("#adaptation-feedback-form"),
    adaptationFeedbackKind: document.querySelector("#adaptation-feedback-kind"),
    adaptationFeedbackId: document.querySelector("#adaptation-feedback-id"),
    adaptationResponseTraceId: document.querySelector("#adaptation-response-trace-id"),
    adaptationFeedbackSummary: document.querySelector("#adaptation-feedback-summary"),
    adaptationFeedbackResult: document.querySelector("#adaptation-feedback-result"),
    adaptationFeedbackTaskNote: document.querySelector("#adaptation-feedback-task-note"),
    adaptationCorrectionBlock: document.querySelector("#adaptation-correction-block"),
    adaptationCorrectionText: document.querySelector("#adaptation-correction-text"),
    adaptationHelpfulBlock: document.querySelector("#adaptation-helpful-block"),
    adaptationHelpful: document.querySelector("#adaptation-helpful"),
    adaptationDimensionLabel: document.querySelector("#adaptation-dimension-label"),
    adaptationDimension: document.querySelector("#adaptation-dimension"),
    adaptationValueLabel: document.querySelector("#adaptation-desired-value-label"),
    adaptationDesiredValue: document.querySelector("#adaptation-desired-value"),
    p2pSummary: document.querySelector("#p2p-summary"),
    refreshP2p: document.querySelector("#refresh-p2p"),
    p2pStatus: document.querySelector("#p2p-status"),
    p2pPairForm: document.querySelector("#p2p-pair-form"),
    p2pPairPeerId: document.querySelector("#p2p-pair-peer-id"),
    p2pPairKeyId: document.querySelector("#p2p-pair-key-id"),
    p2pPairSecret: document.querySelector("#p2p-pair-secret"),
    p2pPairResult: document.querySelector("#p2p-pair-result"),
    p2pExportForm: document.querySelector("#p2p-export-form"),
    p2pExportEnvelopeId: document.querySelector("#p2p-export-envelope-id"),
    p2pExportSenderPeerId: document.querySelector("#p2p-export-sender-peer-id"),
    p2pExportRecipientPeerId: document.querySelector("#p2p-export-recipient-peer-id"),
    p2pExportSenderKeyId: document.querySelector("#p2p-export-sender-key-id"),
    p2pExportIssuedAt: document.querySelector("#p2p-export-issued-at"),
    p2pExportExpiresAt: document.querySelector("#p2p-export-expires-at"),
    p2pExportNonce: document.querySelector("#p2p-export-nonce"),
    p2pExportSchemaVersion: document.querySelector("#p2p-export-schema-version"),
    p2pExportSensitivity: document.querySelector("#p2p-export-sensitivity"),
    p2pExportRetention: document.querySelector("#p2p-export-retention"),
    p2pExportTrust: document.querySelector("#p2p-export-trust"),
    p2pExportSourceTime: document.querySelector("#p2p-export-source-time"),
    p2pExportSecret: document.querySelector("#p2p-export-secret"),
    p2pExportPayload: document.querySelector("#p2p-export-payload"),
    p2pExportResult: document.querySelector("#p2p-export-result"),
    p2pImportForm: document.querySelector("#p2p-import-form"),
    p2pImportEnvelopeId: document.querySelector("#p2p-import-envelope-id"),
    p2pImportSenderPeerId: document.querySelector("#p2p-import-sender-peer-id"),
    p2pImportRecipientPeerId: document.querySelector("#p2p-import-recipient-peer-id"),
    p2pImportOwnerId: document.querySelector("#p2p-import-owner-id"),
    p2pImportSenderKeyId: document.querySelector("#p2p-import-sender-key-id"),
    p2pImportIssuedAt: document.querySelector("#p2p-import-issued-at"),
    p2pImportExpiresAt: document.querySelector("#p2p-import-expires-at"),
    p2pImportNonce: document.querySelector("#p2p-import-nonce"),
    p2pImportSchemaVersion: document.querySelector("#p2p-import-schema-version"),
    p2pImportSensitivity: document.querySelector("#p2p-import-sensitivity"),
    p2pImportRetention: document.querySelector("#p2p-import-retention"),
    p2pImportTrust: document.querySelector("#p2p-import-trust"),
    p2pImportSourceTime: document.querySelector("#p2p-import-source-time"),
    p2pImportSignature: document.querySelector("#p2p-import-signature"),
    p2pImportPayload: document.querySelector("#p2p-import-payload"),
    p2pImportResult: document.querySelector("#p2p-import-result"),
    p2pImportResultJson: document.querySelector("#p2p-import-result-json"),
    toastRegion: document.querySelector("#toast-region"),
    toggleToken: document.querySelector("#toggle-token"),
    webSearchMode: document.querySelector("#web-search-mode"),
  };

  class ApiError extends Error {
    constructor(message, status = 0) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }

  function readSessionToken() {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY) || "";
    } catch {
      return "";
    }
  }

  function writeSessionToken(token) {
    try {
      if (token) {
        window.sessionStorage.setItem(TOKEN_KEY, token);
      } else {
        window.sessionStorage.removeItem(TOKEN_KEY);
      }
    } catch {
      // The in-memory copy still works if browser storage is unavailable.
    }
  }

  function isSecureTransport() {
    return window.location.protocol === "https:" || LOCAL_HOSTS.has(window.location.hostname);
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function setHidden(node, hidden) {
    node.hidden = hidden;
  }

  function setTasksStatus(message, tone = "default") {
    if (!dom.taskStatus) return;
    if (!message) {
      dom.taskStatus.hidden = true;
      dom.taskStatus.textContent = "";
      dom.taskStatus.className = "section-status";
      return;
    }
    dom.taskStatus.hidden = false;
    dom.taskStatus.textContent = message;
    dom.taskStatus.className = `section-status is-${tone}`;
  }

  function setSectionStatus(node, message, tone = "default") {
    if (!node) return;
    if (!message) {
      node.hidden = true;
      node.textContent = "";
      node.className = "section-status";
      return;
    }
    node.hidden = false;
    node.textContent = message;
    node.className = `section-status is-${tone}`;
  }

  function updateAdaptationResponseTraceSuggestion(traceId) {
    if (!dom.adaptationResponseTraceId || dom.adaptationResponseTraceId.value.trim()) return;
    dom.adaptationResponseTraceId.value = traceId || "";
  }

  function setChatActionStatus(message, tone = "default") {
    if (!dom.chatFeedback) return;
    if (!message) {
      dom.chatFeedback.hidden = true;
      dom.chatFeedback.textContent = "";
      dom.chatFeedback.className = "chat-action-feedback";
      return;
    }
    dom.chatFeedback.hidden = false;
    dom.chatFeedback.textContent = message;
    dom.chatFeedback.className = `chat-action-feedback is-${tone}`;
  }

  function formatJson(value) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value || "");
    }
  }

  function requiredTrimmedValue(node, label) {
    const value = typeof node?.value === "string" ? node.value.trim() : "";
    if (!value) {
      throw new ApiError(`${label} is required.`);
    }
    return value;
  }

  function parseISODate(value, label) {
    const normalized = value.trim();
    if (!normalized) throw new ApiError(`${label} is required.`);
    const parsed = Date.parse(normalized);
    if (Number.isNaN(parsed)) throw new ApiError(`${label} must be an ISO date-time.`);
    return normalized;
  }

  function parsePayloadJson(text, label) {
    const trimmed = text.trim();
    if (!trimmed) throw new ApiError(`${label} is required.`);
    let parsed;
    try {
      parsed = JSON.parse(trimmed);
    } catch {
      throw new ApiError(`${label} must be valid JSON.`);
    }
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new ApiError(`${label} must be a JSON object.`);
    }
    return parsed;
  }

  function showDialog(dialog) {
    if (dialog.open) return;
    if (typeof dialog.showModal === "function") {
      dialog.showModal();
    } else {
      dialog.setAttribute("open", "");
    }
  }

  function closeDialog(dialog) {
    if (!dialog.open) return;
    if (typeof dialog.close === "function") {
      dialog.close();
    } else {
      dialog.removeAttribute("open");
    }
  }

  function humanize(value) {
    return String(value || "unknown").replaceAll("_", " ");
  }

  function dependencyLabel(dependency, readyLabel = "Ready") {
    if (!dependency) return "Unknown";
    if (dependency.healthy === true) return readyLabel;
    if (dependency.reason) return humanize(dependency.reason);
    if (dependency.error_code) return humanize(dependency.error_code);
    return "Blocked";
  }

  function setProtectedReadinessLabels(label) {
    dom.databaseStatus.textContent = label;
    dom.inferenceStatus.textContent = label;
    dom.evolutionStatus.textContent = label;
    dom.governanceStatus.textContent = label;
    dom.executorStatus.textContent = label;
  }

  function readinessSidebarSummary(payload, responseOk) {
    const dependencies = payload?.dependencies || {};
    const database = dependencies.database;
    const inference = dependencies.inference;
    const scheduler = dependencies.evolution_scheduler;
    const governance = dependencies.model_governance;
    const executor = dependencies.executor_security;
    const databaseConnected = database?.healthy;
    const inferenceReady = inference?.healthy;
    const inferenceLabel = inferenceReady
      ? `${humanize(inference.backend)} ready`
      : inference?.backend_reachable
        ? "Models missing"
        : "Unavailable";

    return {
      ready: responseOk && payload.status === "ready",
      labels: {
        database: databaseConnected ? "Connected" : "Unavailable",
        inference: inferenceLabel,
        evolution: scheduler
          ? scheduler.enabled
            ? dependencyLabel(
                scheduler,
                scheduler.can_run ? "Can run" : humanize(scheduler.status),
              )
            : "Disabled"
          : "Unknown",
        governance: governance
          ? governance.enabled
            ? dependencyLabel(
                governance,
                governance.candidate_registry?.active_alias || humanize(governance.status),
              )
            : "Disabled"
          : "Unknown",
        executor: executor
          ? executor.enabled
            ? dependencyLabel(executor, "Reviewed")
            : "Restricted"
          : "Unknown",
      },
      calloutPieces: [
        databaseConnected ? "Database connected" : "Database unavailable",
        inferenceReady ? `${humanize(inference.backend)} is ready` : "Inference backend unavailable",
        scheduler ? `Evolution ${humanize(scheduler.status)}` : "Evolution unknown",
        governance ? `Model ${humanize(governance.status)}` : "Model governance unknown",
        executor ? `Executor ${humanize(executor.status)}` : "Executor security unknown",
      ],
    };
  }

  function applyReadinessSidebarSummary(summary) {
    dom.databaseStatus.textContent = summary.labels.database;
    dom.inferenceStatus.textContent = summary.labels.inference;
    dom.evolutionStatus.textContent = summary.labels.evolution;
    dom.governanceStatus.textContent = summary.labels.governance;
    dom.executorStatus.textContent = summary.labels.executor;
  }

  function formatDate(value) {
    if (!value) return "—";
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    }).format(parsed);
  }

  function apiMessage(payload, fallback) {
    if (!payload) return fallback;
    if (typeof payload.detail === "string") return payload.detail;
    if (payload.detail && typeof payload.detail.message === "string") {
      return payload.detail.message;
    }
    if (payload.detail && typeof payload.detail.code === "string") {
      return humanize(payload.detail.code);
    }
    if (typeof payload.message === "string") return payload.message;
    return fallback;
  }

  async function apiFetch(path, options = {}) {
    const { authenticated = true, ...requestOptions } = options;
    if (authenticated && !state.token) {
      openAuth();
      throw new ApiError("Connect with your API token to continue.", 401);
    }

    const headers = new Headers(requestOptions.headers || {});
    headers.set("Accept", "application/json");
    if (typeof requestOptions.body === "string") {
      headers.set("Content-Type", "application/json");
    }
    if (authenticated) headers.set("Authorization", `Bearer ${state.token}`);

    let response;
    try {
      response = await fetch(path, { ...requestOptions, headers });
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

    if (response.status === 401 && authenticated) {
      forgetToken({ notify: false });
      openAuth("The token was rejected. Check it and reconnect.");
    }
    if (!response.ok) {
      throw new ApiError(
        apiMessage(payload, `Request failed with status ${response.status}.`),
        response.status,
      );
    }
    return payload;
  }

  function toast(message, tone = "info") {
    const node = element("div", `toast is-${tone}`, message);
    dom.toastRegion.append(node);
    window.setTimeout(() => node.remove(), 4_500);
  }

  function setReadinessStatus(kind, label, detail = "") {
    for (const dot of [dom.globalStatusDot, dom.sidebarStatusDot]) {
      dot.classList.remove("is-ready", "is-down");
      if (kind === "ready") dot.classList.add("is-ready");
      if (kind === "down") dot.classList.add("is-down");
    }
    dom.globalStatusLabel.textContent = label;
    dom.sidebarStatusLabel.textContent = label;
    if (dom.statusCallout) {
      dom.statusCallout.textContent = detail;
      dom.statusCallout.hidden = !detail;
    }
  }

  async function refreshReadiness({ announce = false } = {}) {
    if (!state.token || !isSecureTransport()) {
      setProtectedReadinessLabels("Protected");
      setReadinessStatus(
        "checking",
        "Connect to inspect",
        "Add a token to unlock API and system status.",
      );
      if (announce) openAuth("Connect with your API token to inspect readiness.");
      return;
    }

    setReadinessStatus("checking", "Checking", "Verifying service status...");
    try {
      const response = await fetch("/v1/readyz", {
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${state.token}`,
        },
      });
      const payload = await response.json();
      if (response.status === 401) {
        forgetToken({ notify: false });
        openAuth("The token was rejected. Reconnect to inspect readiness.");
        return;
      }
      if (!response.ok && response.status !== 503) {
        throw new ApiError(
          apiMessage(payload, `Readiness failed with status ${response.status}.`),
          response.status,
        );
      }
      const summary = readinessSidebarSummary(payload, response.ok);
      applyReadinessSidebarSummary(summary);
      setReadinessStatus(
        summary.ready ? "ready" : "down",
        summary.ready ? "System ready" : "Needs attention",
        summary.ready
          ? summary.calloutPieces.join(" · ")
          : `${summary.calloutPieces.join(" · ")} · Refresh when ready to retry.`,
      );
      if (announce) toast(summary.ready ? "All required services are ready." : "One or more services need attention.", summary.ready ? "success" : "error");
    } catch {
      setProtectedReadinessLabels("Unavailable");
      setReadinessStatus("down", "Offline", "The monGARS API is not reachable right now.");
      if (announce) toast("The monGARS API is not reachable.", "error");
    }
  }

  function configureTransport() {
    const insecure = !isSecureTransport();
    setHidden(dom.insecureWarning, !insecure);
    dom.apiToken.disabled = insecure;
    dom.connectButton.disabled = insecure;
    if (insecure) {
      dom.apiToken.placeholder = "Unavailable over plaintext HTTP";
      if (dom.statusCallout) {
        dom.statusCallout.textContent = "HTTPS is required to store your token securely.";
        dom.statusCallout.hidden = false;
      }
    } else if (dom.statusCallout) {
      dom.statusCallout.hidden = true;
    }
    return !insecure;
  }

  function openAuth(errorMessage = "") {
    configureTransport();
    dom.authError.textContent = errorMessage;
    setHidden(dom.authError, !errorMessage);
    dom.disconnectButton.hidden = !state.token;
    dom.apiToken.value = "";
    dom.apiToken.type = "password";
    dom.toggleToken.textContent = "Show";
    dom.toggleToken.setAttribute("aria-label", "Show token");
    showDialog(dom.authDialog);
    if (isSecureTransport()) window.setTimeout(() => dom.apiToken.focus(), 50);
  }

  function forgetToken({ notify = true } = {}) {
    state.token = "";
    state.sessionId = null;
    state.tasks = [];
    state.taskReviews.clear();
    state.uploadedTaskId = null;
    writeSessionToken("");
    stopTaskPolling();
    renderTaskCount();
    renderTasks();
    if (dom.personalityCurrent) {
      dom.personalityCurrent.textContent = "Sign in to load profile.";
    }
    if (dom.personalityRevisions) {
      dom.personalityRevisions.replaceChildren(
        element("div", "empty-state", "Sign in to load personality revisions."),
      );
    }
    setSectionStatus(dom.personalitySummary, "Connect to load personality.", "muted");
    if (dom.p2pStatus) {
      dom.p2pStatus.textContent = "Sign in to load status.";
    }
    setSectionStatus(dom.p2pSummary, "Connect to load P2P status.", "muted");
    dom.authButton.setAttribute("aria-label", "Connect with API token");
    dom.authButton.removeAttribute("data-connected");
    if (notify) toast("The token was cleared from this tab.", "success");
    setTasksStatus("Connect to view tasks and protected actions.", "muted");
  }

  async function connectWithToken(token) {
    state.token = token;
    try {
      await apiFetch("/v1/tasks?limit=1");
    } catch (error) {
      state.token = "";
      if (error instanceof ApiError && error.status === 401) {
        throw new ApiError("That token was not accepted. Try again.", 401);
      }
      throw error;
    }
    writeSessionToken(token);
    dom.authButton.dataset.connected = "true";
    dom.authButton.setAttribute("aria-label", "Connected; manage API token");
    closeDialog(dom.authDialog);
    toast("Connected securely to monGARS.", "success");
    setReadinessStatus("checking", "Checking", "Connected. Verifying services...");
    await refreshReadiness();
    await refreshTasks({ silent: true });
    await refreshAdaptation({ silent: true }).catch(() => {
      setSectionStatus(dom.personalitySummary, "Could not load personality profile yet.", "error");
    });
    await refreshP2pStatus({ silent: true }).catch(() => {
      setSectionStatus(dom.p2pSummary, "Could not load P2P status yet.", "error");
    });
    refreshAdaptationFeedbackFormVisibility();
    updateAdaptationSliderLabel();
    startTaskPolling();
  }

  function selectView(view, { updateHash = true } = {}) {
    const target = ["chat", "memory", "tasks", "adaptation", "p2p"].includes(view) ? view : "chat";
    state.currentView = target;
    document.querySelectorAll("[data-view]").forEach((section) => {
      const active = section.dataset.view === target;
      section.hidden = !active;
      section.classList.toggle("is-active", active);
    });
    document.querySelectorAll("[data-view-link]").forEach((button) => {
      const active = button.dataset.viewLink === target;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (updateHash) window.history.replaceState(null, "", `#${target}`);
    if (target === "tasks" && state.token) refreshTasks({ silent: true });
    if (target === "adaptation" && state.token) void refreshAdaptation();
    if (target === "p2p" && state.token) void refreshP2pStatus({ silent: false });
    if (target === "memory" && window.matchMedia("(pointer: fine)").matches) {
      window.setTimeout(() => dom.memoryQuery.focus(), 40);
    }
  }

  function resizeComposer() {
    const approximateColumns = window.innerWidth <= 720 ? 34 : 72;
    const visualLines = dom.chatMessage.value.split("\n").reduce(
      (count, line) => count + Math.max(1, Math.ceil(line.length / approximateColumns)),
      0,
    );
    dom.chatMessage.rows = Math.min(7, Math.max(1, visualLines));
  }

  function resetChat() {
    state.sessionId = null;
    dom.chatThread.replaceChildren(dom.emptyChat);
    dom.emptyChat.hidden = false;
    dom.chatMessage.value = "";
    resizeComposer();
    dom.chatMessage.focus();
    toast("Started a new local conversation.");
  }

  function safeExternalUrl(value) {
    try {
      const parsed = new URL(value);
      return ["http:", "https:"].includes(parsed.protocol) && parsed.hostname
        ? { href: parsed.href, hostname: parsed.hostname }
        : null;
    } catch {
      return null;
    }
  }

  function addMessage(role, text, meta = "", sources = []) {
    dom.emptyChat.hidden = true;
    const article = element("article", `message is-${role}`);
    const avatar = element("div", "message-avatar", role === "user" ? "You" : "M");
    avatar.setAttribute("aria-hidden", "true");
    const content = element("div", "message-content");
    const head = element("div", "message-head");
    head.append(element("strong", "", role === "user" ? "You" : "monGARS"));
    if (meta) head.append(element("span", "message-meta", meta));
    content.append(head, element("div", "message-body", text));
    const sourceLinks = sources
      .map((source) => ({ source, target: safeExternalUrl(source?.url) }))
      .filter(({ source, target }) => target && typeof source?.title === "string");
    if (sourceLinks.length) {
      const sourceList = element("div", "message-sources");
      sourceLinks.forEach(({ source, target }) => {
        const title = source.title.trim();
        const label = title && title !== target.hostname
          ? `${target.hostname} · ${title}`
          : target.hostname;
        const link = element("a", "message-source", label);
        link.href = target.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        sourceList.append(link);
      });
      content.append(sourceList);
    }
    article.append(avatar, content);
    dom.chatThread.append(article);
    dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
    return article;
  }

  function setMessageMeta(messageElement, meta) {
    const head = messageElement.querySelector(".message-head");
    if (!head) return;
    let status = head.querySelector(".message-meta");
    if (!meta) {
      if (status) status.remove();
      return;
    }
    if (status) {
      status.textContent = meta;
      return;
    }
    head.append(element("span", "message-meta", meta));
  }

  function setMessageSources(messageElement, sources) {
    if (!messageElement) return;
    const content = messageElement.querySelector(".message-content");
    if (!content) return;
    const existing = messageElement.querySelector(".message-sources");
    if (existing) existing.remove();
    const sourceLinks = Array.isArray(sources)
      ? sources
        .map((source) => ({ source, target: safeExternalUrl(source?.url) }))
        .filter(({ source, target }) => target && typeof source?.title === "string")
      : [];
    if (!sourceLinks.length) return;
    const sourceList = element("div", "message-sources");
    sourceLinks.forEach(({ source, target }) => {
      const title = source.title.trim();
      const label = title && title !== target.hostname
        ? `${target.hostname} · ${title}`
        : target.hostname;
      const link = element("a", "message-source", label);
      link.href = target.href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      sourceList.append(link);
    });
    content.append(sourceList);
  }

  function addTypingMessage() {
    const article = addMessage("assistant", "");
    article.dataset.typing = "true";
    const body = article.querySelector(".message-body");
    body.setAttribute("aria-label", "Cortex is thinking");
    const dots = element("span", "typing-dots");
    dots.append(element("span"), element("span"), element("span"));
    body.append(dots);
    return article;
  }

  async function sendChat(message) {
    if (!state.token) {
      openAuth();
      return;
    }
    const submitButton = dom.chatSend || dom.chatForm.querySelector("button[type='submit']");
    addMessage("user", message);
    const typing = addTypingMessage();
    submitButton.disabled = true;
    dom.chatMessage.disabled = true;
    submitButton.textContent = "Sending…";
    setChatActionStatus("Sending message to Cortex.", "loading");
    const requestBody = {
      session_id: state.sessionId,
      message,
      require_local_only: dom.localOnly.checked,
      web_search: dom.webSearchMode.value,
    };
    try {
      if (dom.chatStream && dom.chatStream.checked) {
        await sendChatStream(requestBody, typing);
      } else {
        const payload = await apiFetch("/v1/chat", {
          method: "POST",
          body: JSON.stringify(requestBody),
        });
        state.sessionId = payload.session_id;
        state.lastChatTraceId = payload.trace_id;
        updateAdaptationResponseTraceSuggestion(payload.trace_id);
        typing.remove();
        const memoryLabel = `${payload.memory_hits} ${payload.memory_hits === 1 ? "memory" : "memories"}`;
        addMessage(
          "assistant",
          payload.answer,
          `${payload.model} · ${memoryLabel}`,
          Array.isArray(payload.sources) ? payload.sources : [],
        );
        setChatActionStatus("Response received.", "success");
      }
    } catch (error) {
      typing.remove();
      addMessage("assistant", error instanceof Error ? error.message : "The request failed.", "Request error");
    } finally {
      submitButton.disabled = false;
      dom.chatMessage.disabled = false;
      submitButton.textContent = "↑";
      setTimeout(() => setChatActionStatus(""), 900);
      dom.chatMessage.focus();
    }
  }

  async function sendChatStream(requestBody, typing) {
    const headers = new Headers();
    headers.set("Accept", "application/x-ndjson");
    headers.set("Content-Type", "application/json");
    headers.set("Authorization", `Bearer ${state.token}`);

    let response;
    try {
      response = await fetch("/v1/chat/stream", {
        method: "POST",
        body: JSON.stringify(requestBody),
        headers,
      });
    } catch {
      throw new ApiError("Could not reach the monGARS API.");
    }

    if (response.status === 401) {
      forgetToken({ notify: false });
      openAuth("The token was rejected. Check it and reconnect.");
      throw new ApiError("Unauthorized.", 401);
    }

    let payload = null;
    if (response.status !== 204) {
      try {
        payload = await response.clone().json();
      } catch {
        payload = null;
      }
    }
    if (!response.ok) {
      throw new ApiError(
        apiMessage(payload, `Request failed with status ${response.status}.`),
        response.status,
      );
    }

    if (!response.body) {
      throw new ApiError("Streaming response body is missing.");
    }

    typing.remove();
    const assistant = addMessage("assistant", "");
    setMessageMeta(assistant, "Streaming response…");
    const bodyNode = assistant.querySelector(".message-body");
    bodyNode.setAttribute("aria-label", "Streaming chat response");
    bodyNode.textContent = "";
    const decoder = new TextDecoder();
    const reader = response.body.getReader();
    let buffer = "";
    let answer = "";
    let finalPayload = null;
    let sources = [];
    let memoryHits = 0;
    let model = "";
    try {
      while (true) {
        const frame = await reader.read();
        if (frame.done) break;
        buffer += decoder.decode(frame.value, { stream: true });
        while (buffer.includes("\n")) {
          const lineBreak = buffer.indexOf("\n");
          const raw = buffer.slice(0, lineBreak);
          buffer = buffer.slice(lineBreak + 1);
          if (!raw.trim()) continue;
          processChatFrame(raw, {
            onDelta(text) {
              answer += text;
              bodyNode.textContent = answer;
              dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
            },
            onFinal(data) {
              finalPayload = data;
              const finalText = typeof data.answer === "string" ? data.answer : "";
              if (finalText) {
                answer = finalText;
                bodyNode.textContent = finalText;
              }
              model = data.model || model;
              sources = data.sources || sources;
              memoryHits = Number.isFinite(data.memory_hits) ? data.memory_hits : memoryHits;
            },
            onSources(value) {
              sources = value || sources;
            },
          });
        }
      }
      if (buffer.trim()) {
        processChatFrame(buffer, {
          onDelta(text) {
            answer += text;
            bodyNode.textContent = answer;
          },
          onFinal(data) {
            finalPayload = data;
            const finalText = typeof data.answer === "string" ? data.answer : "";
            if (finalText) {
              answer = finalText;
              bodyNode.textContent = finalText;
            }
            model = data.model || model;
            sources = data.sources || sources;
            memoryHits = Number.isFinite(data.memory_hits) ? data.memory_hits : memoryHits;
          },
          onSources(value) {
            sources = value || sources;
          },
        });
      }
    } finally {
      reader.releaseLock();
    }

    if (!finalPayload || finalPayload.type !== "final") {
      throw new ApiError("The stream ended without a final answer.");
    }

    state.sessionId = finalPayload.session_id;
    state.lastChatTraceId = finalPayload.trace_id;
    updateAdaptationResponseTraceSuggestion(finalPayload.trace_id);
    const memoryLabel = `${memoryHits} ${memoryHits === 1 ? "memory" : "memories"}`;
    setMessageMeta(
      assistant,
      `${model || "Model"} · ${memoryLabel}${finalPayload.trace_id ? ` · ${finalPayload.trace_id}` : ""}`,
    );
    setMessageSources(assistant, sources);
    setChatActionStatus("Response received.", "success");
    dom.chatThread.scrollTop = dom.chatThread.scrollHeight;
  }

  function processChatFrame(raw, handlers) {
    let frame;
    try {
      frame = JSON.parse(raw);
    } catch {
      throw new ApiError("The stream returned an invalid frame.");
    }
    if (!frame || typeof frame.type !== "string") {
      throw new ApiError("The stream returned an invalid frame.");
    }
    if (frame.type === "delta") {
      if (typeof frame.text === "string") {
        handlers.onDelta(frame.text);
        return;
      }
      throw new ApiError("The stream returned a delta frame without text.");
    }
    if (frame.type === "sources") {
      handlers.onSources(Array.isArray(frame.sources) ? frame.sources : []);
      return;
    }
    if (frame.type === "final") {
      handlers.onFinal(frame);
      return;
    }
    if (frame.type === "error") {
      throw new ApiError(
        frame.code
          ? `The model stream failed (${frame.code}).`
          : "The model stream failed.",
      );
    }
    if (frame.type !== "start") return;
  }

  async function refreshAdaptation({ silent = false } = {}) {
    if (!state.token) {
      dom.personalityCurrent.textContent = "Sign in to load profile.";
      dom.personalityRevisions.replaceChildren(
        element("div", "empty-state", "Sign in to load personality revisions."),
      );
      setSectionStatus(dom.personalitySummary, "Connect to load personality.", "muted");
      return;
    }

    if (!silent) setSectionStatus(dom.personalitySummary, "Loading personality profile…", "loading");
    dom.personalityCurrent.textContent = "Loading profile…";
    dom.personalityRevisions.replaceChildren(
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
    );
    try {
      const [profile, revisions] = await Promise.all([
        apiFetch("/v1/adaptation/profile"),
        apiFetch("/v1/adaptation/profile/revisions?limit=50"),
      ]);
      dom.personalityCurrent.textContent = formatJson(profile);
      renderPersonalityRevisions(Array.isArray(revisions) ? revisions : []);
      setSectionStatus(dom.personalitySummary, "Personality loaded.", "success");
      window.setTimeout(() => setSectionStatus(dom.personalitySummary, "", "default"), 1_100);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not load personality.";
      dom.personalityCurrent.textContent = message;
      dom.personalityRevisions.replaceChildren(
        element("div", "empty-state", message),
      );
      setSectionStatus(dom.personalitySummary, message, "error");
      throw error;
    }
  }

  function renderPersonalityRevisions(revisions) {
    dom.personalityRevisions.replaceChildren();
    if (!revisions.length) {
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(element("strong", "", "No revisions"), element("p", "", "Profile updates will appear here."));
      empty.append(content);
      dom.personalityRevisions.append(empty);
      return;
    }

    for (const item of revisions) {
      const card = element("article", "memory-card");
      const updatedAt = formatDate(item.created_at);
      const taskText = String(item.task_id).slice(0, 8);
      card.append(
        element("h3", "", `${humanize(item.changed_dimension)} · ${updatedAt}`),
        element(
          "p",
          "section-status is-muted",
          `Feedback ${String(item.feedback_id).slice(0, 8)} · Task ${taskText} · Digest ${String(item.feedback_digest).slice(0, 10)}...`,
        ),
        element("pre", "task-result", formatJson(item.snapshot)),
      );
      dom.personalityRevisions.append(card);
    }
  }

  function refreshAdaptationFeedbackFormVisibility() {
    const kind = dom.adaptationFeedbackKind?.value || "correction";
    const showCorrection = kind === "correction";
    const showHelpfulness = kind === "helpfulness";
    const showPreference = kind === "preference";

    if (dom.adaptationCorrectionBlock) dom.adaptationCorrectionBlock.hidden = !showCorrection;
    if (dom.adaptationCorrectionText) dom.adaptationCorrectionText.required = showCorrection;
    if (dom.adaptationHelpfulBlock) dom.adaptationHelpfulBlock.hidden = !showHelpfulness;
    if (dom.adaptationHelpful) dom.adaptationHelpful.required = showHelpfulness;
    if (dom.adaptationDimensionLabel) dom.adaptationDimensionLabel.hidden = !showPreference;
    if (dom.adaptationDimension) dom.adaptationDimension.required = showPreference;
    if (dom.adaptationValueLabel) dom.adaptationValueLabel.hidden = !showPreference;
    if (dom.adaptationDesiredValue) dom.adaptationDesiredValue.required = showPreference;

    if (dom.adaptationResponseTraceId) {
      if (kind === "preference") {
        dom.adaptationResponseTraceId.required = false;
      } else {
        dom.adaptationResponseTraceId.required = true;
      }
    }

    if (dom.adaptationResponseTraceId && dom.adaptationResponseTraceId.required && dom.adaptationResponseTraceId.value.trim()) {
      dom.adaptationResponseTraceId.value = dom.adaptationResponseTraceId.value.trim();
    }
  }

  function adaptationFeedbackPayload() {
    const kind = dom.adaptationFeedbackKind.value;
    const feedbackId = requiredTrimmedValue(dom.adaptationFeedbackId, "Feedback ID");
    const responseTraceId = dom.adaptationResponseTraceId.value.trim();
    if (!/^[0-9a-fA-F-]{36}$/.test(feedbackId)) {
      throw new ApiError("Feedback ID must be a UUID.");
    }

    if (kind === "preference") {
      return {
        kind,
        feedback_id: feedbackId,
        response_trace_id: responseTraceId || null,
        dimension: requiredTrimmedValue(dom.adaptationDimension, "Dimension"),
        desired_value: Number(dom.adaptationDesiredValue.value),
      };
    }

    if (!responseTraceId) {
      throw new ApiError("Response trace ID is required for this feedback kind.");
    }
    if (!/^trc_[0-9a-f]{32}$/.test(responseTraceId)) {
      throw new ApiError("Response trace ID must match trc_[32-hex].");
    }

    if (kind === "correction") {
      return {
        kind,
        feedback_id: feedbackId,
        response_trace_id: responseTraceId,
        correction_text: requiredTrimmedValue(dom.adaptationCorrectionText, "Correction text"),
      };
    }

    return {
      kind,
      feedback_id: feedbackId,
      response_trace_id: responseTraceId,
      helpful: String(dom.adaptationHelpful?.value || "true") === "true",
    };
  }

  async function submitAdaptationFeedback() {
    if (!state.token) return openAuth();
    dom.adaptationFeedbackSummary.hidden = true;
    dom.adaptationFeedbackSummary.className = "field-error";
    dom.adaptationFeedbackSummary.textContent = "";
    dom.adaptationFeedbackResult.hidden = true;
    dom.adaptationFeedbackTaskNote.hidden = true;
    try {
      const payload = adaptationFeedbackPayload();
      if (
        payload.kind === "preference"
        && (
          !Number.isFinite(payload.desired_value)
          || payload.desired_value < 0
          || payload.desired_value > 1
        )
      ) {
        throw new ApiError("Desired value must be between 0 and 1.");
      }
      const result = await apiFetch("/v1/adaptation/feedback", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dom.adaptationFeedbackResult.textContent = formatJson(result);
      dom.adaptationFeedbackResult.hidden = false;
      let note = `Feedback ${result.feedback_id} received as ${result.kind}.`;
      if (result.applied_task_id) note += ` Protected action task: ${result.applied_task_id}.`;
      if (result.created) note += " Stored as new feedback.";
      else note += " Already exists; duplicate suppressed.";
      if (result.proposal_task) {
        note += ` Preference proposal task is pending (${result.proposal_task.id || result.proposal_task}).`;
      }
      dom.adaptationFeedbackTaskNote.textContent = note;
      dom.adaptationFeedbackTaskNote.hidden = false;
      if (result.profile) dom.personalityCurrent.textContent = formatJson(result.profile);
      await refreshAdaptation();
      setSectionStatus(dom.adaptationFeedbackSummary, result.proposal_task ? "Preference task created for review." : "Feedback recorded.", "success");
    } catch (error) {
      dom.adaptationFeedbackSummary.textContent = error instanceof Error
        ? error.message
        : "Could not submit explicit feedback.";
      dom.adaptationFeedbackSummary.hidden = false;
      dom.adaptationFeedbackResult.hidden = true;
      dom.adaptationFeedbackTaskNote.hidden = true;
    }
  }

  function updateAdaptationSliderLabel() {
    if (!dom.adaptationDesiredValue || !dom.adaptationValueLabel) return;
    dom.adaptationValueLabel.textContent = Number(dom.adaptationDesiredValue.value).toFixed(2);
  }

  async function resetPersonalityProfile() {
    if (!state.token) return openAuth();
    if (!window.confirm("Reset profile to default settings? This also clears revision history and active preferences.")) {
      return;
    }
    setSectionStatus(dom.personalitySummary, "Resetting personality profile…", "loading");
    try {
      const result = await apiFetch("/v1/adaptation/personality/reset", {
        method: "POST",
      });
      if (result) dom.personalityCurrent.textContent = formatJson(result);
      await refreshAdaptation();
      setSectionStatus(dom.personalitySummary, "Personality profile reset to defaults.", "success");
      toast("Personality profile reset.", "success");
    } catch (error) {
      setSectionStatus(
        dom.personalitySummary,
        error instanceof Error ? error.message : "Could not reset personality.",
        "error",
      );
    }
  }

  async function deletePersonalityProfile() {
    if (!state.token) return openAuth();
    if (!window.confirm("Delete profile and all explicit feedback? This cannot be undone.")) {
      return;
    }
    setSectionStatus(dom.personalitySummary, "Deleting personality profile…", "loading");
    try {
      await apiFetch("/v1/adaptation/personality", {
        method: "DELETE",
      });
      await refreshAdaptation();
      setSectionStatus(
        dom.personalitySummary,
        "Personality profile deleted. A default profile is now active.",
        "success",
      );
      toast("Personality profile deleted.", "success");
    } catch (error) {
      setSectionStatus(
        dom.personalitySummary,
        error instanceof Error ? error.message : "Could not delete personality.",
        "error",
      );
    }
  }

  async function refreshP2pStatus({ silent = false } = {}) {
    if (!state.token) {
      setSectionStatus(dom.p2pSummary, "Connect to load P2P status.", "muted");
      if (dom.p2pStatus) dom.p2pStatus.textContent = "Sign in to load status.";
      return;
    }
    if (!silent) setSectionStatus(dom.p2pSummary, "Loading status…", "loading");
    try {
      const payload = await apiFetch("/v1/p2p/status");
      if (dom.p2pStatus) {
        dom.p2pStatus.textContent = formatJson(payload);
      }
      const summary = `Paired peers: ${payload.paired_peers} · keys: ${payload.paired_keys} · quarantine: ${payload.quarantine_item_count}`;
      setSectionStatus(dom.p2pSummary, summary, "success");
      return payload;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not load P2P status.";
      if (dom.p2pStatus) dom.p2pStatus.textContent = message;
      setSectionStatus(dom.p2pSummary, message, "error");
      throw error;
    }
  }

  async function submitP2PPair() {
    if (!state.token) return openAuth();
    const submit = dom.p2pPairForm.querySelector("button[type='submit']");
    submit.disabled = true;
    submit.textContent = "Pairing…";
    dom.p2pPairResult.hidden = true;
    try {
      const payload = {
        peer_id: requiredTrimmedValue(dom.p2pPairPeerId, "Peer ID"),
        key_id: requiredTrimmedValue(dom.p2pPairKeyId, "Key ID"),
        signing_secret_b64: requiredTrimmedValue(dom.p2pPairSecret, "Signing secret"),
      };
      const result = await apiFetch("/v1/p2p/pair", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dom.p2pPairResult.className = "section-status is-success";
      dom.p2pPairResult.textContent = `Paired ${result.peer_id} with key ${result.key_id}.`;
      dom.p2pPairResult.hidden = false;
      await refreshP2pStatus({ silent: true });
    } catch (error) {
      dom.p2pPairResult.className = "field-error";
      dom.p2pPairResult.textContent = error instanceof Error
        ? error.message
        : "Could not create pair.";
      dom.p2pPairResult.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = "Create pair";
    }
  }

  async function submitP2PExport() {
    if (!state.token) return openAuth();
    const submit = dom.p2pExportForm.querySelector("button[type='submit']");
    submit.disabled = true;
    submit.textContent = "Exporting…";
    dom.p2pExportResult.hidden = true;
    try {
      const payload = {
        envelope_id: requiredTrimmedValue(dom.p2pExportEnvelopeId, "Envelope ID"),
        sender_peer_id: requiredTrimmedValue(dom.p2pExportSenderPeerId, "Sender peer ID"),
        recipient_peer_id: requiredTrimmedValue(dom.p2pExportRecipientPeerId, "Recipient peer ID"),
        sender_key_id: requiredTrimmedValue(dom.p2pExportSenderKeyId, "Sender key ID"),
        issued_at: parseISODate(dom.p2pExportIssuedAt.value, "Issued at"),
        expires_at: parseISODate(dom.p2pExportExpiresAt.value, "Expires at"),
        nonce: requiredTrimmedValue(dom.p2pExportNonce, "Nonce"),
        schema_version: requiredTrimmedValue(dom.p2pExportSchemaVersion, "Schema version"),
        sensitivity: requiredTrimmedValue(dom.p2pExportSensitivity, "Sensitivity"),
        retention_class: requiredTrimmedValue(dom.p2pExportRetention, "Retention class"),
        trust: requiredTrimmedValue(dom.p2pExportTrust, "Trust"),
        source_time: parseISODate(dom.p2pExportSourceTime.value, "Source time"),
        signing_secret_b64: requiredTrimmedValue(dom.p2pExportSecret, "Signing secret"),
        payload: parsePayloadJson(dom.p2pExportPayload.value, "Payload"),
      };
      const result = await apiFetch("/v1/p2p/envelope/export", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dom.p2pExportResult.textContent = formatJson(result);
      dom.p2pExportResult.hidden = false;
      await refreshP2pStatus({ silent: true });
    } catch (error) {
      dom.p2pExportResult.textContent = error instanceof Error ? error.message : "Could not export envelope.";
      dom.p2pExportResult.hidden = false;
    } finally {
      submit.disabled = false;
      submit.textContent = "Create signed envelope";
    }
  }

  async function submitP2PImport() {
    if (!state.token) return openAuth();
    const submit = dom.p2pImportForm.querySelector("button[type='submit']");
    submit.disabled = true;
    submit.textContent = "Importing…";
    dom.p2pImportResult.hidden = true;
    dom.p2pImportResultJson.hidden = true;
    try {
      const signature = requiredTrimmedValue(dom.p2pImportSignature, "Signature");
      if (!/^[0-9a-fA-F]{64}$/.test(signature)) {
        throw new ApiError("Signature must be 64 hexadecimal characters.");
      }
      const payload = {
        envelope_id: requiredTrimmedValue(dom.p2pImportEnvelopeId, "Envelope ID"),
        sender_peer_id: requiredTrimmedValue(dom.p2pImportSenderPeerId, "Sender peer ID"),
        recipient_peer_id: requiredTrimmedValue(dom.p2pImportRecipientPeerId, "Recipient peer ID"),
        owner_id: requiredTrimmedValue(dom.p2pImportOwnerId, "Owner ID"),
        sender_key_id: requiredTrimmedValue(dom.p2pImportSenderKeyId, "Sender key ID"),
        issued_at: parseISODate(dom.p2pImportIssuedAt.value, "Issued at"),
        expires_at: parseISODate(dom.p2pImportExpiresAt.value, "Expires at"),
        nonce: requiredTrimmedValue(dom.p2pImportNonce, "Nonce"),
        schema_version: requiredTrimmedValue(dom.p2pImportSchemaVersion, "Schema version"),
        sensitivity: requiredTrimmedValue(dom.p2pImportSensitivity, "Sensitivity"),
        retention_class: requiredTrimmedValue(dom.p2pImportRetention, "Retention class"),
        trust: requiredTrimmedValue(dom.p2pImportTrust, "Trust"),
        source_time: parseISODate(dom.p2pImportSourceTime.value, "Source time"),
        signature,
        payload: parsePayloadJson(dom.p2pImportPayload.value, "Payload"),
      };
      const result = await apiFetch("/v1/p2p/envelope/import", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      dom.p2pImportResult.className = "section-status is-success";
      dom.p2pImportResult.textContent = result.reviewed
        ? "Envelope validated and placed into quarantine."
        : "Envelope processed.";
      dom.p2pImportResult.hidden = false;
      dom.p2pImportResultJson.textContent = formatJson(result);
      dom.p2pImportResultJson.hidden = false;
      await refreshP2pStatus({ silent: true });
    } catch (error) {
      dom.p2pImportResult.className = "field-error";
      dom.p2pImportResult.textContent = error instanceof Error
        ? error.message
        : "Could not import envelope.";
      dom.p2pImportResult.hidden = false;
      dom.p2pImportResultJson.hidden = true;
    } finally {
      submit.disabled = false;
      submit.textContent = "Import envelope";
    }
  }

  function renderMemoryLoading() {
    dom.memoryResults.replaceChildren(
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
    );
    dom.memorySummary.textContent = "Searching local memory…";
  }

  function renderMemoryHits(hits, query) {
    dom.memoryResults.replaceChildren();
    dom.memorySummary.textContent = hits.length
      ? `${hits.length} ${hits.length === 1 ? "result" : "results"} for “${query}”`
      : `No memories matched “${query}”.`;
    if (!hits.length) {
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(
        element("strong", "", "No matching memories"),
        element("p", "", "Try a broader phrase, switch search mode, or add a new memory."),
      );
      empty.append(content);
      dom.memoryResults.append(empty);
      return;
    }

    for (const hit of hits) {
      const card = element("article", "memory-card");
      const head = element("div", "memory-card-head");
      head.append(
        element("h3", "", hit.title || "Untitled memory"),
        element("span", "memory-score", Number(hit.score).toFixed(3)),
      );
      const provenance = element("div", "memory-provenance");
      provenance.append(element("span", "", `Document ${String(hit.document_id).slice(0, 8)}`));
      if (hit.source_uri) provenance.append(element("span", "", hit.source_uri));
      card.append(head, element("p", "", hit.text), provenance);
      dom.memoryResults.append(card);
    }
  }

  async function searchMemory(query, mode) {
    renderMemoryLoading();
    try {
      const payload = await apiFetch("/v1/memory/search", {
        method: "POST",
        body: JSON.stringify({ query, top_k: 8, mode }),
      });
      renderMemoryHits(payload.hits || [], query);
    } catch (error) {
      dom.memoryResults.replaceChildren();
      dom.memorySummary.textContent = error instanceof Error ? error.message : "Memory search failed.";
      toast(dom.memorySummary.textContent, "error");
    }
  }

  function selectedDocument() {
    return dom.documentFile.files?.[0] || null;
  }

  function documentExtension(filename) {
    const separator = filename.lastIndexOf(".");
    return separator >= 0 ? filename.slice(separator + 1).toLowerCase() : "";
  }

  function canonicalDocumentMimeType(file) {
    const expected = DOCUMENT_MIME_TYPES[documentExtension(file.name)];
    if (!expected) throw new ApiError("Choose a TXT, Markdown, HTML, PDF, or DOCX document.");
    const declared = String(file.type || "").split(";", 1)[0].trim().toLowerCase();
    if (!GENERIC_DOCUMENT_MIME_TYPES.has(declared) && declared !== expected) {
      throw new ApiError("The selected document type does not match its filename extension.");
    }
    return expected;
  }

  function validateSelectedDocument() {
    const file = selectedDocument();
    if (!file) throw new ApiError("Choose a document to upload.");
    if (typeof file.name !== "string" || !file.name || FORMAT_CONTROL_CHARACTERS.test(file.name)) {
      throw new ApiError("The selected filename contains unsafe or invisible characters.");
    }
    if (!Number.isSafeInteger(file.size) || file.size < 1) {
      throw new ApiError("The selected document is empty or its size is invalid.");
    }
    if (file.size > MAX_DOCUMENT_BYTES) {
      throw new ApiError("The selected document exceeds the 10 MB upload limit.", 413);
    }
    const canonicalMimeType = canonicalDocumentMimeType(file);
    const canonicalContent = file.slice(0, file.size, canonicalMimeType);
    if (canonicalContent.size !== file.size || canonicalContent.type !== canonicalMimeType) {
      throw new ApiError("The browser could not prepare this document safely.");
    }
    return { canonicalContent, canonicalMimeType, file };
  }

  function documentSourceTimestamp(file) {
    const timestamp = Number.isFinite(file.lastModified) && file.lastModified > 0
      ? file.lastModified
      : Date.now();
    const value = new Date(timestamp);
    return Number.isNaN(value.getTime()) ? new Date().toISOString() : value.toISOString();
  }

  function renderSelectedDocument() {
    const file = selectedDocument();
    dom.documentError.hidden = true;
    if (!file) {
      dom.documentFileSummary.textContent = "No file selected.";
      return;
    }
    try {
      const reviewed = validateSelectedDocument();
      const modified = documentSourceTimestamp(reviewed.file);
      dom.documentFileSummary.textContent = `${reviewed.file.name} · ${formatPayloadBytes(reviewed.file.size)} · ${reviewed.canonicalMimeType} · modified ${formatDate(modified)}`;
    } catch (error) {
      dom.documentFile.value = "";
      dom.documentFileSummary.textContent = "The selected file is not eligible for upload.";
      dom.documentError.textContent = error instanceof Error
        ? error.message
        : "This document cannot be uploaded.";
      dom.documentError.hidden = false;
    }
  }

  function resetDocumentUpload() {
    dom.documentForm.reset();
    dom.documentError.hidden = true;
    dom.documentError.textContent = "";
    dom.documentUploadResult.hidden = true;
    dom.documentSubmit.hidden = false;
    dom.documentSubmit.disabled = false;
    dom.documentSubmit.textContent = "Create approval task";
    dom.documentFileSummary.textContent = "No file selected.";
  }

  function openDocumentUpload() {
    if (!state.token) return openAuth();
    resetDocumentUpload();
    showDialog(dom.documentDialog);
    window.setTimeout(() => dom.documentFile.focus(), 50);
  }

  function validateDocumentUploadResponse(payload) {
    if (
      !payload
      || typeof payload.id !== "string"
      || payload.kind !== "document.ingest"
      || payload.status !== "waiting_approval"
      || payload.risk_level !== "local_mutation"
      || typeof payload.action_digest !== "string"
      || !/^[0-9a-f]{64}$/.test(payload.action_digest)
    ) {
      throw new ApiError("The server returned an invalid document approval task.");
    }
    return payload;
  }

  async function uploadDocument() {
    const { canonicalContent, file } = validateSelectedDocument();
    const formData = new FormData();
    formData.append("file", canonicalContent, file.name);
    formData.append("declared_size", String(file.size));
    formData.append("source_timestamp", documentSourceTimestamp(file));
    formData.append("title", dom.documentTitle.value.trim());
    formData.append("sensitivity", dom.documentSensitivity.value);
    formData.append("retention_class", dom.documentRetention.value);

    const payload = validateDocumentUploadResponse(await apiFetch("/v1/documents", {
      method: "POST",
      body: formData,
    }));
    state.uploadedTaskId = payload.id;
    dom.documentUploadStatus.textContent = `${file.name} is staged locally and has not been ingested yet.`;
    dom.documentUploadState.textContent = humanize(payload.status);
    dom.documentUploadTaskId.textContent = payload.id;
    dom.documentUploadResult.hidden = false;
    dom.documentSubmit.hidden = true;
    await refreshTasks({ silent: true });
    toast("Document staged for protected approval.", "success");
  }

  function selectTaskFilter(filter) {
    state.taskFilter = filter;
    document.querySelectorAll("[data-task-filter]").forEach((candidate) => {
      const active = candidate.dataset.taskFilter === filter;
      candidate.classList.toggle("is-active", active);
      candidate.setAttribute("aria-pressed", String(active));
    });
  }

  async function showUploadedTask() {
    closeDialog(dom.documentDialog);
    selectTaskFilter("all");
    selectView("tasks");
    await refreshTasks({ silent: true });
    const card = dom.taskList.querySelector("[data-upload-task-focus='true']");
    if (card) {
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.focus({ preventScroll: true });
    }
  }

  function renderTaskCount() {
    const count = state.tasks.filter((task) => task.status === "waiting_approval").length;
    for (const badge of [dom.taskCount, dom.mobileTaskCount]) {
      badge.textContent = String(count);
      badge.hidden = count === 0;
    }
  }

  function taskResultText(result) {
    if (!result) return "";
    try {
      return JSON.stringify(result, null, 2);
    } catch {
      return "Task returned a result that could not be displayed.";
    }
  }

  function taskPayloadPreview(summary) {
    if (summary.preview_omitted_characters === 0) return summary.preview_head;
    return `${summary.preview_head}\n\n… ${summary.preview_omitted_characters.toLocaleString()} characters omitted …\n\n${summary.preview_tail}`;
  }

  function createTaskReview(detail) {
    const summary = detail.payload_summary;
    if (
      !summary
      || summary.format !== "sorted-pretty-json-v1"
      || summary.encoding !== "utf-8"
      || !Number.isSafeInteger(summary.page_count)
      || summary.page_count < 1
      || !Number.isSafeInteger(summary.page_size_characters)
      || summary.page_size_characters < 1
      || summary.page_size_characters > 8_000
      || typeof summary.preview_head !== "string"
      || typeof summary.preview_tail !== "string"
      || !Number.isSafeInteger(summary.preview_omitted_characters)
      || summary.preview_omitted_characters < 0
    ) {
      throw new ApiError("The server returned an invalid protected payload summary.");
    }
    return {
      actionDigest: typeof detail.action_digest === "string" ? detail.action_digest : "",
      expanded: false,
      integrityFailed: false,
      page: null,
      pageError: "",
      pageIndex: 0,
      pageLoading: false,
      payloadSummary: summary,
    };
  }

  function formatPayloadBytes(byteLength) {
    if (byteLength < 1_024) return `${byteLength} B`;
    if (byteLength < 1_048_576) return `${(byteLength / 1_024).toFixed(1)} KiB`;
    return `${(byteLength / 1_048_576).toFixed(2)} MiB`;
  }

  function renderTaskReview(card, taskId, reviewState) {
    const payloadSummary = reviewState.payloadSummary;
    const pageCount = payloadSummary.page_count;
    const pageIndex = Math.max(0, Math.min(reviewState.pageIndex, pageCount - 1));
    reviewState.pageIndex = pageIndex;

    const review = element("section", "task-review");
    review.append(
      element("strong", "", "Protected approval review"),
      element(
        "p",
        "",
        "The digest covers the complete canonical payload, including content outside this bounded view.",
      ),
    );

    if (reviewState.actionDigest) {
      const digest = element("div", "task-digest");
      digest.append(
        element("span", "", "Integrity digest"),
        element("code", "", reviewState.actionDigest),
      );
      review.append(digest);
    }

    const fieldLabel = `${payloadSummary.top_level_field_count} ${payloadSummary.top_level_field_count === 1 ? "field" : "fields"}`;
    const pageLabel = `${pageCount} ${pageCount === 1 ? "page" : "pages"}`;
    review.append(
      element(
        "div",
        "task-payload-summary",
        `${formatPayloadBytes(payloadSummary.byte_length)} · ${fieldLabel} · ${pageLabel}`,
      ),
    );

    const displayedPayload = reviewState.expanded
      ? reviewState.pageLoading
        ? "Loading this exact payload page…"
        : reviewState.page?.page_index === pageIndex
          ? reviewState.page.content
          : "This payload page is unavailable."
      : taskPayloadPreview(payloadSummary);
    const payload = element("pre", "task-result task-payload-page", displayedPayload);
    payload.tabIndex = 0;
    payload.setAttribute(
      "aria-label",
      reviewState.expanded
        ? `Approval payload page ${pageIndex + 1} of ${pageCount}`
        : "Bounded approval payload preview",
    );
    review.append(payload);

    if (reviewState.pageError || reviewState.integrityFailed) {
      review.append(
        element(
          "div",
          "task-error",
          reviewState.integrityFailed
            ? "The payload digest changed. Close this review and load it again."
            : reviewState.pageError,
        ),
      );
    }

    if (payloadSummary.preview_omitted_characters > 0 || pageCount > 1) {
      const controls = element("div", "task-payload-controls");
      if (reviewState.expanded) {
        const previous = element("button", "", "Previous");
        previous.type = "button";
        previous.disabled = pageIndex === 0 || reviewState.pageLoading || reviewState.integrityFailed;
        previous.dataset.taskReviewAction = "previous";
        previous.dataset.taskId = taskId;

        const pageStatus = element(
          "span",
          "task-payload-page-status",
          `Page ${pageIndex + 1} of ${pageCount}`,
        );
        pageStatus.setAttribute("aria-live", "polite");

        const next = element("button", "", "Next");
        next.type = "button";
        next.disabled = pageIndex === pageCount - 1 || reviewState.pageLoading || reviewState.integrityFailed;
        next.dataset.taskReviewAction = "next";
        next.dataset.taskId = taskId;
        controls.append(previous, pageStatus, next);
      }

      const toggle = element(
        "button",
        "task-payload-toggle",
        reviewState.expanded ? "Show bounded preview" : "Open exact payload pages",
      );
      toggle.type = "button";
      toggle.disabled = reviewState.pageLoading || reviewState.integrityFailed;
      toggle.dataset.taskReviewAction = reviewState.expanded ? "preview" : "full";
      toggle.dataset.taskId = taskId;
      controls.append(toggle);
      review.append(controls);
    }

    card.append(review);
  }

  function reconcileTaskReviews() {
    const taskStatuses = new Map(state.tasks.map((task) => [task.id, task.status]));
    for (const taskId of state.taskReviews.keys()) {
      if (taskStatuses.get(taskId) !== "waiting_approval") state.taskReviews.delete(taskId);
    }
  }

  function taskFilterLabel(filter) {
    return filter === "all" ? "all tasks" : humanize(filter);
  }

  function renderTaskCard(task) {
    const card = element("article", `task-card${task.status === "waiting_approval" ? " is-priority" : ""}`);
    if (task.id === state.uploadedTaskId) {
      card.classList.add("is-upload-result");
      card.dataset.uploadTaskFocus = "true";
      card.tabIndex = -1;
    }
    const head = element("div", "task-card-head");
    const title = element("div");
    title.append(
      element("h3", "", humanize(task.kind)),
      element(
        "div",
        "task-meta",
        `Created ${formatDate(task.created_at)} · Attempt ${task.attempt_count}/${task.max_attempts}`,
      ),
    );
    const badge = element("span", "status-badge", humanize(task.status));
    badge.dataset.status = task.status;
    head.append(title, badge);
    card.append(head);

    const trace = element("div", "task-meta");
    trace.append(
      element("span", "", `Risk: ${humanize(task.risk_level)}`),
      element("span", "", `Trace: ${task.trace_id || "pending"}`),
    );
    if (task.approval_expires_at && task.status === "waiting_approval") {
      trace.append(element("span", "", `Approval expires ${formatDate(task.approval_expires_at)}`));
    }
    card.append(trace);

    if (task.result) card.append(element("pre", "task-result", taskResultText(task.result)));
    if (task.error_text) card.append(element("div", "task-error", task.error_text));

    const reviewState = state.taskReviews.get(task.id);
    if (task.status === "waiting_approval" && reviewState) {
      renderTaskReview(card, task.id, reviewState);
    }

    if (["waiting_approval", "queued"].includes(task.status)) {
      const actions = element("div", "task-actions");
      if (task.status === "waiting_approval") {
        const approve = element(
          "button",
          reviewState ? "approve-action" : "",
          reviewState ? "Approve exact action" : "Review protected action",
        );
        approve.type = "button";
        approve.dataset.taskAction = reviewState ? "approve" : "review";
        approve.dataset.taskId = task.id;
        approve.disabled = Boolean(
          reviewState && (!reviewState.actionDigest || reviewState.integrityFailed),
        );
        actions.append(approve);
      }
      const cancel = element("button", "", "Cancel");
      cancel.type = "button";
      cancel.dataset.taskAction = "cancel";
      cancel.dataset.taskId = task.id;
      actions.append(cancel);
      card.append(actions);
    }
    return card;
  }

  function renderTasks() {
    if (dom.tasksEmptyActions) dom.tasksEmptyActions.hidden = true;
    dom.taskList.replaceChildren();
    if (!state.token) {
      setTasksStatus("Connect to view queued tasks and protected actions.");
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(
        element("strong", "", "Connect to view tasks"),
        element("p", "", "Your token stays inside this browser tab and is required for owner-scoped task data."),
      );
      empty.append(content);
      dom.taskList.append(empty);
      if (dom.tasksEmptyActions) dom.tasksEmptyActions.hidden = true;
      return;
    }

    const filtered = state.taskFilter === "all"
      ? state.tasks
      : state.tasks.filter((task) => task.status === state.taskFilter);
    if (!filtered.length) {
      const noTasksTitle = state.tasks.length ? "No tasks in this view" : "No tasks yet";
      const noTasksMessage = state.tasks.length
        ? "Choose another filter to see your task history."
        : "Create a memory or submit a queued action to see it here.";
      setTasksStatus(
        state.tasks.length ? `No ${taskFilterLabel(state.taskFilter)} tasks found.` : "No tasks yet.",
        "muted",
      );
      if (state.taskFilter === "all" && dom.tasksEmptyActions) {
        dom.tasksEmptyActions.hidden = false;
      }
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(element("strong", "", noTasksTitle), element("p", "", noTasksMessage));
      empty.append(content);
      dom.taskList.append(empty);
      return;
    }

    setTasksStatus(`Showing ${filtered.length} ${filtered.length === 1 ? "task" : "tasks"} in ${taskFilterLabel(state.taskFilter)}.`, "success");

    if (state.taskFilter === "all") {
      const groups = new Map(TASK_GROUPS.map(({ key }) => [key, []]));
      const other = [];
      for (const task of filtered) {
        if (groups.has(task.status)) {
          groups.get(task.status).push(task);
        } else {
          other.push(task);
        }
      }

      for (const { key, label } of TASK_GROUPS) {
        const group = groups.get(key);
        if (!group?.length) continue;
        const section = element("section", "task-group");
        section.append(element("h2", "task-group-title", `${label} (${group.length})`));
        for (const task of group) {
          section.append(renderTaskCard(task));
        }
        dom.taskList.append(section);
      }

      if (other.length) {
        const section = element("section", "task-group");
        section.append(element("h2", "task-group-title", `Other (${other.length})`));
        for (const task of other) {
          section.append(renderTaskCard(task));
        }
        dom.taskList.append(section);
      }
      return;
    }

    for (const task of filtered) {
      dom.taskList.append(renderTaskCard(task));
    }
  }

  function renderTaskLoading() {
    setTasksStatus("Loading tasks…", "loading");
    dom.taskList.replaceChildren(
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
      element("div", "skeleton-card"),
    );
  }

  async function refreshTasks({ silent = false } = {}) {
    if (!state.token) {
      renderTasks();
      return;
    }
    if (!silent) renderTaskLoading();
    try {
      state.tasks = await apiFetch("/v1/tasks?limit=50");
      reconcileTaskReviews();
      renderTaskCount();
      renderTasks();
      setTasksStatus(
        `Latest task list synced · ${state.tasks.length} total item${state.tasks.length === 1 ? "" : "s"}`,
        "success",
      );
      if (!silent) {
        window.setTimeout(() => setTasksStatus(""), 1_100);
      }
    } catch (error) {
      if (!silent) {
        renderTasks();
        const message = error instanceof Error ? error.message : "Could not load tasks.";
        setTasksStatus(message, "error");
        toast(message, "error");
      }
    }
  }

  async function handleTaskAction(button) {
    const taskId = button.dataset.taskId;
    const action = button.dataset.taskAction;
    if (!taskId || !action) return;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = action === "approve"
      ? "Approving…"
      : action === "review"
        ? "Loading exact action…"
        : "Cancelling…";
    setTasksStatus(
      action === "approve"
        ? "Approving protected task…"
        : action === "review"
          ? "Loading task details…"
          : "Cancelling task…",
      "loading",
    );
    try {
      if (action === "review") {
        const detail = await apiFetch(`/v1/tasks/${encodeURIComponent(taskId)}`);
        state.taskReviews.set(taskId, createTaskReview(detail));
        renderTasks();
        setTasksStatus("Task details loaded.", "success");
        return;
      }
      const reviewState = state.taskReviews.get(taskId);
      if (action === "approve" && (!reviewState?.actionDigest || reviewState.integrityFailed)) {
        throw new ApiError("Reload and review this protected action before approving it.");
      }
      await apiFetch(`/v1/tasks/${encodeURIComponent(taskId)}/${action}`, {
        method: "POST",
        body: action === "approve"
          ? JSON.stringify({ action_digest: reviewState.actionDigest })
          : undefined,
      });
      state.taskReviews.delete(taskId);
      const message = action === "approve" ? "Protected action approved." : "Task cancelled.";
      toast(message, "success");
      setTasksStatus(message, "success");
      await refreshTasks({ silent: true });
      if (action === "approve") window.setTimeout(() => refreshTasks({ silent: true }), 1_500);
    } catch (error) {
      const message = error instanceof Error ? error.message : `Could not ${action} task.`;
      setTasksStatus(message, "error");
      toast(message, "error");
      button.disabled = false;
      button.textContent = original;
    }
  }

  async function loadTaskPayloadPage(taskId, reviewState, pageIndex) {
    reviewState.pageLoading = true;
    reviewState.pageError = "";
    setTasksStatus(`Loading protected payload page ${pageIndex + 1}…`, "loading");
    renderTasks();
    try {
      const page = await apiFetch(
        `/v1/tasks/${encodeURIComponent(taskId)}/payload?page=${pageIndex}`,
      );
      if (
        page.task_id !== taskId
        || page.action_digest !== reviewState.actionDigest
        || page.format !== "sorted-pretty-json-v1"
        || page.encoding !== "utf-8"
        || page.page_index !== pageIndex
        || page.page_count !== reviewState.payloadSummary.page_count
        || page.page_size_characters !== reviewState.payloadSummary.page_size_characters
        || page.character_start !== pageIndex * page.page_size_characters
        || !Number.isSafeInteger(page.character_end)
        || page.character_end < page.character_start
        || page.character_end - page.character_start > page.page_size_characters
        || typeof page.content !== "string"
        || page.content.length > page.page_size_characters * 2
      ) {
        reviewState.integrityFailed = true;
        throw new ApiError("The payload page did not match the protected review digest.");
      }
      reviewState.page = page;
      reviewState.pageIndex = pageIndex;
    } catch (error) {
      reviewState.pageError = error instanceof Error
        ? error.message
        : "Could not load this payload page.";
    } finally {
      reviewState.pageLoading = false;
      if (state.taskReviews.get(taskId) === reviewState) renderTasks();
      if (state.taskReviews.get(taskId) === reviewState && !reviewState.pageError) {
        setTasksStatus("Payload page loaded.", "success");
      } else if (state.taskReviews.get(taskId) === reviewState && reviewState.pageError) {
        setTasksStatus(reviewState.pageError, "error");
      }
    }
  }

  async function handleTaskReviewAction(button) {
    const taskId = button.dataset.taskId;
    const action = button.dataset.taskReviewAction;
    const reviewState = taskId ? state.taskReviews.get(taskId) : null;
    if (!reviewState || !action) return;

    if (action === "preview") {
      reviewState.expanded = false;
      renderTasks();
      setTasksStatus("Showing bounded payload preview.", "success");
      return;
    }
    if (reviewState.pageLoading || reviewState.integrityFailed) return;

    let targetPage = reviewState.pageIndex;
    if (action === "full") {
      reviewState.expanded = true;
      targetPage = 0;
      setTasksStatus("Opening exact payload pages…", "loading");
    } else if (action === "previous") targetPage -= 1;
    else if (action === "next") targetPage += 1;
    else return;

    if (targetPage < 0 || targetPage >= reviewState.payloadSummary.page_count) return;
    await loadTaskPayloadPage(taskId, reviewState, targetPage);
  }

  function startTaskPolling() {
    stopTaskPolling();
    state.taskPoll = window.setInterval(() => {
      if (document.visibilityState === "visible" && state.token) refreshTasks({ silent: true });
    }, TASK_POLL_MS);
  }

  function stopTaskPolling() {
    if (state.taskPoll !== null) window.clearInterval(state.taskPoll);
    state.taskPoll = null;
  }

  function bindEvents() {
    document.querySelectorAll("[data-view-link]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.preventDefault();
        selectView(button.dataset.viewLink);
      });
    });

    document.querySelectorAll("[data-prompt]").forEach((button) => {
      button.addEventListener("click", () => {
        dom.chatMessage.value = button.dataset.prompt || "";
        resizeComposer();
        dom.chatForm.requestSubmit();
      });
    });

    document.querySelectorAll("[data-task-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        selectTaskFilter(button.dataset.taskFilter || "all");
        renderTasks();
      });
    });

    dom.authButton.addEventListener("click", () => openAuth());
    dom.authClose.addEventListener("click", () => closeDialog(dom.authDialog));
    dom.statusButton.addEventListener("click", () => refreshReadiness({ announce: true }));
    dom.refreshStatus.addEventListener("click", () => refreshReadiness({ announce: true }));
    dom.refreshTasks.addEventListener("click", () => refreshTasks());
    dom.refreshPersonality.addEventListener("click", () => {
      if (!state.token) return openAuth();
      void refreshAdaptation();
    });
    dom.resetPersonality.addEventListener("click", () => {
      void resetPersonalityProfile();
    });
    dom.deletePersonality.addEventListener("click", () => {
      void deletePersonalityProfile();
    });
    dom.newChat.addEventListener("click", resetChat);
    dom.openDocument.addEventListener("click", openDocumentUpload);
    dom.documentClose.addEventListener("click", () => closeDialog(dom.documentDialog));
    dom.documentFile.addEventListener("change", renderSelectedDocument);
    dom.documentTaskLink.addEventListener("click", (event) => {
      event.preventDefault();
      void showUploadedTask();
    });
    dom.openNote.addEventListener("click", () => {
      if (!state.token) return openAuth();
      dom.noteError.hidden = true;
      showDialog(dom.noteDialog);
      window.setTimeout(() => dom.noteTitle.focus(), 50);
    });
    dom.tasksEmptyOpenDocument?.addEventListener("click", () => {
      closeDialog(dom.documentDialog);
      openDocumentUpload();
    });
    dom.tasksEmptyOpenNote?.addEventListener("click", () => {
      closeDialog(dom.noteDialog);
      if (!state.token) return openAuth();
      dom.noteError.hidden = true;
      showDialog(dom.noteDialog);
      window.setTimeout(() => dom.noteTitle.focus(), 50);
    });
    dom.noteClose.addEventListener("click", () => closeDialog(dom.noteDialog));

    dom.toggleToken.addEventListener("click", () => {
      const visible = dom.apiToken.type === "text";
      dom.apiToken.type = visible ? "password" : "text";
      dom.toggleToken.textContent = visible ? "Show" : "Hide";
      dom.toggleToken.setAttribute("aria-label", visible ? "Show token" : "Hide token");
    });

    dom.disconnectButton.addEventListener("click", () => {
      forgetToken();
      dom.disconnectButton.hidden = true;
      closeDialog(dom.authDialog);
      window.setTimeout(() => openAuth(), 80);
    });

    dom.authForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!isSecureTransport()) return;
      const token = dom.apiToken.value.trim();
      if (!token) return;
      dom.connectButton.disabled = true;
      dom.connectButton.textContent = "Connecting…";
      dom.authError.hidden = true;
      try {
        await connectWithToken(token);
      } catch (error) {
        dom.authError.textContent = error instanceof Error ? error.message : "Connection failed.";
        dom.authError.hidden = false;
      } finally {
        dom.connectButton.disabled = false;
        dom.connectButton.textContent = "Connect";
      }
    });

    dom.chatMessage.addEventListener("input", resizeComposer);
    dom.chatMessage.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        dom.chatForm.requestSubmit();
      }
    });
    dom.chatForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const message = dom.chatMessage.value.trim();
      if (!message) return;
      if (!state.token) {
        openAuth();
        return;
      }
      dom.chatMessage.value = "";
      resizeComposer();
      sendChat(message);
    });

    dom.memorySearchForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const query = dom.memoryQuery.value.trim();
      if (!query) return;
      searchMemory(query, dom.memoryMode.value);
    });
    dom.adaptationFeedbackKind?.addEventListener("change", refreshAdaptationFeedbackFormVisibility);
    dom.adaptationDesiredValue?.addEventListener("input", updateAdaptationSliderLabel);
    refreshAdaptationFeedbackFormVisibility();
    updateAdaptationSliderLabel();
    dom.adaptationFeedbackForm?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const submit = dom.adaptationFeedbackForm.querySelector("button[type='submit']");
      if (!submit) return;
      submit.disabled = true;
      submit.textContent = "Submitting…";
      try {
        await submitAdaptationFeedback();
      } finally {
        submit.disabled = false;
        submit.textContent = "Submit feedback";
      }
    });

    dom.documentForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      dom.documentSubmit.disabled = true;
      dom.documentSubmit.textContent = "Staging document…";
      dom.documentError.hidden = true;
      dom.documentUploadResult.hidden = true;
      try {
        await uploadDocument();
      } catch (error) {
        dom.documentError.textContent = error instanceof Error
          ? error.message
          : "Could not create the document ingestion task.";
        dom.documentError.hidden = false;
        dom.documentSubmit.disabled = false;
        dom.documentSubmit.textContent = "Create approval task";
      }
    });

    dom.noteForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const text = dom.noteText.value.trim();
      if (!text) return;
      const submit = dom.noteForm.querySelector("button[type='submit']");
      submit.disabled = true;
      submit.textContent = "Creating…";
      dom.noteError.hidden = true;
      try {
        await apiFetch("/v1/memory/documents", {
          method: "POST",
          body: JSON.stringify({
            text,
            title: dom.noteTitle.value.trim() || null,
            sensitivity: dom.noteSensitivity.value,
            retention_class: dom.noteRetention.value,
          }),
        });
        dom.noteForm.reset();
        closeDialog(dom.noteDialog);
        await refreshTasks({ silent: true });
        selectView("tasks");
        toast("Memory created as a protected approval task.", "success");
      } catch (error) {
        dom.noteError.textContent = error instanceof Error ? error.message : "Could not create memory task.";
        dom.noteError.hidden = false;
      } finally {
        submit.disabled = false;
        submit.textContent = "Create approval task";
      }
    });
    dom.refreshP2p?.addEventListener("click", () => {
      if (!state.token) return openAuth();
      void refreshP2pStatus();
    });
    dom.p2pPairForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitP2PPair();
    });
    dom.p2pExportForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitP2PExport();
    });
    dom.p2pImportForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitP2PImport();
    });

    dom.taskList.addEventListener("click", (event) => {
      const reviewButton = event.target.closest("[data-task-review-action]");
      if (reviewButton) {
        void handleTaskReviewAction(reviewButton);
        return;
      }
      const button = event.target.closest("[data-task-action]");
      if (button) handleTaskAction(button);
    });

    window.addEventListener("hashchange", () => selectView(window.location.hash.slice(1), { updateHash: false }));
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible") refreshReadiness();
    });
  }

  async function initialize() {
    configureTransport();
    bindEvents();
    selectView(window.location.hash.slice(1) || "chat", { updateHash: false });
    resizeComposer();
    renderTasks();
    await refreshReadiness();

    if (state.token && isSecureTransport()) {
      dom.authButton.dataset.connected = "true";
      dom.authButton.setAttribute("aria-label", "Connected; manage API token");
      try {
        await refreshTasks({ silent: true });
        if (state.currentView === "adaptation") {
          refreshAdaptation({ silent: true }).catch(() => {
            setSectionStatus(dom.personalitySummary, "Could not load personality profile yet.", "error");
          });
        }
        if (state.currentView === "p2p") {
          refreshP2pStatus({ silent: true }).catch(() => {
            setSectionStatus(dom.p2pSummary, "Could not load P2P status yet.", "error");
          });
        }
        startTaskPolling();
      } catch {
        forgetToken({ notify: false });
        openAuth("Reconnect to access owner-scoped data.");
      }
    } else {
      if (state.token && !isSecureTransport()) forgetToken({ notify: false });
      openAuth();
    }
  }

  initialize();
})();
