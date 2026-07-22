(() => {
  "use strict";

  const TOKEN_KEY = "mongars.session.token";
  const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "::1", "[::1]"]);
  const TASK_POLL_MS = 8_000;

  const state = {
    token: readSessionToken(),
    sessionId: null,
    tasks: [],
    taskDetails: new Map(),
    taskFilter: "all",
    currentView: "chat",
    taskPoll: null,
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
    emptyChat: document.querySelector("#empty-chat"),
    globalStatusDot: document.querySelector("#global-status-dot"),
    globalStatusLabel: document.querySelector("#global-status-label"),
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
    openNote: document.querySelector("#open-note"),
    refreshStatus: document.querySelector("#refresh-status"),
    refreshTasks: document.querySelector("#refresh-tasks"),
    sidebarStatusDot: document.querySelector("#sidebar-status-dot"),
    sidebarStatusLabel: document.querySelector("#sidebar-status-label"),
    statusButton: document.querySelector("#status-button"),
    taskCount: document.querySelector("#task-count"),
    taskList: document.querySelector("#task-list"),
    chatForm: document.querySelector("#chat-form"),
    chatMessage: document.querySelector("#chat-message"),
    chatThread: document.querySelector("#chat-thread"),
    newChat: document.querySelector("#new-chat"),
    toastRegion: document.querySelector("#toast-region"),
    toggleToken: document.querySelector("#toggle-token"),
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
    if (requestOptions.body !== undefined) headers.set("Content-Type", "application/json");
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

  function setReadinessStatus(kind, label) {
    for (const dot of [dom.globalStatusDot, dom.sidebarStatusDot]) {
      dot.classList.remove("is-ready", "is-down");
      if (kind === "ready") dot.classList.add("is-ready");
      if (kind === "down") dot.classList.add("is-down");
    }
    dom.globalStatusLabel.textContent = label;
    dom.sidebarStatusLabel.textContent = label;
  }

  async function refreshReadiness({ announce = false } = {}) {
    setReadinessStatus("checking", "Checking");
    try {
      const response = await fetch("/v1/readyz", { headers: { Accept: "application/json" } });
      const payload = await response.json();
      const database = payload.dependencies?.database;
      const inference = payload.dependencies?.inference;
      const ready = response.ok && payload.status === "ready";

      dom.databaseStatus.textContent = database?.healthy ? "Connected" : "Unavailable";
      if (inference?.healthy) {
        dom.inferenceStatus.textContent = `${humanize(inference.backend)} ready`;
      } else if (inference?.backend_reachable) {
        dom.inferenceStatus.textContent = "Models missing";
      } else {
        dom.inferenceStatus.textContent = "Unavailable";
      }
      setReadinessStatus(ready ? "ready" : "down", ready ? "System ready" : "Needs attention");
      if (announce) toast(ready ? "All required services are ready." : "One or more services need attention.", ready ? "success" : "error");
    } catch {
      dom.databaseStatus.textContent = "Unavailable";
      dom.inferenceStatus.textContent = "Unavailable";
      setReadinessStatus("down", "Offline");
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
    state.taskDetails.clear();
    writeSessionToken("");
    stopTaskPolling();
    renderTaskCount();
    renderTasks();
    dom.authButton.setAttribute("aria-label", "Connect with API token");
    dom.authButton.removeAttribute("data-connected");
    if (notify) toast("The token was cleared from this tab.", "success");
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
    await refreshTasks({ silent: true });
    startTaskPolling();
  }

  function selectView(view, { updateHash = true } = {}) {
    const target = ["chat", "memory", "tasks"].includes(view) ? view : "chat";
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
    const submitButton = dom.chatForm.querySelector("button[type='submit']");
    addMessage("user", message);
    const typing = addTypingMessage();
    submitButton.disabled = true;
    dom.chatMessage.disabled = true;
    try {
      const payload = await apiFetch("/v1/chat", {
        method: "POST",
        body: JSON.stringify({
          session_id: state.sessionId,
          message,
          require_local_only: dom.localOnly.checked,
        }),
      });
      state.sessionId = payload.session_id;
      typing.remove();
      const memoryLabel = `${payload.memory_hits} ${payload.memory_hits === 1 ? "memory" : "memories"}`;
      addMessage(
        "assistant",
        payload.answer,
        `${payload.model} · ${memoryLabel}`,
        Array.isArray(payload.sources) ? payload.sources : [],
      );
    } catch (error) {
      typing.remove();
      addMessage("assistant", error instanceof Error ? error.message : "The request failed.", "Request error");
    } finally {
      submitButton.disabled = false;
      dom.chatMessage.disabled = false;
      dom.chatMessage.focus();
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

  function renderTaskReview(card, detail) {
    const review = element("section", "task-review");
    review.append(
      element("strong", "", "Exact action payload"),
      element("p", "", "Approve only if every field below matches the action you intend."),
      element("pre", "task-result", taskResultText(detail.payload)),
    );
    if (detail.action_digest) {
      review.append(element("div", "task-meta", `Integrity digest: ${detail.action_digest}`));
    }
    card.append(review);
  }

  function renderTasks() {
    dom.taskList.replaceChildren();
    if (!state.token) {
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(
        element("strong", "", "Connect to view tasks"),
        element("p", "", "Your token stays inside this browser tab and is required for owner-scoped task data."),
      );
      empty.append(content);
      dom.taskList.append(empty);
      return;
    }

    const filtered = state.taskFilter === "all"
      ? state.tasks
      : state.tasks.filter((task) => task.status === state.taskFilter);
    if (!filtered.length) {
      const empty = element("div", "empty-state");
      const content = element("div");
      content.append(
        element("strong", "", state.tasks.length ? "No tasks in this view" : "No tasks yet"),
        element("p", "", state.tasks.length ? "Choose another filter to see your task history." : "Create a memory or submit a queued action to see it here."),
      );
      empty.append(content);
      dom.taskList.append(empty);
      return;
    }

    for (const task of filtered) {
      const card = element("article", `task-card${task.status === "waiting_approval" ? " is-priority" : ""}`);
      const head = element("div", "task-card-head");
      const title = element("div");
      title.append(
        element("h3", "", humanize(task.kind)),
        element("div", "task-meta", `Created ${formatDate(task.created_at)} · Attempt ${task.attempt_count}/${task.max_attempts}`),
      );
      const badge = element("span", "status-badge", humanize(task.status));
      badge.dataset.status = task.status;
      head.append(title, badge);
      card.append(head);

      const trace = element("div", "task-meta");
      trace.append(
        element("span", "", `Risk: ${humanize(task.risk_level)}`),
        element("span", "", `Trace: ${task.trace_id}`),
      );
      if (task.approval_expires_at && task.status === "waiting_approval") {
        trace.append(element("span", "", `Approval expires ${formatDate(task.approval_expires_at)}`));
      }
      card.append(trace);

      if (task.result) card.append(element("pre", "task-result", taskResultText(task.result)));
      if (task.error_text) card.append(element("div", "task-error", task.error_text));

      const detail = state.taskDetails.get(task.id);
      if (task.status === "waiting_approval" && detail) renderTaskReview(card, detail);

      if (["waiting_approval", "queued"].includes(task.status)) {
        const actions = element("div", "task-actions");
        if (task.status === "waiting_approval") {
          const approve = element(
            "button",
            detail ? "approve-action" : "",
            detail ? "Approve exact action" : "Review protected action",
          );
          approve.type = "button";
          approve.dataset.taskAction = detail ? "approve" : "review";
          approve.dataset.taskId = task.id;
          actions.append(approve);
        }
        const cancel = element("button", "", "Cancel");
        cancel.type = "button";
        cancel.dataset.taskAction = "cancel";
        cancel.dataset.taskId = task.id;
        actions.append(cancel);
        card.append(actions);
      }
      dom.taskList.append(card);
    }
  }

  function renderTaskLoading() {
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
      renderTaskCount();
      renderTasks();
    } catch (error) {
      if (!silent) {
        renderTasks();
        toast(error instanceof Error ? error.message : "Could not load tasks.", "error");
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
    try {
      if (action === "review") {
        const detail = await apiFetch(`/v1/tasks/${encodeURIComponent(taskId)}`);
        state.taskDetails.set(taskId, detail);
        renderTasks();
        return;
      }
      await apiFetch(`/v1/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" });
      toast(action === "approve" ? "Protected action approved." : "Task cancelled.", "success");
      await refreshTasks({ silent: true });
      if (action === "approve") window.setTimeout(() => refreshTasks({ silent: true }), 1_500);
    } catch (error) {
      toast(error instanceof Error ? error.message : `Could not ${action} task.`, "error");
      button.disabled = false;
      button.textContent = original;
    }
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
        state.taskFilter = button.dataset.taskFilter || "all";
        document.querySelectorAll("[data-task-filter]").forEach((candidate) => {
          const active = candidate === button;
          candidate.classList.toggle("is-active", active);
          candidate.setAttribute("aria-pressed", String(active));
        });
        renderTasks();
      });
    });

    dom.authButton.addEventListener("click", () => openAuth());
    dom.authClose.addEventListener("click", () => closeDialog(dom.authDialog));
    dom.statusButton.addEventListener("click", () => refreshReadiness({ announce: true }));
    dom.refreshStatus.addEventListener("click", () => refreshReadiness({ announce: true }));
    dom.refreshTasks.addEventListener("click", () => refreshTasks());
    dom.newChat.addEventListener("click", resetChat);
    dom.openNote.addEventListener("click", () => {
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

    dom.taskList.addEventListener("click", (event) => {
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
