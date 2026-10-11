# AGENTS.md — GeoAgent 项目说明与开发规范

> 本文件面向后续参与开发的 AI 智能体与人类开发者。所有代码注释、本文档均使用**中文**；
> 所有喂给 LLM 的提示词（system prompt、工具描述、路由指令等）当前统一使用**中文**
> （2026-09 起按业务要求；工具"调用名"仍为英文标识，前端按中文名展示）。

## 1. 项目要做什么

GeoAgent 是一个**通过自然语言对话完成地理空间分析与业务问答**的智能体应用：

- 用户在聊天窗口输入自然语言，Agent 自主规划并调用数据库查询 / 空间分析工具，
  把分析结果以可视化形式渲染在会话窗口内（GeoJSON 图层、表格等），
  而不是打开一个整页底图应用。
- 当前主线：围绕 2026 年第一期地类变化图斑表做**前后变化统计问答**（如"耕地转为
  建设用地的面积是多少"）与 **Word 快报生成**。
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
| 数据与分析引擎 | PostgreSQL/PostGIS（土地变化检测库，当前主线）；PyQGIS（计划，worker 进程隔离） | PostGIS 受控查询层已接入（tools/pg.py） |
| LLM | OpenAI 兼容接口，内置 qwen3.8-27b / qwen3.7-flash / qwen3.7-plus / qwen3.7-max-2026-06-08（默认 qwen3.8-27b） | 已支持多模型切换 |
| 前端 | Vue3 + Vite + OpenLayers | 骨架已搭建（会话列表 + 会话窗口 + 流式工具卡片 + 内嵌地图） |
| 存储 | 会话：JSONL（当前）→ SQLite/PostGIS（规划）；业务数据：PostgreSQL | JSONL 已可用 |

## 2. 目前做到什么程度（2026-09-04）

### 已完成

- **异步图编排**（`backend/geoagent/core/node.py`）：`Node.exec(ctx, payload)` 返回
  `(action, next_payload)`，用 `node - "action" >> next_node` 构图；支持重试、循环检测。
- **Agent 抽象**（`core/agent.py`）：Agent 就是一个 Node，组合了系统提示词 + 工具集 +
  模型配置 + "LLM → 工具 → 再问"循环，可与其他自定义节点混编成图。
- **多模型切换**（`core/llm.py` + `config.py`）：`ModelProfile` 注册表内置
  qwen3.8-27b / qwen3.7-flash / qwen3.7-plus / qwen3.7-max-2026-06-08
  （默认 qwen3.8-27b）；支持 `OPENAI_BASE_URL` 全局覆盖；
  每个会话可通过 REST 接口随时切换模型。
- **工具系统**（`tools/`）：装饰器注册 + Pydantic 参数校验自动生成 LLM schema；
  异步执行器返回结构化错误；`ToolResult` 同时携带给 LLM 的文本 `content` 和
  给前端渲染的 `artifacts`（geojson / table）。
- **上下文压缩与持久化**（`memory/`）：参考 learn-claude-code s08 的四步压缩管线
  （大工具结果转存 → 旧消息归档 → 已读结果占位 → LLM 事实摘要），每次调用模型前执行，
  API 报 prompt_too_long 时补救一次，`compact` 工具可主动压缩；
  孤儿 tool 消息清理；JSONL 会话存储；长期记忆接口占位（`MemoryProvider`）。
- **Agent 内置机制**（`core/agent.py` + `tools/builtin.py`）：所有 Agent 默认携带
  `task` 子 Agent 委派、`list_skills` / `load_skill` 技能按需加载、`compact`
  主动压缩工具；
- **子 Agent 与技能加载**（`core/subagent.py` + `skills.py`）：`task` 工具以全新
  会话上下文运行嵌套 Agent（不持久化、不污染父上下文，子 Agent 不含 task，
  有深度限制），返回最终文本；`list_skills` / `load_skill` 实现技能按需加载——
  启动时扫描 `skills/*/SKILL.md` 建立目录并注入 system prompt，完整说明按需读取。
- **智能体与图**（`agents/`）：RouterNode（LLM 路由 + 关键字兜底，当前目标
  sql / elder_care / chat）、SQLAgent、ChatAgent、ElderCareAgent，
  `build_geo_graph()` 组装默认图。
