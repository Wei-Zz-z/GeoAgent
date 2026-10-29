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
- 支持多用户、多会话：已接入省域平台 2.0 ticket 入口验证（`GEOAGENT_AUTH_MODE=ticket`）
  并按登录用户隔离会话；未开启时仍是 dev 模式（不校验、会话全局可见），权限分级待做。

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
| 存储 | 会话与消息：PostgreSQL（`geoagent` schema，默认与业务库同实例，可用 `GEOAGENT_CHAT_DSN` 分库）；
业务数据：PostgreSQL | 已入库 |

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
  孤儿 tool 消息清理；PostgreSQL 会话存储（`geoagent` schema，Agent 循环内存暂存、每轮 flush 落库）；
  长期记忆接口占位（`MemoryProvider`）。
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
  前后时项比较统一在三大类层面（前时项走三调三级映射；后时项 TBLX 清单以
  `knowledge_base.dict_tblx` 为正式来源，分类边界项标记 pending_business）。
- **预警分析（图斑上图）**（`tools/warning.py` + 前端 `ArtifactView.vue`）：
  `warning_yongnong_polygons` / `warning_redline_polygons` 按“建设用地（含疑似，19 类）”口径
  筛出涉及永久基本农田(`SFYN=是`)/生态保护红线(`SFHX=是`)的图斑，一次返回数量面积、
  属性明细表与图斑边界 GeoJSON（输出前统一 `ST_Transform(geom, 4326)`，前端按 4326→3857
  叠加底图渲染）；问题库 19 号条目（疑似占用永农）已改为直接调用该工具，并新增
  `redline_construction_polygons` 条目；结论只能是“疑似”，是否违法需叠加审批数据；
  几何只有 2026 年第二期有（第一期 `geom` 全为空），无几何的期次只给明细并说明无法出图。
  **地图与表格联动**：有几何时只出一个 artifact，GeoJSON 上挂 `data.linked_table`
  （`feature_key=objectid` ↔ `row_key=主键`）；前端 `ArtifactView.vue` 在地图下方渲染该表，
  **点表格行会缩放到对应图斑并红色高亮**（再点一次取消），点图斑也会反选表格行。
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
- **智能问数语义匹配与准确性优化**（`backend/geoagent/qa_semantics.py` + `qa_verify.py` +
  `skills/qa-library/assets/terms.json`、`metric_cards.json`）：预匹配从“别名/关键词整句打分”
  升级为“查询理解（主体/前后时项/聚合/维度/限定/转换方向）→ 候选召回 → 槽位重排 →
  风险分级决策”；低风险基础阈值 0.42，中/高风险由 `terms.json` 继续抬高；输出
  `execute` / `candidates` / `clarify` / `fallback`，候选与澄清不查库，未命中时把
  “理解计划”注入自由 SQL，避免静默降级；执行后校验空结果、负值、比例范围、
  净变化=流入−流出、分项合计与区县筛选；回答附口径卡（主体/范围/期次/单位/风险）与
  校验结论，匹配决策写入 `data/qa_match_audit.jsonl`。离线评测（留一别名问法）：
  自动执行率 10.5%→73.7%（叠加 embedding 向量召回 + RRF + p(correct) 校准后 93.0%），
  Top-1 70.2%→100%，误执行 0；独立留出 38 条 65.8%→92.1%、
  锁定 19 条 73.7%→100%。置信度已校准为 top-1 p(correct)，
  ECE 约 0.046~0.054（锁定集 0.0538）；灰度区可选 LLM judge 复核候选。
  向量召回默认开启（`GEOAGENT_QA_VECTOR=1`）、LLM judge 默认开启
  （`GEOAGENT_QA_JUDGE=1`，只在 candidates/clarify 调用）；无 API Key 或调用失败时
  自动降级为纯规则。评估脚本 `scripts/eval_question_matching.py`
  （`--vector` 对比向量效果），校准训练 `scripts/fit_match_calibration.py`，
  指标卡生成 `scripts/build_metric_cards.py`。
  **自由 SQL 数字溯源**（`number_guard.py`）：正文数字逐条对照本轮工具 content/artifact；
  可机械修正的单位错误直接修正，不能修正的用证据重写一次，仍失败则阻断正文只保留表格；
  `message` 事件带 `number_guard` 的 `corrections/rewrite_attempted/blocked`。
  **多轮追问**（`dialogue.py`）：`那第二期呢`、
  `这个县呢`、`再按类型分`、`那林地呢` 继承上一轮完整问题；规则覆盖不了的短追问
  走一次结构化状态抽取（只输出 resolved_question/fields/confidence）；`query_state`
  随最终消息持久化，跨 REST/WS 恢复。
