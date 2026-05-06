/*
 * steelg8 Chat — 前端脚本
 * -----------------------
 * 职责：
 *   - 拉 /providers 填模型下拉框
 *   - 调 /chat/stream SSE，流式渲染 assistant 消息
 *   - 渲染 Markdown（使用自带 SteelMarkdown）
 *
 * 后端约定（见 Python/server.py）：
 *   POST /chat/stream
 *     body: { message, model?, history?, stream: true }
 *     resp: SSE，事件 JSON：
 *           { type: "meta",  decision: {model, provider, layer, reason} }
 *           { type: "delta", content: "..." }
 *           { type: "done",  full: "...", source: "provider:xxx" }
 *           { type: "error", error: "..." }
 */
(function () {
  "use strict";

  // KERNEL 配置 + fetchJSON + swiftBridge 已经搬到 api.js（window.SteelG8Api）。
  // 这里取出来本地化别名，让原有调用点不用改。
  const SG8 = window.SteelG8Api || {};
  const HASH_PARAMS = SG8.HASH_PARAMS || new URLSearchParams();
  const KERNEL_PORT = SG8.KERNEL_PORT;
  const API_BASE = SG8.API_BASE;
  const KERNEL_AUTH_TOKEN = SG8.KERNEL_AUTH_TOKEN;
  const apiURL = SG8.apiURL;
  const isKernelURL = SG8.isKernelURL;
  const withKernelAuth = SG8.withKernelAuth;
  const fetchJSON = SG8.fetchJSON;

  const $ = (id) => document.getElementById(id);

  const UI = {
    body: document.querySelector(".body"),
    messages: $("messages"),
    input: $("input"),
    send: $("send"),
    model: $("model-picker"),
    reload: $("reload-providers"),
    dot: $("health-dot"),
    routing: $("routing-hint"),
    error: $("error-hint"),
    scratch: $("scratch"),
    chatCol: document.querySelector(".chat-col"),
    scratchNote: $("scratch-note"),
    scratchSaveState: $("scratch-save-state"),
    scratchToggle: $("scratch-toggle"),
    scratchResizer: $("scratch-resizer"),
    projectStackResizer: $("project-stack-resizer"),
    conversationStackResizer: $("conversation-stack-resizer"),
    scratchToNotes: $("scratch-to-notes"),
    scratchClear: $("scratch-clear"),
    attachRow: $("attach-chip-row"),
    sidebarProject: $("sidebar-project"),
    sidebarConversation: $("sidebar-conv"),
    projectsList: $("projects-list"),
    projectOpenBtn: $("project-open-btn"),
    syncModelsBtn: $("sync-models"),
    convList: $("conv-list"),
    convNewBtn: $("conv-new-btn"),
    canvas: $("canvas"),
    canvasResizer: $("canvas-resizer"),
    composer: document.querySelector(".composer"),
    composerResizer: $("composer-resizer"),
    stop: $("stop"),
  };

  // 运行环境探测 + Swift 桥本地别名（已搬到 api.js）
  const HAS_SWIFT_BRIDGE = !!SG8.hasSwiftBridge;
  const swiftBridge = SG8.swiftBridge || (() => false);

  // 独立召唤窗模式（#scratch）：把 chat 列隐藏，侧栏撑满窗口
  const SCRATCH_ONLY = SG8.isScratchOnly ? SG8.isScratchOnly() : HASH_PARAMS.has("scratch");
  if (SCRATCH_ONLY) {
    document.documentElement.classList.add("scratch-only-mode");
  }

  /** 对话历史（只用于 UI 渲染，不再随请求发给后端 —— 后端按 conversationId 从 DB 读） */
  const history = [];

  let sending = false;
  let activeDeltaNode = null;
  let activeFullBuffer = "";
  /** 当前请求的 AbortController，用于中途停止 */
  let activeController = null;

  /** 当前激活的 conversation id；null 表示下一次 send 会自动建一个新会话 */
  let activeConversationId = null;
  /** 会话列表缓存 */
  let conversationsCache = [];

  // Scratch 便签（单文本）
  let scratchNoteText = "";
  let scratchSaveTimer = null;

  // ================== 可拖拽布局 ==================

  const LAYOUT_STORAGE_KEY = "steelg8.chat.layout.v2";
  const LAYOUT_DEFAULTS = Object.freeze({
    scratchWidth: 280,
    canvasWidth: 420,
    projectHeight: 112,
    conversationHeight: 180,
    composerHeight: 132,
  });
  const LAYOUT_LIMITS = Object.freeze({
    scratchMin: 220,
    scratchMax: 560,
    canvasMin: 300,
    canvasMax: 820,
    chatMin: 420,
    compactChatMin: 280,
    projectMin: 68,
    projectMax: 280,
    conversationMin: 86,
    conversationMax: 460,
    scratchNoteMin: 160,
    composerMin: 112,
    composerMax: 360,
    messagesMin: 220,
    compactMessagesMin: 140,
  });
  const layoutState = loadLayoutState();
  let layoutSaveTimer = null;

  function loadLayoutState() {
    try {
      const raw = localStorage.getItem(LAYOUT_STORAGE_KEY);
      const parsed = raw ? JSON.parse(raw) : {};
      return {
        scratchWidth: Number(parsed.scratchWidth) || LAYOUT_DEFAULTS.scratchWidth,
        canvasWidth: Number(parsed.canvasWidth) || LAYOUT_DEFAULTS.canvasWidth,
        projectHeight: Number(parsed.projectHeight) || LAYOUT_DEFAULTS.projectHeight,
        conversationHeight: Number(parsed.conversationHeight) || LAYOUT_DEFAULTS.conversationHeight,
        composerHeight: Number(parsed.composerHeight) || LAYOUT_DEFAULTS.composerHeight,
      };
    } catch (_) {
      return Object.assign({}, LAYOUT_DEFAULTS);
    }
  }

  function saveLayoutStateSoon() {
    clearTimeout(layoutSaveTimer);
    layoutSaveTimer = setTimeout(saveLayoutState, 120);
  }

  function saveLayoutState() {
    try {
      localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(layoutState));
    } catch (_) {}
  }

  function resetLayoutKind(kind) {
    if (kind === "scratch") layoutState.scratchWidth = LAYOUT_DEFAULTS.scratchWidth;
    if (kind === "canvas") layoutState.canvasWidth = LAYOUT_DEFAULTS.canvasWidth;
    if (kind === "project") layoutState.projectHeight = LAYOUT_DEFAULTS.projectHeight;
    if (kind === "conversation") layoutState.conversationHeight = LAYOUT_DEFAULTS.conversationHeight;
    if (kind === "composer") layoutState.composerHeight = LAYOUT_DEFAULTS.composerHeight;
    applyLayout({ persist: true });
  }

  function clampNumber(value, min, max) {
    const safeMax = Math.max(min, max);
    const n = Number(value);
    if (!Number.isFinite(n)) return min;
    return Math.min(Math.max(n, min), safeMax);
  }

  function bodyWidth() {
    const rect = UI.body ? UI.body.getBoundingClientRect() : null;
    return Math.max(0, rect && rect.width ? rect.width : window.innerWidth || 0);
  }

  function bodyHeight() {
    const rect = UI.body ? UI.body.getBoundingClientRect() : null;
    return Math.max(0, rect && rect.height ? rect.height : window.innerHeight || 0);
  }

  function scratchHeight() {
    const rect = UI.scratch ? UI.scratch.getBoundingClientRect() : null;
    return Math.max(0, rect && rect.height ? rect.height : bodyHeight());
  }

  function chatHeight() {
    const rect = UI.chatCol ? UI.chatCol.getBoundingClientRect() : null;
    return Math.max(0, rect && rect.height ? rect.height : bodyHeight());
  }

  function chatMinWidth() {
    const total = bodyWidth();
    return total < 900 ? LAYOUT_LIMITS.compactChatMin : LAYOUT_LIMITS.chatMin;
  }

  function messagesMinHeight() {
    return chatHeight() < 620 ? LAYOUT_LIMITS.compactMessagesMin : LAYOUT_LIMITS.messagesMin;
  }

  function isScratchCollapsed() {
    return UI.scratch && UI.scratch.getAttribute("data-mode") === "collapsed";
  }

  function isCanvasOpen() {
    if (!UI.canvas) return false;
    const mode = UI.canvas.getAttribute("data-mode");
    return !!mode && mode !== "closed";
  }

  function hasConversationPanel() {
    return !!(UI.sidebarConversation && !UI.sidebarConversation.hidden);
  }

  function maxScratchWidth() {
    const total = bodyWidth();
    const canvas = isCanvasOpen() ? layoutState.canvasWidth : 0;
    const available = total - canvas - chatMinWidth();
    return Math.max(
      LAYOUT_LIMITS.scratchMin,
      Math.min(LAYOUT_LIMITS.scratchMax, available)
    );
  }

  function maxCanvasWidth() {
    const total = bodyWidth();
    const scratch = isScratchCollapsed() ? 36 : layoutState.scratchWidth;
    const available = total - scratch - chatMinWidth();
    return Math.max(
      LAYOUT_LIMITS.canvasMin,
      Math.min(LAYOUT_LIMITS.canvasMax, available)
    );
  }

  function maxProjectHeight() {
    const total = scratchHeight();
    const conversation = hasConversationPanel() ? layoutState.conversationHeight : 0;
    const available = total - conversation - LAYOUT_LIMITS.scratchNoteMin;
    return Math.max(
      LAYOUT_LIMITS.projectMin,
      Math.min(LAYOUT_LIMITS.projectMax, available)
    );
  }

  function maxConversationHeight() {
    const total = scratchHeight();
    const available = total - layoutState.projectHeight - LAYOUT_LIMITS.scratchNoteMin;
    return Math.max(
      LAYOUT_LIMITS.conversationMin,
      Math.min(LAYOUT_LIMITS.conversationMax, available)
    );
  }

  function maxComposerHeight() {
    const total = chatHeight();
    // chat-col padding: 18px top + 16px bottom = 34px；messages margin-bottom: 8px
    // chat-resizer 负边距抵消自身高度（净占 0px），不计入
    const overhead = 42;
    const available = total - overhead - messagesMinHeight();
    return Math.max(
      LAYOUT_LIMITS.composerMin,
      Math.min(LAYOUT_LIMITS.composerMax, available)
    );
  }

  function normalizeLayoutState() {
    layoutState.scratchWidth = clampNumber(
      layoutState.scratchWidth,
      LAYOUT_LIMITS.scratchMin,
      maxScratchWidth()
    );
    layoutState.canvasWidth = clampNumber(
      layoutState.canvasWidth,
      LAYOUT_LIMITS.canvasMin,
      maxCanvasWidth()
    );
    if (!SCRATCH_ONLY && !isScratchCollapsed()) {
      layoutState.projectHeight = clampNumber(
        layoutState.projectHeight,
        LAYOUT_LIMITS.projectMin,
        maxProjectHeight()
      );
      if (hasConversationPanel()) {
        layoutState.conversationHeight = clampNumber(
          layoutState.conversationHeight,
          LAYOUT_LIMITS.conversationMin,
          maxConversationHeight()
        );
      }
    }
    layoutState.composerHeight = clampNumber(
      layoutState.composerHeight,
      LAYOUT_LIMITS.composerMin,
      maxComposerHeight()
    );
  }

  function syncLayoutClasses() {
    if (!UI.body) return;
    UI.body.classList.toggle("canvas-open", isCanvasOpen());
    UI.body.classList.toggle("scratch-collapsed", isScratchCollapsed());
  }

  function applyLayout(opts) {
    normalizeLayoutState();
    document.documentElement.style.setProperty(
      "--scratch-width",
      `${Math.round(layoutState.scratchWidth)}px`
    );
    document.documentElement.style.setProperty(
      "--canvas-width",
      `${Math.round(layoutState.canvasWidth)}px`
    );
    document.documentElement.style.setProperty(
      "--project-panel-height",
      `${Math.round(layoutState.projectHeight)}px`
    );
    document.documentElement.style.setProperty(
      "--conversation-panel-height",
      `${Math.round(layoutState.conversationHeight)}px`
    );
    document.documentElement.style.setProperty(
      "--composer-height",
      `${Math.round(layoutState.composerHeight)}px`
    );
    syncLayoutClasses();
    if (opts && opts.persist) saveLayoutStateSoon();
  }

  function setLayoutWidth(kind, value, persist) {
    if (kind === "scratch") {
      layoutState.scratchWidth = clampNumber(
        value,
        LAYOUT_LIMITS.scratchMin,
        maxScratchWidth()
      );
    } else if (kind === "canvas") {
      layoutState.canvasWidth = clampNumber(
        value,
        LAYOUT_LIMITS.canvasMin,
        maxCanvasWidth()
      );
    }
    applyLayout({ persist: !!persist });
  }

  function setLayoutHeight(kind, value, persist) {
    if (kind === "project") {
      layoutState.projectHeight = clampNumber(
        value,
        LAYOUT_LIMITS.projectMin,
        maxProjectHeight()
      );
    } else if (kind === "conversation") {
      layoutState.conversationHeight = clampNumber(
        value,
        LAYOUT_LIMITS.conversationMin,
        maxConversationHeight()
      );
    } else if (kind === "composer") {
      layoutState.composerHeight = clampNumber(
        value,
        LAYOUT_LIMITS.composerMin,
        maxComposerHeight()
      );
    }
    applyLayout({ persist: !!persist });
  }

  function setupPaneResizer(handle, kind) {
    if (!handle || !UI.body) return;

    handle.addEventListener("dblclick", () => resetLayoutKind(kind));

    handle.addEventListener("pointerdown", (event) => {
      if (SCRATCH_ONLY) return;
      if (event.button !== undefined && event.button !== 0) return;
      if (kind === "scratch" && isScratchCollapsed()) return;
      if (kind === "canvas" && !isCanvasOpen()) return;

      event.preventDefault();
      const bodyRect = UI.body.getBoundingClientRect();
      const pointerId = event.pointerId;
      let finished = false;

      handle.classList.add("active");
      UI.body.classList.add("is-resizing", "is-resizing-x");
      try { handle.setPointerCapture(pointerId); } catch (_) {}

      const move = (ev) => {
        ev.preventDefault();
        const width = kind === "scratch"
          ? ev.clientX - bodyRect.left
          : bodyRect.right - ev.clientX;
        setLayoutWidth(kind, width, false);
      };

      const finish = () => {
        if (finished) return;
        finished = true;
        handle.removeEventListener("pointermove", move);
        handle.classList.remove("active");
        UI.body.classList.remove("is-resizing", "is-resizing-x");
        try {
          if (handle.hasPointerCapture(pointerId)) {
            handle.releasePointerCapture(pointerId);
          }
        } catch (_) {}
        saveLayoutState();
      };

      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", finish, { once: true });
      handle.addEventListener("pointercancel", finish, { once: true });
    });

    handle.addEventListener("keydown", (event) => {
      if (SCRATCH_ONLY) return;
      if (kind === "scratch" && isScratchCollapsed()) return;
      if (kind === "canvas" && !isCanvasOpen()) return;
      const step = event.shiftKey ? 48 : 16;
      let next = null;
      if (kind === "scratch") {
        if (event.key === "ArrowLeft") next = layoutState.scratchWidth - step;
        if (event.key === "ArrowRight") next = layoutState.scratchWidth + step;
      } else if (kind === "canvas") {
        if (event.key === "ArrowLeft") next = layoutState.canvasWidth + step;
        if (event.key === "ArrowRight") next = layoutState.canvasWidth - step;
      }
      if (next === null) return;
      event.preventDefault();
      setLayoutWidth(kind, next, true);
    });
  }

  function setupStackResizer(handle, kind) {
    if (!handle || !UI.body) return;

    handle.addEventListener("dblclick", () => resetLayoutKind(kind));

    handle.addEventListener("pointerdown", (event) => {
      if (SCRATCH_ONLY) return;
      if (event.button !== undefined && event.button !== 0) return;
      if (isScratchCollapsed() && kind !== "composer") return;

      event.preventDefault();
      const pointerId = event.pointerId;
      const projectRect = UI.sidebarProject ? UI.sidebarProject.getBoundingClientRect() : null;
      const conversationRect = UI.sidebarConversation ? UI.sidebarConversation.getBoundingClientRect() : null;
      const chatRect = UI.chatCol ? UI.chatCol.getBoundingClientRect() : null;
      let finished = false;

      handle.classList.add("active");
      UI.body.classList.add("is-resizing", "is-resizing-y");
      try { handle.setPointerCapture(pointerId); } catch (_) {}

      const move = (ev) => {
        ev.preventDefault();
        let height = null;
        if (kind === "project" && projectRect) {
          height = ev.clientY - projectRect.top;
        } else if (kind === "conversation" && conversationRect) {
          height = ev.clientY - conversationRect.top;
        } else if (kind === "composer" && chatRect) {
          height = chatRect.bottom - ev.clientY;
        }
        if (height !== null) setLayoutHeight(kind, height, false);
      };

      const finish = () => {
        if (finished) return;
        finished = true;
        handle.removeEventListener("pointermove", move);
        handle.classList.remove("active");
        UI.body.classList.remove("is-resizing", "is-resizing-y");
        try {
          if (handle.hasPointerCapture(pointerId)) {
            handle.releasePointerCapture(pointerId);
          }
        } catch (_) {}
        saveLayoutState();
      };

      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", finish, { once: true });
      handle.addEventListener("pointercancel", finish, { once: true });
    });

    handle.addEventListener("keydown", (event) => {
      if (SCRATCH_ONLY) return;
      if (isScratchCollapsed() && kind !== "composer") return;
      const step = event.shiftKey ? 48 : 16;
      let next = null;
      if (kind === "project") {
        if (event.key === "ArrowUp") next = layoutState.projectHeight - step;
        if (event.key === "ArrowDown") next = layoutState.projectHeight + step;
      } else if (kind === "conversation") {
        if (event.key === "ArrowUp") next = layoutState.conversationHeight - step;
        if (event.key === "ArrowDown") next = layoutState.conversationHeight + step;
      } else if (kind === "composer") {
        if (event.key === "ArrowUp") next = layoutState.composerHeight + step;
        if (event.key === "ArrowDown") next = layoutState.composerHeight - step;
      }
      if (next === null) return;
      event.preventDefault();
      setLayoutHeight(kind, next, true);
    });
  }

  function initResizableLayout() {
    applyLayout();
    setupPaneResizer(UI.scratchResizer, "scratch");
    setupPaneResizer(UI.canvasResizer, "canvas");
    setupStackResizer(UI.projectStackResizer, "project");
    setupStackResizer(UI.conversationStackResizer, "conversation");
    setupStackResizer(UI.composerResizer, "composer");

    if (window.MutationObserver) {
      const observer = new MutationObserver(() => applyLayout());
      if (UI.scratch) {
        observer.observe(UI.scratch, {
          attributes: true,
          attributeFilter: ["data-mode"],
        });
      }
      if (UI.canvas) {
        observer.observe(UI.canvas, {
          attributes: true,
          attributeFilter: ["data-mode"],
        });
      }
      if (UI.sidebarConversation) {
        observer.observe(UI.sidebarConversation, {
          attributes: true,
          attributeFilter: ["hidden", "aria-hidden"],
        });
      }
    }

    if (window.ResizeObserver && UI.body) {
      const resizeObserver = new ResizeObserver(() => applyLayout());
      resizeObserver.observe(UI.body);
      if (UI.chatCol) resizeObserver.observe(UI.chatCol);
      if (UI.scratch) resizeObserver.observe(UI.scratch);
    }

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => applyLayout({ persist: true }), 80);
    });
  }

  initResizableLayout();

  // --------------- bootstrap ---------------

  async function refreshHealth() {
    try {
      const j = await fetchJSON("/health", { cache: "no-store" }, 3000);
      UI.dot.classList.remove("dot-off");
      UI.dot.classList.add("dot-on");
      UI.dot.title = `${j.mode || "ok"}\ndefault: ${j.defaultModel || "—"}`;
      return j;
    } catch (_) {}
    UI.dot.classList.remove("dot-on");
    UI.dot.classList.add("dot-off");
    UI.dot.title = "内核未就绪";
    return null;
  }

  // ================== 便签（单文本，自动保存） ==================

  async function loadScratchNote() {
    if (!UI.scratchNote) return;
    try {
      const j = await fetchJSON("/scratch/note", { cache: "no-store" }, 5000);
      const remote = (j && j.text) || "";
      // 避免用户正在输入时被远端覆盖：只在本地没改过 / 空 / 首次时同步
      if (!scratchNoteText && document.activeElement !== UI.scratchNote) {
        UI.scratchNote.value = remote;
      } else if (!UI.scratchNote.value && remote) {
        UI.scratchNote.value = remote;
      }
      scratchNoteText = UI.scratchNote.value;
      setSaveState("已同步");
    } catch (e) {
      console.error("loadScratchNote failed", e);
    }
  }

  function scheduleScratchSave() {
    setSaveState("未保存…");
    clearTimeout(scratchSaveTimer);
    scratchSaveTimer = setTimeout(doScratchSave, 600);
  }

  async function doScratchSave() {
    const text = UI.scratchNote ? UI.scratchNote.value : "";
    try {
      await fetchJSON("/scratch/note", {
        method: "POST",
        json: { text },
      }, 5000);
      scratchNoteText = text;
      setSaveState("已保存");
    } catch (_) {
      setSaveState("保存失败");
    }
  }

  function setSaveState(msg) {
    if (UI.scratchSaveState) UI.scratchSaveState.textContent = msg || "";
  }

  function clearScratchNote() {
    if (!UI.scratchNote) return;
    UI.scratchNote.value = "";
    scheduleScratchSave();
    UI.scratchNote.focus();
  }

  function saveNoteToAppleNotes() {
    const text = UI.scratchNote ? UI.scratchNote.value : "";
    if (!text.trim()) {
      setSaveState("便签是空的");
      setTimeout(() => setSaveState("已保存"), 1200);
      return;
    }
    const title = text.split("\n")[0].trim().slice(0, 40) || "steelg8 便签";
    const ok = swiftBridge("saveToNotes", { folder: "steelg8", title, body: text });
    if (!ok) {
      setSaveState("需要在 WKWebView 里");
      return;
    }
    setSaveState("已推到 Apple 备忘录");
    setTimeout(() => setSaveState("已保存"), 2000);
  }

  // ================== usage pill ==================

  // ================== 多项目列表 ==================

  let projectsCache = [];
  let activeIndexStatus = {};
  let activeProjectKey = null;

  async function refreshProject() {
    // 同时拉列表 + 索引状态
    try {
      const [listJ, statusJ] = await Promise.all([
        fetchJSON("/projects", { cache: "no-store" }, 7000),
        fetchJSON("/project/status", { cache: "no-store" }, 7000),
      ]);
      projectsCache = listJ.items || [];
      activeIndexStatus = statusJ || {};
      renderProjectsList();
      const active = projectsCache.find((p) => p.active) || null;
      const nextProjectKey = active ? `project:${active.id}` : "global";
      if (!sending && nextProjectKey !== activeProjectKey) {
        activeProjectKey = nextProjectKey;
        activeConversationId = null;
        await loadProjectConversation();
      }
    } catch (_) {}
  }

  function renderProjectsList() {
    if (!UI.projectsList) return;
    if (!projectsCache.length) {
      UI.projectsList.innerHTML = `
        <div class="projects-empty">
          还没有项目。点「+ 打开」选文件夹，steelg8 会索引里面的 .md / .txt 供对话引用。
        </div>`;
      return;
    }
    const esc = (s) => window.SteelMarkdown.escape(String(s || ""));
    UI.projectsList.innerHTML = projectsCache.map((p) => {
      const cls = p.active ? " active" : "";
      let statusHtml = "";
      if (p.active) {
        const idx = activeIndexStatus || {};
        if (idx.state === "running") {
          statusHtml = '<div class="proj-status running">索引中…</div>';
        } else if (idx.state === "error") {
          statusHtml = `<div class="proj-status error">索引失败：${esc(idx.error || "")}</div>`;
        }
      }
      return `
        <div class="proj-item${cls}" data-id="${p.id}">
          <div class="proj-row">
            <span class="proj-name" title="${esc(p.name)} · ${esc(p.path)}">${esc(p.name)}</span>
            <button class="proj-more" data-more="${p.id}" title="更多">⋮</button>
          </div>
          ${statusHtml}
        </div>
      `;
    }).join("");

    UI.projectsList.querySelectorAll(".proj-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        // 点「⋯」不触发激活
        if (e.target.closest(".proj-more") || e.target.closest(".proj-menu")) return;
        const pid = Number(el.getAttribute("data-id"));
        if (!pid) return;
        const p = projectsCache.find((x) => x.id === pid);
        if (p && !p.active) activateProject(pid);
      });
    });
    UI.projectsList.querySelectorAll("[data-more]").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        const pid = Number(btn.getAttribute("data-more"));
        openProjectMenu(pid, btn);
      });
    });
  }

  async function loadProjectConversation() {
    if (!UI.messages) return;
    UI.messages.innerHTML = '<div class="welcome"><h2>加载项目上下文…</h2></div>';
    history.length = 0;
    try {
      const j = await fetchJSON("/project/conversation", { cache: "no-store" }, 8000);
      const conv = j.conversation || null;
      activeConversationId = conv && conv.id ? conv.id : null;
      renderLoadedMessages(
        j.messages || [],
        '<div class="welcome"><h2>项目上下文</h2><p>这个项目会持续使用同一个对话上下文。</p></div>'
      );
    } catch (e) {
      console.error("loadProjectConversation failed", e);
      UI.messages.innerHTML = `<div class="welcome"><h2>上下文加载失败</h2><p>${e.message || e}</p></div>`;
    }
  }

  function renderLoadedMessages(msgs, emptyHTML) {
    UI.messages.innerHTML = "";
    if (!msgs.length) {
      UI.messages.innerHTML = emptyHTML;
      return;
    }
    msgs.forEach((m) => {
      if (m.role !== "user" && m.role !== "assistant") return;
      const content = m.content || "";
      const node = addMessage(m.role, content, m.compressed ? "已压缩" : "");
      if (m.compressed) node.node.classList.add("compressed");
      history.push({ role: m.role, content });
    });
  }

  function openProjectMenu(pid, anchorBtn) {
    // 关掉其他菜单
    document.querySelectorAll(".proj-menu").forEach((n) => {
      if (typeof n._steelg8Cleanup === "function") {
        n._steelg8Cleanup();
      } else {
        n.remove();
      }
    });
    const p = projectsCache.find((x) => x.id === pid);
    if (!p) return;

    const menu = document.createElement("div");
    menu.className = "proj-menu";
    let cleanupMenu = () => menu.remove();
    menu.innerHTML = `
      <button data-act="reindex">重新索引</button>
      <button data-act="rename">改名</button>
      <button data-act="reveal">在 Finder 里打开</button>
      <div class="proj-menu-divider"></div>
      <button data-act="remove" class="danger">移除项目</button>
    `;
    document.body.appendChild(menu);
    positionProjectMenu(menu, anchorBtn);

    menu.querySelectorAll("button[data-act]").forEach((b) => {
      b.addEventListener("click", async (e) => {
        e.stopPropagation();
        const act = b.getAttribute("data-act");
        cleanupMenu();
        await handleProjectMenuAction(pid, act);
      });
    });

    setTimeout(() => {
      if (!menu.isConnected) return;
      const onViewportChange = () => {
        if (menu.isConnected) positionProjectMenu(menu, anchorBtn);
      };
      const off = (e) => {
        if (!menu.contains(e.target) && e.target !== anchorBtn) {
          cleanupMenu();
        }
      };
      cleanupMenu = () => {
        document.removeEventListener("click", off, true);
        window.removeEventListener("resize", onViewportChange, true);
        window.removeEventListener("scroll", onViewportChange, true);
        menu.remove();
      };
      menu._steelg8Cleanup = cleanupMenu;
      document.addEventListener("click", off, true);
      window.addEventListener("resize", onViewportChange, true);
      window.addEventListener("scroll", onViewportChange, true);
    }, 0);
  }

  function positionProjectMenu(menu, anchorBtn) {
    const rect = anchorBtn.getBoundingClientRect();
    const gap = 6;
    const menuWidth = Math.max(150, menu.offsetWidth || 150);
    const left = Math.min(
      Math.max(8, rect.right - menuWidth),
      Math.max(8, window.innerWidth - menuWidth - 8)
    );
    const top = Math.min(
      rect.bottom + gap,
      Math.max(8, window.innerHeight - (menu.offsetHeight || 160) - 8)
    );
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
  }

  async function handleProjectMenuAction(pid, act) {
    const p = projectsCache.find((x) => x.id === pid);
    if (!p) return;
    switch (act) {
      case "activate":
        await activateProject(pid);
        break;
      case "reindex":
        if (!p.active) await activateProject(pid);
        if (!swiftBridge("reindexProject")) {
          await fetchJSON("/project/reindex", { method: "POST" }, 7000).catch(() => {});
        }
        setTimeout(refreshProject, 500);
        break;
      case "rename": {
        const newName = prompt("改名：", p.name);
        if (!newName || newName === p.name) return;
        try {
          await fetchJSON(`/projects/${pid}/rename`, {
            method: "POST",
            json: { name: newName },
          }, 7000);
          await refreshProject();
        } catch (e) {
          console.error("rename failed", e);
        }
        break;
      }
      case "reveal":
        swiftBridge("revealInFinder", { path: p.path });
        break;
      case "remove":
        if (!confirm(`移除「${p.name}」？\n索引数据会一起删掉（对话会话不会删）。`)) return;
        try {
          await fetchJSON(`/projects/${pid}`, { method: "DELETE" }, 7000);
          await refreshProject();
        } catch (e) {
          console.error("remove failed", e);
        }
        break;
    }
  }

  async function activateProject(pid) {
    try {
      await fetchJSON(`/projects/${pid}/activate`, { method: "POST" }, 7000);
      await refreshProject();
    } catch (e) {
      console.error("activate failed", e);
    }
  }

  if (UI.projectOpenBtn) {
    UI.projectOpenBtn.addEventListener("click", () => {
      if (!swiftBridge("openProjectPicker")) {
        flashRouting("菜单栏 → 打开项目文件夹…（只在 WKWebView 里能直接弹面板）");
      }
    });
  }

  function flashRouting(msg) {
    if (!UI.routing) return;
    const prev = UI.routing.textContent;
    UI.routing.textContent = msg;
    setTimeout(() => (UI.routing.textContent = prev || ""), 2200);
  }

  // ================== RAG hits chips ==================

  function renderRagChips(hits, targetBubble) {
    if (!hits || !hits.length || !targetBubble) return;
    const row = document.createElement("div");
    row.className = "rag-chips";
    hits.forEach((h, i) => {
      const chip = document.createElement("span");
      chip.className = "rag-chip";
      const citation = h.citation || {};
      const label = citation.heading || h.heading || h.relPath;
      const via = h.retrieval ? ` · ${h.retrieval}` : "";
      chip.innerHTML = `
        <span class="rag-path" title="${window.SteelMarkdown.escape(h.relPath)}">📎 ${window.SteelMarkdown.escape(label)}</span>
        <span class="rag-score">${h.score}</span>
        <span class="rag-via">${window.SteelMarkdown.escape(via)}</span>
      `;
      chip.addEventListener("click", (ev) => showRagPopover(ev, h));
      row.appendChild(chip);
    });
    targetBubble.parentElement.insertBefore(row, targetBubble.nextSibling);
  }

  function showRagPopover(ev, hit) {
    document.querySelectorAll(".rag-popover").forEach((n) => n.remove());
    const pop = document.createElement("div");
    pop.className = "rag-popover";
    const citation = hit.citation || {};
    const page = citation.page || hit.page;
    const heading = citation.heading || hit.heading || "";
    const charStart = citation.charStart ?? hit.charStart ?? 0;
    const charEnd = citation.charEnd ?? hit.charEnd ?? 0;
    const sourceType = citation.sourceType || hit.sourceType || "project";
    const retrieval = citation.retrieval || hit.retrieval || "vector";
    const detail = [
      sourceType,
      retrieval,
      page ? `page ${page}` : "",
      heading ? `# ${heading}` : "",
      `${charStart}-${charEnd}`,
    ].filter(Boolean).join(" · ");
    pop.innerHTML = `
      <div class="rp-head">
        <span>${window.SteelMarkdown.escape(hit.relPath)} · chunk#${hit.chunkIdx}</span>
        <span>score ${hit.score}</span>
      </div>
      <div class="rp-meta">${window.SteelMarkdown.escape(detail)}</div>
      <div class="rp-body">${window.SteelMarkdown.escape(hit.preview || "")}</div>
    `;
    document.body.appendChild(pop);
    const rect = ev.currentTarget.getBoundingClientRect();
    pop.style.left = Math.min(rect.left, window.innerWidth - 540) + "px";
    pop.style.top = Math.min(rect.bottom + 6, window.innerHeight - 40 - pop.offsetHeight) + "px";
    const off = (e) => {
      if (!pop.contains(e.target)) {
        pop.remove();
        document.removeEventListener("click", off, true);
      }
    };
    setTimeout(() => document.addEventListener("click", off, true), 0);
  }

  // ================== 会话列表（持久化） ==================

  async function refreshConversations() {
    if (!UI.convList) return;
    try {
      const j = await fetchJSON("/conversations", { cache: "no-store" }, 7000);
      conversationsCache = j.items || [];
      renderConversationList();
    } catch (_) {}
  }

  function renderConversationList() {
    if (!UI.convList) return;
    if (!conversationsCache.length) {
      UI.convList.innerHTML = '<div class="conv-empty">还没有对话。点「+ 新建」或直接发消息开一场。</div>';
      return;
    }
    const esc = (s) => window.SteelMarkdown.escape(String(s || ""));
    const html = conversationsCache.map((c) => {
      const cls = c.id === activeConversationId ? " active" : "";
      const title = c.title || `#${c.id}`;
      const tokens = c.summaryTokens
        ? `<span class="conv-sub" title="已压缩历史 ${c.summaryTokens} tokens">🗜</span>`
        : "";
      return `
        <div class="conv-item${cls}" data-id="${c.id}">
          <span class="conv-title" title="${esc(title)}">${esc(title)}</span>
          ${tokens}
          <button class="conv-del" data-del="${c.id}" title="删除会话">×</button>
        </div>
      `;
    }).join("");
    UI.convList.innerHTML = html;
    UI.convList.querySelectorAll(".conv-item").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.classList && e.target.classList.contains("conv-del")) return;
        const id = Number(el.getAttribute("data-id"));
        if (id && id !== activeConversationId) switchConversation(id);
      });
    });
    UI.convList.querySelectorAll(".conv-del").forEach((btn) => {
      btn.addEventListener("click", async (e) => {
        e.stopPropagation();
        const id = Number(btn.getAttribute("data-del"));
        if (!id) return;
        if (!confirm("删除这个会话？不可恢复。")) return;
        await deleteConversation(id);
      });
    });
  }

  async function switchConversation(id) {
    if (sending) return;
    activeConversationId = id;
    history.length = 0;
    UI.messages.innerHTML = '<div class="welcome"><h2>加载中…</h2></div>';
    try {
      const j = await fetchJSON(`/conversations/${id}/messages`, { cache: "no-store" }, 8000);
      UI.messages.innerHTML = "";
      const msgs = j.messages || [];
      renderLoadedMessages(
        msgs,
        '<div class="welcome"><h2>项目上下文</h2><p>开始说吧。</p></div>'
      );
      renderConversationList();
    } catch (e) {
      console.error("switchConversation failed", e);
      UI.messages.innerHTML = `<div class="welcome"><h2>加载失败</h2><p>${e.message || e}</p></div>`;
    }
  }

  async function deleteConversation(id) {
    try {
      await fetchJSON(`/conversations/${id}`, { method: "DELETE" }, 7000);
      // 如果删的是当前会话，清空 UI 到欢迎态
      if (id === activeConversationId) {
        activeConversationId = null;
        history.length = 0;
        UI.messages.innerHTML = '<div class="welcome"><h2>欢迎回来</h2><p>有什么要让我干的？直接说。</p></div>';
      }
      await refreshConversations();
    } catch (e) {
      console.error("deleteConversation failed", e);
    }
  }

  async function startNewConversation() {
    if (sending) return;
    activeConversationId = null;
    history.length = 0;
    UI.messages.innerHTML = `
      <div class="welcome">
        <h2>新会话</h2>
        <p>开始说吧。</p>
        <div class="hints">
          <button data-prompt="帮我写一段产品发布的推文，100 字左右">产品推文</button>
          <button data-prompt="把下面这段中文翻译成英文：\\n\\n我们的目标是把文案工作者从模型厂商手里解放出来。">翻译任务</button>
          <button data-prompt="用 mermaid flowchart 画一个 steelg8 的架构图">架构图</button>
        </div>
      </div>
    `;
    renderConversationList();
  }

  let lastProvidersOK = 0;

  async function refreshProviders() {
    try {
      const j = await fetchJSON("/providers", { cache: "no-store" }, 7000);
      lastProvidersOK = Date.now();
      const opts = ['<option value="">自动路由</option>'];
      const defaultModel = j.defaultModel || "";
      (j.providers || []).forEach((p) => {
        // 未就绪的 provider（没 key）直接跳过 —— 显示了也选不动，徒增噪音
        if (!p.ready) return;
        const models = p.models || [];
        if (!models.length) return;
        opts.push(`<optgroup label="${p.name}">`);
        models.forEach((m) => {
          const selected = m === defaultModel ? " selected" : "";
          opts.push(`<option value="${m}"${selected}>${m}</option>`);
        });
        opts.push("</optgroup>");
      });
      UI.model.innerHTML = opts.join("");
    } catch (e) {
      console.error("refreshProviders failed", e);
    }
  }

  // --------------- rendering ---------------

  function addMessage(role, content, meta) {
    // 首次消息：清掉欢迎页
    const welcome = UI.messages.querySelector(".welcome");
    if (welcome) welcome.remove();

    const node = document.createElement("div");
    node.className = `message ${role}`;
    node.innerHTML = `
      <div class="avatar">${role === "user" ? "你" : "⚒"}</div>
      <div class="content">
        <div class="bubble"></div>
        <div class="bubble-actions"></div>
        <div class="meta"></div>
      </div>
    `;
    const bubble = node.querySelector(".bubble");
    const metaEl = node.querySelector(".meta");
    const actionsEl = node.querySelector(".bubble-actions");

    if (role === "user") {
      bubble.textContent = content;
    } else {
      bubble.innerHTML = content
        ? window.SteelMarkdown.render(content)
        : '<span class="cursor"></span>';
    }

    if (meta) metaEl.textContent = meta;
    UI.messages.appendChild(node);
    scrollToBottom();
    return { node, bubble, metaEl, actionsEl };
  }

  function updateStreamingAssistant(bubble, full) {
    bubble.innerHTML =
      window.SteelMarkdown.render(full) + '<span class="cursor"></span>';
    scrollToBottom();
  }

  function finalizeAssistant(bubble, full) {
    bubble.innerHTML = window.SteelMarkdown.render(full);
    scrollToBottom();
  }

  function scrollToBottom() {
    UI.messages.scrollTop = UI.messages.scrollHeight;
  }

  // ---- Tool call chips ----
  function ensureToolRow(bubble) {
    const content = bubble.parentElement;
    let row = content.querySelector(".tool-chips");
    if (!row) {
      row = document.createElement("div");
      row.className = "tool-chips";
      bubble.parentElement.insertBefore(row, bubble.nextSibling);
    }
    return row;
  }

  function renderToolChip(bubble, call) {
    const row = ensureToolRow(bubble);
    const chip = document.createElement("div");
    chip.className = "tool-chip tool-running";
    chip.setAttribute("data-tool-id", call.id || "");
    chip.title = "点击展开 / 收起";
    const argsJson = compactJSON(call.args || {}, 4000);
    chip.innerHTML = `
      <span class="tool-icon">🛠️</span>
      <span class="tool-name">${window.SteelMarkdown.escape(call.name || "?")}</span>
      <span class="tool-status">⏳</span>
      <div class="tool-details">
        <div class="tool-details-label">参数</div>
        <pre class="tool-details-body">${window.SteelMarkdown.escape(argsJson)}</pre>
      </div>
    `;
    chip.addEventListener("click", (e) => {
      if (e.target.closest("button")) return;
      chip.classList.toggle("expanded");
    });
    row.appendChild(chip);

    // 让 bubble 不再只有一个光标占空 —— 显示一个轻量占位
    if (!bubble.textContent.trim() || bubble.querySelector(".tool-running-placeholder")) {
      bubble.innerHTML = `
        <div class="tool-running-placeholder">
          <span class="spinner-dot"></span>
          正在调用工具…
        </div>
      `;
    }
  }

  function updateToolChip(bubble, id, result) {
    const content = bubble.parentElement;
    const chip = content.querySelector(`.tool-chip[data-tool-id="${id || ""}"]`);
    if (!chip) return;
    chip.classList.remove("tool-running");
    const isErr = result && result.error;
    chip.classList.add(isErr ? "tool-err" : "tool-ok");

    // 更新状态图标：⏳ → ✓ / ❌
    const statusEl = chip.querySelector(".tool-status");
    if (statusEl) statusEl.textContent = isErr ? "❌" : "✓";

    // 结果塞进展开区
    const full = compactJSON(result || {}, 8000);
    const details = chip.querySelector(".tool-details");
    if (details) {
      details.insertAdjacentHTML("beforeend", `
        <div class="tool-details-label">${isErr ? "错误" : "结果"}</div>
        <pre class="tool-details-body${isErr ? " tool-err-text" : ""}">${window.SteelMarkdown.escape(full)}</pre>
      `);
    }

    // 如果结果里有 "output" 指向一个文件，加"打开 / Finder"两个快捷按钮
    const outputPath = !isErr && result && (result.output || result.path);
    if (outputPath && typeof outputPath === "string" && _looksLikeOfficePath(outputPath)) {
      const row = document.createElement("div");
      row.className = "tool-file-actions";

      const openBtn = document.createElement("button");
      openBtn.textContent = "📂";
      openBtn.title = `打开 ${outputPath}`;
      openBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        if (!swiftBridge("openFile", { path: outputPath })) {
          flashRouting("WebView 外无法调用系统打开");
        }
      });

      const revealBtn = document.createElement("button");
      revealBtn.textContent = "🔍";
      revealBtn.title = `在 Finder 里定位 ${outputPath}`;
      revealBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        swiftBridge("revealInFinder", { path: outputPath });
      });

      row.appendChild(openBtn);
      row.appendChild(revealBtn);
      // 紧挨着 status 图标放（chip 头部右侧），不要进展开区
      const status = chip.querySelector(".tool-status");
      if (status) status.after(row); else chip.appendChild(row);
    }
  }

  function _looksLikeOfficePath(s) {
    return /\.(docx|doc|xlsx|xls|pptx|ppt|pdf)$/i.test(s);
  }

  function compactJSON(obj, maxLen) {
    try {
      const s = JSON.stringify(obj, null, 0);
      if (s.length <= maxLen) return s;
      return s.slice(0, maxLen) + "…";
    } catch (_) {
      return String(obj);
    }
  }

  // ---- Canvas 集成 ----
  function attachCanvasActions(actionsEl, fullText) {
    if (!actionsEl || !fullText) return;
    actionsEl.innerHTML = "";
    const btn = document.createElement("button");
    btn.textContent = "🖼️ 打开 Canvas";
    btn.title = "把这条回复加载到右侧 Canvas";
    btn.addEventListener("click", () => {
      window.SteelCanvas && window.SteelCanvas.open(fullText, "来自对话");
    });
    actionsEl.appendChild(btn);
    // 有值得 Canvas 展示的内容（mermaid/代码/长结构），默认显示按钮
    if (window.SteelCanvas && window.SteelCanvas.isWorthy(fullText)) {
      actionsEl.parentElement?.parentElement?.classList.add("has-canvas-action");
    }
  }

  function maybeAutoOpenCanvas(fullText) {
    // 只自动打开一次 mermaid 图那种明显需要的；其他情况用户手动点
    if (!window.SteelCanvas) return;
    if (!/```mermaid/.test(fullText)) return;
    if (!window.SteelCanvas.isOpen()) {
      window.SteelCanvas.open(fullText, "来自对话（自动打开）");
    }
  }

  // Canvas 的 "发送到对话" 按钮事件
  window.addEventListener("steelg8:canvas-to-chat", (ev) => {
    const t = ev.detail && ev.detail.text;
    if (!t) return;
    UI.input.value = t;
    UI.input.focus();
  });

  function setRoutingHint(decision) {
    if (!decision) {
      UI.routing.textContent = "";
      return;
    }
    const layer = decision.layer || "-";
    const model = decision.model || "-";
    const provider = decision.provider || "-";
    UI.routing.textContent = `路由：${layer} · ${provider}/${model}`;
  }

  function setErrorHint(msg) {
    UI.error.textContent = msg || "";
  }

  // --------------- send flow ---------------

  let sendStartTs = 0;

  async function sendMessage(text) {
    // 防御：上一次 send 卡超过 2 分钟，强制重置（UI 挂住的保护）
    if (sending) {
      const stuck = Date.now() - sendStartTs > 120_000;
      if (!stuck) {
        setErrorHint("已有消息正在发送中…");
        return;
      }
      console.warn("steelg8: 检测到 send 卡住超过 2 分钟，强制重置");
      if (activeController) {
        try { activeController.abort(); } catch (_) {}
      }
      sending = false;
      UI.send.disabled = false;
    }
    text = (text || "").trim();
    if (!text) return;
    sendStartTs = Date.now();

    const finalMessage = text;

    sending = true;
    UI.send.disabled = true;
    UI.send.style.display = "none";
    if (UI.stop) UI.stop.style.display = "flex";
    setErrorHint("");
    setRoutingHint(null);
    activeController = new AbortController();
    // 标记本次是否已经收到 done；收到 done 后的 abort 是为了释放 TCP，不是用户取消
    let normallyFinished = false;
    let assistantFinalized = false;

    // 用户消息入列（UI 显示原始文本，发给后端的是拼接后的 finalMessage）
    addMessage("user", text);
    history.push({ role: "user", content: finalMessage });

    // assistant 占位
    const { bubble, metaEl, actionsEl } = addMessage("assistant", "", "");
    activeDeltaNode = bubble;
    activeFullBuffer = "";
    const activeActionsEl = actionsEl;

    const payload = {
      message: finalMessage,
      model: UI.model.value || null,
      conversationId: activeConversationId,  // null → 后端自动建一个新会话
      stream: true,
    };

    try {
      const streamURL = `${API_BASE}/chat/stream`;
      const resp = await fetch(streamURL, {
        method: "POST",
        headers: withKernelAuth({
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        }, streamURL),
        body: JSON.stringify(payload),
        signal: activeController ? activeController.signal : undefined,
      });

      if (!resp.ok || !resp.body) {
        throw new Error(`HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder("utf-8");
      let buffer = "";
      let lastDecision = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE: 事件以 \n\n 分隔
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const rawEvent = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const lines = rawEvent.split("\n");
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            const data = line.slice("data:".length).trim();
            if (!data) continue;
            let evt;
            try {
              evt = JSON.parse(data);
            } catch (_) {
              continue;
            }
            if (evt.type === "conversation") {
              // 后端回传的 conversation id — 如果是新建的会话，前端要记下来
              if (evt.conversationId && evt.conversationId !== activeConversationId) {
                activeConversationId = evt.conversationId;
                // 异步刷新侧栏，让新会话出现在列表里
                setTimeout(refreshConversations, 150);
              }
              if (evt.compression && evt.compression.compressed) {
                const c = evt.compression;
                flashRouting(`🗜 已压缩 ${c.count} 条 → 摘要 ${c.summaryTokens} tok`);
              }
            } else if (evt.type === "meta") {
              lastDecision = evt.decision;
              setRoutingHint(evt.decision);
            } else if (evt.type === "rag") {
              // RAG 命中数默默记下（meta 里加一个很小的 badge），不再渲染完整引用卡片
              const n = (evt.hits || []).length;
              if (n > 0) {
                metaEl.dataset.ragCount = String(n);
              }
            } else if (evt.type === "tool_start") {
              renderToolChip(bubble, {
                id: evt.id,
                name: evt.name,
                args: evt.args,
                state: "running",
              });
            } else if (evt.type === "tool_result") {
              updateToolChip(bubble, evt.id, evt.result);
            } else if (evt.type === "delta") {
              activeFullBuffer += evt.content || "";
              updateStreamingAssistant(bubble, activeFullBuffer);
            } else if (evt.type === "usage") {
              // 每次 assistant 收尾前到一条 usage，挂到当前气泡的 meta
              const u = evt.usage || {};
              const cost = Number(evt.costUsd || 0);
              const pieces = [];
              if (lastDecision) {
                pieces.push(`${lastDecision.provider || "?"}/${lastDecision.model || "?"}`);
              }
              if (u.prompt_tokens || u.completion_tokens) {
                pieces.push(
                  `${u.prompt_tokens || 0} in / ${u.completion_tokens || 0} out`
                );
              }
              if (cost > 0) {
                pieces.push(`<span class="cost">$${cost.toFixed(6)}</span>`);
              } else if (cost === 0 && (u.prompt_tokens || u.completion_tokens)) {
                pieces.push(`<span class="cost">free</span>`);
              }
              const ragN = Number(metaEl.dataset.ragCount || "0");
              if (ragN > 0) {
                pieces.push(`<span class="rag-badge" title="本轮检索命中 ${ragN} 条引用">📎 ${ragN}</span>`);
              }
              metaEl.innerHTML = pieces.join(" · ");
            } else if (evt.type === "error") {
              setErrorHint(`上游错误：${evt.error}`);
            } else if (evt.type === "done") {
              if (evt.full) {
                activeFullBuffer = evt.full;
              }
              if (!assistantFinalized) {
                finalizeAssistant(bubble, activeFullBuffer);
                assistantFinalized = true;
              }
              if (lastDecision) {
                const { provider, model, layer } = lastDecision;
                metaEl.textContent = `${provider || "mock"}/${model || "-"} · ${layer}`;
              }
              if (evt.source) {
                metaEl.textContent = (metaEl.textContent || "") + ` · ${evt.source}`;
              }
              // 回复含 mermaid 图 / 长代码块时才给 Canvas 入口（不再每条都挂按钮）
              if (window.SteelCanvas && window.SteelCanvas.isWorthy(activeFullBuffer)) {
                attachCanvasActions(activeActionsEl, activeFullBuffer);
              }
              maybeAutoOpenCanvas(activeFullBuffer);

              // 收到 done 就立刻切回 UI —— 不再等 reader EOF（某些 SSE 源不会主动关 TCP）
              normallyFinished = true;
              sending = false;
              UI.send.disabled = false;
              UI.send.style.display = "";
              if (UI.stop) UI.stop.style.display = "none";
              if (activeFullBuffer) {
                history.push({ role: "assistant", content: activeFullBuffer });
              }
              // 主动 abort 让 reader 退出循环（TCP 留给后端自然超时）
              if (activeController) {
                try { activeController.abort(); } catch (_) {}
              }
            }
          }
        }
      }

      // flush 剩余
      if (activeFullBuffer && !assistantFinalized) {
        finalizeAssistant(bubble, activeFullBuffer);
        history.push({ role: "assistant", content: activeFullBuffer });
      } else if (!assistantFinalized) {
        bubble.innerHTML = "<em>（空响应）</em>";
      }
    } catch (err) {
      // 用户主动停止不算错误；done 后内部 abort 不是取消
      if (err && (err.name === "AbortError" || /aborted/i.test(err.message || ""))) {
        if (normallyFinished) {
          // 正常结束后的清理 abort —— 什么都不做
        } else if (activeFullBuffer) {
          if (!assistantFinalized) {
            finalizeAssistant(bubble, activeFullBuffer + "\n\n_（已停止）_");
            assistantFinalized = true;
          }
          history.push({ role: "assistant", content: activeFullBuffer });
        } else {
          bubble.innerHTML = "<em>（已停止，未生成内容）</em>";
        }
      } else {
        console.error(err);
        setErrorHint(`连接失败：${err.message || err}`);
        bubble.innerHTML = `<em>请求失败：${
          err.message || err
        }。请检查 Python 内核是否已启动。</em>`;
      }
    } finally {
      sending = false;
      UI.send.disabled = false;
      UI.send.style.display = "";
      if (UI.stop) UI.stop.style.display = "none";
      activeController = null;
      activeDeltaNode = null;
    }
  }

  // --------------- events ---------------

  UI.send.addEventListener("click", () => {
    const text = UI.input.value;
    UI.input.value = "";
    sendMessage(text);
  });

  if (UI.convNewBtn) {
    UI.convNewBtn.addEventListener("click", startNewConversation);
  }

  // 停止按钮：中断当前 SSE，已输出的内容保留并入 DB（server 侧 finally 分支会处理）
  function stopStreaming() {
    if (!sending || !activeController) return;
    try {
      activeController.abort();
    } catch (_) {}
    setRoutingHint(null);
  }
  if (UI.stop) {
    UI.stop.addEventListener("click", stopStreaming);
  }
  // Esc 停止（只在发送中 + 焦点不在 textarea 或 textarea 空时触发，避免误中断输入）
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!sending) return;
    e.preventDefault();
    stopStreaming();
  });

  // 便签 events
  if (UI.scratchNote) {
    UI.scratchNote.addEventListener("input", scheduleScratchSave);
    UI.scratchNote.addEventListener("blur", doScratchSave);
  }
  if (UI.scratchToNotes) {
    UI.scratchToNotes.addEventListener("click", saveNoteToAppleNotes);
  }
  if (UI.scratchClear) {
    UI.scratchClear.addEventListener("click", () => {
      if (UI.scratchNote && UI.scratchNote.value &&
          !confirm("清空便签？")) return;
      clearScratchNote();
    });
  }
  if (UI.scratchToggle) {
    UI.scratchToggle.addEventListener("click", () => {
      const mode = UI.scratch.getAttribute("data-mode") === "collapsed" ? "sidebar" : "collapsed";
      UI.scratch.setAttribute("data-mode", mode);
      applyLayout({ persist: true });
    });
  }
  // ⌘⇧N 展开并聚焦便签
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "N" && UI.scratchNote) {
      e.preventDefault();
      UI.scratch.setAttribute("data-mode", "sidebar");
      applyLayout({ persist: true });
      UI.scratchNote.focus();
    }
  });

  UI.input.addEventListener("keydown", (e) => {
    // 回车发送；Shift+回车换行；IME 输入法组字状态不拦（e.isComposing）
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      const text = UI.input.value;
      UI.input.value = "";
      sendMessage(text);
    }
  });

  // 欢迎页 hint 按钮
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".hints button");
    if (!btn) return;
    const prompt = btn.getAttribute("data-prompt") || "";
    UI.input.value = prompt.replace(/\\n/g, "\n");
    UI.input.focus();
  });

  UI.reload.addEventListener("click", async () => {
    UI.reload.disabled = true;
    try {
      // 让内核热加载 providers，再拉一次列表
      await fetchJSON("/providers/reload", { method: "POST" }, 7000).catch(() => {});
      await refreshHealth();
      await refreshProviders();
    } finally {
      UI.reload.disabled = false;
    }
  });

  if (UI.syncModelsBtn) {
    UI.syncModelsBtn.addEventListener("click", async () => {
      UI.syncModelsBtn.disabled = true;
      const origText = UI.syncModelsBtn.textContent;
      UI.syncModelsBtn.textContent = "⟲ 同步中…";
      try {
        const pj = await fetchJSON("/providers", { cache: "no-store" }, 5000);
        const ready = (pj.providers || []).filter((p) => p.ready);
        const results = await Promise.allSettled(
          ready.map((p) =>
            fetchJSON(`/providers/${p.name}/sync-models`, { method: "POST" }, 20000)
          )
        );
        const summary = results.map((r, i) => {
          const name = ready[i].name;
          if (r.status === "fulfilled" && r.value && r.value.ok) {
            return `${name}: ${r.value.count}`;
          }
          return `${name}: 失败`;
        }).join(" · ");
        flashRouting(`已同步 — ${summary}`);
        // 同步完 reload + 刷下拉
        await fetchJSON("/providers/reload", { method: "POST" }, 7000).catch(() => {});
        await refreshProviders();
      } catch (e) {
        flashRouting(`同步失败：${e.message || e}`);
      } finally {
        UI.syncModelsBtn.textContent = origText;
        UI.syncModelsBtn.disabled = false;
      }
    });
  }

  // 输入区高度现在由可拖拽 composer 控制；清掉历史 inline height，避免和布局变量打架。
  UI.input.addEventListener("input", () => {
    UI.input.style.height = "";
  });

  // --------------- init ---------------

  (async function init() {
    await refreshHealth();
    await refreshProviders();
    await loadScratchNote();
    await refreshProject();
    await refreshConversations();
    setInterval(refreshHealth, 8000);
    setInterval(refreshProject, 3000);
    setInterval(refreshConversations, 30_000);
    // 下拉框如果 30s 内没成功拉到 providers，就重试（防止 kernel 启动慢 / 前端来早了）
    setInterval(() => {
      if (Date.now() - lastProvidersOK > 30_000) refreshProviders();
    }, 10_000);

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshProject();
      }
    });
    window.addEventListener("focus", () => {
      refreshProject();
    });
  })();
})();
