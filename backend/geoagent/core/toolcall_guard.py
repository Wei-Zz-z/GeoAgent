"""文本化工具调用（tool-call 标记泄漏）的识别、还原与清洗。

部分 OpenAI 兼容端点在模型输出 XML 风格工具调用时，未能把它解析成结构化的
``tool_calls``，而是把标记原样留在了正文里，例如：

    <function_calls>
    <invoke name="run_sql">
    <parameter name="sql">SELECT count(*) FROM data."2026_1_change_landuse"</parameter>
    </invoke>
    </function_calls>

这类文本若不处理，会被 Agent 当成「没有工具调用」的最终回答，原样流给前端并落库，
污染会话历史。本模块提供三件事：

- ``has_leaked_call``：判断正文里是否含工具调用标记；
- ``parse_leaked_calls``：把标记还原成 ``LeakedCall``，供上层补成真正的工具调用执行；
- ``scrub_leaked_calls``：剥掉标记，只保留人类可读的正文。

另附 ``StreamLeakFilter``：流式转发时缓冲疑似标记，避免脏文本已经显示在界面上。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

# 作用域标签：兼容 antml: 前缀，以及 function_calls / tool_calls 两种常见写法。
_WRAPPER = r"(?:antml:)?(?:function_calls|tool_calls)"
_INVOKE = r"(?:antml:)?invoke"
_PARAM = r"(?:antml:)?parameter"
_JSON_TAG = r"(?:antml:)?(?:tool_call|function_call)"

# 完整的作用域块：<function_calls>...</function_calls>（内部 invoke 随块一起处理）。
_BLOCK_RE = re.compile(
    rf"<\s*{_WRAPPER}\s*>.*?<\s*/\s*{_WRAPPER}\s*>",
    re.DOTALL | re.IGNORECASE,
)
# 单条调用：<invoke name="run_sql">...</invoke>，也覆盖没有外层包裹的情况。
_INVOKE_RE = re.compile(
    rf"<\s*{_INVOKE}\s+name\s*=\s*(?P<quote>[\"'])(?P<name>[^\"']+)(?P=quote)\s*>"
    rf"(?P<body>.*?)<\s*/\s*{_INVOKE}\s*>",
    re.DOTALL | re.IGNORECASE,
)
# 参数：<parameter name="sql">SELECT ...</parameter>
_PARAM_RE = re.compile(
    rf"<\s*{_PARAM}\s+name\s*=\s*(?P<quote>[\"'])(?P<name>[^\"']+)(?P=quote)\s*>"
    rf"(?P<value>.*?)<\s*/\s*{_PARAM}\s*>",
    re.DOTALL | re.IGNORECASE,
)
# JSON 风格：<tool_call>{"name": "...", "arguments": {...}}</tool_call>
_JSON_TAG_RE = re.compile(
    rf"<\s*{_JSON_TAG}\s*>(?P<body>\s*\{{.*\}})\s*<\s*/\s*{_JSON_TAG}\s*>",
    re.DOTALL | re.IGNORECASE,
)
# 未闭合的标记起始标签：命中后，从该标签起到结尾的内容都无法保证是正常正文。
_OPEN_TAG_RE = re.compile(rf"<\s*(?:{_WRAPPER}|{_INVOKE})\b", re.IGNORECASE)
# 残留标签（闭合标签、被拆散的单标签）。
_STRAY_TAG_RE = re.compile(
    rf"<\s*/?\s*(?:{_WRAPPER}|{_INVOKE}|{_PARAM})\b[^>]*>",
    re.IGNORECASE,
)

# 流式过滤器需要识别的标记起始片段（用于判断「这段文本可能是标记的开头」）。
_MARKER_PREFIXES: tuple[str, ...] = (
    "<function_calls",
    "<function_call",
    "<tool_calls",
    "<tool_call",
    "<invoke",
    "<parameter",
    "<antml:function_calls",
    "<antml:function_call",
    "<antml:tool_calls",
    "<antml:tool_call",
    "<antml:invoke",
    "<antml:parameter",
)


@dataclass(frozen=True)
class LeakedCall:
    """从正文标记里还原出来的工具调用。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