- **套合入库（前后时相切分 → 一期套合表）**（`overlay/` + `server/overlay_routes.py` +
  前端对话窗口上方的「套合入库」按钮，大弹窗承载、不跳页；`/?view=overlay` 仍可直接访问）：上传后时相（变化监测图斑）、前时相（变更地类图斑）与可选的
  永久基本农田、生态保护红线，在 PostGIS 内按后时相边界切分前时相碎片、挂接母斑属性、
  “相交即算”标记永农/红线、重算面积（平方米），几何统一转 EPSG:4490 后写入
  `data."<用户指定表名>"`（表结构、索引、注释、更新时间触发器与现有两期完全一致），
  并自动把这一期登记进 `datasets.json`，前端“数据表”选择框与问答/快报链路随即可用
  （走页面时运行中的进程会自动刷新白名单，命令行登记后需重启后端）。表名由用户填写，
  年份与期次用下拉框选择，
  默认表名 = `年份_期次_change_landuse`。已实测：抽稀版四图层约 9 秒完成，27,722 行 /
  90 个县，行数、各县分布、图斑类型与地类分布与现有套合表完全一致，红线标记 1332 行一致；
  全量数据预计分钟级。命令行入口 `scripts/overlay_cli.py` 可离线跑同一套流程。
  **套合数据管理**：套合弹窗内列出 `data` schema 下的全部套合表（期次、行数、面积、几何覆盖、
  创建时间），可删除不再需要的期次——删除前必须把表名原样输入确认，默认先用 `pg_dump` 备份到
  `<data_dir>/overlay_backups`（备份失败即中止，不出现"备份没成功但表已删"），删表后同步摘掉
  `datasets.json` 登记并刷新运行时白名单与前端"数据表"下拉框。  源数据的三个坑已在 `overlay/layers.py` 兜住：`.prj` 无法自动识别 EPSG（按 CGCS2000
  3 度带 40 带即 4528 绑定）、存在未闭合环（OGR 容错 + 库内 `ST_MakeValid`）、
  `.cpg` 声明 UTF-8 实为 GBK（编码候选逐个尝试）。
- **LLM 层稳定性**（`core/llm.py`）：流式工具调用按 index/id 分槽、名称去重，修复
  工具名粘连（run_sqlrun_sql）问题；流式正文统一切成小段转发，上游不支持流式时
  回退一次性补全并按小段回放，保证前端逐字追加效果；Agent 空回复自动重试一次。
- **土地变化监测快报**（`skills/land-report/` + `report/briefing.py` +
  `tools/report.py`）：`generate_briefing` 工具按默认模板生成 Word 快报（统计口径
  统计、输出到仓库外目录，默认 `%LOCALAPPDATA%/GeoAgent/reports`，可用
  `GEOAGENT_REPORTS_DIR` 覆盖）；`GET /api/files/reports/{文件名}` 提供下载，
  前端支持 Word 在线预览（docx-preview）；TBLX→三大类映射资产在
  `skills/land-report/assets/tblx_categories.json`（字典驱动，边界项待业务确认）；
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
  **可整体关闭**：`GEOAGENT_ELDER_CARE=0` 时启动阶段不再导入 `analysis/` 的重依赖
  （geopandas / pandas / networkx / shapely），`/api/agents`、路由枚举与前端选择框
  同步隐藏该场景，选到它时返回“未启用”提示；部署机上按需开启——这套分析栈常驻
  会把内存抬高一个量级（路网图按 networkx 建图后不释放）。
