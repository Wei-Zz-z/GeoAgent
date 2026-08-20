# AGENTS.md — GeoAgent 项目说明与开发规范

> 本文件面向后续参与开发的 AI 智能体与人类开发者。所有代码注释、本文档均使用**中文**；
> 所有喂给 LLM 的提示词（system prompt、工具描述、路由指令等）统一使用**英文**。

## 1. 项目要做什么

GeoAgent 是一个**通过自然语言对话完成地理空间分析与业务问答**的智能体应用：

- 用户在聊天窗口输入自然语言，Agent 自主规划并调用数据库查询 / 空间分析工具，
  把分析结果以可视化形式渲染在会话窗口内（GeoJSON 图层、表格等），
  而不是打开一个整页底图应用。
- 支持多用户、多会话（登录/权限暂未实现，当前为 dev 模式，所有会话全局可见）。

### 核心能力链路（按依赖关系）

1. **土地变化数据查询问答（基础能力）**：基于土地变化检测结果构建的
   PostgreSQL/PostGIS 库，进行多表 SQL 查询、统计与空间计算，回答用户问题
   （如"某区县耕地转为建设用地的面积是多少"）。
2. **模板化快报生成（近期目标）**：在查询分析结果之上，按预设模板生成
   土地流向变化快报；快报模板与生成流程通过 **skill** 接入，与主链路解耦。
3. **场景评估（远期目标）**：养老机构可达性分析评估、资源环境承载力评估，
   每个场景以独立场景 Agent + 共享工具层实现，路由按场景分发。

### 技术栈

| 层 | 选型 | 状态 |
| --- | --- | --- |
| 后端 | Python + FastAPI + WebSocket + openai SDK，uv 管理环境 | 骨架已搭建 |
| 数据与分析引擎 | PostgreSQL/PostGIS（土地变化检测库，当前主线）；PyQGIS（计划，worker 进程隔离） | PostGIS 查询层待接入 |
| LLM | OpenAI 兼容接口，内置 qwen3.7-flash / qwen3.7-plus | 已支持多模型切换 |
| 前端 | Vue3 + Vite + OpenLayers | 骨架已搭建（会话列表 + 会话窗口 + 流式工具卡片 + 内嵌地图） |
| 存储 | 会话：JSONL（当前）→ SQLite/PostGIS（规划）；业务数据：PostgreSQL | JSONL 已可用 |

## 2. 目前做到什么程度（2026-08-20）

### 已完成

- **异步图编排**（`backend/geoagent/core/node.py`）：`Node.exec(ctx, payload)` 返回
  `(action, next_payload)`，用 `node - "action" >> next_node` 构图；支持重试、循环检测。
- **Agent 抽象**（`core/agent.py`）：Agent 就是一个 Node，组合了系统提示词 + 工具集 +
  模型配置 + "LLM → 工具 → 再问"循环，可与其他自定义节点混编成图。
- **多模型切换**（`core/llm.py` + `config.py`）：`ModelProfile` 注册表内置
  qwen3.7-flash / qwen3.7-plus；支持 `OPENAI_BASE_URL` 全局覆盖；
  每个会话可通过 REST 接口随时切换模型。
- **工具系统**（`tools/`）：装饰器注册 + Pydantic 参数校验自动生成 LLM schema；
  异步执行器返回结构化错误；`ToolResult` 同时携带给 LLM 的文本 `content` 和
  给前端渲染的 `artifacts`（geojson / table）。
- **演示地理工具**（`tools/geo.py`）：list_datasets、load_dataset、buffer_point、
  polygon_area、distance_between_points（纯 Python，无 PyQGIS 依赖）。
- **短期会话与持久化**（`memory/`）：消息窗口裁剪、规则滚动摘要、孤儿 tool 消息清理；
  JSONL 会话存储；长期记忆接口占位（`MemoryProvider`）。
- **Agent 内置机制**（`core/agent.py` + `tools/builtin.py`）：所有 Agent 默认携带
  `todo_write` 任务清单工具（整体替换、上限 20 项、单 in_progress、内容非空）；
  多步任务时模型先规划再执行，连续多轮未更新清单会注入 reminder；
  清单通过 `todo` 事件实时推送前端，并随最终助手消息持久化。
- **子 Agent 与技能加载**（`core/subagent.py` + `skills.py`）：`task` 工具以全新
  会话上下文运行嵌套 Agent（不持久化、不污染父上下文，子 Agent 不含 task，
  有深度限制），返回最终文本；`list_skills` / `load_skill` 实现技能按需加载——
  启动时扫描 `skills/*/SKILL.md` 建立目录并注入 system prompt，完整说明按需读取。
- **智能体与图**（`agents/`）：RouterNode（LLM 路由 + 关键字兜底，当前目标 chat / geo）、
  ChatAgent、GeoAgent，`build_geo_graph()` 组装默认图。
