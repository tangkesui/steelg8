# steelg8

> 让文案工作者不用再求任何人。
>
> A personal macOS-native AI agent for copywriters — menu-bar resident, multi-provider, local-first.

steelg8 是一个跑在自己 Mac 上的个人 AI 助手。它常驻菜单栏，用一个全局快捷键唤出，底层通过一个可插拔的多模型网关（Kimi / DeepSeek / Qwen / OpenRouter …）工作，不依赖任何一家厂商。

当前仓库正处于 **Phase 0（脚手架打通）** 阶段：SwiftUI 外壳 + Python 子进程 + HTTP IPC + 多模型 Provider 注册表已经跑通，可以在本地完成一次「按快捷键 → 走模型网关 → 菜单栏显示回复」的最小闭环。

## 设计理念

- **多模型独立**：避免被任何单一厂商锁死；切换 Provider 只是改一行 JSON。
- **成本敏感**：MVP 阶段云端 Embedding + 云端 LLM，本地模型/Rerank/向量库等重依赖统一延后到 Phase 6 双机下沉后再启用。
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
                         │  HTTP (localhost:8765)
┌────────────────────────▼─────────────────────────────────┐
│  Python Kernel (stdlib-only, fork of Hermes Agent)       │
│   · /health · /providers · /chat                         │
│   · Provider Registry (Kimi / DeepSeek / Qwen / OpenRouter)│
└──────────────────────────────────────────────────────────┘
```

更详细的设计可以翻上层目录里的 `steelg8-产品设计方案-v0.1.md`（仓库暂不公开，后续可能裁剪后发布）。

## 当前功能（Phase 0 → 1 过渡）

- [x] Swift 菜单栏壳子，含 About / Preferences / Quit
- [x] 全局热键框架（HotkeyRegistry）
- [x] Python 子进程由 Swift 拉起、退出时自动回收
- [x] `/health` `/providers` `/capabilities` 端点
- [x] `/chat`（非流式）+ `/chat/stream`（SSE 流式）双端点
- [x] **四层路由漏斗**（规则 → Embedding stub → 廉价云 → 高能模型 → mock）
- [x] **模型能力画像表** (`Python/capabilities.py`)
- [x] **轻量 agent loop**（消息历史 + 流式 yield + mock 降级）
- [x] 多 Provider 注册表（`~/.steelg8/providers.json` / 环境变量 / example 三级加载）
- [x] **SwiftUI Settings 窗口**（Provider 管理 + 默认模型，⌘, 打开）
- [x] **WKWebView 对话主窗**：React-free 原生 HTML + 流式 SSE + 自写 Markdown 渲染
- [ ] 记忆层 L2 user.md / L3 project steelg8.md（Phase 2）
- [ ] Qdrant + 云 Embedding + Office 模板填充（Phase 2）
- [ ] Canvas / Scratch 多工位（Phase 2 后期）

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
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/providers
curl -X POST http://127.0.0.1:8765/chat \
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
│   ├── server.py              HTTP kernel（stdlib-only）
│   ├── providers.py           多 Provider 注册表
│   ├── capabilities.py        模型能力画像表
│   ├── router.py              四层路由漏斗
│   └── agent.py               轻量 agent loop（含流式）
├── Web/chat/                  WKWebView 加载的前端
│   ├── index.html
│   ├── styles.css
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

- **Phase 0** ✅ 脚手架打通
- **Phase 0.5** ✅ 四层路由 + 模型画像 + agent loop + SSE 流式
- **Phase 1** 🔄 WKWebView 对话窗（已做）→ Canvas 基础 → Scratch 侧栏
- **Phase 2** 项目记忆 + 模板库 + 云端 Embedding
- **Phase 3** Canvas / Scratch 扩展 + Web 三件套
- **Phase 4** 全局热键与系统集成深化
- **Phase 5** 飞书 Bot（移动端接入）
- **Phase 6** 双机架构（Tailscale）+ 本地模型下沉（Ollama / bge-m3）
- **Phase 7** 打包 / 发布 / 安装脚本

## License

MIT — 详见 [LICENSE](./LICENSE)。

## 来源与致谢

- **Hermes Agent**（NousResearch）：Python kernel 思路与早期 scaffolding
- **OpenClaw / Manus**：Agent 工作流与菜单栏常驻的产品形态启发
- **LiteLLM**：多 Provider 路由范式（steelg8 用 stdlib 重新实现，避免 pip 依赖）
