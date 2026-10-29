# GeoAgent Backend

## 快报与区县地图扩展

模板动态查询与回填、区县地图统计以及两个本机stdio MCP的安装、调用顺序和支持边界，见[接入说明](../docs/快报与区县地图接入说明.md)。PDF和自由问数的支持限制分别列出，不以Word生成成功替代全部内容验收。

## 固定模板解析与报告工作流

前端会话内提供“上传快报模板”入口，当前支持不超过 10MB 的 DOCX；老式 `.doc` 会在上传时
自动转成 DOCX（优先 LibreOffice，可用 `GEOAGENT_DOC_CONVERTER` 指定路径；Windows 下回退
本机 Microsoft Word），因为模板解析与回填基于 `python-docx`，只能读 OOXML。后端
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
- **多会话**：会话与消息持久化到 PostgreSQL（`geoagent` schema 的 `conversations` +
  `conversation_messages`），按登录用户归属；入口验证走省域平台 2.0 ticket
  （`GEOAGENT_AUTH_MODE=ticket`），未开启时为 dev 模式（不校验、会话全局可见）。

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
| POST | `/api/auth/ticket` | 用省域平台 `sykjzlptv2_ticket` 换登录态（ticket 一次性、2 分钟超时；2 分钟内重复提交复用已签发凭据） |
| GET | `/api/auth/me` | 当前登录用户（dev 模式返回开发身份；顺带把 Cookie 续到完整有效期） |
| POST | `/api/auth/logout` | 退出登录（清 Cookie） |
| GET | `/api/models` | 可用模型列表（含是否已配 key；已内置阿里千问 qwen3.8-27b / qwen3.7-flash / qwen3.7-plus / qwen3.7-max-2026-06-08） |
| GET | `/api/datasets` | 已登记的期次数据集（一张套合表 = 一期数据，供前端展示/选择期次） |
| GET | `/api/overlay/options` | 套合表单选项：四个图层说明、年份/期次候选、默认表名 |
| GET | `/api/overlay/datasets` | 当前已登记期次（套合完成后应立刻出现新期次） |
| POST | `/api/overlay/build` | 上传图层并启动套合任务（multipart：`hsx`/`qsx` 必填，`yjn`/`hx` 可选；字段 `table_name`/`year`/`round`/`overwrite`/`register`） |
| GET | `/api/overlay/jobs/{id}` | 套合任务进度与结果（前端轮询） |
| GET | `/api/overlay/jobs` | 最近套合任务列表 |
| GET | `/api/overlay/tables` | 套合数据清单：data schema 下的表 + 行数/面积/几何/期次登记，并给出是否具备删除前备份能力 |
| POST | `/api/overlay/delete` | 删除一期套合数据（`dataset_id`/`table_name`/`confirm`/`backup`）：默认先备份再删表，并摘掉期次登记 |
| GET | `/api/agents` | 可选智能体目录（sql 土地变化统计问答 / elder_care 养老可达性评估 / chat 通用对话），供会话旁的“智能体”选择框使用 |
| GET/POST | `/api/conversations` | 会话列表 / 创建会话 |
| DELETE | `/api/conversations/{id}` | 删除会话（含消息文件） |
| GET | `/api/conversations/{id}/messages` | 历史消息（含 artifacts） |
| PUT | `/api/conversations/{id}/model` | 切换该会话的模型 |
| POST | `/api/conversations/{id}/messages` | 发消息（非流式，返回最终回复） |
| WS | `/api/conversations/{id}/ws` | 流式对话：token / tool_call / tool_result / artifact / message 事件 |

会话列表按创建时间倒序返回（最新在前）。新建会话默认为"新会话"标题，
收到首条用户消息后自动截取消息内容作为标题（最长 20 字，超出加省略号）。

## 入口验证（省域平台 2.0 ticket）

按《省域平台2.0浙政钉用户接入文档2023》接入：平台场景跳转时在 URL 上携带
`sykjzlptv2_ticket`（可能落在查询串，也可能落在 `#` 之后的片段里），前端取出后调用
`POST /api/auth/ticket`；后端用 httpx 请求
`https://kjzl.zrzyt.zj.gov.cn/bev2/api/Ticket/ValidateTicket?ticket=...` 换取用户信息
（ticket 只能用一次、2 分钟超时），再把用户签成 HttpOnly Cookie（HMAC-SHA256，
服务端不存会话表），之后所有 `/api` 与 WebSocket 握手都按该 Cookie 鉴权。