- **服务层**（`server/`）：REST（会话 CRUD、模型切换、发消息）+ WebSocket 流式事件
  （token / route / tool_call / tool_result / artifact / message / error / turn_end）。
- **验证**：13 个 pytest 用例全部通过；已用阿里千问 qwen3.7-flash 真实跑通
  "加载数据集 → 点缓冲区 → GeoJSON artifact 输出"的完整链路。
- **前端骨架**（`frontend/`）：左侧历史会话列表 + 右侧会话窗口；流式展示
  token / 路由 / 工具调用卡片（运行中/完成/失败）；GeoJSON 用 OpenLayers
  内嵌小地图渲染、表格用 HTML 表格渲染；会话级模型切换下拉框；Vite 代理
  `/api`（含 WebSocket）到后端，前端不硬编码后端地址。

### 近期主线（按顺序推进）

1. **SQL 数据访问层**（规划 `tools/pg.py`）：接入土地变化检测 PostgreSQL/PostGIS 库，
   提供 `list_tables` / `describe_table` / `run_sql`（受控只读）等工具；
2. **通用 SQL 问答 Agent**（规划 `agents/sql.py`）：复用 Agent 工具循环完成
   "查询 → 分析 → 问答"，路由新增 `sql` 目标；
3. **快报生成（skill 接入）**：以 skill 形式提供土地流向变化快报模板与生成流程，
   在查询分析结果基础上按模板产出快报；
4. **场景评估 Agent**（规划 `agents/elder_care.py`、`agents/carrying.py`）：
   养老机构可达性分析评估、资源环境承载力评估，路由新增对应目标；
5. 任务规划机制（复杂请求拆分为子任务/子图），按场景需要引入。

### 尚未完成 / 规划中

