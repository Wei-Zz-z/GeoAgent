# AGENTS.md — GeoAgent 项目说明与开发规范

> 本文件面向后续参与开发的 AI 智能体与人类开发者。所有代码注释、本文档均使用**中文**；
> 所有喂给 LLM 的提示词（system prompt、工具描述、路由指令等）统一使用**英文**。

## 1. 项目要做什么

GeoAgent 是一个**通过自然语言对话完成地理空间分析**的智能体应用：

- 用户在聊天窗口输入自然语言（如"给国贸做一个 10 公里缓冲区"、"计算两个点的距离"），
  由 Agent 自主规划并调用空间分析工具，把分析结果**以可视化的形式渲染在会话窗口内**
  （GeoJSON 图层、表格等），而不是打开一个整页底图应用。
- 支持多用户、多会话（登录/权限暂未实现，当前为 dev 模式，所有会话全局可见）。

### 技术栈

| 层 | 选型 | 状态 |
| --- | --- | --- |
| 后端 | Python + FastAPI + WebSocket + openai SDK，uv 管理环境 | 骨架已搭建 |
| 分析引擎 | PyQGIS（计划，worker 进程隔离） | 未开始，先用纯 Python 演示工具 |
| LLM | OpenAI 兼容接口（阿里千问 / OpenAI / 智谱 / DeepSeek…） | 已支持多模型切换 |
| 前端 | Vue3 + Vite + OpenLayers | 骨架已搭建（会话列表 + 会话窗口 + 流式工具卡片 + 内嵌地图） |
| 存储 | JSONL（当前）→ SQLite/PostGIS（规划） | JSONL 已可用 |

## 2. 目前做到了什么程度（2026-08-17）

### 已完成

- **异步图编排**（`backend/geoagent/core/node.py`）：`Node.exec(ctx, payload)` 返回
  `(action, next_payload)`，用 `node - "action" >> next_node` 构图；支持重试、循环检测。
- **Agent 抽象**（`core/agent.py`）：Agent 就是一个 Node，组合了系统提示词 + 工具集 +
  模型配置 + "LLM → 工具 → 再问"循环，可与其他自定义节点混编成图。
- **多模型切换**（`core/llm.py` + `config.py`）：`ModelProfile` 注册表内置
  qwen-flash / qwen-turbo / qwen-plus / qwen-max / gpt-4o / gpt-4o-mini /
  glm-4.7-flash / deepseek-chat；支持 `OPENAI_BASE_URL` 全局覆盖；
  每个会话可通过 REST 接口随时切换模型。
- **工具系统**（`tools/`）：装饰器注册 + Pydantic 参数校验自动生成 LLM schema；
  异步执行器返回结构化错误；`ToolResult` 同时携带给 LLM 的文本 `content` 和
  给前端渲染的 `artifacts`（geojson / table）。
- **演示地理工具**（`tools/geo.py`）：list_datasets、load_dataset、buffer_point、
  polygon_area、distance_between_points（纯 Python，无 PyQGIS 依赖）。
- **短期会话与持久化**（`memory/`）：消息窗口裁剪、规则滚动摘要、孤儿 tool 消息清理；
  JSONL 会话存储；长期记忆接口占位（`MemoryProvider`）。
- **智能体与图**（`agents/`）：RouterNode（LLM 路由 + 关键字兜底）、ChatAgent、
  GeoAgent，`build_geo_graph()` 组装默认图。
- **服务层**（`server/`）：REST（会话 CRUD、模型切换、发消息）+ WebSocket 流式事件
  （token / route / tool_call / tool_result / artifact / message / error / turn_end）。
- **验证**：13 个 pytest 用例全部通过；已用阿里千问 qwen-flash 真实跑通
  "加载数据集 → 点缓冲区 → GeoJSON artifact 输出"的完整链路。
- **前端骨架**（`frontend/`）：左侧历史会话列表 + 右侧会话窗口；流式展示
  token / 路由 / 工具调用卡片（运行中/完成/失败）；GeoJSON 用 OpenLayers
  内嵌小地图渲染、表格用 HTML 表格渲染；会话级模型切换下拉框；Vite 代理
  `/api`（含 WebSocket）到后端，前端不硬编码后端地址。

### 尚未完成 / 规划中

- PyQGIS 真实空间分析（必须以 worker 进程方式接入，禁止直接 import 进 FastAPI 进程）
- 长期记忆（记忆类型、检索、注入）与上下文压缩（LLM 滚动摘要）
- 工具失败兜底机制（重试 → 换工具 → 澄清 → 询问用户）
- 任务规划机制（复杂请求拆分为子任务/子图）
- 文件上传（shp / GeoJSON / CSV）与真实数据集管理
- 前端完善：更多 artifact 类型（图片/图表）、地图交互、多轮上下文展示
- 多用户认证/权限、SQLite/PostGIS、日志与可观测性