- **智能体由会话选择，不再依赖 LLM 路由**（`agents/graph.py` + 前端 `ChatWindow.vue`）：
  对话窗口顶部提供“智能体”选择框（选项来自 `GET /api/agents`：sql 土地变化统计问答 /
  elder_care 养老可达性评估 / chat 通用对话），选择结果按会话记忆并随每轮提问以 `agent` 下发；
  `run_turn()` 命中已选智能体时直接执行该 Agent（并发 `route` 事件、reason=“会话选择的智能体”），
  RouterNode 只在未指定/未知智能体时兜底，兼容老客户端。
- **受控 SQL 数据访问层**（`tools/pg.py`）：接入土地变化检测 PostgreSQL/PostGIS 库，
  提供 `list_tables` / `describe_table` / `run_sql` 三个只读工具；强制
  单语句、仅 SELECT、表/视图白名单、外层 LIMIT、查询超时与 JSONL 查询审计日志；
  连接池懒加载并挂在 `app.state.pg`，通过会话上下文注入工具。
- **土地变化统计 SQL 问答 Agent**（`agents/sql.py`）：复用 Agent 工具循环完成
  "查询 → 分析 → 问答"，路由目标 `sql`；白名单 = 图斑主表 + 图斑类型字典
  `dict_tblx` + 合并地类字典表 `dict_land_classification_summary`（编码/名称/一级类/
  三大类），提示词只描述表结构与口径规则，数据知识由模型按需 JOIN/查询；
  提示词第 6 节约定卡片（表格/图表）负责展示明细、正文只给结论式总结，
  避免复述卡片数据；
  `scripts/eval_sql_agent.py` 提供 7 个 golden 用例做准确率回归。
- **确定性统计工具**（`tools/stat.py`）：`summarize_by_type` / `fragment_stats` /
  `farmland_flow_summary` / `construction_change_summary` / `top_conversions`，
  后端预写 SQL 固化 join、口径与合计，模型只调用并依据结果给出结论式回答，
  提升统计稳定性；
  前后时项比较统一在三大类层面（前时项走三调三级映射，后时项走 TBLX 模糊映射）。
- **预警分析（图斑上图）**（`tools/warning.py` + 前端 `ArtifactView.vue`）：
  `warning_yongnong_polygons` / `warning_redline_polygons` 按“建设用地（含疑似，19 类）”口径
  筛出涉及永久基本农田(`SFYN=是`)/生态保护红线(`SFHX=是`)的图斑，一次返回数量面积、
  属性明细表与图斑边界 GeoJSON（输出前统一 `ST_Transform(geom, 4326)`，前端按 4326→3857
  叠加底图渲染）；问题库 19 号条目（疑似占用永农）已改为直接调用该工具，并新增
  `redline_construction_polygons` 条目；结论只能是“疑似”，是否违法需叠加审批数据；
  几何只有 2026 年第二期有（第一期 `geom` 全为空），无几何的期次只给明细并说明无法出图。
- **期次数据集与指标预匹配**（`datasets.py` + `prematch.py`）：一张套合表 = 一期数据，
  期次登记在 `skills/qa-library/assets/datasets.json`（`{table}` 占位符由执行前绑定，
  换期只改资产）；问答链路先解析期次、再按指标匹配标准问题，命中即确定性执行
  （数字与口径不经模型），未命中或执行失败才走自由 SQL 兜底；问题里提到未登记期次时
  明确提示，不用默认期次顶替；自由 SQL 引用非当期表会给出提示而非静默混算；
  已登记数据表：2026 年第一期、第二期（`data."2026_1_change_landuse"` / `data."2026_2_change_landuse"`）。
  **行政区下钻**：问题里出现具体区县（与当期表 `XMC` 精确匹配，如“缙云县”）时，预匹配把
  `county` 传给支持该参数的工具（当前是预警工具，区县清单按表名缓存）；标准条目表达不了这个
  筛选时**不套用整表口径**，改走自由 SQL——避免“统计某县”被静默按全库统计。