- **入口验证与用户体系（省域平台 2.0 ticket）**（`auth/` + `server/auth_routes.py` +
  前端 `src/auth.js` + `components/LoginGate.vue`）：按《省域平台2.0浙政钉用户接入文档2023》，
  平台场景跳转时在 URL 上携带 `sykjzlptv2_ticket`（可能落在查询串，也可能落在 `#` 之后的片段里，
  后端收不到片段，所以由前端取参数）；前端把 ticket 交给 `POST /api/auth/ticket`，
  后端用 httpx 调 `GET /bev2/api/Ticket/ValidateTicket?ticket=...` 换用户信息（ticket 一次性、
  2 分钟超时，成功取 `result`，失败透传 `message`），随后把用户签成自包含的 HttpOnly Cookie
  （HMAC-SHA256，服务端不存会话表，重启不掉线）；此后所有 `/api`（含 WebSocket 握手）按该 Cookie 鉴权，
  未登录返回 401、WS 以 4401 关闭。同一 ticket 在 2 分钟内重复提交复用已签发凭据，配合前端
  `history.replaceState` 抹掉地址栏上的 ticket，避免刷新页面被“ticket 无效或超时”踢出去；
  登录/失败/退出写 `data/auth_audit.jsonl` 审计（不记录 ticket 本身）。
  **会话按用户隔离**：`ConversationStore` 增加 `owner_id`/`owner_name`，列表、读写、WS 都按登录用户过滤，
  访问他人会话一律 404（不暴露其是否存在）；改造前遗留的无主会话（`owner_id` 为空）仍对登录用户可见，
  避免升级后历史会话消失。开关 `GEOAGENT_AUTH_MODE=dev|ticket` 默认 dev，现有联调与用例行为不变。
  已知限制：快报文件 `/api/files/reports/{文件名}` 目前对所有登录用户共享，未按用户隔离。

- **MCP 对外接口（Streamable HTTP，供其他前端/服务调用）**（`mcp_http.py` + `stateless.py` +
  `auth/downloads.py`）：把问答与快报能力以 MCP 工具暴露给外部系统，挂载在同一个后端的 `/mcp`。
  **三个工具**：`ask`（无状态问答，调用方自带 history，只返回最终结论文本，不返回模型的思考过程与
  工具轨迹，需要大产物时用 `include_data=true`）、`generate_briefing`（默认模板快报，返回**带有效期
  的签名下载链接**）、`list_datasets`（只读列出已登记期次与可用智能体）。
  **不维护用户会话**：问答走 `ConversationSession(store=None)` 的内存上下文，不写会话表，因此没有
  「内存状态绑单进程」的限制；多轮上下文与历史由调用方自己维护、每轮回传。**只记审计**：每次 `ask`
  往 `geoagent.mcp_calls` 写一条「提问 + 最终回复」（外加调用方、透传用户标识、期次、路由、耗时、
  是否成功），不存过程消息。
  **鉴权各管各的**：MCP 接口的令牌由 **MCP 层自己校验**（SDK 的 `TokenVerifier`：
  `GEOAGENT_MCP_TOKENS` 配 `令牌:调用方名`，请求带 `Authorization: Bearer <token>`；
  未带或带错返回 401 + `WWW-Authenticate`，未配置任何令牌时一律拒绝）。web 登录中间件只管 `/api`
  的 Cookie 会话，不参与 MCP 鉴权；MCP 令牌也只对 `/mcp` 有效（拿它调 `/api/conversations` 仍是 401）。
  最终用户身份用请求头 `X-GeoAgent-User` 透传，只进审计不做隔离。
  **快报下载**：`GET /api/files/reports/{文件名}` 自己校验凭据——登录 Cookie 或
  `?e=<过期时间>&s=<签名>` 的一次性链接（外部前端的浏览器直接点开即可下载，不需要把主令牌
  下发给浏览器）；中间件只把该路径列入「自己负责鉴权」的白名单。
  自检：`scripts/check_mcp.py` 一条命令验证存活、401 鉴权、令牌不越权、握手、工具发现、期次、
  快报签名下载与真实问答（`--skip-ask --skip-briefing` 可只做连通性检查），退出码非 0 即不可用。
  已知限制：本机 stdio MCP（`mcp_server.py`）在 `GEOAGENT_AUTH_MODE=ticket` 下会因内部的 `/api`
  调用没有登录态而失败，需要它的话要么用 dev 模式，要么把那批工具改成直接调用内部函数。

