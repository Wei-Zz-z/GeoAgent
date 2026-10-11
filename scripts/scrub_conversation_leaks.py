"""清洗历史会话里被写进正文的工具调用标记。

背景：部分 OpenAI 兼容端点偶发未把工具调用解析成结构化 ``tool_calls``，标记被当作
正文落库（例如 ``<function_calls><invoke name="run_sql">...``）。这类文本会一直留在
会话历史里，并可能被模型模仿。本脚本扫描 JSONL 会话文件，剥掉标记、保留可读正文，
写入前先备份原文件为 ``*.jsonl.bak``。

用法：
    backend/.venv/Scripts/python.exe scripts/scrub_conversation_leaks.py          # 预览
    backend/.venv/Scripts/python.exe scripts/scrub_conversation_leaks.py --apply  # 实际写入
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从仓库任意目录运行：把 backend 加入导入路径。
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from geoagent.core.toolcall_guard import has_leaked_call, scrub_leaked_calls  # noqa: E402

# 整条消息只是一个发不出去的标记时，替换成与运行时一致的说明文本。
FALLBACK_NOTICE = "（本轮模型未按工具调用格式返回结果，已中止。请重试或换一种问法。）"


def scrub_line(raw: str) -> tuple[str, bool]:
    """处理一行；返回 (新行, 是否发生修改)。解析失败的行原样保留。"""
    try:
        message = json.loads(raw)
    except json.JSONDecodeError:
        return raw, False
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return raw, False
    content = message.get("content")
    if not isinstance(content, str) or not has_leaked_call(content):
        return raw, False
    cleaned = scrub_leaked_calls(content)
    message["content"] = cleaned if cleaned.strip() else FALLBACK_NOTICE
    return json.dumps(message, ensure_ascii=False), True


def scrub_file(path: Path, apply: bool) -> int:
    """清洗单个会话文件，返回被改动的消息条数。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = 0
    output: list[str] = []
    for line in lines:
        if not line.strip():
            output.append(line)
            continue
        new_line, modified = scrub_line(line)
        changed += 1 if modified else 0
        output.append(new_line)
    if changed and apply:
        backup = path.with_suffix(path.suffix + ".bak")
        if not backup.exists():
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="清洗会话历史里的文本化工具调用标记")
    parser.add_argument(
        "--data-dir",
        default=str(BACKEND_DIR / "data"),
        help="会话数据目录（默认 backend/data）",
    )
    parser.add_argument("--apply", action="store_true", help="实际写入（默认只预览）")
    args = parser.parse_args()

    conversations = Path(args.data_dir) / "conversations"
    if not conversations.is_dir():
        print(f"未找到会话目录: {conversations}")
        return 1
    total_files = 0
    total_messages = 0
    for path in sorted(conversations.glob("*.jsonl")):
        changed = scrub_file(path, apply=args.apply)
        if changed:
            total_files += 1
            total_messages += changed
            print(f"  {path.name}: {changed} 条助手消息含标记")
    mode = "已写入（原文件备份为 *.jsonl.bak）" if args.apply else "预览模式，未写入"
    print(f"共 {total_files} 个文件 / {total_messages} 条消息需要清洗；{mode}")
    if not args.apply and total_messages:
        print("加 --apply 执行清洗。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())