- 前端取的参数名默认 `sykjzlptv2_ticket`，可用 Vite 环境变量 `VITE_TICKET_PARAM` 覆盖；
- 开关：`GEOAGENT_AUTH_MODE=dev`（默认，行为同改造前）| `ticket`（开启入口验证并按用户隔离会话）；
- `dev` 模式下中间件注入“开发模式”身份，会话归属为 `dev`，现有联调方式与用例不受影响；
- 同一 ticket 在有效期内重复提交会复用已签发凭据，避免用户刷新页面被判定为票据失效；
- 登录、失败与退出写入 `data/auth_audit.jsonl`（不记录 ticket 本身）；
- 会话按登录用户隔离，访问他人会话统一 404；改造前遗留的无主会话仍可见（避免历史数据消失）；
- 现场自检：`uv run python ../scripts/check_ticket_auth.py` 先看本机到平台的网络连通性，
  加 `--ticket xxx` 可真实校验一次票据（ticket 一次性，运行即消费）；
- 已知限制：快报文件下载接口对所有登录用户共享，未按用户隔离。

## 会话存储（PostgreSQL）

会话与消息由 `ConversationStore`（`memory/store.py`）写入 PostgreSQL：默认与业务库同实例，
可用 `GEOAGENT_CHAT_DSN` 指到独立库；schema 默认 `geoagent`（`GEOAGENT_CHAT_SCHEMA` 可改），
应用启动时自动建表（幂等）。

| 表 | 字段 | 说明 |
| --- | --- | --- |
| `conversations` | `id / title / model / owner_id / owner_name / created_at / updated_at` | 会话元数据；`owner_id` 为省域平台返回的用户 Id（dev 模式为 `dev`） |
| `conversation_messages` | `conversation_id / role / payload(jsonb) / created_at` | 每条消息一行，`payload` 与旧 JSONL 行等价（含 `tool_calls` / `artifacts`），按自增 id 排序，会话删除时级联清理 |

约束与约定：

- Agent 循环仍是同步内存操作：新增消息先由 `ConversationSession` 暂存，每轮结束（成功或失败）
  统一 `flush()` 落库，保证 `turn_end` 之后前端能拉到完整历史；
- 会话按登录用户隔离，访问他人会话统一 404；改造前遗留的无主会话（`owner_id` 为空）仍可见；
- `GEOAGENT_CHAT_STORE=memory` 改用内存实现（单元测试与未配库的本地联调用，重启即丢失）；
  未配置任何 DSN 时会退回内存并打印告警，不会让服务起不来；
- 旧的 `data/conversations/*.jsonl` 已不再读取、也不做迁移，可以直接删除。

## MCP 对外接口（Streamable HTTP）

其他前端/服务可以不改协议、直接用 MCP 调用本系统的问答与快报能力，端点挂在同一个后端上
（**给对方看的完整对接文档见 [`docs/MCP对接说明.md`](../docs/MCP对接说明.md)**）：
`POST https://<域名>/mcp/`（stateless_http=True，不需要保持 MCP 会话）。

配置：

```bash
# 令牌必须是 <令牌>:<调用方名> 形式，逗号分隔；由 MCP 层校验，未配置时 /mcp 一律拒绝
GEOAGENT_MCP_TOKENS=abc123:门户A
# 快报下载链接有效期（秒，默认 1 小时）
GEOAGENT_MCP_DOWNLOAD_TTL_S=3600
# 对外基地址：配上以后返回的下载链接是绝对地址
GEOAGENT_PUBLIC_BASE_URL=https://geoagent.example.gov.cn
```

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `ask` | `question`（必填）、`history`（调用方维护的多轮上下文）、`dataset_id`、`agent_id`、`include_data` | `answer`（最终结论）、`route`、`dataset_id`、`is_error`；`include_data=true` 时附 `artifacts` |
| `generate_briefing` | `dataset_id`（留空用默认期次） | `period`、`summary`、`files[].url`（签名下载链接）、`expires_at` |
| `list_datasets` | 无 | `default_id`、`datasets`、可用 `agents` |

