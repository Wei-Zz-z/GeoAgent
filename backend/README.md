# GeoAgent Backend

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
- **多会话**：会话与消息持久化到 `data/conversations/*.jsonl`（未做登录，dev 模式全局可见）。

## 快速开始

```bash
cd backend
uv sync                                   # 安装依赖（首次会下载托管 Python）
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000
```

打开 http://127.0.0.1:8000/docs 查看接口。

## 关键接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 |
| GET | `/api/models` | 可用模型列表（含是否已配 key；已内置 OpenAI / 阿里千问 / 智谱 / DeepSeek） |
| GET/POST | `/api/conversations` | 会话列表 / 创建会话 |
| GET | `/api/conversations/{id}/messages` | 历史消息（含 artifacts） |
| PUT | `/api/conversations/{id}/model` | 切换该会话的模型 |
| POST | `/api/conversations/{id}/messages` | 发消息（非流式，返回最终回复） |
| WS | `/api/conversations/{id}/ws` | 流式对话：token / tool_call / tool_result / artifact / message 事件 |

## WebSocket 事件协议

前端唯一依赖此协议渲染对话过程（前后端解耦的契约，新增事件需在此登记）：

| 事件类型 | 方向 | 字段 | 说明 |
| --- | --- | --- | --- |
| `turn_start` | 服务端→客户端 | `conversation_id` | 一轮对话开始 |
| `route` | 服务端→客户端 | `target`, `reason` | 路由结果（geo / chat） |
| `token` | 服务端→客户端 | `delta` | 流式增量文本 |
| `tool_call` | 服务端→客户端 | `id`, `name`, `arguments` | 正在调用工具 |
| `tool_result` | 服务端→客户端 | `id`, `name`, `is_error`, `content` | 工具执行结果（摘要文本） |
| `artifact` | 服务端→客户端 | `kind`, `name`, `data` | 可视化产物（geojson / table 等） |
| `message` | 服务端→客户端 | `role`, `content`, `model` | 最终助手消息 |
| `error` | 服务端→客户端 | `message` | 错误信息 |
| `turn_end` | 服务端→客户端 | `conversation_id` | 一轮对话结束 |
| `user` | 客户端→服务端 | `content` | 用户发送消息 |

客户端发送格式：`{"type": "user", "content": "..."}`。

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

# RouterNode 判断意图 -> "geo" 走 GeoAgent，否则 ChatAgent
router = RouterNode(model="gpt-4o-mini")
geo = GeoAgent(model="gpt-4o")
chat = ChatAgent()
router - "geo" >> geo
router - "chat" >> chat
flow = Flow(router)
```

新增一个 Agent 或自定义节点，然后用 `- action >>` 连进图即可；Agent 内部自带工具循环。

## 目录

```text
geoagent/
├── core/          # node(图) / llm(模型切换) / agent(智能体循环) / context / events
├── tools/         # 装饰器注册 + Pydantic 校验 + 执行器 + geo 演示工具
├── memory/        # 短期会话窗口（滚动摘要/截断） + 会话存储 + 长期记忆接口占位
├── agents/        # 路由器 / 通用对话 / 地理分析 Agent 与图编排
└── server/        # FastAPI 应用与路由
```

## 下一步规划

- PyQGIS 分析以 worker 进程方式接入（替换演示工具）
- 长期/短期记忆、上下文压缩、工具失败兜底、任务规划
- 前端完善：更多 artifact 类型（图片/图表）、地图交互、多轮上下文展示
