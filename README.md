# steelg8

> 方案不求人。
>
> A personal macOS-native AI agent for copywriters — menu-bar resident, multi-provider, local-first.

steelg8 是一个跑在自己 Mac 上的个人 AI 助手。它常驻菜单栏，用全局快捷键唤出，底层通过一个可插拔的多模型网关（Kimi / DeepSeek / Qwen / OpenRouter …）工作，不依赖任何一家厂商。

当前仓库以 [`docs/blueprint.md`](../docs/blueprint.md) 为唯一现行真相。当前主线是 **Phase 12：管理界面 + 主聊天 SwiftUI 原生化**；Phase 9 性能增强和 Phase 10 业务扩展已让位到 Phase 12 默认翻转之后。历史完整记录见 [`docs/reference/architecture-hardening-plan.md`](../docs/reference/architecture-hardening-plan.md)。

## 设计理念

- **多模型独立**：避免被任何单一厂商锁死；切换 Provider 只是改一行 JSON。
- **成本敏感**：优先用可控的云端 cheap API 和本地配置；本地 runtime、Rerank、向量库加速等重能力按 blueprint 排期逐步下沉。
- **一切本地可改**：soul.md / user.md / project.md / providers.json 都是纯文本/JSON，用户随时可以编辑。
- **macOS-native 原生外壳**：Swift + SwiftUI，常驻菜单栏，不占 Dock，全局热键一键唤起。

## 架构概览

```
┌──────────────────────────────────────────────────────────┐
│  macOS Shell (Swift / SwiftUI)                          │
│   · 菜单栏 StatusItem                                   │
│   · 全局热键 (Carbon API)                               │
│   · WKWebView 预留给 Chat / Canvas / Scratch             │
└────────────────────────┬─────────────────────────────────┘
                         │  HTTP (localhost:${STEELG8_PORT:-8765})
┌────────────────────────▼─────────────────────────────────┐
│  Python Kernel (stdlib-only, fork of Hermes Agent)       │
│   · /health · /providers · /chat                         │
│   · Provider Registry (Kimi / DeepSeek / Qwen / OpenRouter)│
└──────────────────────────────────────────────────────────┘
```

更详细的早期产品设计可以翻上层目录里的 [`docs/reference/`](../docs/reference/)；这些资料只作背景参考，当前工程状态以 [`docs/blueprint.md`](../docs/blueprint.md) 为准。

## 当前功能

**基础设施**

- [x] Swift 菜单栏壳 + 全局热键（⌘⇧D 截图 OCR / ⌘⇧N 便签召唤）
- [x] Python 子进程自动拉起 + 回收，venv 随 .app 分发
- [x] 多 Provider 注册表（10 家国内外预设：百炼 / DeepSeek / Kimi / 智谱 / 豆包 / 阶跃 / 零一 / MiniMax / 硅基流动 / OpenRouter / Tavily）
- [x] 显式 / 默认 / 兜底 / mock 模型路由 + 模型能力画像 + SSE 流式

**交互**

- [x] WKWebView 对话主窗 + 自写 Markdown 流式渲染
- [x] Settings 窗口：Provider 管理 + "+ 添加供应商" 市场
- [x] Canvas 右侧画板（Markdown / Mermaid / 代码 / 预览-源码-分栏三模式）
- [x] 左侧便签（单 textarea 自动保存 + 一键推 Apple Notes）
- [x] Token 计费 pill + 按模型拆分

**五层记忆**

- [x] **L1 soul.md**（产品人格）
- [x] **L2 user.md**（用户画像，可被 `remember` tool 追加）
- [x] **L3 `<project>/steelg8.md`**（项目记忆，自动生成）
- [x] **L4 会话**（内存 + 流式 history）
- [x] **L5 知识库**（`~/.steelg8/knowledge/` + 独立向量集合，每次对话都会被召回）

**RAG + 文档**

- [x] 项目索引：`.md / .txt / .docx / .pdf / .pptx / .doc`（macOS textutil）
- [x] Qwen text-embedding-v3 + qwen3-rerank，单模型追踪 + 不一致告警
- [x] docx 操作全套：fill / insert_section / append_paragraphs / append_row / read / diff
- [x] 模板库 `~/Documents/steelg8/templates/`（Finder 可见 / iCloud 可同步）

**Tool calling**

- [x] OpenAI-style tool calling（非流式 + 流式都支持）；13 个 tool：
      docx_* ×7 / remember / save_knowledge / templates_list / diff_documents /
      web_search（Tavily）/ web_fetch（Jina Reader）
- [x] 路径沙箱（只允许 $HOME 下）+ 结果 chip UI（运行中 → 成功 / 失败）
- [x] 文件操作类 tool 自动附 "📂 打开 / 🔍 Finder" 按钮

**架构与诊断**

- [x] 本地 capability token（per-launch 随机生成，Swift / Web / Python 端到端）
- [x] HTTP kernel 拆 service 层：`server.py` 只做 adapter，业务逻辑在 `services/*_service.py`
- [x] route table 取代手写 if/else 路由
- [x] 系统面板（体检 / 索引 / RAG / 日志四页）
- [x] 增量索引 + `file_manifest`：不变的文件不再重新解析/embed
- [x] hybrid RAG 召回：vector / keyword / title / knowledge 四路 + rerank + citation
- [x] 模板化切块（report / policy / meeting / table-heavy）+ heading/table-aware chunker

**外壳与启动**