约定：

- **不维护用户会话**：`ask` 用不落库的内存上下文执行，多轮上下文由调用方每轮回传；
  这条链路没有进程内状态，服务端可以多实例部署；
- **只记审计**：每次 `ask` 往 `geoagent.mcp_calls` 写一条「提问 + 最终回复」
  （调用方、`X-GeoAgent-User` 透传的最终用户、期次、路由、耗时、是否成功），不存过程消息；
- **不返回思考过程**：只回最终结论文本，模型的工具轨迹不外发；
- **快报文件**：`files[].url` 是带过期时间的签名链接，浏览器直接点开即可下载；
  主令牌只放调用方后端，不要下发给浏览器；
- **鉴权各管各的**：`/mcp` 的令牌由 MCP 层校验（未带或带错返回 401 + `WWW-Authenticate`，
  未配置令牌时一律拒绝），web 登录中间件只管 `/api` 的 Cookie 会话；MCP 令牌只对 `/mcp` 有效；
- 快报下载接口自己校验凭据：登录 Cookie 或签名链接，中间件只放行不代判；
- nginx 反代 `/mcp` 时记得 `proxy_buffering off` 与较大的 `proxy_read_timeout`。

联调与自检：

```bash
# 一条命令查完：存活 / 401 鉴权 / 令牌不越权 / 握手 / 工具发现 / 期次 / 快报+签名下载 / 真实问答
cd backend && uv run python ../scripts/check_mcp.py --url http://127.0.0.1:8000/mcp/
# 不想生成文件、不想调模型时
cd backend && uv run python ../scripts/check_mcp.py --skip-ask --skip-briefing
# 图形化排查
npx @modelcontextprotocol/inspector   # 端点填 /mcp/，请求头带 Authorization: Bearer <令牌>
```

`check_mcp.py` 默认从 `GEOAGENT_MCP_TOKENS`（或 `backend/.env`）取第一个令牌，
逐项打印 [通过]/[失败]，全部通过时退出码 0，可直接用于部署后的验收与 CI。

已知限制：本机 stdio MCP（`geoagent.mcp_server`）通过进程内 HTTP 调用本服务的 `/api`，
在 `GEOAGENT_AUTH_MODE=ticket` 下会因没有登录态而失败；需要继续用它时保持 dev 模式，
或把它的工具改为直接调用内部函数。

## 套合入库（前后时相 → 一期套合表）

入口：对话窗口上方的「套合入库」按钮（大弹窗，不跳页；`/?view=overlay` 仍可直接访问），命令行入口 `uv run python ../scripts/overlay_cli.py`。
上传后时相（变化监测图斑）、前时相（变更地类图斑）与可选的永久基本农田、生态保护红线，
系统在 PostGIS 内按后时相边界切分前时相碎片、挂接母斑属性、“相交即算”标记永农/红线、
按碎片几何重算面积，几何统一转 EPSG:4490 写入 `data."<表名>"`（结构与现有两期一致，含
主键、索引、注释与更新时间触发器），并把这一期登记进 `skills/qa-library/assets/datasets.json`。

- 表名由用户填写；年份与期次用下拉框选择，默认表名 = `年份_期次_change_landuse`。
- 同名表默认拒绝写入，勾选“覆盖同名表”才会 DROP 重建；套合失败会回滚目标表并清理暂存表。
- 未上传永农/红线时，`SFYN`/`SFHX` 统一记为“否”，并在结果里给出提示。
- 输入支持 Shapefile 套装（.shp/.shx/.dbf/.prj/.cpg 一次选中）、Shapefile ZIP 或 GeoJSON；
  `.prj` 无法识别 EPSG 时按 CGCS2000 3 度带 40 带（4528）绑定，属性表编码按
  UTF-8 → GBK → GB18030 逐个尝试。
  只缺 `.shx` 时自动重建空间索引；一份 ZIP 内含多套 Shapefile 时按图层名（HSX / QSX / 永农 / 红线）自动匹配，
  四个位置可以上传同一份 ZIP。漏选 `.dbf`/`.prj` 会在提交时直接提示缺哪些文件，不再抛 GDAL 原文错误。
