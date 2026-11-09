# GeoAgent MCP 对接说明

> 面向对接方（外部前端/服务团队）。GeoAgent 侧提供**问答**与**默认模板快报**两项能力，
> 通过 MCP（Streamable HTTP）调用；我们不维护你们的用户会话，只按调用记录审计。

## 1. 接入参数（我方提供，请填好再发给对方）

| 项 | 值 | 说明 |
| --- | --- | --- |
| 端点 | `https://<域名>/mcp/` | **结尾斜杠要带**；Streamable HTTP，`stateless`，不需要保持 MCP 会话 |
| 鉴权 | 请求头 `Authorization: Bearer <令牌>` | 一个调用方一个令牌，由我方分配（见第 6 节） |
| 最终用户标识 | 请求头 `X-GeoAgent-User: <用户Id>`（可选） | 只进我方审计，不做隔离；不传也能调 |
| 协议版本 | 客户端协商 | 实测 `2025-11-25` |
| 网络 | 你们的服务/浏览器需能访问上述域名 | 内网跨段、证书信任请提前打通 |

## 2. 三个工具

| 工具 | 参数 | 返回 |
| --- | --- | --- |
| `ask` | `question`（必填）、`history`（你们维护的多轮上下文）、`dataset_id`、`agent_id`、`include_data`（默认 false） | `answer`（最终结论）、`route`、`dataset_id`、`is_error`；`include_data=true` 时附 `artifacts`（表格/图表/图斑结构化数据） |
| `generate_briefing` | `dataset_id`（留空用默认期次） | `period`、`summary`（关键数字）、`files[].url`（签名下载链接）、`expires_at`、`notice` |
| `list_datasets` | 无 | `default_id`、`datasets`（期次清单，供上面两个工具选 `dataset_id`）、`agents` |

`history` 形如：

```json
[{"role": "user", "content": "耕地转为建设用地的面积是多少"},
 {"role": "assistant", "content": "……"}]
```

## 3. 客户端配置（mcpServers）

多数 MCP 客户端直接读一段 JSON，通常长这样——把域名和令牌换掉即可：

```json
{
  "mcpServers": {
    "geoagent": {
      "url": "https://<我方域名>/mcp/",
      "headers": {
        "Authorization": "Bearer <令牌>"
      }
    }
  }
}
```

说明：

- `geoagent` 这个名字随便起，只用于在客户端里标识这个服务；
- **URL 建议带结尾斜杠**。实测 `POST /mcp` 会 307 跳到 `/mcp/`，官方 SDK 客户端能自动跟随，
  两种写法都能连通；如果你们的 HTTP 栈不跟随跳转，就一定要写 `/mcp/`；
- 需要透传最终用户标识时，在 `headers` 里再加一项 `"X-GeoAgent-User": "<你们系统里的用户Id>"`；
- 有些客户端不支持 `headers` 字段（只能填 URL），那种情况用 stdio 桥接：

```json
{
  "mcpServers": {
    "geoagent": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://<我方域名>/mcp/",
        "--header", "Authorization: Bearer <令牌>"
      ]
    }
  }
}
```

（桥接参数以 `mcp-remote` 当前文档为准。）

- **令牌只放在你们服务端**：如果浏览器直接调我们，令牌会暴露在前端；
  建议由你们的后端持有令牌并转发，前端只跟你们的服务通信。

## 4. 调用示例

官方 Python SDK（与我们的自检脚本同一套）：

```python
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

HEADERS = {"Authorization": "Bearer <令牌>", "X-GeoAgent-User": "10086"}
URL = "https://<域名>/mcp/"

async with httpx2.AsyncClient(headers=HEADERS, timeout=600) as client:
    async with streamable_http_client(URL, http_client=client) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            data = await session.call_tool("list_datasets", {})       # 先看有哪些期次
            answer = await session.call_tool("ask", {
                "question": "统计一下地类变化图斑有多少个，面积合计多少亩？",
                "dataset_id": "2026_1",
            })
            briefing = await session.call_tool("generate_briefing", {})
```

纯 HTTP 探测（判断端点是否可达、令牌是否有效）：

