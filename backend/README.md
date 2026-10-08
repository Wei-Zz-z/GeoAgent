# GeoAgent Backend

## 快报与区县地图扩展

模板动态查询与回填、区县地图统计以及两个本机stdio MCP的安装、调用顺序和支持边界，见[接入说明](../docs/快报与区县地图接入说明.md)。PDF和自由问数的支持限制分别列出，不以Word生成成功替代全部内容验收。

## 固定模板解析与报告工作流

前端会话内提供“上传快报模板”入口，当前支持不超过10MB的DOCX。后端
`POST /api/report-templates/parse?filename=模板.docx` 解析标题、正文、表格、占位项、样式、图片和图表锚点，返回带原文、原子查询项与稳定回填位置的结构化JSON。

解析问题会与 `skills/qa-library/assets/question_library.json` 中的标准问题进行可解释规则匹配。候选仅供推荐，用户可修改问题、选择候选并确认；确认后问题进入当前智能体会话，展示受控只读SQL调用、结果表格和口径说明。每项查询只执行一次，原始结果同时用于对话、模板回填和图表生成。

当示例模板所需的五项标准问题全部完成后，可按上传模板生成Word报告，并在运行环境支持时同步转换PDF。正文、表格、饼图和柱状图复用同一批查询结果，生成过程及文件下载卡片均写入当前会话。此流程只保证标准问题库已覆盖的统计口径；问题库外的问题仍需人工确认或由通用问数能力处理。

默认固定快报继续保留，当前统一使用亩、正式数字不加千分位逗号，并增加多类原生Word图表。查询时间取本次统计开始时的服务器系统时间，监测期仍取数据库数据所属期。

基于 **uv + FastAPI + OpenAI 兼容接口** 的对话式地理空间分析后端框架。

核心设计（借鉴并改进了 `poipoi-agent` 的 Node/Flow、Tool 注册、LLM 调用方式）：

- **Node / Flow**：异步图编排。`Node.exec(ctx, payload)` 返回 `(action, next_payload)`，
  用 `node - "action" >> next_node` 构图，支持循环（Agent 内部就是一个循环）。
- **Agent**：一个自包含的节点 = 系统提示词 + 工具集 + 模型配置 + "LLM→工具→再问"循环。
  不同功能的 Agent 可以通过图编排组合（路由、规划、分析、总结…）。
- **模型切换**：`ModelProfile` 注册表，任意 OpenAI 兼容端点（OpenAI / 智谱 / DeepSeek…），
  每个会话可单独切换模型，Agent 也可固定自己的模型。
- **工具注册**：装饰器注册 + Pydantic 参数校验，自动生成 LLM function schema；
  工具结果携带 `artifacts`（GeoJSON/表格等），供前端在会话窗口内可视化。
- **Agent 内置机制**：task 子 Agent（全新上下文）、
  list_skills / load_skill 技能按需加载（目录注入 system prompt，全文按需读取）、
  上下文压缩（s08 四步管线：大结果转存 / 旧消息归档 / 已读结果占位 / LLM 摘要，
  每次调模型前执行，compact 工具主动压缩，prompt_too_long 补救一次）。
- **多会话**：会话与消息持久化到 `data/conversations/*.jsonl`（未做登录，dev 模式全局可见）。

## 快速开始

```bash
cd backend
uv sync                                   # 安装依赖（首次会下载托管 Python）
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000
```

打开 http://127.0.0.1:8000/docs 查看接口。快报生成后会保留 Word，并在运行机器安装
Microsoft Word（Windows）或 LibreOffice 时同步生成 PDF；可用
`GEOAGENT_PDF_CONVERTER` 指定 LibreOffice 可执行文件。

## 关键接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/models` | 可用模型列表（含是否已配 key；已内置阿里千问 qwen3.8-27b / qwen3.7-flash / qwen3.7-plus / qwen3.7-max-2026-06-08） |
| GET/POST | `/api/conversations` | 会话列表 / 创建会话 |
| DELETE | `/api/conversations/{id}` | 删除会话（含消息文件） |
| GET | `/api/conversations/{id}/messages` | 历史消息（含 artifacts） |
| PUT | `/api/conversations/{id}/model` | 切换该会话的模型 |
| POST | `/api/conversations/{id}/messages` | 发消息（非流式，返回最终回复） |
| WS | `/api/conversations/{id}/ws` | 流式对话：token / tool_call / tool_result / artifact / message 事件 |

会话列表按创建时间倒序返回（最新在前）。新建会话默认为"新会话"标题，
收到首条用户消息后自动截取消息内容作为标题（最长 20 字，超出加省略号）。

## WebSocket 事件协议

前端唯一依赖此协议渲染对话过程（前后端解耦的契约，新增事件需在此登记）：