- 套合数据管理：弹窗底部列出 data schema 下的套合表，可删除不再需要的期次。删除必须把表名
  原样输入确认；默认先 `pg_dump` 备份到 `<data_dir>/overlay_backups`（可用 `GEOAGENT_PG_DUMP`
  指定 pg_dump 路径），备份失败即中止；删除后同步摘掉 `datasets.json` 登记并刷新白名单与
  前端"数据表"下拉框。同一时间有套合任务在跑时不允许删除。- 套合是重任务（全量数据分钟级），接口只负责起任务，进度由 `GET /api/overlay/jobs/{id}` 轮询；
  同一时间只允许一个套合任务。
- 写库使用独立写入句柄（需要建表权限），问答链路的只读受控网关与白名单不受影响；
  通过页面套合时，运行中的进程会自动刷新期次表与白名单，无需重启；用命令行脚本登记后需重启后端才生效。
## 卡片文案（工具名 / 卡片标题 / 表格字段）

- 工具卡片名：英文调用名 → 中文名映射在 `frontend/src/toolLabels.js`，新工具必须登记
  （`backend/tests/test_tool_labels.py` 会校验）。
- 卡片标题：后端 `artifact.name` 是给程序用的英文标识符（报告链路按它取数），
  展示走 `frontend/src/artifactLabels.js` 的 `artifactLabel(name, kind)`：含汉字的名称原样使用，
  已登记的英文标识符翻译为中文，未登记的按卡片类型兜底（保证卡片上不出现英文）。
- 表格字段名：工具产出的 table artifact 列名一律中文（面积列走 `tools/units.py` 换算成亩）；
  自由 SQL 结果由 `tools/labels.py` 的 `humanize_table` 翻译——库内字段走 `COLUMN_LABELS`，
  模型自写的英文别名（`cnt` / `area_mu` / `tblx_name` 等）走 `SQL_ALIAS_LABELS`，
  SQLAgent 提示词第 4 节第 10 条也要求别名用中文。
- 提示：翻译发生在查询时，因此改动前生成的历史消息仍保留当时的表头；重新提问即为中文表头。
## WebSocket 事件协议

前端唯一依赖此协议渲染对话过程（前后端解耦的契约，新增事件需在此登记）：

| 事件类型 | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `turn_start` | 服务端→客户端 | `conversation_id` | 一轮对话开始 |
| `route` | 服务端→客户端 | `target`, `reason` | 本轮使用的智能体（sql / elder_care / chat）；会话已选择智能体时 reason 为“会话选择的智能体”，不再调用路由模型 |
| `standard_match` | 服务端→客户端 | `matched`, `mode`, `decision`, `entry_id`, `score`, `confidence`, `p_correct`, `calibrated`, `margin`, `risk`, `threshold`, `p_threshold`, `vector_used`, `vector_entries`, `judge_used`, `judge_entry`, `judge_confidence`, `judge_reason`, `resolved_question`, `data_version`, `reasons`, `slots`, `slot_labels`, `candidates`, `candidate_details`, `caliber`, `dataset_id`, `dataset_label`, `table` | 语义预匹配结果；`decision=execute` 才确定性执行，`candidates/clarify` 不查库，`fallback` 转自由 SQL；`confidence` 为校准后的 p(correct)；`resolved_question` 为多轮补全后的问题 |
| `token` | 服务端→客户端 | `delta` | 流式增量文本 |
| `tool_call` | 服务端→客户端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 服务端→客户端 | `id`, `name`, `is_error`, `content` | 工具执行结果（摘要文本） |
| `artifact` | 服务端→客户端 | `kind`, `name`, `data` | 可视化产物（geojson / table / chart / file 等）；geojson 可带 `data.style`，预警图斑为红色描边 + 半透明红填充 |
| `subagent_start` | 服务端→客户端 | `id`, `prompt` | 子 Agent 开始运行（task） |
| `subagent_end` | 服务端→客户端 | `id`, `is_error`, `content` | 子 Agent 结束并返回最终文本 |
| `message` | 服务端→客户端 | `role`, `content`, `model`, `number_guard`, `query_state` | 最终助手消息；`number_guard` 为自由 SQL 数字溯源，`query_state` 供下一轮追问继承 |
| `error` | 服务端→客户端 | `message` | 错误信息 |
| `turn_end` | 服务端→客户端 | `conversation_id` | 一轮对话结束 |
| `user` | 客户端→服务端 | `content`, `agent`, `dataset_id` | 用户发送消息；`agent` 指定本轮智能体（不传走 RouterNode 兜底），`dataset_id` 指定期次表 |

