"""入口验证联调自检：先看本机到省域平台的网络，再可选校验一个真实 ticket。

用法（在后端目录下执行）：
    uv run python ../scripts/check_ticket_auth.py                  # 只做连通性自检
    uv run python ../scripts/check_ticket_auth.py --ticket xxx     # 顺带校验票据（真实消费一次）

注意：ticket 只能用一次且 2 分钟过期。带 --ticket 运行会真实消费该票据，
如需在浏览器里走完整流程，请回到平台 ticket 测试页重新取票。
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import socket
import sys
from urllib.parse import urlparse

# 允许直接以仓库根目录下的脚本方式运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from geoagent.auth.ticket import TicketValidator  # noqa: E402
from geoagent.config import Settings, load_dotenv  # noqa: E402


def probe_dns(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return ["解析失败：%s" % exc]
    return sorted({item[4][0] for item in infos})


def probe_tcp(host: str, port: int, timeout: float = 6.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "可连通"
    except OSError as exc:
        return False, "失败：%s" % exc


async def validate(ticket: str, url: str, timeout_s: float) -> int:
    validator = TicketValidator(url=url, timeout_s=timeout_s)
    try:
        user = await validator.validate(ticket)
    except Exception as exc:  # TicketError 或网络异常
        print("票据校验失败：%s（status=%s）" % (exc, getattr(exc, "status", "-")))
        return 1
    print("票据校验成功，平台返回用户信息：")
    for key, value in user.to_dict().items():
        print("  %-18s %s" % (key, value))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="省域平台入口验证自检")
    parser.add_argument("--ticket", default="", help="平台跳转 URL 上的 sykjzlptv2_ticket（一次性）")
    parser.add_argument("--timeout", type=float, default=8.0, help="接口超时（秒）")
    args = parser.parse_args()

    load_dotenv()
    settings = Settings()
    url = settings.auth_ticket_validate_url
    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    print("入口验证模式：%s（ticket=开启校验并按用户隔离会话）" % settings.auth_mode)
    print("校验接口：%s" % url)
    print("解析结果：%s" % ", ".join(probe_dns(host)))

    ok, message = probe_tcp(host, port, timeout=args.timeout)
    print("网络连通：%s %s:%d %s" % ("是" if ok else "否", host, port, message))
    if not ok:
        print("提示：省域平台通常只对政务外网/专线开放；如需经代理访问，可设置")
        print("      HTTPS_PROXY 环境变量（httpx 默认读取环境变量中的代理配置）。")
        return 1

    print("连通性自检通过；如已取到 ticket，可加 --ticket 校验或直接用浏览器走完整流程。")
    if args.ticket:
        return asyncio.run(validate(args.ticket.strip(), url, args.timeout))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())