| 事件类型 | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `turn_start` | 服务端→客户端 | `conversation_id` | 一轮对话开始 |
| `route` | 服务端→客户端 | `target`, `reason` | 路由结果（sql / elder_care / chat） |
| `token` | 服务端→客户端 | `delta` | 流式增量文本 |
| `tool_call` | 服务端→客户端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 服务端→客户端 | `id`, `name`, `is_error`, `content` | 工具执行结果（摘要文本） |
| `artifact` | 服务端→客户端 | `kind`, `name`, `data` | 可视化产物（geojson / table / chart / file 等） |
| `subagent_start` | 服务端→客户端 | `id`, `prompt` | 子 Agent 开始运行（task） |
| `subagent_end` | 服务端→客户端 | `id`, `is_error`, `content` | 子 Agent 结束并返回最终文本 |
| `message` | 服务端→客户端 | `role`, `content`, `model` | 最终助手消息 |
| `error` | 服务端→客户端 | `message` | 错误信息 |
| `turn_end` | 服务端→客户端 | `conversation_id` | 一轮对话结束 |
| `user` | 客户端→服务端 | `content` | 用户发送消息 |

客户端发送格式：`{"type": "user", "content": "..."}`。

`token` 事件始终以小段转发：后端会先把上游文本切成 12 字左右的小片再逐片推送；
若接口不支持流式（例如带工具时流式不可用），后端自动回退到一次性补全，
并把正文按同样的小片回放给前端，保证正文回复始终呈逐字追加效果。

## 前端联调

```bash
# 终端 1：启动后端
cd backend
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000

# 终端 2：启动前端（Vite 将 /api 代理到后端，含 WebSocket）
cd frontend
npm install
npm run dev          # 打开 http://localhost:5173
```

端到端冒烟（需前后端已启动、已配置千问 key）：

```bash
cd frontend
node scripts/smoke.mjs
```

## 图编排示例

```python
from geoagent.agents import build_geo_graph

# RouterNode 判断意图 -> "sql" 走 SQLAgent（土地变化统计/数据库问答），
# "elder_care" 走 ElderCareAgent，否则 ChatAgent
router = RouterNode(model="qwen3.7-flash")
sql = SQLAgent(model="qwen3.7-plus")
elder = ElderCareAgent(model="qwen3.7-plus")
chat = ChatAgent()
router - "sql" >> sql
router - "elder_care" >> elder
router - "chat" >> chat
flow = Flow(router)
```

新增一个 Agent 或自定义节点，然后用 `- action >>` 连进图即可；Agent 内部自带工具循环。

## 养老可达性分析（ElderCareAgent）

场景 Agent 已接入：路由目标 `elder_care`，核心能力为
**路网步行可达性 + E2SFCA 可达性指数 + 街道/区供需匹配**。

可用工具：

| 工具 | 说明 |
| --- | --- |
| `describe_dataset` | 查看数据集要素数 / 字段 / bbox |
| `build_demand_grid` | 生成 1km 需求网格（七普区级老年人口按面积分摊） |
| `nearest_facility` | 各需求网格到最近养老机构的步行时间 |
| `isochrone` | 机构周边步行等时圈 |
| `e2sca_analysis` | E2SFCA 可达性指数（网格 / 街道 / 区三级输出） |
| `supply_demand_summary` | 按街道聚合覆盖床位与千人床位数 |

有真实步行路网（`data/road/beijing_walk_{nodes,edges}.csv`）时使用路网 Dijkstra，
否则回退到直线距离估计并在结果中标注。数据准备见仓库根目录 `data/README.md`。

本地冒烟（不调用 LLM）：`backend/.venv/Scripts/python.exe scripts/smoke_elder_care.py`

## SQL 数据查询（SQLAgent）

路由目标 `sql`：围绕 2026 年第一期地类变化图斑表的"查询 → 分析 → 问答"，
统计土地资源的前后变化（原土地类型 → 图斑类型）。所有查询经由受控 SQL 工具层
`geoagent/tools/pg.py` 执行，LLM 不直接持有数据库连接。

可用工具：

| 工具 | 说明 |
| --- | --- |
| `list_tables` | 列出白名单内的可用表（含中文表名与描述） |
| `describe_table` | 查看白名单表的列结构（information_schema） |
| `run_sql` | 执行只读 SELECT 并返回表格 artifact（自动强制 LIMIT） |
| `make_chart` | 生成饼图/柱状图/折线图 artifact（构成占比、类别对比、趋势变化） |

构成/占比类问题用饼图、各类别数量/面积对比用柱状图、随时间变化的趋势用折线图；
选型规则登记在 make_chart 的工具描述里（提示词不复述工具说明），
图表数据必须来自查询结果并保持口径一致。

正文与卡片的分工：表格/图表卡片负责完整展示明细，最终正文只给结论式总结——
直接回答用户问题，关键数字（总量、净变化、最大项等）带单位出现一次，
再点出最值得注意的 1–3 个要点；用户明确要求列出/详细说明时才在正文展开明细，
避免把卡片数据逐行复述一遍。

受控护栏（任一不满足即拒绝并返回结构化错误）：

