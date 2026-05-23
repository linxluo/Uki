# Uki

> 一个多变的日常助手。通过加载不同的 Skill，在不同时间扮演不同身份，解决不同问题。

## 我是谁

Uki 是一个多变的日常助手。它不像传统助手那样只有一个固定身份，而是可以动态加载 Skill，在需要时切换角色和能力，应对不同场景。

## 我能做什么

- 加载不同的 Skill 来获得对应的能力
- 在不同时间以不同身份解决问题
- （随着课程推进，这个清单会越来越长）

## 我不能做什么

（学习中，逐步明确边界）

## 我适合谁

- 所有需要日常辅助的人
- 希望被服务，而不是被技术门槛挡在外面的人

## 核心理念

**协作，不替代。** 人负责判断和审美，Uki 负责执行和重复劳动。

---

## 项目结构

```
UkiAgent/
├── README.md           # 项目说明 + 架构文档
├── package.json        # Electron 入口（npm start 启动桌面应用）
├── server.py           # FastAPI 本地服务器（Electron 后端）
├── gui.py              # 备选：纯 Tkinter 桌面窗口
├── main.py             # CLI 入口（python main.py）
├── requirements.txt    # Python 依赖
├── .env.example        # 环境变量模板
├── .env                # 实际配置（不入 git）
├── .gitignore
├── UKI.md              # 项目规则（Uki 每次启动自动读取）
│
├── uki/                # 核心引擎
│   ├── agent.py        # Agent 代理循环（思考→行动→观察）
│   ├── tools.py        # 工具集（list/read/write/search_code）
│   ├── config.py       # 配置管理 + 模型窗口智能适配
│   ├── commands.py     # 斜杠命令系统（/help /init 等）
│   ├── display.py      # 终端输出（颜色 + 阶段符号）
│   ├── plugin_manager.py # 插件管理器（发现/加载/生命周期）
│   └── model_fetcher.py # OpenRouter API 缓存（自动获取模型窗口）
│
├── plugins/            # 插件目录
│   └── time_utils/     # 示例插件（时间工具）
│       ├── uki_plugin.json
│       └── plugin.py
│
├── electron/           # Electron 桌面壳
│   └── main.js         # 主进程（启动 Python 后端 + 创建窗口）
│
├── ui/                 # 前端界面
│   └── index.html      # 聊天界面 + 设置面板
│
├── documents/          # 学习文档
│   ├── study_outline.md    # 完整课程大纲
│   └── 1到8课的内容总结.MD   # 第一阶段总结
│
└── uki_model_cache.json  # OpenRouter 模型缓存（7 天自动更新，不入 git）
```

## 架构

```
┌────────────────────────────────────────────┐
│  界面层（三选一）                              │
│  Electron / Tkinter GUI / CLI               │
├────────────────────────────────────────────┤
│  服务层  server.py（FastAPI :8765）           │
│  接收用户消息，流式返回 Agent 输出              │
├────────────────────────────────────────────┤
│  核心层  uki/                                │
│  agent.py: 代理循环（工具调用 + 上下文管理）     │
│  tools.py: 文件操作 / 搜索                    │
│  config.py: 配置 + 模型窗口                   │
│  commands.py: 本地命令 /help /init 等         │
│  plugin_manager.py: 插件发现/加载/生命周期     │
├────────────────────────────────────────────┤
│  扩展层                                      │
│  plugins/: 插件目录（动态加载工具和命令）        │
├────────────────────────────────────────────┤
│  存储层                                      │
│  UKI.md: 项目规则    .env: LLM 配置            │
│  conversation_history: 对话记忆               │
│  uki_model_cache.json: 模型窗口缓存            │
└────────────────────────────────────────────┘
```

### 消息流

```
用户输入 → 界面层 → POST /chat → agent.run()
  → system prompt（身份 + UKI.md 规则 + Git 状态）
  → + 对话历史（自动总结超阈值）
  → + 当前消息
  → 工具列表 = 内置工具 + MCP 工具 + 插件工具
  → LLM 返回 tool_calls？
     是 → 执行工具（插件优先 > MCP > 内置）→ 结果截断 4K → 继续循环
     否 → 返回文本回复 → SSE 流式推送 → 界面显示
```

## 开发日志

- **第一课（2026-05-21）**：定义 Uki 的身份和核心理念，创建项目 README
- **第二课（2026-05-21）**：搭建 Python 项目骨架。创建入口文件、配置管理、核心 Agent 类。Uki 已经可以进行简单对话。
- **第三课（2026-05-21）**：修复 requirements.txt 编码问题，配置 API key，Uki 第一次成功对话。
- **第四课（2026-05-21）**：实现代理循环。Uki 从"一问一答"升级为"思考→行动→观察"的循环模式。新增 tools.py（文件操作工具集），重写 agent.py 的 run() 方法。
- **第五课（2026-05-21）**：新增命令系统。以 / 开头的输入由本地处理（/help, /tools, /config, /model），不消耗 LLM token。命令注册表可扩展，为后续插件系统打基础。
- **第六课（2026-05-21）**：扩展工具集，新增 search_code（文件名和内容搜索），Uki 现在能在项目中自己找代码了。
- **第七课（2026-05-21）**：实现规则文件系统（UKI.md）。Uki 启动时自动读取，注入 system prompt。你在 UKI.md 里写的规则，Uki 每次都遵守。
- **第八课（2026-05-21）**：实现上下文管理。token 估算、自动裁剪、/compact 和 /context 命令。让 Uki 意识到自己记忆有限并自动处理。
- **第九课（2026-05-21）**：改善终端输出（display.py，ANSI 颜色 + 阶段符号）。Electron 桌面应用搭建，FastAPI 后端 + SSE 流式 + HTML 深色气泡界面 + 设置面板。
- **第十课（2026-05-21）**：完善项目结构和架构文档。README 加入四层架构图和消息流说明。新增 /init 命令（一键初始化 UKI.md 和 .env.example）。
- **第十一课（2026-05-22）**：权限控制。default/auto/readonly 三种模式，CLI 用 input() 确认，Electron 用 SSE + 前端确认栏。
- **第十二课（2026-05-22）**：Git 状态感知。通过 UKI_GIT_CONTEXT=1 环境变量控制开关，在 system prompt 中注入 Git 摘要。
- **第十三课（2026-05-22）**：MCP 外部工具。JSON-RPC stdio 协议客户端，支持 .uki_mcp.json 配置，内置 fetch 和 sample MCP 服务器。
- **第十四课（2026-05-22）**：子代理。delegate 工具实现并行任务拆分，子代理独立上下文 + 只读工具，4 轮限制防递归。
- **第十五课（2026-05-23）**：插件系统。对应 Claude Code 的 Plugin 机制：自包含插件目录 + uki_plugin.json 清单 + 动态发现加载 + 标准接口（工具/命令）。内置 /plugin 命令查看状态。示例插件 time_utils 提供时间和日期计算。