客户端发送格式：`{"type": "user", "content": "...", "agent": "sql", "dataset_id": "2026_2"}`；`agent` 与 `dataset_id` 均可省略。

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

前端 store 回归（不需要后端，`uv run pytest` 也会跑这条）：

```bash
cd frontend
node --import ./scripts/esm-register.mjs ./scripts/check-chat-store.mjs
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
- 面积 `"MJ"` 库内单位为平方米；对用户展示统一换算为亩（1 平方米 = 0.0015 亩，见 `tools/units.py`）
- 无修饰的"地类/耕地/建设用地"默认按原土地类型统计；"图斑类型/变化后"按 `TBLX`
- TBLX→三大类：字典驱动映射（`skills/land-report/assets/tblx_categories.json`），
  清单以 `knowledge_base.dict_tblx` 为正式来源，6 个边界类型标记 pending_business；
  用户提供正式规则后替换

展示映射：`run_sql` 返回表格的表头与 `TBLX` 值自动映射为中文、面积列换算为亩
（见 `geoagent/tools/labels.py` 与 `geoagent/tools/units.py`）；字段语义或字典更新时需同步这些文件。

预警分析（图斑上图）：`warning_yongnong_polygons` / `warning_redline_polygons` 按
“建设用地（含疑似，19 类：`tools/categories.py` 的 `construction_codes()`）”口径筛出涉及
永久基本农田（`SFYN=是`）/生态保护红线（`SFHX=是`）的图斑，一次返回数量面积、属性明细表与
图斑边界 GeoJSON；几何取自主表 `geom`，输出前统一 `ST_Transform(geom, 4326)`，前端以
EPSG:4326 → EPSG:3857 叠加底图（OSM）渲染。几何当前只有 2026 年第二期有（第一期 `geom`
全为空），没有几何的期次只给明细并在回答里说明无法出图；结论只能是“疑似”，是否违法需叠加
农转用/供地等审批数据。预警图层随 GeoJSON 下发 `style`（红色描边 + 半透明红填充），前端按它渲染红色预警图斑。有几何时地图与明细表在同一个 artifact 里（`data.linked_table`，`feature_key=objectid` ↔ `row_key=主键`）：点表格行会缩放到对应图斑并红色高亮（再点取消），点图斑也会反选表格行；没有几何的期次退化成纯明细表。底图为天地图 WMTS（矢量底图 `vec_c` + 中文注记 `cva_c`，配置见 `frontend/src/mapConfig.js`，可用 `VITE_TIANDITU_TOKEN` 覆盖内置密钥）。
**坐标系统一用经纬度（EPSG:4326）**：天地图 `c` 矩阵集实测是 EPSG:4490 经纬度网格
（能力文档 TopLeftCorner=90/-180、第 1 级 2×1 瓦片），地图 View 与 GeoJSON 都按 4326 处理、
不做重投影，瓦片分辨率取 `360/256/2^z`；按 Web Mercator 建网格会取到空白瓦片、底图与数据对不上。
瓦片加载失败时仅在控制台告警（底图只用天地图，不切换到其它底图服务）。
问题里出现具体区县（与当期表 `XMC` 精确匹配，如“缙云县”）时，预匹配会把 `county` 下钻给
预警工具（区县清单按表名缓存，泛称如“每个县/哪些县”不会误判）；若命中的条目表达不了这个
筛选，则不套用整表口径、直接转自由 SQL，避免按全库口径误答。

提示词瘦身：SQLAgent 的 system prompt 只保留"表结构索引 + 口径 + 纪律"；
编码/名称/一级类等数据知识放在白名单字典（dict_tblx、合并表
dict_land_classification_summary）中，由模型按需 JOIN/查询；其他字段结构用
describe_table 查看，快报流程在 `skills/land-report`。

连接串从环境变量读取（禁止硬编码账号密码）：

```env
GEOAGENT_PG_DSN=postgresql://user:pass@192.168.3.209:5432/zhejiang_agent_project
GEOAGENT_PG_WHITELIST=data."2026_1_change_landuse",data."2026_2_change_landuse",knowledge_base.dict_tblx,knowledge_base.dict_land_classification_summary
# datasets.json 中登记的全部期次表会自动并入白名单（显式配置时也会并入）
GEOAGENT_QA_PREMATCH=execute
GEOAGENT_QA_PREMATCH_CONFIDENCE=0.42
# 向量召回 + RRF（0=纯规则；无 OPENAI_API_KEY 时自动降级）
GEOAGENT_QA_VECTOR=1
# 灰度区（candidates/clarify）是否调用 LLM judge 复核候选
GEOAGENT_QA_JUDGE=1
```

准确率评估（手动运行，真实库 + 真实 LLM）：

```bash
backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py --refs-only   # 只验证参考 SQL
backend/.venv/Scripts/python.exe scripts/eval_sql_agent.py               # 跑 LLM 问答并比对
```

### 文本化工具调用的兜底（tool-call 标记泄漏）

部分 OpenAI 兼容端点偶发不把模型输出的 XML 风格工具调用解析成结构化 `tool_calls`，
而是把标记留在正文里（`<function_calls><invoke name="run_sql">...`）。若不处理，
标记会被当成最终回答流给前端并落库，污染会话历史、诱导模型模仿。

`core/toolcall_guard.py` 提供三层处理，`core/agent.py` 与 `core/llm.py` 分别接入：

- 流式转发前用 `StreamLeakFilter` 缓冲疑似标记，完整标记整段丢弃（不会先显示再删）；
- 正文里还原出的调用若指向已注册工具，补成真正的工具调用继续执行（消息里带 `tool_calls`）；
- 还原不了就剥掉标记只留正文；整条回复只有标记时纠正模型一次，仍失败则给出明确说明，
  绝不让标记外发或落库。

历史数据可用脚本清洗（数据库版：扫描 `conversation_messages`，写入前把受影响行备份成 JSONL）：

```bash
backend/.venv/Scripts/python.exe scripts/scrub_conversation_leaks.py          # 预览
backend/.venv/Scripts/python.exe scripts/scrub_conversation_leaks.py --apply  # 执行
```

## 问数链路：期次数据集、指标预匹配与自由 SQL 兜底

一张套合表 = 一期数据，因此"表名"和"统计口径"必须分开管理：口径写在标准问题条目里
（`skills/qa-library/assets/question_library.json`，跨期次不变），表名登记在
`skills/qa-library/assets/datasets.json`（`id` / `label` / `table` / `year` / `round` / `aliases`），
问题库与确定性工具里的 SQL 只写 `{table}` 占位符，执行前按当期绑定。

一轮问答的顺序（`GEOAGENT_QA_PREMATCH`）：

1. **解析期次**：前端传 `dataset_id` → 问题文本里识别（别名 / "2026年第二期" 之类）→ 默认期次；
   问题里提到的期次若未登记，直接说明并停止，不用默认期次顶替；
2. **语义匹配**：`qa_semantics.py` 先做查询理解（主体 / 前后时项 / 聚合 / 维度 / 限定 /
   转换方向），再召回标准问题并做槽位重排；字面分只作为特征之一。默认再叠加
   `qa_library.vector_scores_many` 的 embedding 余弦相似度，与词法排名做 RRF（只做有界加分），
   无 API Key/调用失败时自动回退纯规则。`confidence` 是离线逻辑回归校准后的
   `p(correct)`，`score` 是原始匹配分；p < 0.60 时不自动执行。输出
   `decision=execute|candidates|clarify|fallback`，中/高风险口径（占比、流向、永农、红线）
   由 `terms.json` 抬高自动执行门槛；
3. **确定性执行**：`execute` 时按条目的 `strategy` 调确定性工具或执行参考 SQL，
   正文由结果生成，数字与口径不经过模型，并附口径卡（主体/范围/期次/单位/风险）；
   正文不使用 Markdown `#` 标题，图斑类型不附字段编码，期次不展示内部表名；