- 仅允许单条 SELECT（禁止 WITH / EXPLAIN / 分号 / 任何 DML / DDL）
- 表/视图白名单：图斑主表 `data."2026_1_change_landuse"` + 图斑类型字典
  `knowledge_base.dict_tblx` + 合并地类字典表
  `knowledge_base.dict_land_classification_summary`（原三张地类字典合并，含编码/名称/
  一级类/三大类；可用 `GEOAGENT_PG_WHITELIST` 覆盖）
- 强制外层 LIMIT（默认 200 行，`GEOAGENT_PG_MAX_ROWS` 可调）
- 查询超时（默认 10 秒，`GEOAGENT_PG_TIMEOUT_S` 可调）
- 查询审计日志（`data/pg_audit.jsonl`，含 SQL、耗时、行数、错误）

注意：该库的表名与列名多为带引号的大写标识符（如 `"TBLX"`、`"XZQDM"`），
SQL 中需按 `describe_table` 返回的原文加双引号，否则 PostgreSQL 会折叠为小写
导致"列不存在"。

业务口径（已写入 SQLAgent 提示词）：

- 原土地类型 = `"DLBM"`/`"DLMC"`（三调地类体系，含农用地/建设用地/未利用地大类）
- 图斑类型（变化后） = `"TBLX"`（影像地类体系，33 类；数据中 1-4 前导零为 01-04）
- 面积 `"MJ"` 单位平方米（当前按平方米测试，确认其他单位后再调整）
- 无修饰的"地类/耕地/建设用地"默认按原土地类型统计；"图斑类型/变化后"按 `TBLX`
- TBLX→三大类：默认模糊映射模板（`skills/land-report/assets/tblx_categories.json`），
  用户提供正式规则后替换

展示映射：`run_sql` 返回表格的表头与 `TBLX` 值自动映射为中文
（见 `geoagent/tools/labels.py`）；字段语义或字典更新时需同步该文件。

提示词瘦身：SQLAgent 的 system prompt 只保留"表结构索引 + 口径 + 纪律"；
编码/名称/一级类等数据知识放在白名单字典（dict_tblx、合并表
dict_land_classification_summary）中，由模型按需 JOIN/查询；其他字段结构用
describe_table 查看，快报流程在 `skills/land-report`。

连接串从环境变量读取（禁止硬编码账号密码）：

```env
GEOAGENT_PG_DSN=postgresql://user:pass@192.168.3.209:5432/zhejiang_agent_project
GEOAGENT_PG_WHITELIST=data."2026_1_change_landuse",knowledge_base.dict_tblx,knowledge_base.dict_land_classification_summary
```

准确率评估（手动运行，真实库 + 真实 LLM）：

```bash
backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py --refs-only   # 只验证参考 SQL
backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py               # 跑 LLM 问答并比对
```

## 土地变化监测快报（land-report）

对话中说"生成快报/简报"时，SQLAgent 按 `skills/land-report/SKILL.md` 流程调用
`generate_briefing` 工具：对图斑表全量执行预定义统计（耕地流出/流入/净变化、
流向建设用地、恢复性地类流入、新增建设用地、净减少/新增面积 Top10 县等），
按默认模板结构生成 Word 文件到**仓库外**的目录
（默认 `%LOCALAPPDATA%/GeoAgent/reports`，可用 `GEOAGENT_REPORTS_DIR` 覆盖）。
前端通过 `GET /api/files/reports/{文件名}` 下载/预览（返回 .docx，文件名做目录穿越校验）。

工具/技能展示已中文化：前端工具卡片按中文名展示（映射见
`frontend/src/toolLabels.js`），工具描述、技能说明与 SQLAgent/路由提示词均为中文。

- 口径与 SQLAgent 知识卡一致：原土地类型 = DLBM/DLMC，图斑类型 = TBLX；
  TBLX→三大类使用临时映射 `geoagent/tools/categories.py`（正式映射下发后替换）
- 面积单位平方米，暂未换算；疑似违法占地段落留空（需执法/审批数据）
- 用户模板上传与单位换算为后续扩展项

## 目录

```text
geoagent/
├── core/          # node(图) / llm(模型切换) / agent(智能体循环) / context / events
├── tools/         # 装饰器注册 + Pydantic 校验 + 执行器 + pg 受控 SQL 层 + 快报/统计工具
├── memory/        # 短期会话窗口（滚动摘要/截断） + 会话存储 + 长期记忆接口占位
├── agents/        # 路由器 / SQL 问答 / 通用对话 / 养老评估 Agent 与图编排
└── server/        # FastAPI 应用与路由
```

## 下一步规划

- PyQGIS 分析以 worker 进程方式接入（替换演示工具）
- 快报生成（skill 接入）：在 SQL 查询结果基础上按模板生成土地流向变化快报
- 养老机构点位数据补充（民政名录 + 坐标化）、分区 60+ 比例补齐
- 可达性结果前端可视化完善（等时圈/供需图层交互）
- 长期/短期记忆、上下文压缩、工具失败兜底、任务规划
- 前端完善：更多 artifact 类型（图片/图表）、地图交互、多轮上下文展示
