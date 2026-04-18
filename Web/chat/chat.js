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
    scratchList: $("scratch-list"),
    scratchInput: $("scratch-input"),
    scratchAdd: $("scratch-add"),
    scratchToggle: $("scratch-toggle"),
    attachRow: $("attach-chip-row"),
  };

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

  // Scratch 状态
  let scratchEntries = [];              // 后端返回的活跃 entry
  const attachedIds = new Set();        // 被"附加"的 entry id，下次发送会一起注入

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

  // ================== Scratch 捕获台 ==================

  async function refreshScratch() {
    try {
      const r = await fetch(`${API_BASE}/scratch`, { cache: "no-store" });
      if (!r.ok) return;
      const j = await r.json();
      scratchEntries = j.items || [];
      // 清理掉被删除的 attachedIds
      for (const id of [...attachedIds]) {
        if (!scratchEntries.find((e) => e.id === id)) attachedIds.delete(id);
      }
      renderScratch();
      renderAttachChips();
    } catch (e) {
      console.error("refreshScratch failed", e);
    }
  }

  function renderScratch() {
    if (!UI.scratchList) return;
    if (!scratchEntries.length) {
      UI.scratchList.innerHTML =
        '<div class="scratch-empty">还没有内容。在下面随手写一条。</div>';
      return;
    }
    // 时间轴：最旧在上，最新在下
    const html = scratchEntries
      .map((e) => {
        const attached = attachedIds.has(e.id);
        const savedCls = e.saved ? " is-saved" : "";
        const attachCls = attached ? " is-attached" : "";
        const ts = friendlyTime(e.ts);
        const text = window.SteelMarkdown.escape(e.text);
        return `
        <div class="scratch-item${savedCls}${attachCls}" data-id="${e.id}">
          <div class="si-text">${text}</div>
          <div class="si-meta">${ts}${e.origin && e.origin !== "manual" ? " · " + e.origin : ""}</div>
          <div class="si-actions">
            <button data-action="send" title="发送到对话">🔁 发送</button>
            <button data-action="attach" class="${attached ? "active" : ""}" title="作为上下文附加">📎 ${attached ? "已附加" : "附加"}</button>
            <button data-action="card" title="存为知识卡片">💾 ${e.saved ? "已存" : "存卡片"}</button>
            <button data-action="organize" title="让 AI 整理这段">✨ 整理</button>
            <button data-action="delete" class="danger" title="删除">✕</button>
          </div>
        </div>`;
      })
      .join("");
    UI.scratchList.innerHTML = html;
    // 滚到底部（最新一条）
    UI.scratchList.scrollTop = UI.scratchList.scrollHeight;
  }

  function renderAttachChips() {
    if (!UI.attachRow) return;
    if (!attachedIds.size) {
      UI.attachRow.innerHTML = "";
      return;
    }
    const chips = [...attachedIds]
      .map((id) => scratchEntries.find((e) => e.id === id))
      .filter(Boolean)
      .map((e) => {
        const preview = window.SteelMarkdown.escape(
          (e.text || "").slice(0, 30) + (e.text.length > 30 ? "…" : "")
        );
        return `<span class="attach-chip" data-id="${e.id}">
          <span class="chip-text">📎 ${preview}</span>
          <span class="chip-x" data-id="${e.id}">×</span>
        </span>`;
      })
      .join("");
    UI.attachRow.innerHTML = chips;
  }

  function friendlyTime(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      const now = new Date();
      const diff = (now - d) / 1000;
      if (diff < 60) return "刚刚";
      if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
      if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
      return d.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_) {
      return iso;
    }
  }

  async function addScratch(text) {
    text = (text || "").trim();
    if (!text) return;
    try {
      const r = await fetch(`${API_BASE}/scratch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, origin: "manual" }),
      });
      if (!r.ok) {
        setErrorHint(`追加失败：HTTP ${r.status}`);
        return;
      }
      await refreshScratch();
    } catch (e) {
      setErrorHint(`追加失败：${e.message || e}`);
    }
  }

  async function deleteScratch(id) {
    try {
      await fetch(`${API_BASE}/scratch/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      attachedIds.delete(id);
      await refreshScratch();
    } catch (_) {}
  }

  async function markScratchSaved(id) {
    try {
      await fetch(`${API_BASE}/scratch/${encodeURIComponent(id)}/save`, {
        method: "POST",
      });
      await refreshScratch();
    } catch (_) {}
  }

  async function organizeScratch(id) {
    const entry = scratchEntries.find((e) => e.id === id);
    if (!entry) return;
    showOrganizeModal(entry, null, "loading");
    try {
      const r = await fetch(
        `${API_BASE}/scratch/${encodeURIComponent(id)}/organize`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        }
      );
      const j = await r.json();
      showOrganizeModal(entry, j, "ok");
      refreshUsagePill(); // organize 也算一次计费调用
    } catch (e) {
      showOrganizeModal(entry, { organized: "整理失败：" + (e.message || e) }, "error");
    }
  }

  function showOrganizeModal(entry, result, state) {
    const existing = document.querySelector(".modal-backdrop");
    if (existing) existing.remove();

    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";

    const loadingHtml = '<div style="text-align:center;color:var(--text-dim);padding:20px">AI 整理中…</div>';
    const body =
      state === "loading"
        ? loadingHtml
        : `
      <div class="modal-section">
        <h4>原文</h4>
        <div class="original-text">${window.SteelMarkdown.escape(entry.text)}</div>
      </div>
      <div class="modal-section">
        <h4>AI 整理后</h4>
        <div>${window.SteelMarkdown.render(result.organized || "")}</div>
      </div>
    `;

    const cost = result && result.costUsd ? `花费 $${Number(result.costUsd).toFixed(6)} · ${result.model || ""}` : "";

    backdrop.innerHTML = `
      <div class="modal">
        <div class="modal-head">
          <h3>✨ AI 整理这条</h3>
          <button class="modal-close" data-action="close">×</button>
        </div>
        <div class="modal-body">${body}</div>
        <div class="modal-foot">
          <span class="cost-hint">${cost}</span>
          ${state === "ok"
            ? `<button data-action="replace">替换原文</button>
               <button data-action="append">追加到捕获台</button>
               <button class="primary" data-action="send">发送到对话</button>`
            : `<button data-action="close">关闭</button>`}
        </div>
      </div>`;

    backdrop.addEventListener("click", async (e) => {
      const action = e.target.getAttribute("data-action");
      if (!action && e.target !== backdrop) return;
      if (action === "close" || e.target === backdrop) {
        backdrop.remove();
        return;
      }
      if (!result) return;
      if (action === "replace") {
        // 用整理后的文本新增一条，删除原条
        await fetch(`${API_BASE}/scratch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: result.organized,
            origin: "ai-organize",
            tags: entry.tags,
          }),
        });
        await deleteScratch(entry.id);
        backdrop.remove();
      } else if (action === "append") {
        await fetch(`${API_BASE}/scratch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text: result.organized,
            origin: "ai-organize",
            tags: entry.tags,
          }),
        });
        await refreshScratch();
        backdrop.remove();
      } else if (action === "send") {
        backdrop.remove();
        UI.input.value = result.organized;
        UI.input.focus();
      }
    });

    document.body.appendChild(backdrop);
  }

  // 用户点击 scratch list 里的按钮
  function handleScratchClick(ev) {
    const btn = ev.target.closest("button[data-action]");
    if (!btn) return;
    const card = ev.target.closest(".scratch-item");
    if (!card) return;
    const id = card.getAttribute("data-id");
    const entry = scratchEntries.find((e) => e.id === id);
    if (!entry) return;
    const action = btn.getAttribute("data-action");

    switch (action) {
      case "send":
        // 把 scratch 文本塞进输入框，不直接发
        UI.input.value = entry.text;
        UI.input.focus();
        break;
      case "attach":
        if (attachedIds.has(id)) attachedIds.delete(id);
        else attachedIds.add(id);
        renderScratch();
        renderAttachChips();
        break;
      case "card":
        markScratchSaved(id);
        break;
      case "organize":
        organizeScratch(id);
        break;
      case "delete":
        if (confirm("删除这条 scratch？")) deleteScratch(id);
        break;
    }
  }

  function handleAttachChipClick(ev) {
    const x = ev.target.closest(".chip-x");
    if (!x) return;
    const id = x.getAttribute("data-id");
    if (id) {
      attachedIds.delete(id);
      renderScratch();
      renderAttachChips();
    }
  }

  // ================== usage pill ==================

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
        const models = p.models || [];
        if (!models.length) return;
        const disabled = p.ready ? "" : " disabled";
        opts.push(`<optgroup label="${p.name}${p.ready ? "" : " (未就绪)"}">`);
        models.forEach((m) => {
          const selected = m === defaultModel ? " selected" : "";
          opts.push(`<option value="${m}"${selected}${disabled}>${m}</option>`);
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

  async function sendMessage(text) {
    if (sending) return;
    text = (text || "").trim();
    if (!text) return;

    // 如果有附加的 scratch，拼到 message 前面做为背景资料
    const attachedEntries = [...attachedIds]
      .map((id) => scratchEntries.find((e) => e.id === id))
      .filter(Boolean);
    let finalMessage = text;
    if (attachedEntries.length) {
      const ctxBlocks = attachedEntries
        .map((e, i) => `[${i + 1}] ${e.text}`)
        .join("\n\n");
      finalMessage =
        `【背景资料（来自捕获台）】\n${ctxBlocks}\n\n【我的问题】\n${text}`;
      // 发送后清空附加，让用户下一条默认不带
      attachedIds.clear();
      renderScratch();
      renderAttachChips();
    }

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

  // Scratch events
  if (UI.scratchAdd) {
    UI.scratchAdd.addEventListener("click", async () => {
      const t = UI.scratchInput.value;
      UI.scratchInput.value = "";
      await addScratch(t);
    });
  }
  if (UI.scratchInput) {
    UI.scratchInput.addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
        e.preventDefault();
        const t = UI.scratchInput.value;
        UI.scratchInput.value = "";
        addScratch(t);
      }
    });
  }
  if (UI.scratchList) {
    UI.scratchList.addEventListener("click", handleScratchClick);
  }
  if (UI.scratchToggle) {
    UI.scratchToggle.addEventListener("click", () => {
      const mode = UI.scratch.getAttribute("data-mode") === "collapsed" ? "sidebar" : "collapsed";
      UI.scratch.setAttribute("data-mode", mode);
    });
  }
  if (UI.attachRow) {
    UI.attachRow.addEventListener("click", handleAttachChipClick);
  }
  // ⌘⇧N 聚焦到 scratch 输入框（hotkey 在 Swift 也会召唤窗口）
  document.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === "N" && UI.scratchInput) {
      e.preventDefault();
      UI.scratch.setAttribute("data-mode", "sidebar");
      UI.scratchInput.focus();
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
    await refreshScratch();
    // 每 8 秒 health，每 15 秒 usage，每 5 秒 scratch（同步给多窗口快一些）
    setInterval(refreshHealth, 8000);
    setInterval(refreshUsagePill, 15000);
    setInterval(refreshScratch, 5000);

    // 窗口重新被看见时立刻刷一轮，多窗口之间同步更即时
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        refreshScratch();
        refreshUsagePill();
      }
    });
    window.addEventListener("focus", () => {
      refreshScratch();
      refreshUsagePill();
    });
  })();
})();