4. **结果校验**：`qa_verify.py` 校验空结果、负值、比例范围、净变化=流入-流出、
   按类型分项合计、区县筛选；校验失败会在正文显著标注，不把可疑数字当结论；
5. **候选/澄清**：`candidates` 给 2~3 个最接近的标准问题让用户选；`clarify` 只问一个
   关键口径问题（含对抗/编造要求），两者都不查库；灰度区可再调用一次 LLM judge 复核
   Top-3（`GEOAGENT_QA_JUDGE=1`），judge 不直接执行，只维持候选/澄清；
6. **自由 SQL 兜底**：未命中、执行失败或 `hint` 模式时，进入 SQLAgent 的 LLM 工具循环，
   并把识别到的槽位与候选条目作为“理解计划”注入提示词，不再是静默降级；
7. **自由 SQL 数字溯源**：`number_guard.py` 把最终正文中的数字与本轮 `tool` 消息的
   `content`/`artifact` 逐条对照；可机械修正的单位错误直接修正，不能修正的用证据重写一次，
   仍失败则阻断正文只保留表格；`message` 事件带 `number_guard` 的
   `corrections/rewrite_attempted/blocked`；
8. **多轮追问**：`dialogue.py` 在预匹配前补全 `那第二期呢`、`这个县呢`、`再按类型分`、
   `那林地呢`；`query_state` 随最终消息持久化，换 REST/WS 连接后仍能继承上一轮问题。

