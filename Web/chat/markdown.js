/*
 * 极简 Markdown 渲染器（无依赖，单文件，stream-safe）
 * --------------------------------------------------
 * 支持：
 *   # / ## / ### 标题
 *   空行分段
 *   - 列表 / * 列表 / 数字列表
 *   ```lang\n...\n``` 代码块
 *   `行内代码`
 *   **粗体** / *斜体*
 *   [文本](链接)
 *   > 引用
 *
 * 不支持（MVP 暂不做）：表格、图片、脚注、HTML 透传、多级嵌套列表
 *
 * 为什么自己写：WKWebView 本地加载 HTML，不想走 CDN 也不想搞 bundler。
 * 等 Phase 1 晚期再替换成 markdown-it + shiki。
 */
(function () {
  "use strict";

  function escHTML(s) {
    return s
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderInline(text) {
    // 行内代码先提出来占位（防止粗体/链接 regex 误伤）
    const codes = [];
    text = text.replace(/`([^`\n]+)`/g, (_m, p1) => {
      codes.push(p1);
      return `\u0000CODE${codes.length - 1}\u0000`;
    });

    text = escHTML(text);

    // 链接 [text](url)
    text = text.replace(
      /\[([^\]]+)\]\(([^)\s]+)\)/g,
      (_m, label, url) =>
        `<a href="${url}" target="_blank" rel="noopener">${label}</a>`
    );

    // 粗体 **x**
    text = text.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    // 斜体 *x*
    text = text.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");

    // 还原行内代码
    text = text.replace(/\u0000CODE(\d+)\u0000/g, (_m, i) => {
      return "<code>" + escHTML(codes[Number(i)]) + "</code>";
    });

    return text;
  }

  function render(md) {
    if (!md) return "";
    const lines = md.replace(/\r\n/g, "\n").split("\n");
    const out = [];
    let i = 0;

    while (i < lines.length) {
      const line = lines[i];

      // 代码块
      const fence = line.match(/^```(\w*)\s*$/);
      if (fence) {
        const lang = fence[1] || "";
        i++;
        const buf = [];
        while (i < lines.length && !/^```\s*$/.test(lines[i])) {
          buf.push(lines[i]);
          i++;
        }
        // 跳过结束 fence
        if (i < lines.length) i++;
        out.push(
          `<pre><code data-lang="${escHTML(lang)}">${escHTML(
            buf.join("\n")
          )}</code></pre>`
        );
        continue;
      }

      // 标题
      const h = line.match(/^(#{1,3})\s+(.+?)\s*$/);
      if (h) {
        const level = h[1].length;
        out.push(`<h${level}>${renderInline(h[2])}</h${level}>`);
        i++;
        continue;
      }

      // 引用
      if (/^>\s?/.test(line)) {
        const buf = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        out.push(`<blockquote>${renderInline(buf.join(" "))}</blockquote>`);
        continue;
      }

      // 无序列表
      if (/^\s*[-*]\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*]\s+/, ""));
          i++;
        }
        out.push(
          "<ul>" +
            items.map((x) => `<li>${renderInline(x)}</li>`).join("") +
            "</ul>"
        );
        continue;
      }

      // 有序列表
      if (/^\s*\d+\.\s+/.test(line)) {
        const items = [];
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
          i++;
        }
        out.push(
          "<ol>" +
            items.map((x) => `<li>${renderInline(x)}</li>`).join("") +
            "</ol>"
        );
        continue;
      }

      // 空行 → 段落分割
      if (!line.trim()) {
        i++;
        continue;
      }

      // 段落（收集后续连续非空非块级）
      const paraBuf = [line];
      i++;
      while (
        i < lines.length &&
        lines[i].trim() &&
        !/^```/.test(lines[i]) &&
        !/^#{1,3}\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i]) &&
        !/^\s*[-*]\s+/.test(lines[i]) &&
        !/^\s*\d+\.\s+/.test(lines[i])
      ) {
        paraBuf.push(lines[i]);
        i++;
      }
      out.push(`<p>${renderInline(paraBuf.join(" "))}</p>`);
    }

    return out.join("\n");
  }

  window.SteelMarkdown = { render, renderInline, escape: escHTML };
})();
