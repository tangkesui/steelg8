/*
 * steelg8 chat — API 客户端
 * --------------------------
 *
 * 把 KERNEL 配置（端口 / baseURL / token）和 fetchJSON 从 chat.js 里独立出来。
 * 这块没有 DOM 依赖，所有函数都纯，方便单独审计 token 注入和超时逻辑。
 *
 * 暴露：window.SteelG8Api = { KERNEL_PORT, API_BASE, KERNEL_AUTH_TOKEN,
 *                              apiURL, isKernelURL, withKernelAuth, fetchJSON,
 *                              hasSwiftBridge, swiftBridge, HASH_PARAMS,
 *                              isScratchOnly }
 */
(function () {
  "use strict";

  const HASH_PARAMS = (() => {
    try {
      const raw = location.hash.replace(/^#/, "").replace(/^\?/, "");
      return new URLSearchParams(raw);
    } catch (_) {
      return new URLSearchParams();
    }
  })();

  const KERNEL_PORT = (() => {
    try {
      const injected = window.STEELG8_KERNEL && window.STEELG8_KERNEL.port;
      if (injected) return String(injected);
      const fromHash = HASH_PARAMS.get("port");
      if (fromHash) return fromHash;
    } catch (_) {}
    return "8765";
  })();

  const API_BASE = (
    (window.STEELG8_KERNEL && window.STEELG8_KERNEL.baseURL) ||
    `http://127.0.0.1:${KERNEL_PORT}`
  ).replace(/\/+$/, "");

  const KERNEL_AUTH_TOKEN = (() => {
    try {
      const injected = window.STEELG8_KERNEL && window.STEELG8_KERNEL.authToken;
      if (injected) return String(injected);
      return HASH_PARAMS.get("token") || "";
    } catch (_) {
      return "";
    }
  })();

  function apiURL(path) {
    const cleaned = String(path || "");
    if (/^https?:\/\//i.test(cleaned)) return cleaned;
    return `${API_BASE}${cleaned.startsWith("/") ? cleaned : "/" + cleaned}`;
  }

  function isKernelURL(url) {
    try {
      return new URL(url).origin === new URL(API_BASE).origin;
    } catch (_) {
      return false;
    }
  }

  function withKernelAuth(headers, url) {
    const out = Object.assign({}, headers || {});
    if (KERNEL_AUTH_TOKEN && isKernelURL(url)) {
      out.Authorization = `Bearer ${KERNEL_AUTH_TOKEN}`;
    }
    return out;
  }

  async function fetchJSON(path, options, timeoutMs) {
    const opts = Object.assign({}, options || {});
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs || 8000);
    const externalSignal = opts.signal;
    let onExternalAbort = null;
    if (externalSignal) {
      if (externalSignal.aborted) controller.abort();
      onExternalAbort = () => controller.abort();
      externalSignal.addEventListener("abort", onExternalAbort, { once: true });
    }
    if (Object.prototype.hasOwnProperty.call(opts, "json")) {
      opts.headers = Object.assign(
        { "Content-Type": "application/json" },
        opts.headers || {}
      );
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    const targetURL = apiURL(path);
    opts.headers = withKernelAuth(opts.headers, targetURL);
    opts.signal = controller.signal;
    try {
      const resp = await fetch(targetURL, opts);
      const raw = await resp.text();
      let data = {};
      if (raw) {
        try {
          data = JSON.parse(raw);
        } catch (_) {
          data = { raw };
        }
      }
      if (!resp.ok) {
        const msg = data.error || data.message || data.raw || `HTTP ${resp.status}`;
        throw new Error(`HTTP ${resp.status}: ${msg}`);
      }
      return data;
    } finally {
      clearTimeout(timer);
      if (externalSignal && onExternalAbort) {
        externalSignal.removeEventListener("abort", onExternalAbort);
      }
    }
  }

  const HAS_SWIFT_BRIDGE = !!(
    window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.steelg8
  );

  function swiftBridge(action, payload) {
    if (HAS_SWIFT_BRIDGE) {
      const msg = Object.assign({ action }, payload || {});
      window.webkit.messageHandlers.steelg8.postMessage(msg);
      return true;
    }
    return false;
  }

  function isScratchOnly() {
    return HASH_PARAMS.has("scratch");
  }

  window.SteelG8Api = {
    HASH_PARAMS,
    KERNEL_PORT,
    API_BASE,
    KERNEL_AUTH_TOKEN,
    apiURL,
    isKernelURL,
    withKernelAuth,
    fetchJSON,
    hasSwiftBridge: HAS_SWIFT_BRIDGE,
    swiftBridge,
    isScratchOnly,
  };
})();
