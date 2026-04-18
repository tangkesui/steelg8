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

  // 允许通过 URL hash 覆盖 kernel 端口，未来多进程 / 其他测试用
  const KERNEL_PORT = (() => {
    try {
      const hash = new URL(location.href).hash;
      const m = hash.match(/port=(\d+)/);
      if (m) return m[1];
    } catch (_) {}
    return "8765";
  })();

  const API_BASE = `http://127.0.0.1:${KERNEL_PORT}`;

  const $ = (id) => document.getElementById(id);

  const UI = {
    messages: $("messages"),
    input: $("input"),
    send: $("send"),
    model: $("model-picker"),
    reload: $("reload-providers"),
    dot: $("health-dot"),
    routing: $("routing-hint"),
    error: $("error-hint"),
    usagePill: $("usage-pill"),
    scratch: $("scratch"),
    scratchNote: $("scratch-note"),
    scratchSaveState: $("scratch-save-state"),
    scratchToggle: $("scratch-toggle"),
    scratchToNotes: $("scratch-to-notes"),
    scratchClear: $("scratch-clear"),
    attachRow: $("attach-chip-row"),
    projectPill: $("project-pill"),
    projectName: $("project-name"),
    projectChunks: $("project-chunks"),
    sidebarProject: $("sidebar-project"),
    spBody: $("sp-body"),
    projectOpenBtn: $("project-open-btn"),
  };

  // 是否运行在 WKWebView 里（有 webkit bridge）
  const HAS_SWIFT_BRIDGE = !!(window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.steelg8);
  function swiftBridge(action, payload) {
    if (HAS_SWIFT_BRIDGE) {
      const msg = Object.assign({ action }, payload || {});
      window.webkit.messageHandlers.steelg8.postMessage(msg);
      return true;
    }
    return false;
  }

  // 独立召唤窗模式（#scratch）：把 chat 列隐藏，侧栏撑满窗口
  const SCRATCH_ONLY = location.hash.replace(/^#/, "") === "scratch";
  if (SCRATCH_ONLY) {
    document.documentElement.classList.add("scratch-only-mode");
  }

  /** 对话历史（不含当前 turn） */
  const history = [];

  let sending = false;
  let activeDeltaNode = null;
  let activeFullBuffer = "";

  // Scratch 便签（单文本）
  let scratchNoteText = "";
  let scratchSaveTimer = null;

  // --------------- bootstrap ---------------

  async function refreshHealth() {
    try {
      const r = await fetch(`${API_BASE}/health`, { cache: "no-store" });
      if (r.ok) {
        const j = await r.json();
        UI.dot.classList.remove("dot-off");
        UI.dot.classList.add("dot-on");
        UI.dot.title = `${j.mode || "ok"}\ndefault: ${j.defaultModel || "—"}`;
        return j;
      }
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
      const r = await fetch(`${API_BASE}/scratch/note`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const remote = (j && j.text) || "";
      // 避免用户正在输入时被远端覆盖：只在本地没改过 / 空 / 首次时同步
      if (!scratchNoteText && !document.activeElement === UI.scratchNote) {
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
      const r = await fetch(`${API_BASE}/scratch/note`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) {
        setSaveState("保存失败");
        return;
      }
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

  // ================== Project（RAG 项目）==================

  async function refreshProject() {
    try {
      const r = await fetch(`${API_BASE}/project`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const active = j.active;
      renderProjectPill(active);
      renderSidebarProject(active);
    } catch (_) {}
  }

  function renderProjectPill(active) {
    if (!UI.projectPill) return;
    if (!active) {
      UI.projectPill.classList.add("is-empty");
      UI.projectPill.classList.remove("indexing", "error");
      if (UI.projectName) UI.projectName.textContent = "未选项目";
      if (UI.projectChunks) UI.projectChunks.textContent = "";
      UI.projectPill.title = "点击打开一个文件夹";
      return;
    }
    UI.projectPill.classList.remove("is-empty");
    if (UI.projectName) UI.projectName.textContent = active.name || "项目";
    const idx = active.indexStatus || {};
    const isRunning = idx.state === "running";
    const isError = idx.state === "error";
    UI.projectPill.classList.toggle("indexing", isRunning);
    UI.projectPill.classList.toggle("error", isError);
    let chunkLabel;
    if (isRunning) chunkLabel = `索引中 ${idx.embedded_chunks || 0}/${idx.total_chunks || "?"}`;
    else if (isError) chunkLabel = "索引失败 ⚠";
    else chunkLabel = `${active.chunkCount} chunks`;
    if (UI.projectChunks) UI.projectChunks.textContent = chunkLabel;
    const tip = [
      `项目：${active.name}`,
      `路径：${active.path}`,
      `chunks：${active.chunkCount}`,
      `状态：${idx.state || "?"}`,
    ];
    if (idx.error) tip.push(`错误：${idx.error}`);
    UI.projectPill.title = tip.join("\n");
  }

  function renderSidebarProject(active) {
    if (!UI.spBody) return;
    if (!active) {
      UI.spBody.classList.add("sp-empty");
      UI.spBody.innerHTML =
        '<div class="sp-hint">还没选项目。点「打开…」选一个文件夹，steelg8 会索引里面的 .md / .txt 供对话引用。</div>';
      return;
    }
    UI.spBody.classList.remove("sp-empty");
    const idx = active.indexStatus || {};
    const state = idx.state || "idle";
    let stateLabel = "就绪";
    let stateCls = "";
    if (state === "running") {
      stateLabel = `索引中 ${idx.embedded_chunks || 0}/${idx.total_chunks || "?"}`;
      stateCls = "sp-running";
    } else if (state === "error") {
      stateLabel = "索引失败";
      stateCls = "sp-error";
    }
    const esc = (s) => window.SteelMarkdown.escape(String(s || ""));
    const errorBlock = idx.error
      ? `<div class="sp-error-msg">${esc(idx.error)}</div>`
      : "";
    UI.spBody.innerHTML = `
      <div class="sp-name">${esc(active.name)}</div>
      <div class="sp-path" title="${esc(active.path)}">${esc(active.path)}</div>
      <div class="sp-status ${stateCls}">
        <span class="sp-state">${esc(stateLabel)}</span>
        <span class="sp-chunks">${active.chunkCount || 0} chunks</span>
      </div>
      ${errorBlock}
      <div class="sp-actions">
        <button data-action="reindex">重新索引</button>
        <button data-action="change">换目录</button>
        <button data-action="close" class="danger">关闭</button>
      </div>
    `;
    UI.spBody.querySelectorAll("button[data-action]").forEach((b) => {
      b.addEventListener("click", () => handleProjectAction(b.getAttribute("data-action")));
    });
  }

  function handleProjectAction(action) {
    switch (action) {
      case "reindex":
        if (!swiftBridge("reindexProject")) {
          fetch(`${API_BASE}/project/reindex`, { method: "POST" }).catch(() => {});
        }
        setTimeout(refreshProject, 500);
        break;
      case "change":
        if (!swiftBridge("openProjectPicker")) {
          flashRouting("菜单栏 → 打开项目文件夹…");
        }
        break;
      case "close":
        if (!confirm("关闭当前项目？索引缓存仍保留，下次可直接打开。")) return;
        if (!swiftBridge("closeProject")) {
          fetch(`${API_BASE}/project/close`, { method: "POST" }).catch(() => {});
        }
        setTimeout(refreshProject, 300);
        break;
    }
  }

  if (UI.projectOpenBtn) {
    UI.projectOpenBtn.addEventListener("click", () => {
      if (!swiftBridge("openProjectPicker")) {
        flashRouting("菜单栏 → 打开项目文件夹…（只在 WKWebView 里能直接弹面板）");
      }
    });
  }

  if (UI.projectPill) {
    UI.projectPill.addEventListener("click", () => {
      if (!swiftBridge("openProjectPicker")) {
        flashRouting("菜单栏 → 打开项目文件夹…（⌘⇧O）");
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
      chip.innerHTML = `
        <span class="rag-path" title="${window.SteelMarkdown.escape(h.relPath)}">📎 ${window.SteelMarkdown.escape(h.relPath)}</span>
        <span class="rag-score">${h.score}</span>
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
    pop.innerHTML = `
      <div class="rp-head">
        <span>${window.SteelMarkdown.escape(hit.relPath)} · chunk#${hit.chunkIdx}</span>
        <span>score ${hit.score}</span>
      </div>
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

  async function refreshUsagePill() {
    if (!UI.usagePill) return;
    try {
      const r = await fetch(`${API_BASE}/usage/summary`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      const s = j.session || {};
      const today = j.today || {};
      const mainEl = UI.usagePill.querySelector(".usage-main");
      const subEl = UI.usagePill.querySelector(".usage-sub");
      if (mainEl) mainEl.textContent = `$${Number(s.cost_usd || 0).toFixed(4)}`;
      if (subEl) {
        const tk = (s.total || 0);
        subEl.textContent = `· ${formatTokens(tk)} · 今 $${Number(today.cost_usd || 0).toFixed(2)}`;
      }
      const cny = (j.usdToCny || 7.2);
      const title = [
        `本次会话：$${Number(s.cost_usd || 0).toFixed(6)}  （≈ ¥${(
          Number(s.cost_usd || 0) * cny
        ).toFixed(4)}）`,
        `  ${s.total || 0} tokens · ${s.calls || 0} 次调用`,
        ``,
        `今日：$${Number(today.cost_usd || 0).toFixed(4)}  （≈ ¥${(
          Number(today.cost_usd || 0) * cny
        ).toFixed(2)}）`,
        `  ${today.total || 0} tokens · ${today.calls || 0} 次调用`,
      ];
      if (j.sessionBreakdown && j.sessionBreakdown.length) {
        title.push("");
        title.push("本次会话按模型拆分：");
        j.sessionBreakdown.slice(0, 5).forEach((b) => {
          title.push(
            `  · ${b.model} → ${b.calls} 次, ${b.prompt + b.completion} tok, $${Number(b.cost_usd).toFixed(6)}`
          );
        });
      }
      UI.usagePill.title = title.join("\n");
    } catch (_) {}
  }

  function formatTokens(n) {
    n = Number(n || 0);
    if (n < 1000) return `${n} tok`;
    if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
    return `${(n / 1_000_000).toFixed(2)}M`;
  }

  async function refreshProviders() {
    try {
      const r = await fetch(`${API_BASE}/providers`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
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
    const argsPreview = compactJSON(call.args || {}, 120);
    chip.innerHTML = `
      <span class="tool-icon">🛠️</span>
      <span><span class="tool-name">${window.SteelMarkdown.escape(call.name || "?")}</span>
        <span class="tool-args">${window.SteelMarkdown.escape(argsPreview)}</span></span>
    `;
    row.appendChild(chip);
  }

  function updateToolChip(bubble, id, result) {
    const content = bubble.parentElement;
    const chip = content.querySelector(`.tool-chip[data-tool-id="${id || ""}"]`);
    if (!chip) return;
    chip.classList.remove("tool-running");
    const isErr = result && result.error;
    chip.classList.add(isErr ? "tool-err" : "tool-ok");
    const preview = compactJSON(result || {}, 220);
    const resultEl = document.createElement("div");
    resultEl.className = "tool-result";
    resultEl.textContent = (isErr ? "❌ " : "✓ ") + preview;
    chip.appendChild(resultEl);

    // 如果结果里有 "output" 指向一个文件，加"打开 / Finder"两个快捷按钮
    const outputPath = !isErr && result && (result.output || result.path);
    if (outputPath && typeof outputPath === "string" && _looksLikeOfficePath(outputPath)) {
      const row = document.createElement("div");
      row.className = "tool-file-actions";

      const openBtn = document.createElement("button");
      openBtn.textContent = "📂 打开";
      openBtn.title = `在默认应用打开 ${outputPath}`;
      openBtn.addEventListener("click", () => {
        if (!swiftBridge("openFile", { path: outputPath })) {
          flashRouting("WebView 外无法调用系统打开");
        }
      });

      const revealBtn = document.createElement("button");
      revealBtn.textContent = "🔍 Finder";
      revealBtn.title = `在 Finder 里定位 ${outputPath}`;
      revealBtn.addEventListener("click", () => {
        swiftBridge("revealInFinder", { path: outputPath });
      });

      row.appendChild(openBtn);
      row.appendChild(revealBtn);
      chip.appendChild(row);
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
      sending = false;
      UI.send.disabled = false;
    }
    text = (text || "").trim();
    if (!text) return;
    sendStartTs = Date.now();

    const finalMessage = text;

    sending = true;
    UI.send.disabled = true;
    setErrorHint("");
    setRoutingHint(null);

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
      history: history.slice(0, -1), // 不重复发送最后一条 user
      stream: true,
    };

    try {
      const resp = await fetch(`${API_BASE}/chat/stream`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(payload),
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
            if (evt.type === "meta") {
              lastDecision = evt.decision;
              setRoutingHint(evt.decision);
            } else if (evt.type === "rag") {
              // 召回到相关项目 chunks，挂到 assistant 气泡下
              renderRagChips(evt.hits || [], bubble);
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
              metaEl.innerHTML = pieces.join(" · ");
              // 刷新 header pill
              refreshUsagePill();
            } else if (evt.type === "error") {
              setErrorHint(`上游错误：${evt.error}`);
            } else if (evt.type === "done") {
              if (evt.full) {
                activeFullBuffer = evt.full;
              }
              finalizeAssistant(bubble, activeFullBuffer);
              if (lastDecision) {
                const { provider, model, layer } = lastDecision;
                metaEl.textContent = `${provider || "mock"}/${model || "-"} · ${layer}`;
              }
              if (evt.source) {
                metaEl.textContent = (metaEl.textContent || "") + ` · ${evt.source}`;
              }
              // 完整内容就绪 → 给消息挂上 Canvas 动作 + 按需自动打开
              attachCanvasActions(activeActionsEl, activeFullBuffer);
              maybeAutoOpenCanvas(activeFullBuffer);
            }
          }
        }
      }

      // flush 剩余
      if (activeFullBuffer) {
        finalizeAssistant(bubble, activeFullBuffer);
        history.push({ role: "assistant", content: activeFullBuffer });
      } else {
        bubble.innerHTML = "<em>（空响应）</em>";
      }
    } catch (err) {
      console.error(err);
      setErrorHint(`连接失败：${err.message || err}`);
      bubble.innerHTML = `<em>请求失败：${
        err.message || err
      }。请检查 Python 内核是否已启动。</em>`;
    } finally {
      sending = false;
      UI.send.disabled = false;
      activeDeltaNode = null;
    }
  }

  // --------------- events ---------------

  UI.send.addEventListener("click", () => {
    const text = UI.input.value;
    UI.input.value = "";
    sendMessage(text);
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
    });
  }
  // ⌘⇧N 展开并聚焦便签
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "N" && UI.scratchNote) {
      e.preventDefault();
      UI.scratch.setAttribute("data-mode", "sidebar");
      UI.scratchNote.focus();
    }
  });

  UI.input.addEventListener("keydown", (e) => {
    // ⌘/Ctrl + Enter 发送
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
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
      await fetch(`${API_BASE}/providers/reload`, { method: "POST" }).catch(() => {});
      await refreshHealth();
      await refreshProviders();
    } finally {
      UI.reload.disabled = false;
    }
  });

  // 自动调整输入框高度
  UI.input.addEventListener("input", () => {
    UI.input.style.height = "auto";
    UI.input.style.height = Math.min(UI.input.scrollHeight, 200) + "px";
  });

  // --------------- init ---------------

  (async function init() {
    await refreshHealth();
    await refreshProviders();
    await refreshUsagePill();
    await loadScratchNote();
    await refreshProject();
    // 每 8s health、15s usage、3s project（索引跑时能看到进度）
    setInterval(refreshHealth, 8000);
    setInterval(refreshUsagePill, 15000);
    setInterval(refreshProject, 3000);

    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshUsagePill();
        refreshProject();
      }
    });
    window.addEventListener("focus", () => {
      refreshUsagePill();
      refreshProject();
    });
  })();
})();