- **数据表（期次）选择与亩口径**（前端 `ChatWindow.vue` + `tools/units.py`）：
  会话输入框上方提供“数据表”选择框（选项来自 `GET /api/datasets`，即 datasets.json），
  选择结果按会话记忆在本地并随每轮提问以 `dataset_id` 下发，后端优先按它决定统计哪张表；
  面积在展示层统一换算为亩（库内 `"MJ"` 为平方米，1 平方米 = 0.0015 亩），
  覆盖表格 artifact、工具文本、确定性统计工具与图表，正文单位同步标注为亩。
- **标准问题库扩充**（`skills/qa-library/assets/question_variants.json`，问题库 v1.2）：
  按《智能体问题.docx》19 条问题扩写同义改写/口语化/歧义/对抗问法 95 条；
  同义与口语问法并入条目 aliases（不新增 keywords：关键词按命中比例计分，新增会稀释既有命中；条目 37→41，新增转换活跃度、退耕还林、
  两山转化、总面积与净变化口径解释 4 条）；歧义与对抗问法只登记期望行为、不并入别名，
  用于检验回答是否写明口径、是否拒绝编造；回归用例见 `backend/tests/test_question_variants.py`。
- **LLM 层稳定性**（`core/llm.py`）：流式工具调用按 index/id 分槽、名称去重，修复
  工具名粘连（run_sqlrun_sql）问题；流式正文统一切成小段转发，上游不支持流式时
  回退一次性补全并按小段回放，保证前端逐字追加效果；Agent 空回复自动重试一次。
- **土地变化监测快报**（`skills/land-report/` + `report/briefing.py` +
  `tools/report.py`）：`generate_briefing` 工具按默认模板生成 Word 快报（统计口径
  统计、输出到仓库外目录，默认 `%LOCALAPPDATA%/GeoAgent/reports`，可用
  `GEOAGENT_REPORTS_DIR` 覆盖）；`GET /api/files/reports/{文件名}` 提供下载，
  前端支持 Word 在线预览（docx-preview）；TBLX→三大类默认模糊映射模板在
  `skills/land-report/assets/tblx_categories.json`（用户提供正式规则后替换）；
  **统计期次跟随会话**：数据表由会话上方“数据表”选择框决定（`context_table(ctx)`），
  不选时才回退 datasets.json 的默认期次；标题监测期行、正文与文件名都标注所选期次。
  **预警段落已接入**：第四部分“疑似违法占地情况（预警）”按“涉及永农(SFYN)/红线(SFHX) +
  建设用地（含疑似，19 类）”口径给出数量面积与分县前 10（表4/表5），只作疑似提示，
  违法认定仍需执法与用地管理信息套合数据；卡片只给期次与关键数字（不暴露本机路径/内部链接），
  Word 可在线预览、Word 与 PDF 均可下载；正文出口统一走 `toolcall_guard.scrub_internal_links`
  清掉模型可能补的“### 下载链接 / [Word 版本](/api/files/…)”段落，提示词里也明确禁止。
  用户上传模板支持 `.docx` 与老式 `.doc`：`.doc`（OLE2 二进制）在上传时自动转成
  `.docx`（`report/docconvert.py`，优先 LibreOffice、可用 `GEOAGENT_DOC_CONVERTER` 指定，
  Windows 下回退本机 Word）——因为模板解析与回填基于 `python-docx`，只能读 OOXML；
  回填写回走 `report/docx_styles.py` 的样式兜底，上传模板缺 `List Bullet` 等内置样式也能生成。
- **养老可达性分析**（`analysis/` + `agents/elder_care.py` + `tools/accessibility.py`）：
  共享分析核心（数据集注册、1km 网格需求、步行路网、E2SFCA 与供需匹配），
  场景 Agent 已接入路由目标 `elder_care`；路网由脚本从 OSM PBF 构建，
  无路网时自动回退到直线距离估计并在结果中标注。
- **服务层**（`server/`）：REST（会话 CRUD、模型切换、发消息）+ WebSocket 流式事件
  （token / route / tool_call / tool_result / artifact / message / error / turn_end）。
- **验证**：89 个 pytest 用例全部通过；7 个 golden 用例在 qwen3.7-max-2026-06-08
  下真实跑通（7/7）。
