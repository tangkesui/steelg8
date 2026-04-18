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
  };

  /** 对话历史（不含当前 turn） */
  const history = [];

  let sending = false;
  let activeDeltaNode = null;
  let activeFullBuffer = "";

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
        <div class="meta"></div>
      </div>
    `;
    const bubble = node.querySelector(".bubble");
    const metaEl = node.querySelector(".meta");

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
    return { node, bubble, metaEl };
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

    sending = true;
    UI.send.disabled = true;
    setErrorHint("");
    setRoutingHint(null);

    // 用户消息入列
    addMessage("user", text);
    history.push({ role: "user", content: text });

    // assistant 占位
    const { bubble, metaEl } = addMessage("assistant", "", "");
    activeDeltaNode = bubble;
    activeFullBuffer = "";

    const payload = {
      message: text,
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
    // 每 8 秒 ping 一次 health，每 15 秒刷 usage
    setInterval(refreshHealth, 8000);
    setInterval(refreshUsagePill, 15000);
  })();
})();