语义层资产（业务可编辑，位于 `skills/qa-library/assets/`）：

- `terms.json`：主体/过滤/聚合/维度/时项/转换方向/风险规则/决策阈值；
- `metric_cards.json`：42 条标准问题的指标卡（由 `scripts/build_metric_cards.py` 生成）；
- `match_calibration.json`：top-1 p(correct) 校准参数（由 `scripts/fit_match_calibration.py` 拟合）；
- `field_dictionaries.json`：TZ / QSX / CZCSXM 业务字典（未确认值保留原码）；
- `data_versions.json`：数据/口径/映射版本与变更记录；
- `question_holdout.json` / `question_locked.json`：独立留出与锁定评估问法。

事件上 `standard_match` 会带出 `decision`、`score`、`p_correct`、`calibrated`、`margin`、
`risk`、`slots`、`slot_labels`、`caliber`、`candidate_details`、`resolved_question`；
`message` 事件可带 `caliber`、`verification`、`number_guard`、`query_state`。工具卡片仍沿用
`tool_call` / `tool_result` / `artifact`，前端不改也能展示。

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
  TBLX→三大类使用 `geoagent/tools/categories.py` 加载字典资产（清单以 dict_tblx 为准，
  边界项待业务确认）；
- 面积库内为平方米、展示统一按亩换算（`tools/units.py`）
- 统计期次跟随会话上方“数据表”选择框（`tools/report.py` 用 `context_table(ctx)` 取表），
  标题监测期行、正文与输出文件名都标注所选期次
- 卡片行为：正文只给期次与关键数字，不暴露本机路径与内部下载链接；文件以 artifact 下发，
  卡片里 Word 可在线预览、Word/PDF 均可下载（PDF 不走 docx-preview，避免预览失败）。
  正文出口还会做一次兜底清理（`core/toolcall_guard.scrub_internal_links`），模型即使补了
  “### 下载链接 / [Word 版本](/api/files/…)”也会被去掉；SQLAgent 提示词里同样禁止输出下载链接
- 第四部分“疑似违法占地情况（预警）”：涉及永农(`SFYN=是`)/红线(`SFHX=是`) 且变化后类型属于
  建设用地（含疑似，19 类），给出数量面积与分县前 10（表4/表5）；只作疑似提示，
  违法认定需执法与用地管理信息套合数据
- 用户模板上传、自定义单位切换为后续扩展项

## 目录

```text
geoagent/
├── core/          # node(图) / llm(模型切换) / agent(智能体循环) / context / events
├── datasets.py    # 期次数据集注册表（一张套合表 = 一期数据，{table} 占位符绑定）
├── prematch.py    # 标准问题指标预匹配（命中即确定性执行，未命中转自由 SQL）
├── tools/         # 装饰器注册 + Pydantic 校验 + 执行器 + pg 受控 SQL 层 + 快报/统计/预警工具
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