```bash
curl -i -X POST https://<域名>/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Authorization: Bearer <令牌>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

- 不带令牌 → `401` 且带 `WWW-Authenticate`（正常，说明鉴权生效）
- 带令牌 → `200` + `text/event-stream`

如果你们的客户端只支持 **stdio**（如本机桌面客户端），可用社区常见的桥接把远程 HTTP 包成 stdio，
命令形如 `npx -y mcp-remote https://<域名>/mcp/ --header "Authorization: Bearer <令牌>"`
（具体参数以桥接工具当前文档为准）；支持 HTTP 的客户端通常只需填 URL + 请求头。

## 5. 约定与限制

- **没有逐字流式**：`ask` 一次返回最终结论。需要打字机效果的话请在你们侧做"生成中"提示，
  或与我们另做一个 SSE 流式接口。
- **不返回思考过程**：模型的推理与工具调用轨迹不外发，只有最终文本（需要数据时用 `include_data`）。
- **不维护会话**：我们不在服务端保存你们的对话；多轮上下文由你们每轮回传 `history`。
  不回传就是单轮问答（追问"那缙云县呢"这类会缺上下文）。
- **我们只记审计**：每次 `ask` 记录「提问 + 最终回复 + 调用方 + `X-GeoAgent-User` + 期次 + 耗时 + 是否成功」。
- **快报链接会过期**：`files[].url` 是带签名的临时链接（默认 1 小时），到期后重新调用
  `generate_briefing` 生成即可；链接可被任何人持有下载，请勿公开分发。
- **成本与并发**：每次 `ask` 会真实调用模型并消耗额度，请在你们侧做频率与并发限制。
- **口径**：涉及"疑似违法占地"的结论为预警口径，违法认定需另行叠加审批数据，请原样传达给最终用户。

## 6. 联调验收（建议第一次一起走一遍）

1. 列出工具，应看到 `ask` / `generate_briefing` / `list_datasets`；
2. 调 `list_datasets`，确认能看到期次（如 `2026_1`）；
3. 调 `ask` 问一个简单统计问题，确认 `is_error=false` 且 `answer` 非空；
4. 调 `generate_briefing`，拿到 `files[].url`，**用浏览器直接打开**确认能下载；
5. 我方可用 `cd backend && uv run python ../scripts/check_mcp.py --url https://<域名>/mcp/`
   一次性把上面几项跑完（退出码 0 即可用）。

常见错误对照：

| 现象 | 原因 |
| --- | --- |
| 401（响应带 `WWW-Authenticate`） | 令牌缺失/错误，或我方未配置 `GEOAGENT_MCP_TOKENS` |
| 连接被拒 / 超时 | 网络或证书没通；确认端点带结尾斜杠 |
| `ask` 返回 `is_error=true` | 我方模型不可用（看返回里的原因）或问题超出数据范围 |
| 快报链接 `401` | 链接过期（默认 1 小时），重新调用生成 |
| 快报链接 `404` | 文件已被清理，重新调用生成 |

## 7. 我方需要做的配置（运维清单）

```bash
# 1) 生成一个令牌（一个调用方一个，便于审计与单独吊销）
python -c "import secrets; print(secrets.token_urlsafe(32))"

# 2) backend/.env 里登记（格式 <令牌>:<调用方名>，逗号分隔多个）
GEOAGENT_MCP_TOKENS=<令牌>:<对方系统名>
# 快报下载链接返回绝对地址（不配则返回相对路径，对方要自己拼域名）
GEOAGENT_PUBLIC_BASE_URL=https://<域名>
# 链接有效期（秒，默认 1 小时）
GEOAGENT_MCP_DOWNLOAD_TTL_S=3600

# 3) 重启后端（必须带 --env-file，否则模型与数据库配置都不会生效）
uv run --env-file .env uvicorn geoagent.server.app:app --host 127.0.0.1 --port 8000
```

nginx 反代要点（`/mcp/` 走 SSE，必须关缓冲）：

```nginx
location /mcp/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;              # SSE 流式响应必须关
    proxy_read_timeout 600s;          # ask 可能跑 30~60 秒
    proxy_send_timeout 600s;
}
```

吊销：从 `GEOAGENT_MCP_TOKENS` 里删掉对应令牌并重启后端即可（当前是静态令牌，无按用户吊销的运行时接口）。

## 8. 还没做的、可以后续补的

- 令牌按调用方分权（如只允许快报、不允许问答）；
- 调用频率限制与配额统计；
- SSE 流式问答接口（如果他们需要逐字输出）；
- 官方 OAuth 接入（当前用预共享令牌，MCP 规范里的 OAuth 授权服务器流程未实现）。