- PyQGIS 真实空间分析（必须以 worker 进程方式接入，禁止直接 import 进 FastAPI 进程）
- 长期记忆（记忆类型、检索、注入）与上下文压缩（LLM 滚动摘要）
- 工具失败兜底机制（重试 → 换工具 → 澄清 → 询问用户）
- 文件上传（shp / GeoJSON / CSV）与真实数据集管理
- 前端完善：更多 artifact 类型（图片/图表）、地图交互、多轮上下文展示
- 多用户认证/权限、日志与可观测性

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
│       │   ├── subagent.py      # 子 Agent（全新上下文嵌套执行）
│       │   ├── llm.py           # LLMService：多模型、流式、工具调用归一化
│       │   ├── context.py       # ConversationContext：每会话状态（替代全局 shared）
│       │   └── events.py        # 流式事件定义
│       ├── skills.py            # 技能加载器（扫描 / 目录 / load_skill）
│       ├── tools/               # 工具注册与执行
│       │   ├── registry.py      # @register_tool 装饰器、Tool、schema 生成
│       │   ├── executor.py      # 异步执行器（校验、错误归一化）
│       │   ├── result.py        # ToolResult / Artifact
│       │   ├── geo.py           # 地理演示工具
│       │   └── pg.py            # PostGIS 受控 SQL 查询工具层（规划）
│       ├── memory/              # 记忆与会话
│       │   ├── session.py       # 短期消息窗口（裁剪/摘要/清理）
│       │   ├── store.py         # JSONL 会话存储
│       │   └── memory.py        # 长期记忆接口（占位）
│       ├── agents/              # 业务智能体与图编排
│       │   ├── router.py        # 意图路由
│       │   ├── chat.py          # 通用对话
│       │   ├── geo.py           # 地理分析（工具循环）
│       │   ├── sql.py           # 通用 SQL 问答 Agent（规划）
│       │   ├── elder_care.py    # 养老机构可达性评估 Agent（规划）
│       │   ├── carrying.py      # 资源环境承载力评估 Agent（规划）
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
├── skills/                      # 技能目录（每个子目录一个技能：{name}/SKILL.md）
│   └── README.md                # 技能编写说明
└── README.md
```

> 目录中标注"规划"的文件尚未落地，实现后移除标注。
> 快报生成以 skill 形式接入：技能目录为仓库根目录 `skills/`（模板与生成流程由
> `skills/{name}/SKILL.md` 维护），业务代码只负责提供结构化查询结果。

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

#### PostgreSQL/PostGIS 访问规范

- 连接串从环境变量 `GEOAGENT_PG_DSN` 读取，**禁止硬编码**账号密码。
- 使用异步连接池（asyncpg 或 SQLAlchemy async），连接池挂在 `app.state`，
  禁止每次请求新建连接。
- **SQL 受控执行**：LLM 不直接持有数据库连接；所有查询经由受控工具层执行，必须满足：
  只读账号（仅 SELECT 权限）、强制 LIMIT、查询超时、单语句、表/视图白名单、
  查询审计日志。违反任一护栏的查询直接拒绝并返回结构化错误。

### 4.3 如何新增内容

- **新增模型**：在 `geoagent/config.py` 的 `default_model_registry()` 增加一个
  `ModelProfile`。API key 永远从环境变量读取（`api_key_env`），**禁止硬编码 key**。
- **新增工具**：
  1. 在 `geoagent/tools/` 下写函数，用 `@register_tool(name, description, params)`
     注册，`params` 传 Pydantic 模型（自动生成 schema 并校验参数）；
  2. 返回 `ToolResult(content=给LLM的文本摘要, artifacts=[Artifact(kind, data)])`；
     完整数据放 artifacts，content 只放摘要（超长会被截断）；
  3. 在 `tools/geo.py` 的 `get_geo_tools()` 或对应 Agent 的工具列表中加入；
  4. SQL 查询类工具必须走受控执行层（见 4.2），不得裸执行 LLM 生成的任意 SQL。
  所有 Agent 共享的内置工具（todo_write / task / list_skills / load_skill）统一放在
  `tools/builtin.py`，在 `core/agent.py` 中自动合并，业务工具不要与内置工具重名。
  新增内置工具时同步更新本文档与 `backend/README.md` 的协议表。
- **新增技能**：在仓库根目录 `skills/` 下建 `{skill_name}/SKILL.md`（默认技能目录，
  可用 `GEOAGENT_SKILLS_DIR` 覆盖到服务器其他路径）；可选 frontmatter
  （name / description），正文为完整说明；技能名称只用于加载器注册表查询，
  不做文件路径拼接。参考 `skills/README.md`。
- **新增 Agent**：继承 `core.agent.Agent`（提供 `name / system_prompt / tools / model`），
  或按需写自定义 `Node`（参考 `agents/router.py`）。system prompt 用英文。
  场景类 Agent（如可达性、承载力）复用共享 SQL 工具层，只追加场景专用工具，
  不重复实现查询能力。
- **修改图编排**：在 `agents/graph.py` 中用 `node - "action" >> next_node` 组合，
  保留 `build_geo_graph()` 作为默认入口；路由新增目标时同步更新
  `router.py` 的 enum、关键字兜底与本文档协议表。
- **新增前端可见事件**：先扩展 `core/events.py` 的事件类型，在 `server/routes.py`
  的 WebSocket 链路中 emit，并在 `backend/README.md` 的事件表中登记。
- **快报生成（skill 接入）**：快报模板与生成流程由 skill 维护，业务代码只负责
  提供结构化查询结果；新增/修改快报类型时更新 skill 模板，并在本文档登记
  可生成快报的清单。

### 4.4 测试规范

- 新功能必须带 pytest 用例（`backend/tests/`）。
- 单元测试**不得依赖真实 API key / 网络 / 真实数据库**：LLM 相关测试只验证
  配置缺失时的错误路径（如 502 + 提示环境变量）；SQL 工具用 mock 连接池测试
  护栏逻辑（LIMIT 强制、白名单、单语句拒绝、超时）；真实模型/数据库调用
  只允许在手动冒烟脚本中进行。
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
| `route` | 后端→前端 | `target`, `reason` | 路由结果（chat / geo，规划扩展 sql / elder_care / carrying） |
| `token` | 后端→前端 | `delta` | 流式增量文本 |
| `tool_call` | 后端→前端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 后端→前端 | `id`, `name`, `is_error`, `content` | 工具结果摘要 |
| `artifact` | 后端→前端 | `kind`, `name`, `data` | 可视化产物 |
| `todo` | 后端→前端 | `todos` | 任务清单整体更新（todo_write） |
| `subagent_start` | 后端→前端 | `id`, `prompt` | 子 Agent 开始运行（task） |
| `subagent_end` | 后端→前端 | `id`, `is_error`, `content` | 子 Agent 结束并返回最终文本 |
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
GEOAGENT_DEFAULT_MODEL=qwen3.7-flash

# 土地变化检测库（PostgreSQL/PostGIS）
GEOAGENT_PG_DSN=postgresql://user:pass@127.0.0.1:5432/land_change

# 技能目录（默认项目根目录 skills/）
GEOAGENT_SKILLS_DIR=
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
6. **业务数据查询走受控 SQL 工具层**：LLM 不直接连接数据库；所有查询经只读、限行、
   超时、白名单、审计的受控执行器，防止误操作与性能事故。
7. **能力按"共享工具层 + 场景 Agent + 路由"组织**：SQL 查询问答是公共基础能力；
   快报生成通过 skill 接入，与查询链路解耦；养老可达性、承载力等场景各自独立 Agent，
   避免提示词与工具集互相污染。
8. **子 Agent 隔离上下文、技能按需加载**：子任务在全新消息窗口中执行，只返回最终
   文本，避免中间过程污染父上下文；技能目录常驻 system prompt、完整 SKILL.md 按需
   读取，避免把全部文档堆进提示词。
