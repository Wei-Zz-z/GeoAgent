"""MCP 可用性自检：一条命令验证"能不能被外部前端正常调用"。

逐项检查并给出结论（任一项失败即退出码 1）：

1. 后端存活（/api/health）
2. 未带令牌访问 /mcp/ → 期望 401 + WWW-Authenticate（MCP 层鉴权生效）
3. MCP 令牌不能读写会话接口（/api/conversations → 期望 401）
4. MCP 握手 initialize + 工具发现（应含 ask / generate_briefing / list_datasets）
5. list_datasets 能看到已登记期次
6. generate_briefing 生成快报，且返回的签名链接**不带 Cookie 能下载**、被篡改后不能下载
7. ask 真实提问一次，拿到非空结论（可用 --skip-ask 跳过，避免调用模型）

用法（在 backend 目录下执行）：

    uv run python ../scripts/check_mcp.py                      # 默认查本机 8000 端口
    uv run python ../scripts/check_mcp.py --url https://域名/mcp/ --token xxxx:门户A
    uv run python ../scripts/check_mcp.py --skip-ask --skip-briefing   # 不产生文件、不调模型

令牌优先级：--token > 环境变量 GEOAGENT_MCP_TOKENS 的第一个 > backend/.env 里的同名变量。
注意：本脚本会真实生成一份快报文件（除非 --skip-briefing），ask 会真实调用模型（除非 --skip-ask）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402

ACCEPT = {"accept": "application/json, text/event-stream"}
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "geoagent-selfcheck", "version": "1.0"},
    },
}
DEFAULT_QUESTION = "统计一下地类变化图斑有多少个，面积合计多少亩？"

passed = 0
failed = 0


def report(step: str, ok: bool, detail: str, skipped: bool = False) -> None:
    global passed, failed
    if skipped:
        print("%-6s %-34s %s" % ("[跳过]", step, detail), flush=True)
        return
    if ok:
        passed += 1
    else:
        failed += 1
    print("%-6s %-34s %s" % ("[通过]" if ok else "[失败]", step, detail), flush=True)


def load_token(cli_token: str) -> str:
    if cli_token:
        # 允许直接粘贴 `令牌:调用方名` 这种写法，与 GEOAGENT_MCP_TOKENS 保持一致
        return cli_token.split(":")[0].strip()
    from geoagent.config import load_dotenv

    load_dotenv(BACKEND_DIR / ".env")
    raw = os.getenv("GEOAGENT_MCP_TOKENS", "")
    first = next((item.strip() for item in raw.split(",") if item.strip()), "")
    return first.split(":")[0].strip() if first else ""


def mcp_client(url: str, token: str, user: str):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"}
    if user:
        headers["X-GeoAgent-User"] = user
    return httpx2.AsyncClient(headers=headers, timeout=600), ClientSession, streamable_http_client


def payload_of(result):
    """工具返回的字典被 SDK 序列化成 JSON 文本，这里解析回字典。"""
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if not text:
            continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return getattr(result, "structuredContent", None)


async def check_mcp(
    url: str,
    origin: str,
    token: str,
    user: str,
    question: str,
    auth_mode: str,
    do_briefing: bool,
    do_ask: bool,
) -> None:
    transport, ClientSession, streamable_http_client = mcp_client(url, token, user)
    async with transport as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                info = await session.initialize()
                # SDK 的初始化结果用下划线字段（server_info / protocol_version）
                server = getattr(info, "server_info", None) or getattr(info, "serverInfo", None)
                name = str(getattr(server, "name", "") or "未命名")
                report(
                    "4. MCP 握手 initialize",
                    bool(name),
                    "服务名 %s（协议 %s）"
                    % (name, getattr(info, "protocol_version", None) or getattr(info, "protocolVersion", "?")),
                )

                tools = [tool.name for tool in (await session.list_tools()).tools]
                expected = {"ask", "generate_briefing", "list_datasets"}
                report("4b. 工具发现 tools/list", expected <= set(tools), "工具：%s" % ", ".join(tools))

                data = payload_of(await session.call_tool("list_datasets", {}))
                datasets = data.get("datasets") if isinstance(data, dict) else None
                ok = bool(datasets)
                report(
                    "5. list_datasets 期次清单",
                    ok,
                    "默认期次 %s，共 %s 期" % (data.get("default_id"), len(datasets or []))
                    if ok
                    else "没有登记任何期次（请先做套合入库或检查 datasets.json）",
                )

                if do_briefing:
                    try:
                        data = payload_of(await session.call_tool("generate_briefing", {}))
                        files = (data.get("files") if isinstance(data, dict) else None) or []
                        # GEOAGENT_PUBLIC_BASE_URL 为空时后端返回相对路径，这里按端点来源补全
                        links = [urljoin(origin, item["url"]) for item in files]
                        downloads = []
                        for item, link in zip(files, links):
                            resp = httpx.get(link, timeout=180)
                            downloads.append((item["kind"], resp.status_code, len(resp.content)))
                        ok = bool(links) and all(code == 200 and size > 1000 for _, code, size in downloads)
                        report(
                            "6. 快报 + 签名下载",
                            ok,
                            "; ".join("%s %d %d字节" % d for d in downloads) or "未返回文件",
                        )
                        if links and auth_mode == "dev":
                            report(
                                "6b. 链接篡改/去签名被拒",
                                True,
                                "dev 模式跳过（该模式不校验下载凭据）；签名为 ticket 模式下的凭据",
                                skipped=True,
                            )
                        elif links:
                            tampered = httpx.get(links[0].replace("快报", "其他"), timeout=60)
                            unsigned = httpx.get(links[0].split("?")[0], timeout=60)
                            report(
                                "6b. 链接篡改/去签名被拒",
                                tampered.status_code == 401 and unsigned.status_code == 401,
                                "篡改 -> %d，去签名 -> %d" % (tampered.status_code, unsigned.status_code),
                            )
                    except Exception as exc:
                        report("6. 快报 + 签名下载", False, "%s: %s" % (type(exc).__name__, str(exc)[:140]))

                if do_ask:
                    try:
                        data = payload_of(await session.call_tool("ask", {"question": question}))
                        answer = str((data or {}).get("answer", "")).strip() if isinstance(data, dict) else ""
                        ok = bool(answer) and not (data or {}).get("is_error")
                        report(
                            "7. ask 真实问答",
                            ok,
                            ("route=%s，回答 %d 字：%s"
                             % (data.get("route"), len(answer), answer[:40].replace("\n", " ")))
                            if ok
                            else "回答为空或报错：%s" % answer[:120],
                        )
                    except Exception as exc:
                        report("7. ask 真实问答", False, "%s: %s" % (type(exc).__name__, str(exc)[:140]))


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP 可用性自检")
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp/", help="MCP 端点地址")
    parser.add_argument("--token", default="", help="MCP 令牌（默认取 GEOAGENT_MCP_TOKENS 的第一个）")
    parser.add_argument("--user", default="selfcheck", help="透传的最终用户标识（审计用）")
    parser.add_argument("--question", default=DEFAULT_QUESTION, help="ask 使用的问题")
    parser.add_argument("--skip-ask", action="store_true", help="跳过真实问答（不调用模型）")
    parser.add_argument("--skip-briefing", action="store_true", help="跳过快报生成（不产生文件）")
    args = parser.parse_args()

    url = args.url if args.url.endswith("/") else args.url + "/"
    parts = urlsplit(url)
    origin = "%s://%s" % (parts.scheme, parts.netloc)
    token = load_token(args.token)

    print("端点：%s" % url)
    if not token:
        print("未找到 MCP 令牌：请给 --token，或在 backend/.env 配置 GEOAGENT_MCP_TOKENS")
        return 1
    print("令牌：已读取（不打印内容）；用户标识：%s\n" % args.user)

    # 1. 后端存活
    try:
        health = httpx.get(urljoin(origin, "/api/health"), timeout=15)
        report("1. 后端存活 /api/health", health.status_code == 200, "HTTP %d" % health.status_code)
    except Exception as exc:
        report("1. 后端存活 /api/health", False, "连不上 %s（%s）" % (origin, exc))
        print("\n结论：MCP 不可用（后端未启动或地址不对）")
        return 1

    # 2. 未带令牌：应由 MCP 层返回 401
    anon = httpx.post(url, json=INITIALIZE, headers=ACCEPT, timeout=30)
    www = anon.headers.get("www-authenticate", "")
    report(
        "2. 未带令牌被拒",
        anon.status_code == 401 and "Bearer" in www,
        "HTTP %d，WWW-Authenticate：%s" % (anon.status_code, www[:60] or "(缺失)"),
    )

    # 3. 令牌不能越权访问会话接口
    auth_mode = ""
    try:
        auth_mode = str((httpx.get(urljoin(origin, "/api/auth/me"), timeout=15).json() or {}).get("auth_mode", ""))
    except Exception:
        auth_mode = ""
    if auth_mode == "dev":
        report(
            "3. 令牌不能读写会话接口",
            True,
            "dev 模式跳过（该模式 /api 本身不校验登录态）；切成 ticket 后本项才有意义",
            skipped=True,
        )
    else:
        leak = httpx.get(
            urljoin(origin, "/api/conversations"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        report("3. 令牌不能读写会话接口", leak.status_code == 401, "HTTP %d" % leak.status_code)

    # 4-7. 走真实 MCP 客户端
    try:
        asyncio.run(
            check_mcp(
                url,
                origin,
                token,
                args.user,
                args.question,
                auth_mode,
                not args.skip_briefing,
                not args.skip_ask,
            )
        )
    except Exception as exc:
        report("4-7. MCP 客户端调用链", False, "%s: %s" % (type(exc).__name__, str(exc)[:160]))

    total = passed + failed
    print("\n结论：%s（%d/%d 项通过）" % ("MCP 可用" if failed == 0 else "MCP 不可用", passed, total))
    if failed:
        print("排查提示：401 多为令牌不对或未配置；握手失败看后端日志 /mcp 挂载与 lifespan；")
        print("          快报失败看 GEOAGENT_REPORTS_DIR 是否可写；问答失败看模型 key 与 OPENAI_BASE_URL。")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())