- **服务层**（`server/`）：REST（会话 CRUD、模型切换、发消息）+ WebSocket 流式事件
  （token / route / tool_call / tool_result / artifact / message / error / turn_end）。
- **验证**：352 个 pytest 用例；7 个 golden 用例在
  qwen3.7-max-2026-06-08 下真实跑通（7/7）。本机环境下 5 个 Word/模板/区县图表相关
  用例因临时目录 `os.replace` 拒绝访问或陈旧 mock 失败（`test_manual_binding` 偶发），
  改动前的干净副本同样失败（环境问题，与代码无关）。
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
- 文件上传与数据集管理：套合工作台已支持 Shapefile 套装 / ZIP 上传并入库成期次，其余任意数据集管理待办
- 外部审批数据接入（违法认定仍为疑似口径）、自定义单位切换、TBLX 6 个边界类型的业务确认
- 快报扩展：违法认定的官方审批数据接入（当前预警段落为“疑似”口径）、自定义单位切换
- 前端完善：更多 artifact 类型（图片/图表）、地图交互、多轮上下文展示
- 权限分级（按 Role/NodesAuth 限制套合入库、删除期次等重操作）、快报文件按用户隔离、
  日志与可观测性

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
│       ├── auth/                # 入口验证与用户体系（票据校验/会话凭据/中间件/签名下载/审计）
│       ├── datasets.py          # 期次数据集注册表（一张套合表 = 一期数据）
│       ├── qa_semantics.py      # 语义槽位匹配 + 向量 RRF + p(correct) 校准
│       ├── qa_judge.py          # 灰度区 LLM judge 复核候选
│       ├── number_guard.py      # 自由 SQL 正文数字 ↔ 工具结果溯源
│       ├── dialogue.py          # 多轮追问补全与 QueryState
│       ├── qa_verify.py         # 确定性结果校验（比例/合计/净变化/区县）
│       ├── stateless.py         # 无状态问答执行（MCP 用：不落会话，只记审计）
│       ├── mcp_http.py          # MCP 对外接口（Streamable HTTP：ask / 快报 / 期次）
│       ├── mcp_server.py        # 本机 stdio MCP（快报工作流，面向本机客户端）
│       ├── prematch.py          # 标准问题指标预匹配（命中即确定性执行，未命中转自由 SQL）
│       ├── skills.py            # 技能加载器（扫描 / 目录 / load_skill）
│       ├── tools/               # 工具注册与执行
│       │   ├── registry.py      # @register_tool 装饰器、Tool、schema 生成
│       │   ├── executor.py      # 异步执行器（校验、错误归一化）
│       │   ├── result.py        # ToolResult / Artifact
│       │   ├── accessibility.py # 养老可达性分析工具（E2SFCA / 供需匹配）
│       │   ├── labels.py        # 查询结果中文表头 / TBLX/TZ/QSX/CZCSXM 映射 / 面积按亩展示
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
│       │   ├── store.py         # 会话存储（PostgreSQL 实现 + 内存实现）
│       │   └── memory.py        # 长期记忆接口（占位）
│       ├── overlay/             # 套合入库（前后时相切分 → 一期套合表）
│       │   ├── spec.py          # 图层规范、必填字段、表名与期次命名
│       │   ├── layers.py        # 矢量读取（编码/未闭合环/坐标系兜底）
│       │   ├── ddl.py           # 目标表建表、索引、注释 SQL
│       │   ├── db.py            # 写入型数据库句柄（与只读网关分离）
│       │   ├── runner.py        # 套合流程编排（解析→暂存→切分→标记→入库→登记）
│       │   └── registry.py      # 套合完成后登记期次（datasets.json）
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
│           ├── auth_routes.py   # 入口验证接口（ticket 换登录态 / 当前用户 / 退出）
│           ├── routes.py        # REST + WebSocket
│           ├── overlay_routes.py # 套合上传、任务进度与期次查询接口
│           └── schemas.py       # API 请求/响应模型
├── frontend/                    # Vue3 + Vite + OpenLayers
│   ├── vite.config.js           # /api 代理（REST + WS）到后端
│   ├── scripts/smoke.mjs        # 端到端冒烟脚本（需前后端已启动）
│   └── src/
│       ├── api/client.js        # REST 封装 + WebSocket 事件入口
│       ├── auth.js              # 入口验证：ticket 换登录态 + 当前用户
│       ├── stores/chat.js       # 轻量响应式会话 store
│       ├── toolLabels.js        # 工具英文标识 → 前端中文名
│       ├── mapConfig.js         # 底图配置（天地图 WMTS + OSM 回退）
│       ├── components/          # 会话列表/窗口/气泡/工具卡片/DocxPreview/Artifact 渲染
│       └── style.css            # 全局样式
├── skills/                      # 技能目录（每个子目录一个技能：{name}/SKILL.md）
│   ├── land-report/             # 土地变化监测快报（默认模板 + 统计需求清单）
│   ├── qa-library/              # 标准问题库 + 语义资产 + 期次（assets/question_library.json、terms.json、metric_cards.json、match_calibration.json、question_variants.json、question_holdout.json、question_locked.json、datasets.json、field_dictionaries.json、data_versions.json）
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
     完整数据放 artifacts，content 只放摘要（超长会被截断）；table artifact 的列名用中文，面积列走 `tools/units.py`；
  3. 在对应业务 Agent 的工具列表中加入（SQL 类工具走受控层 `tools/pg.py`）；
     同时在 `frontend/src/toolLabels.js` 的 `TOOL_LABELS` 登记卡片中文名——工具卡片只展示
     中文名（未登记时兜底显示“工具调用”，英文调用名仅在悬浮提示里）；
     `backend/tests/test_tool_labels.py` 会校验“已注册工具都有中文名”，漏登记会直接失败；
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
- **新增/调整语义匹配口径**：主体、同义词、聚合、维度、过滤、转换方向、风险规则改
  `skills/qa-library/assets/terms.json`；新增标准问题或改动问句后运行
  `backend/.venv/Scripts/python.exe scripts/build_metric_cards.py` 刷新指标卡；
  改完运行 `scripts/eval_question_matching.py --check`（有 API Key 时再加 `--vector`），
  确认留一别名覆盖率、误执行与独立留出集不退化；向量召回可用 `GEOAGENT_QA_VECTOR=0` 关闭。
  校准参数用 `scripts/fit_match_calibration.py` 重新拟合；LLM judge 可用
  `GEOAGENT_QA_JUDGE=0` 关闭。指标卡与自动推导不一致时 `test_semantic_matching.py` 会失败。
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
- **底图统一走 `src/mapConfig.js`**：天地图 WMTS（矢量底图 `vec_c` + 中文注记 `cva_c`，
  同一密钥轮询 t0-t7 子域），密钥优先取前端环境变量 `VITE_TIANDITU_TOKEN`、缺省用内置 key；
  **坐标系必须用经纬度**：天地图 `c` 矩阵集实测是 EPSG:4490 经纬度网格（能力文档
  TopLeftCorner=90/-180、第 1 级 2×1 瓦片），所以地图 View 与 GeoJSON 都用 EPSG:4326、
  瓦片分辨率取 `360/256/2^z`；按 Web Mercator 建网格会请求到空白瓦片，导致底图与数据对不上；
  底图**只用天地图**（瓦片加载失败仅在控制台告警、地图容器底色透出，不切其它底图服务）。
  新增地图组件复用 `createBaseLayers()`，
  不要各自创建底图图层。注意：浏览器端密钥必然打进前端产物，请在天地图控制台按域名做白名单限制。