def _coerce(value: str) -> Any:
    """把参数文本还原成合适类型：数字/布尔/JSON 字面量转原生类型，其余保持字符串。

    SQL 这类含引号与换行的字符串不会被误转——只有整体是合法 JSON 字面量时才转换。
    """
    text = value.strip()
    if not text:
        return ""
    if text[0] in "[{":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    if text in ("true", "false", "null") or re.fullmatch(r"-?\d+(\.\d+)?", text):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _merge(target: dict[str, Any], extra: dict[str, Any]) -> None:
    """把解析出的参数并入结果，后者不覆盖已有值。"""
    for key, value in extra.items():
        target.setdefault(str(key), value)


def parse_leaked_calls(text: Optional[str]) -> list[LeakedCall]:
    """解析正文里的工具调用标记，按出现顺序返回（相同调用去重）。"""
    if not text or "<" not in text:
        return []
    calls: list[LeakedCall] = []
    for match in _INVOKE_RE.finditer(text):
        name = match.group("name").strip()
        if not name:
            continue
        arguments: dict[str, Any] = {}
        for param in _PARAM_RE.finditer(match.group("body")):
            arguments[param.group("name").strip()] = _coerce(param.group("value"))
        if not arguments:
            # 少数端点把参数写成 invoke 体内的 JSON 对象。
            body = match.group("body").strip()
            if body.startswith("{"):
                try:
                    loaded = json.loads(body)
                except json.JSONDecodeError:
                    loaded = None
                if isinstance(loaded, dict):
                    _merge(arguments, loaded)
        calls.append(LeakedCall(name=name, arguments=arguments))

    for match in _JSON_TAG_RE.finditer(text):
        try:
            loaded = json.loads(match.group("body"))
        except json.JSONDecodeError:
            continue
        if not isinstance(loaded, dict):
            continue
        name = str(loaded.get("name") or loaded.get("tool") or "").strip()
        raw_args = loaded.get("arguments", loaded.get("parameters", {}))
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except json.JSONDecodeError:
                raw_args = {}
        if name and isinstance(raw_args, dict):
            calls.append(LeakedCall(name=name, arguments=dict(raw_args)))

    unique: list[LeakedCall] = []
    for call in calls:
        if any(c.name == call.name and c.arguments == call.arguments for c in unique):
            continue
        unique.append(call)
    return unique


def has_leaked_call(text: Optional[str]) -> bool:
    """判断正文里是否含工具调用标记。"""
    if not text or "<" not in text:
        return False
    return bool(
        _INVOKE_RE.search(text) or _JSON_TAG_RE.search(text) or _OPEN_TAG_RE.search(text)
    )


def scrub_leaked_calls(text: Optional[str]) -> str:
    """剥掉工具调用标记，返回只含人类可读正文的文本。

    完整标记整段删除；出现未闭合标记时，从该标记起到结尾的内容一并丢弃——
    这类内容无法保证是正常正文，保留下来只会继续污染回答。
    """
    if not text or "<" not in text:
        return text or ""
    cleaned = _BLOCK_RE.sub("", text)
    cleaned = _INVOKE_RE.sub("", cleaned)
    cleaned = _JSON_TAG_RE.sub("", cleaned)
    trailing = _OPEN_TAG_RE.search(cleaned)
    if trailing:
        cleaned = cleaned[: trailing.start()]
    cleaned = _STRAY_TAG_RE.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    return "\n".join(lines).strip()