## 3. 目录结构

```text
GeoAgent/
├── AGENTS.md                    # 本文档
├── backend/
│   ├── pyproject.toml           # uv 项目定义（依赖、pytest 配置）
│   ├── .env.example             # 环境变量示例（API key 一律走环境变量）
│   └── geoagent/
│       ├── config.py            # Settings + ModelProfile 模型注册表
│       ├── core/                # 与业务无关的框架核心
│       │   ├── node.py          # 异步 Node / Flow 图编排
│       │   ├── agent.py         # Agent（工具循环）抽象
│       │   ├── llm.py           # LLMService：多模型、流式、工具调用归一化
│       │   ├── context.py       # ConversationContext：每会话状态（替代全局 shared）
│       │   └── events.py        # 流式事件定义
│       ├── tools/               # 工具注册与执行
│       │   ├── registry.py      # @register_tool 装饰器、Tool、schema 生成
│       │   ├── executor.py      # 异步执行器（校验、错误归一化）
│       │   ├── result.py        # ToolResult / Artifact
│       │   └── geo.py           # 地理演示工具
│       ├── memory/              # 记忆与会话
│       │   ├── session.py       # 短期消息窗口（裁剪/摘要/清理）
│       │   ├── store.py         # JSONL 会话存储
│       │   └── memory.py        # 长期记忆接口（占位）
│       ├── agents/              # 业务智能体与图编排
│       │   ├── router.py        # 意图路由
│       │   ├── chat.py          # 通用对话
│       │   ├── geo.py           # 地理分析（工具循环）
│       │   └── graph.py         # build_geo_graph() 默认图
│       └── server/              # FastAPI 入口
│           ├── app.py           # create_app()
│           ├── routes.py        # REST + WebSocket
│           └── schemas.py       # API 请求/响应模型
├── frontend/                    # Vue3 + Vite + OpenLayers
│   ├── vite.config.js           # /api 代理（REST + WS）到后端
│   ├── scripts/smoke.mjs        # 端到端冒烟脚本（需前后端已启动）
│   └── src/
│       ├── api/client.js        # REST 封装 + WebSocket 事件入口
│       ├── stores/chat.js       # 轻量响应式会话 store
│       ├── components/          # 会话列表 / 会话窗口 / 消息气泡 / 工具卡片 / artifact 渲染
│       └── style.css            # 全局样式
└── README.md
```

## 4. 开发规范

### 4.1 语言约定（重要）

- **LLM 提示词一律英文**：system prompt、工具 name/description、参数 description、
  路由指令等。英文指令对模型的遵从度和输出稳定性更好。
- **代码注释、文档、commit message 用中文**。
- 模型生成的用户回复语言由模型根据用户输入自行决定（中文用户则中文回复）。

### 4.2 Python 代码规范

- Python >= 3.12，所有函数/参数/返回值写类型注解。
- 数据结构优先使用 `dataclass`；API 校验使用 Pydantic v2。
- **异步优先**：所有 IO、LLM 调用、工具执行必须 `async/await`；
  禁止在事件循环内跑阻塞调用（PyQGIS 这类阻塞计算将来放 worker 进程）。
- **禁止全局可变状态**（`poipoi-agent` 的 `shared` dict 是反面教材）；
  每会话状态一律放在 `ConversationContext`，跨节点通过 ctx 传递。
- 新增文件默认 UTF-8，无 BOM。

### 4.3 如何新增内容

- **新增模型**：在 `geoagent/config.py` 的 `default_model_registry()` 增加一个
  `ModelProfile`。API key 永远从环境变量读取（`api_key_env`），**禁止硬编码 key**。
- **新增工具**：
  1. 在 `geoagent/tools/` 下写函数，用 `@register_tool(name, description, params)`
     注册，`params` 传 Pydantic 模型（自动生成 schema 并校验参数）；
  2. 返回 `ToolResult(content=给LLM的文本摘要, artifacts=[Artifact(kind, data)])`；
     完整数据放 artifacts，content 只放摘要（超长会被截断）；
  3. 在 `tools/geo.py` 的 `get_geo_tools()` 或对应 Agent 的工具列表中加入。
- **新增 Agent**：继承 `core.agent.Agent`（提供 `name / system_prompt / tools / model`），
  或按需写自定义 `Node`（参考 `agents/router.py`）。system prompt 用英文。
- **修改图编排**：在 `agents/graph.py` 中用 `node - "action" >> next_node` 组合，
  保留 `build_geo_graph()` 作为默认入口。