- **会话隔离**：切换 / 新建会话时，异步回包与旧 WebSocket 事件必须按"发起时的会话 id"判废
  （`stores/chat.js` 的 `selectConversation` / `refreshMessages` / `connect` / `switchModel`），
  否则旧会话的消息或流式事件会串进新会话；行为回归见 `frontend/scripts/check-chat-store.mjs`
  （`backend/tests/test_chat_store_race.py` 会调用，本机无 node 时跳过）。
- **不要用 JS 内置对象名做 import 绑定**：OpenLayers 的 `Map` 必须写成 `OlMap`。遮蔽内置 `Map`
  会让 `new Map()` 静默变成 OpenLayers 地图对象，组件卸载时抛错、Vue 卸载流程中断、旧 DOM 残留
  （曾导致"新建会话仍显示上一段对话"）；`backend/tests/test_frontend_guards.py` 会静态拦截。- UI 文案使用中文：
  - 工具卡片名：新工具必须在 `frontend/src/toolLabels.js` 登记中文名（`backend/tests/test_tool_labels.py` 会校验）；
  - 卡片标题：后端 `artifact.name` 是给程序用的英文标识符（报告链路按它取数），
    展示统一走 `frontend/src/artifactLabels.js` 的 `artifactLabel(name, kind)`——
    含汉字的名称原样使用，已登记的英文标识符翻译为中文，未登记的按卡片类型兜底，
    保证卡片上不出现英文；
  - 表格字段名：工具产出的 table artifact 列名必须中文（面积列统一走 `tools/units.py` 换算成亩）；
    自由 SQL 结果由 `tools/labels.py` 的 `humanize_table` 翻译表头与 TBLX 编码。
  组件按职责拆分（列表/窗口/气泡/卡片/渲染器），
  单个组件不超过约 200 行。
