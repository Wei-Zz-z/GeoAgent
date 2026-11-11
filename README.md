# GeoAgent

通过自然语言对话完成**土地变化统计问答与监测快报生成**的智能体应用：用户在会话
窗口输入自然语言，Agent 规划并调用受控 SQL/统计工具，把结果以表格、地图、Word
快报等形式渲染在会话窗口内，而不是打开整页底图应用。

开发规范见 [AGENTS.md](AGENTS.md)，后端细节见 [backend/README.md](backend/README.md)。

## 当前能力（2026-09）

- **土地变化统计问答**（SQLAgent，路由 `sql`）：围绕 2026 年第一期地类变化图斑表
  回答前后变化问题——耕地流出/流入/净变化、每种图斑类型面积、建设用地占比、
  转换类型、县区排名等；白名单 = 图斑主表 + `dict_tblx` + 合并地类字典
  `dict_land_classification_summary`（原类型三级映射：二级类→一级类→三大类）。
- **确定性统计工具**：`summarize_by_type`、`fragment_stats`、`farmland_flow_summary`、
  `construction_change_summary`、`top_conversions` 等后端预写 SQL，避免模型手写
  出错；前后时项统一到三大类比较（后时项使用 TBLX→三大类默认模糊映射，正式规则
  下发后可替换）。
- **Word 快报生成**：说"生成快报"即可按默认模板生成 .docx（输出到仓库外目录，
  默认 `%LOCALAPPDATA%/GeoAgent/reports`），前端可**下载**并**在线预览**（docx-preview）。
- **养老可达性分析**（ElderCareAgent，路由 `elder_care`）：杭州设施出发的步行及简化驾车范围、
  多时长地图、空间叠加与专题图。新增居住用地覆盖试算及缺项状态，见[养老监测指标说明](docs/养老监测指标.md)；
  单设施试算不作为正式区域指标，通用E2SFCA示例也不等同于杭州已完成供需评价。
- **受控 SQL 层**：只读账号、单语句、表白名单、强制 LIMIT、超时与审计日志，LLM
  不直接持有数据库连接。
- **中文界面**：工具卡片按中文名展示（`frontend/src/toolLabels.js`），系统提示词、
  工具/技能描述均为中文。

默认模型：`qwen3.7-max-2026-06-08`（注册表内置 flash / plus / max，会话可切换）。

## 快速开始

```bash
# 后端
cd backend && uv sync
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000

# 前端（另开终端）
cd frontend && npm install
npm run dev          # http://localhost:5173
```

关键环境变量（参考 `backend/.env.example`）：`OPENAI_API_KEY`、`GEOAGENT_DEFAULT_MODEL`、
`GEOAGENT_PG_DSN`、`GEOAGENT_PG_WHITELIST`、`GEOAGENT_REPORTS_DIR`。API key 一律走
环境变量，禁止入库；`.env`、`data/`、会话记录与审计日志均不提交。

## 验证与回归

- 后端：`cd backend && uv run pytest`（当前 89 个用例全绿）。
- 准确率回归（真实库 + 真实 LLM，手动）：`uv run --env-file .env python ../scripts/eval_sql_agent.py`
  （7 个 golden 用例，qwen3.7-max 下 7/7）。
- 数据库冒烟：`uv run --env-file .env python ../scripts/smoke_pg.py`。

## 目录

```text
GeoAgent/
├── backend/     # Python + FastAPI（受控 SQL/统计工具、快报引擎、REST/WS）
├── frontend/    # Vue3 + Vite + OpenLayers（会话窗口、表格/地图/Word 预览）
├── skills/      # land-report 快报技能（模板与流程）
├── scripts/     # 冒烟 / 评估 / 数据准备脚本
└── AGENTS.md    # 开发规范
```