class StreamLeakFilter:
    """流式转发过滤器：缓冲疑似工具调用标记，避免脏文本流到前端。

    用法：每个上游文本增量调用 ``feed``，把返回值转发给前端；流结束时调用 ``flush``。
    完整标记整段丢弃；未闭合的完整标记在结束时丢弃；仅「可能是标记开头」的残缺文本
    （例如正文里单独出现的 ``<``）按普通文本放行，避免误伤正常回答。
    """

    # 疑似标记但迟迟不闭合时的放行上限，避免缓冲区无限增长。
    MAX_HOLD_CHARS = 4096

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, delta: str) -> str:
        if not delta:
            return ""
        self._buffer += delta
        return self._drain(final=False)

    def flush(self) -> str:
        return self._drain(final=True)

    def _drain(self, final: bool) -> str:
        emitted: list[str] = []
        while self._buffer:
            index, kind = self._marker_kind(self._buffer)
            if index is None:
                emitted.append(self._buffer)
                self._buffer = ""
                break
            if index > 0:
                emitted.append(self._buffer[:index])
                self._buffer = self._buffer[index:]
                continue
            block_len = self._complete_block_len(self._buffer)
            if block_len is not None:
                self._buffer = self._buffer[block_len:]
                continue
            if len(self._buffer) > self.MAX_HOLD_CHARS:
                emitted.append(self._buffer)
                self._buffer = ""
                break
            if final:
                # 完整的标记起始但未闭合 → 丢弃；只是残缺前缀 → 当作普通文本。
                if kind == "partial":
                    emitted.append(self._buffer)
                self._buffer = ""
            break
        return "".join(emitted)

    @staticmethod
    def _marker_kind(text: str) -> tuple[Optional[int], Optional[str]]:
        """返回 (下标, "start" | "partial")；不含标记线索时返回 (None, None)。"""
        lowered = text.lower()
        for index, char in enumerate(lowered):
            if char != "<":
                continue
            tail = lowered[index:]
            if any(tail.startswith(marker) for marker in _MARKER_PREFIXES):
                return index, "start"
            if any(marker.startswith(tail) for marker in _MARKER_PREFIXES):
                return index, "partial"
        return None, None

    @staticmethod
    def _complete_block_len(text: str) -> Optional[int]:
        for pattern in (_BLOCK_RE, _INVOKE_RE, _JSON_TAG_RE):
            match = pattern.match(text)
            if match is not None:
                return match.end()
        return None

# 内部文件地址：正文不该出现（文件以卡片形式给出预览/下载）
_INTERNAL_MD_LINK = re.compile(
    r"\[([^\]]*)\]\(\s*/api/(?:files|reports)/[^)\s]*\s*\)"
)
_INTERNAL_BARE_LINK = re.compile(r"/api/(?:files|reports)/[^\s)\]]+")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+•]\s*|\d+[.、]\s*)*")
_DOWNLOAD_HEADING = re.compile(
    r"^\s*#{1,6}\s*(?:下载链接|下载|文件下载|附件|文件列表|预览)\s*$"
)


def scrub_internal_links(text: str) -> str:
    """去掉正文里的内部下载链接与空的“下载链接”小标题。

    快报等文件已经以 artifact 卡片给出预览/下载，正文再列一遍链接既冗余又暴露内部地址；
    模型偶尔仍会照着旧格式补一段“### 下载链接”，这里做一次出口兜底。
    """
    lines = str(text or "").splitlines()
    kept: list[str] = []
    for line in lines:
        if "/api/files/" in line or "/api/reports/" in line:
            residue = _LIST_PREFIX.sub("", _INTERNAL_MD_LINK.sub("", line))
            residue = _INTERNAL_BARE_LINK.sub("", residue).strip(" -—·:：")
            if not residue:
                # 整行就是链接（如“- [Word 版本](/api/files/...)”）→ 丢弃
                kept.append("")
                continue
            kept.append(_INTERNAL_MD_LINK.sub(lambda m: m.group(1), line).rstrip())
            continue
        kept.append(line)

    result: list[str] = []
    for index, line in enumerate(kept):
        if _DOWNLOAD_HEADING.match(line):
            following = [item for item in kept[index + 1 : index + 3]]
            if not any(item.strip() for item in following):
                continue
        result.append(line)
    cleaned = "\n".join(result)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