- **前端骨架**（`frontend/`）：左侧历史会话列表 + 右侧会话窗口；流式展示
  token / 路由 / 工具调用卡片（运行中/完成/失败）；GeoJSON 用 OpenLayers
  内嵌小地图渲染、表格用 HTML 表格渲染；会话级模型切换下拉框；Vite 代理
  `/api`（含 WebSocket）到后端，前端不硬编码后端地址；工具卡片按中文名展示
  （`src/toolLabels.js`），文件类 artifact 支持下载与 Word 预览；
  工具调用开始后清掉此前流出的过程话术，正文只保留工具执行后的结论流式输出，
  并展示在工具卡片之后。

### 近期主线（按顺序推进）

1. **快报生成扩展**：v1 已接入 `skills/land-report`（默认模板、全库统计、docx
   输出）；后续支持用户模板上传、单位换算与违法数据接入后扩展模板；
2. **场景评估 Agent**（`agents/elder_care.py` 已实现；`agents/carrying.py` 规划中）：
   养老机构可达性分析评估已接入路由 `elder_care`；资源环境承载力评估待实现；
3. 任务规划机制（复杂请求拆分为子任务/子图），按场景需要引入。

### 尚未完成 / 规划中

- PyQGIS 真实空间分析（必须以 worker 进程方式接入，禁止直接 import 进 FastAPI 进程）
- 长期记忆（记忆类型、检索、注入）
- 工具失败兜底机制（重试 → 换工具 → 澄清 → 询问用户）
- 文件上传（shp / GeoJSON / CSV）与真实数据集管理
- 正式 TBLX→三大类映射（当前为默认模糊模板，待业务规则下发后替换）
- 快报扩展：违法认定的官方审批数据接入（当前预警段落为“疑似”口径）、自定义单位切换
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
│       ├── datasets.py          # 期次数据集注册表（一张套合表 = 一期数据）
│       ├── prematch.py          # 标准问题指标预匹配（命中即确定性执行，未命中转自由 SQL）
│       ├── skills.py            # 技能加载器（扫描 / 目录 / load_skill）
│       ├── tools/               # 工具注册与执行
│       │   ├── registry.py      # @register_tool 装饰器、Tool、schema 生成
│       │   ├── executor.py      # 异步执行器（校验、错误归一化）
│       │   ├── result.py        # ToolResult / Artifact
│       │   ├── accessibility.py # 养老可达性分析工具（E2SFCA / 供需匹配）
│       │   ├── labels.py        # 查询结果中文表头 / TBLX 中文名映射 / 面积按亩展示
│       │   ├── units.py         # 面积单位换算（库内平方米 → 展示亩）
│       │   ├── categories.py    # TBLX→三大类 映射加载器（数据在技能资产 JSON）
│       │   ├── chart.py         # 统计图表生成（make_chart：饼图/柱状图/折线图）
│       │   ├── pg.py            # PostGIS 受控 SQL 查询工具层
│       │   ├── stat.py          # 确定性统计工具（按类型/细碎/耕地流向/建设用地/转换）
│       │   ├── warning.py       # 预警分析（图斑边界 GeoJSON + 明细，前端叠加底图）
│       │   └── report.py        # 快报生成工具（generate_briefing）
│       ├── report/              # 快报统计、模板解析/回填与 docx 生成
│       │   ├── briefing.py      # 预定义统计 SQL + python-docx 渲染
│       │   ├── docconvert.py    # 老式 .doc → .docx 转换（LibreOffice / Word）
│       │   └── docx_styles.py   # 写回样式兜底（上传模板缺内置样式时不报错）
│       ├── memory/              # 记忆与会话
│       │   ├── session.py       # 短期消息窗口（裁剪/摘要/清理）
│       │   ├── store.py         # JSONL 会话存储
│       │   └── memory.py        # 长期记忆接口（占位）
│       ├── analysis/            # 共享空间分析核心（数据集/网格/路网/E2SFCA）
│       ├── agents/              # 业务智能体与图编排
│       │   ├── router.py        # 意图路由
│       │   ├── chat.py          # 通用对话
│       │   ├── sql.py           # 土地变化统计 SQL 问答 Agent
│       │   ├── elder_care.py    # 养老机构可达性评估 Agent
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
│       ├── toolLabels.js        # 工具英文标识 → 前端中文名
│       ├── components/          # 会话列表/窗口/气泡/工具卡片/DocxPreview/Artifact 渲染
│       └── style.css            # 全局样式
├── skills/                      # 技能目录（每个子目录一个技能：{name}/SKILL.md）
│   ├── land-report/             # 土地变化监测快报（默认模板 + 统计需求清单）
│   ├── qa-library/              # 标准问题库 + 问法目录 + 期次数据集（assets/question_library.json、question_variants.json、datasets.json）
│   └── README.md                # 技能编写说明
└── README.md
```

> 目录中标注"规划"的文件尚未落地，实现后移除标注。
> 快报生成以 skill 形式接入：技能目录为仓库根目录 `skills/`（模板与生成流程由
> `skills/{name}/SKILL.md` 维护），后端提供确定性统计与 docx 渲染能力
> （`report/briefing.py` + `tools/report.py`）。

## 4. 开发规范

### 4.1 语言约定（重要）

- **LLM 提示词与描述统一中文**：system prompt、工具 description、参数 description、
  路由指令、技能说明等。工具**调用名**保持英文标识（OpenAI 兼容接口要求 ASCII），
  前端按 `toolLabels.js` 展示中文名。若模型遵从度下降，可整体切回英文并同步本文档。
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

#### 面积单位约定

- 数据库 `"MJ"` 与统计 SQL 的 `area` / `area_m2` / `*_m2` 列都是**平方米**；
- 对用户可见的输出（正文、表格 artifact、图表、快报）一律按**亩**展示，换算关系 1 平方米 = 0.0015 亩（1 亩 = 666.6667 平方米），保留两位小数；
- 展示层换算统一走 `tools/units.py`（`humanize_table` / `convert_mu_headers` / `normalize_unit`），不要在各自的 SQL 里乘系数，避免重复换算；报告与模板链路各自在展示端换算，不经这里。

### 4.3 如何新增内容

- **新增模型**：在 `geoagent/config.py` 的 `default_model_registry()` 增加一个
  `ModelProfile`。API key 永远从环境变量读取（`api_key_env`），**禁止硬编码 key**。
- **新增工具**：
  1. 在 `geoagent/tools/` 下写函数，用 `@register_tool(name, description, params)`
     注册，`params` 传 Pydantic 模型（自动生成 schema 并校验参数）；
  2. 返回 `ToolResult(content=给LLM的文本摘要, artifacts=[Artifact(kind, data)])`；
     完整数据放 artifacts，content 只放摘要（超长会被截断）；
  3. 在对应业务 Agent 的工具列表中加入（SQL 类工具走受控层 `tools/pg.py`）；
  4. SQL 查询类工具必须走受控执行层（见 4.2），不得裸执行 LLM 生成的任意 SQL。
  5. 高频/明确口径的统计模式建议做成**确定性工具**（参考 `tools/stat.py`：后端预写
     SQL、Pydantic 参数、返回中文表格 artifact），并在 SQLAgent 提示词第 5 节登记。
  6. 工具的"是什么、参数怎么填、适用哪类问题"只写在注册的 description / 参数
     description 里（调用时随 `tools` 参数下发）；system prompt **不复述**工具说明，
     只写 schema 表达不了的策略（口径默认值、工具优先级、正文与卡片分工等），
     避免同一件事两处维护、改工具时提示词变成过期信息。
  所有 Agent 共享的内置工具（task / list_skills / load_skill / compact）统一放在
  `tools/builtin.py`，在 `core/agent.py` 中自动合并，业务工具不要与内置工具重名。
  新增内置工具时同步更新本文档与 `backend/README.md` 的协议表。
- **新增技能**：在仓库根目录 `skills/` 下建 `{skill_name}/SKILL.md`（默认技能目录，
  可用 `GEOAGENT_SKILLS_DIR` 覆盖到服务器其他路径）；可选 frontmatter
  （name / description），正文为完整说明；技能名称只用于加载器注册表查询，
  不做文件路径拼接。参考 `skills/README.md`。
- **新增期次数据**（一张套合表 = 一期）：在 `skills/qa-library/assets/datasets.json`
  的 `datasets` 追加一条（id / label / table / year / round / aliases），必要时改
  `default_id`；问题库与统计工具的 SQL 只写 `{table}` 占位符，口径写在问题条目里、
  与期次无关，因此**不要**在 SQL、提示词或工具代码里写死某一期的表名。
  已登记期次表会**始终**自动并入白名单（即使显式配置了 `GEOAGENT_PG_WHITELIST` 也并入），新登记一期无需再改 `.env`。
- **新增 Agent**：继承 `core.agent.Agent`（提供 `name / system_prompt / tools / model`），
  或按需写自定义 `Node`（参考 `agents/router.py`）。system prompt 用中文。
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
  分支渲染（geojson→OpenLayers、table→HTML 表格、file→下载链接/Word 预览、其他→
  JSON 预览、chart→饼图/柱状图/折线图（`ChartView.vue`）），并登记协议表。
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
| `route` | 后端→前端 | `target`, `reason` | 本轮使用的智能体（sql / elder_care / chat，规划扩展 carrying）；会话选择了智能体时 reason 为“会话选择的智能体”，表示未经路由模型判断 |
| `standard_match` | 后端→前端 | `matched`, `mode`, `entry_id`, `confidence`, `reasons`, `candidates`, `dataset_id`, `dataset_label`, `table` | 标准问题指标预匹配结果（`matched=false` 表示转自由 SQL 兜底；期次未登记时也会带出提示） |
| `token` | 后端→前端 | `delta` | 流式增量文本 |
| `tool_call` | 后端→前端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 后端→前端 | `id`, `name`, `is_error`, `content` | 工具结果摘要 |
| `artifact` | 后端→前端 | `kind`, `name`, `data` | 可视化产物（geojson / table / chart / file 等）；geojson 可带 `data.style`（预警图斑为红色描边 + 半透明红填充） |
| `subagent_start` | 后端→前端 | `id`, `prompt` | 子 Agent 开始运行（task） |
| `subagent_end` | 后端→前端 | `id`, `is_error`, `content` | 子 Agent 结束并返回最终文本 |
| `message` | 后端→前端 | `role`, `content`, `model` | 最终助手消息 |
| `error` | 后端→前端 | `message` | 错误信息 |
| `turn_end` | 后端→前端 | `conversation_id` | 一轮对话结束 |
| `user` | 前端→后端 | `content`, `agent`, `dataset_id`, `workflow_id`, `question_id`, `source` | 用户消息；`agent` 为会话旁“智能体”选择框选中的智能体（不传则走 RouterNode 兜底），`dataset_id` 为输入框上方“数据表”选择框选中的期次（不传则按问题文本识别） |

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
GEOAGENT_DEFAULT_MODEL=qwen3.8-27b

# 土地变化检测库（PostgreSQL/PostGIS）
GEOAGENT_PG_DSN=postgresql://user:pass@127.0.0.1:5432/land_change
GEOAGENT_PG_WHITELIST=data."2026_1_change_landuse",data."2026_2_change_landuse",knowledge_base.dict_tblx,knowledge_base.dict_land_classification_summary

# 快报等生成文件输出目录（默认 %LOCALAPPDATA%/GeoAgent/reports，仓库外）
GEOAGENT_REPORTS_DIR=

# 技能目录（默认项目根目录 skills/）
GEOAGENT_SKILLS_DIR=

# 问答链路：先标准问题指标匹配（execute/hint/off），未命中再自由 SQL 兜底
GEOAGENT_QA_PREMATCH=execute
GEOAGENT_QA_PREMATCH_CONFIDENCE=0.5
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
9. **上下文压缩完全采用 learn-claude-code s08 的四步管线**：先做无模型调用的整理
   （大工具结果转存到磁盘、中间历史归档到 transcripts、已读工具结果占位），
   字符仍超限时才调用 LLM 生成只含事实的状态摘要，并用 [Compacted] 消息替换历史；
   每次调用模型前执行，API 报 prompt_too_long 时补救一次，`compact` 工具可主动压缩。