- **新增前端可见事件**：先扩展 `core/events.py` 的事件类型，在 `server/routes.py`
  的 WebSocket 链路中 emit，并在 `backend/README.md` 的事件表中登记。

### 4.4 测试规范

- 新功能必须带 pytest 用例（`backend/tests/`）。
- 单元测试**不得依赖真实 API key / 网络**：LLM 相关测试只验证配置缺失时的错误路径
  （如 502 + 提示环境变量）；真实模型调用只允许在手动冒烟脚本中进行。
- 提交前必须 `uv run pytest` 全绿。

### 4.5 前端开发规范

- 技术栈：Vue3 `<script setup>` + Vite + OpenLayers；状态用 `src/stores/chat.js`
  轻量响应式对象，未来可平滑迁移 Pinia，不引入重型状态库。
- **接口一律走相对路径 `/api`**，经 Vite 代理转发（`vite.config.js`），
  禁止在前端硬编码后端地址；WebSocket 地址由 `location.host` 推导。
- **事件协议是前后端唯一契约**：前端渲染只依赖 WebSocket 事件；新增事件必须
  同步登记在 `backend/README.md` 与本文档的协议表中。
- artifact 渲染：新增可视化类型时，在 `components/ArtifactView.vue` 按 `kind`
  分支渲染（geojson→OpenLayers、table→HTML 表格、其他→JSON 预览），并登记协议表。
- 流式状态约定：`turn_start` 创建流式助手消息，`token` 逐字追加，`tool_call`
  生成工具卡片，`tool_result` 更新卡片状态，`artifact` 挂到最近的工具卡片下，
  `turn_end` 后以服务端持久化消息为准重建列表。
- UI 文案使用中文；组件按职责拆分（列表/窗口/气泡/卡片/渲染器），
  单个组件不超过约 200 行。
- 联调命令：`npm run dev` 启动开发服务器；`npm run build` 验证可编译；
  端到端冒烟用 `node scripts/smoke.mjs`（真实调用模型，仅手动执行）。

### 4.6 前端-后端事件协议（WebSocket）

| 事件类型 | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `turn_start` | 后端→前端 | `conversation_id` | 一轮对话开始 |
| `route` | 后端→前端 | `target`, `reason` | 路由结果（geo / chat） |
| `token` | 后端→前端 | `delta` | 流式增量文本 |
| `tool_call` | 后端→前端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 后端→前端 | `id`, `name`, `is_error`, `content` | 工具结果摘要 |
| `artifact` | 后端→前端 | `kind`, `name`, `data` | 可视化产物 |
| `message` | 后端→前端 | `role`, `content`, `model` | 最终助手消息 |
| `error` | 后端→前端 | `message` | 错误信息 |
| `turn_end` | 后端→前端 | `conversation_id` | 一轮对话结束 |
| `user` | 前端→后端 | `content` | 用户消息 |

### 4.7 运行方式

```bash
# 后端
cd backend && uv sync
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000

# 前端（另开终端）
cd frontend && npm install
npm run dev           # http://localhost:5173
```

环境变量参考 `backend/.env.example`；阿里千问（DashScope 兼容端点）示例：

```env
OPENAI_API_KEY=sk-xxxx
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
GEOAGENT_DEFAULT_MODEL=qwen-flash
```

### 4.8 Git 规范

- 提交信息用中文，格式：`<类型>: <简述>`（如 `feat: 新增 xxx 工具`、
  `refactor: 调整上下文裁剪逻辑`）。
- 不提交：`.venv/`、`data/`、`.env`、`__pycache__/`（已在 `backend/.gitignore` 中）。

## 5. 关键架构决策记录（ADR 摘要）

1. **借鉴 `poipoi-agent` 的 Node/Flow 与工具注册思想**，但改为：异步执行、每会话
   context、装饰器注册 + Pydantic 校验、模型配置化（可切换）。旧项目的全局 `shared`
   dict 和硬编码 API key 是必须避免的问题。
2. **Agent 即 Node**：不做重型 Agent 框架（如 LangGraph），保持图编排轻量，
   任何 Agent 都可以作为图中的一个节点与自定义节点混编。
3. **PyQGIS 必须进程隔离**：Web 服务进程不 import PyQGIS，分析任务放入独立
   worker 进程（subprocess / 进程池），防止 QGIS 崩溃拖垮服务、避免多用户互相影响。
4. **工具输出双通道**：`content` 只给 LLM 看（文本摘要），`artifacts` 给前端渲染
   （GeoJSON/表格等），二者严格分离。
5. **事件驱动流式**：前端只依赖 WebSocket 事件协议，后端内部实现可自由演进。