- [x] `LSUIElement=true` 菜单栏壳，混合 accessory↔regular 切换确保 frontmost
- [x] per-launch 动态端口，避免 8765 冲突和旧内核误连
- [x] healthcheck 30s 退避等待，Python 进程异常退出时给出明确状态码

## 快速开始

### 0. 前置条件

- macOS 14+（Sonoma 以上）
- **完整的 Xcode**（不能只是 Command Line Tools —— 某些 macOS 版本上 CLT 的 Swift 会和系统 SDK 版本对不齐，建议直接装 Xcode）
- Python 3.10+（系统自带即可）
- 至少一家模型厂商的 API Key（推荐 DeepSeek，国内访问稳定、便宜）

### 1. 克隆仓库

```bash
git clone https://github.com/tangkesui/steelg8.git
cd steelg8
```

### 2. 配置 Provider

两种方式，任选其一：

**方式 A：环境变量（推荐用来跑第一次）**

```bash
cp .env.example .env
# 编辑 .env，至少填一个 API Key，比如：
#   DEEPSEEK_API_KEY=sk-xxxxxxxx
```

**方式 B：JSON 配置文件（推荐长期使用）**

```bash
mkdir -p ~/.steelg8
cp config/providers.example.json ~/.steelg8/providers.json
# 编辑 ~/.steelg8/providers.json，把 api_key_env 指向你实际使用的环境变量名
# 然后在 shell rc 里 export 对应的 key
```

### 3. 单独跑一下 Python kernel，确认 Provider 就绪

```bash
cd Python
python3 server.py
# 在另一个终端：
curl "http://127.0.0.1:${STEELG8_PORT:-8765}/health"
curl "http://127.0.0.1:${STEELG8_PORT:-8765}/providers"
curl -X POST "http://127.0.0.1:${STEELG8_PORT:-8765}/chat" \
     -H "Content-Type: application/json" \
     -d '{"message":"hello"}'
```

### 4. 构建 macOS 应用

```bash
swift build
# 或者：
./bundle.sh   # 打包 .app 并 symlink 到 /Applications
```

然后从 Launchpad 或 `/Applications/steelg8.app` 启动：

1. 菜单栏会出现 🔨 图标，点开看到「内核状态 / 最近回复 / 测试 Agent 链路 / 设置…」
2. 主窗口默认打开 **对话** Tab（WKWebView 加载 `Web/chat/`）
3. 右上角下拉选模型或「自动路由」；输入框 `⌘+Enter` 发送
4. 没填 API Key 时会走 mock 回退，能看到路由层级；填好 key 后流式真回复

## 目录结构

```
steelg8/
├── Package.swift              Swift 包定义
├── Sources/steelg8/           macOS 外壳（SwiftUI + AppKit）
│   ├── App.swift              入口
│   ├── AppController.swift    菜单栏 / 热键 / Python runtime 总控
│   ├── ContentView.swift      Tab 容器（对话 / 状态 / OCR）
│   ├── AgentBridge/           Swift ↔ Python IPC
│   ├── Chat/                  WKWebView 对话宿主
│   ├── Hotkeys/               全局热键注册
│   ├── OCR/                   截图 OCR
│   ├── Settings/              Provider 配置 UI
│   └── Shared/                工具类
├── Python/
│   ├── server.py              HTTP adapter（stdlib-only / route table 分发）
│   ├── kernel/                auth / request / response / routing helpers
│   ├── services/              chat / project / conversation / diagnostics …（业务逻辑）
│   ├── document/              parser registry / IR blocks / template chunker
│   ├── rag_store.py           RagStore 接口 + SQLiteBruteForceStore
│   ├── vectordb.py            SQLite schema + 增量 manifest
│   ├── providers.py           多 Provider 注册表
│   ├── router.py              显式 / 默认 / 兜底 / mock 路由
│   ├── agent.py               agent loop（流式 + tool calling）
│   └── skills/                tool 注册表 + docx skills
├── Web/chat/                  WKWebView 加载的前端
│   ├── index.html
│   ├── styles/
│   ├── markdown.js            自写极简 Markdown 渲染
│   └── chat.js                SSE 客户端 + UI
├── config/
│   └── providers.example.json 默认 Provider 模板
├── prompts/
│   └── soul.md                Agent 人格 / 原则
├── .env.example               环境变量模板
└── bundle.sh                  打包脚本
```

## Roadmap

完整阶段定义见 [`docs/blueprint.md`](../docs/blueprint.md)，下面只保留当前推进位置摘要。

- **Phase 1–8** ✅ 本地安全边界 / service 拆层 / agent 可靠性 / RAG 管线 / Web UI 模块化 / 诊断与产品打磨
- **Phase 9–10** 🕒 让位：性能增强与业务扩展，等 Phase 12 默认翻转后再启动
- **Phase 11** 🔀 并入：WebView 注入面收尾已并入 Phase 12
- **Phase 12** 🔄 当前主线：Track A 管理窗口原生化推进中；下一步见 [`docs/plan/active/`](../docs/plan/active/)

历史里的"飞书 Bot"和"双机 + 本地模型"想法暂未排期；新想法统一收口到 [`docs/ideas.md`](../docs/ideas.md) 或 blueprint Backlog。

## License

MIT — 详见 [LICENSE](./LICENSE)。

## 来源与致谢

- **Hermes Agent**（NousResearch）：Python kernel 思路与早期 scaffolding
- **OpenClaw / Manus**：Agent 工作流与菜单栏常驻的产品形态启发
- **LiteLLM**：多 Provider 路由范式（steelg8 用 stdlib 重新实现，避免 pip 依赖）
