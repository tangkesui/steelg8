/*
 * steelg8 Canvas（右侧画板）
 * ---------------------------
 *
 * 职责：
 *   - 接收来自 chat 的内容（markdown 字符串），在右侧面板渲染
 *   - 三种模式：preview / source / split
 *   - Markdown 走 SteelMarkdown；```mermaid 块走 mermaid.js；```代码块保留 <pre>
 *   - 源码模式下可编辑 textarea，切回 preview/split 时实时重渲
 *
 * 不负责：
 *   - 文件存盘（Phase 2 做"另存为"+版本历史）
 *   - HTML 预览 iframe（Phase 3）
 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);

  const mermaidReady = (function loadMermaid() {
    return new Promise((resolve) => {
      function check() {
        if (window.mermaid) {
          try {
            window.mermaid.initialize({
              startOnLoad: false,
              theme: detectDarkMode() ? "dark" : "default",
              securityLevel: "loose",
              fontFamily:
                "-apple-system, 'SF Pro Text', 'PingFang SC', sans-serif",
            });
          } catch (_) {}
          resolve(window.mermaid);
        } else {
          setTimeout(check, 80);
        }
      }
      check();
    });
  })();

  function detectDarkMode() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  const UI = {
    canvas: $("canvas"),
    preview: $("canvas-preview"),
    source: $("canvas-source"),
    hint: $("canvas-source-hint"),
    close: $("canvas-close"),
    copy: $("canvas-copy"),
    sendChat: $("canvas-send-chat"),
    modeButtons: document.querySelectorAll(".canvas-mode"),
  };

  const state = {
    content: "",
    sourceLabel: "",      // 来自哪条消息 / 文件 / 等
    mode: "preview",       // preview | source | split
  };

  let renderSeq = 0;     // 防止慢的 mermaid 回调覆盖后面的渲染

  function open(content, label) {
    state.content = content || "";
    state.sourceLabel = label || "草稿";
    UI.source.value = state.content;
    setMode(state.mode || "preview", { skipRender: false });
    UI.canvas.setAttribute("data-mode", state.mode);
    UI.hint.textContent = state.sourceLabel;
    // 有 mermaid 时自动用 split 方便用户对照
    if (/```mermaid/.test(state.content) && state.mode === "preview") {
      setMode("preview");
    }
  }

  function close() {
    UI.canvas.setAttribute("data-mode", "closed");
  }

  function isOpen() {
    const m = UI.canvas.getAttribute("data-mode");
    return m && m !== "closed";
  }

  function setMode(mode, opts) {
    state.mode = mode;
    UI.canvas.setAttribute("data-mode", mode);
    UI.modeButtons.forEach((b) =>
      b.classList.toggle("active", b.getAttribute("data-canvas-mode") === mode)
    );
    if (!opts || !opts.skipRender) renderPreview();
  }

  async function renderPreview() {
    if (!UI.preview) return;
    const seq = ++renderSeq;
    const text = state.content || "";

    // 1) 先把 ```mermaid 块抽出来替换成占位 div
    const mermaidBlocks = [];
    const withoutMermaid = text.replace(
      /```mermaid\s*\n([\s\S]*?)\n```/g,
      (_m, code) => {
        const idx = mermaidBlocks.length;
        mermaidBlocks.push(code.trim());
        return `<!--MERMAID_SLOT_${idx}-->`;
      }
    );

    // 2) 其余走现有 Markdown 渲染
    let html = window.SteelMarkdown.render(withoutMermaid);

    // 3) 把 mermaid 占位还原成 .mermaid 容器
    html = html.replace(/<!--MERMAID_SLOT_(\d+)-->/g, (_m, i) => {
      const id = `mermaid-${seq}-${i}`;
      return `<div class="mermaid" id="${id}" data-src="${encodeURIComponent(
        mermaidBlocks[Number(i)]
      )}"></div>`;
    });

    UI.preview.innerHTML = html;

    // 4) 触发 mermaid 渲染
    if (mermaidBlocks.length) {
      const mermaid = await mermaidReady;
      const nodes = UI.preview.querySelectorAll(".mermaid");
      for (const node of nodes) {
        if (seq !== renderSeq) return;  // 已有新渲染，丢弃
        const raw = decodeURIComponent(node.getAttribute("data-src") || "");
        try {
          const { svg } = await mermaid.render(`mmd-${seq}-${node.id}`, raw);
          node.innerHTML = svg;
        } catch (err) {
          node.innerHTML = `<pre class="mermaid-error">Mermaid 渲染失败：\n${String(err && err.message ? err.message : err)}\n\n源：\n${raw}</pre>`;
        }
      }
    }
  }

  // source 文本变化时，实时重渲 preview（split 模式下特别有用）
  if (UI.source) {
    UI.source.addEventListener("input", () => {
      state.content = UI.source.value;
      if (state.mode !== "source") {
        renderPreview();
      }
    });
  }

  UI.modeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setMode(btn.getAttribute("data-canvas-mode")));
  });

  if (UI.close) UI.close.addEventListener("click", close);

  if (UI.copy) {
    UI.copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.content || "");
        flashHint("已复制");
      } catch (_) {
        flashHint("复制失败（权限或协议限制）");
      }
    });
  }

  if (UI.sendChat) {
    UI.sendChat.addEventListener("click", () => {
      // 让 chat.js 拿到内容塞进输入框
      window.dispatchEvent(
        new CustomEvent("steelg8:canvas-to-chat", { detail: { text: state.content || "" } })
      );
    });
  }

  function flashHint(msg) {
    if (!UI.hint) return;
    const prev = UI.hint.textContent;
    UI.hint.textContent = msg;
    setTimeout(() => (UI.hint.textContent = prev || "—"), 1200);
  }

  /** 暴露给 chat.js 的 API */
  window.SteelCanvas = {
    open,
    close,
    isOpen,
    setMode,
    /** 内容里是否有值得用 Canvas 展示的东西 */
    isWorthy(text) {
      if (!text) return false;
      if (/```mermaid/.test(text)) return true;
      if (/```[\w+-]*\s*\n/.test(text)) return true;
      if (text.length > 800) return true;
      const headings = (text.match(/^#{1,3}\s+/gm) || []).length;
      if (headings >= 3) return true;
      return false;
    },
  };
})();