- 联调命令：`npm run dev` 启动开发服务器；`npm run build` 验证可编译；
  端到端冒烟用 `node scripts/smoke.mjs`（真实调用模型，仅手动执行）。

### 4.6 前端-后端事件协议（WebSocket）

| 事件类型 | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `turn_start` | 后端→前端 | `conversation_id` | 一轮对话开始 |
| `route` | 后端→前端 | `target`, `reason` | 本轮使用的智能体（sql / elder_care / chat，规划扩展 carrying）；会话选择了智能体时 reason 为“会话选择的智能体”，表示未经路由模型判断 |
| `standard_match` | 后端→前端 | `matched`, `mode`, `decision`, `entry_id`, `score`, `confidence`, `p_correct`, `calibrated`, `margin`, `risk`, `threshold`, `p_threshold`, `vector_used`, `vector_entries`, `judge_used`, `judge_entry`, `judge_confidence`, `judge_reason`, `resolved_question`, `data_version`, `reasons`, `slots`, `slot_labels`, `candidates`, `candidate_details`, `caliber`, `dataset_id`, `dataset_label`, `table` | 语义预匹配结果（`decision=execute` 才确定性执行；`candidates/clarify` 不查库；`fallback` 转自由 SQL；`confidence` 为校准后的 p(correct)；`resolved_question` 为多轮补全后的问题） |
| `token` | 后端→前端 | `delta` | 流式增量文本 |
| `tool_call` | 后端→前端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 后端→前端 | `id`, `name`, `is_error`, `content` | 工具结果摘要 |
| `artifact` | 后端→前端 | `kind`, `name`, `data` | 可视化产物（geojson / table / chart / file 等）；geojson 可带 `data.style`（预警图斑为红色描边 + 半透明红填充）与 `data.linked_table`（`{columns, rows, feature_key, row_key}`，前端据此实现表格行 ↔ 图斑的定位高亮联动） |
| `subagent_start` | 后端→前端 | `id`, `prompt` | 子 Agent 开始运行（task） |
| `subagent_end` | 后端→前端 | `id`, `is_error`, `content` | 子 Agent 结束并返回最终文本 |
| `message` | 后端→前端 | `role`, `content`, `model`, `number_guard`, `query_state` | 最终助手消息；`number_guard` 为自由 SQL 数字溯源结果，`query_state` 为供下一轮追问继承的轻量状态 |
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
GEOAGENT_QA_PREMATCH_CONFIDENCE=0.42
# 预匹配是否启用 embedding 向量召回 + RRF（0=纯规则；无 API Key 自动降级）
GEOAGENT_QA_VECTOR=1
# 灰度区（candidates/clarify）是否调用 LLM judge 复核候选
GEOAGENT_QA_JUDGE=1
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
