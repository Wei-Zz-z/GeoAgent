"""文本化工具调用（tool-call 标记泄漏）的识别、还原、清洗与流式拦截回归测试。

不依赖真实 API / 数据库：所有模型响应都是本地构造的桩数据。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest

from geoagent.config import Settings
from geoagent.core.llm import LLMService
from geoagent.core.toolcall_guard import (
    StreamLeakFilter,
    has_leaked_call,
    parse_leaked_calls,
    scrub_leaked_calls,
)

# 与线上实际泄漏形态一致：正文 + 空行 + <function_calls> 包裹的 run_sql 调用。
LEAKED_TEXT = (
    "根据查询结果，全库变化后为建设用地的图斑合计 13,969,861.25 平方米。\n\n"
    "完整 90 个区县的明细如下表：\n\n"
    "<function_calls>\n"
    '<invoke name="run_sql">\n'
    '<parameter name="sql">SELECT "XZQDM", "XMC", COUNT(*) AS "图斑数" '
    'FROM data."2026_1_change_landuse" WHERE "TBLX" IN (\'20\',\'YH\') '
    'GROUP BY "XZQDM", "XMC" ORDER BY SUM("MJ") DESC</parameter>\n'
    "</invoke>\n"
    "</function_calls>"
)


def test_detects_and_parses_leaked_invoke() -> None:
    assert has_leaked_call(LEAKED_TEXT) is True
    calls = parse_leaked_calls(LEAKED_TEXT)
    assert len(calls) == 1
    assert calls[0].name == "run_sql"
    sql = calls[0].arguments["sql"]
    assert 'data."2026_1_change_landuse"' in sql
    assert "GROUP BY" in sql
    # 参数里的引号与括号必须原样保留，不能被当成 JSON 解析掉。
    assert sql.startswith('SELECT "XZQDM"')


def test_scrub_keeps_prose_and_drops_markup() -> None:
    cleaned = scrub_leaked_calls(LEAKED_TEXT)
    assert "<function_calls" not in cleaned
    assert "<invoke" not in cleaned
    assert "<parameter" not in cleaned
    assert cleaned.startswith("根据查询结果")
    assert cleaned.endswith("完整 90 个区县的明细如下表：")
    assert "\n\n\n" not in cleaned


def test_plain_text_with_angle_brackets_is_untouched() -> None:
    plain = "面积<50平方米的细碎图斑占比更高，例如 <p> 这类写法不受影响。"
    assert has_leaked_call(plain) is False
    assert parse_leaked_calls(plain) == []
    assert scrub_leaked_calls(plain) == plain


def test_parses_json_style_tool_call_and_coerces_numbers() -> None:
    text = '<tool_call>{"name": "top_conversions", "arguments": {"top_n": 5}}</tool_call>'
    calls = parse_leaked_calls(text)
    assert [(c.name, c.arguments) for c in calls] == [("top_conversions", {"top_n": 5})]
    assert calls[0].arguments["top_n"] == 5


def test_parameter_numbers_are_coerced_but_sql_stays_text() -> None:
    text = (
        '<invoke name="fragment_stats">'
        '<parameter name="threshold_m2">5000</parameter>'
        "</invoke>"
    )
    calls = parse_leaked_calls(text)
    assert calls[0].arguments == {"threshold_m2": 5000}


def test_unclosed_marker_is_dropped_to_the_end() -> None:
    text = "结论如下。\n\n<function_calls>\n<invoke name=\"run_sql\">\n<parameter name=\"sql\">SELECT 1"
    assert has_leaked_call(text) is True
    assert scrub_leaked_calls(text) == "结论如下。"


def test_duplicate_calls_are_deduplicated() -> None:
    text = (
        '<invoke name="run_sql"><parameter name="sql">SELECT 1</parameter></invoke>'
        '<invoke name="run_sql"><parameter name="sql">SELECT 1</parameter></invoke>'
    )
    assert len(parse_leaked_calls(text)) == 1


def test_stream_filter_drops_marker_split_across_chunks() -> None:
    """逐字符（最坏情况的分片）转发时，标记不得流出，正文必须完整保留。"""
    filter_ = StreamLeakFilter()
    forwarded = ""
    for char in LEAKED_TEXT:
        forwarded += filter_.feed(char)
    forwarded += filter_.flush()
    assert "function_calls" not in forwarded
    assert "<invoke" not in forwarded
    assert forwarded.startswith("根据查询结果")
    assert forwarded.strip().endswith("完整 90 个区县的明细如下表：")


def test_stream_filter_passes_bare_angle_bracket_at_flush() -> None:
    """结尾只有一个" < "时按普通文本放行，避免误伤正常回答。"""
    filter_ = StreamLeakFilter()
    forwarded = filter_.feed("面积 <")
    forwarded += filter_.flush()
    assert forwarded == "面积 <"


def _content_chunk(content: str) -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))]
    )


async def _leaky_stream() -> AsyncIterator[Any]:
    # 标记被拆在多个分片里推送，模拟真实流式行为。
    yield _content_chunk("结论如下。\n\n")
    yield _content_chunk("<function_calls>\n")
    yield _content_chunk('<invoke name="run_sql">\n<parameter name="sql">SELECT 1</parameter>\n')
    yield _content_chunk("</invoke>\n</function_calls>")


class _LeakyCompletions:
    @staticmethod
    async def create(**kwargs: Any) -> AsyncIterator[Any]:
        return _leaky_stream()


class _LeakyClient:
    chat = SimpleNamespace(completions=_LeakyCompletions())


@pytest.mark.asyncio
async def test_stream_chat_does_not_forward_leaked_markup(monkeypatch) -> None:
    """流式转发拦掉标记，但返回的 message.content 仍保留原文，供 Agent 还原成调用。"""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    settings = Settings()
    service = LLMService(settings)
    service._client = lambda profile: _LeakyClient()  # type: ignore[method-assign]
    tokens: list[str] = []

    async def collect(delta: str) -> None:
        tokens.append(delta)

    message = await service.stream_chat(
        model=settings.default_model,
        messages=[{"role": "user", "content": "列出各区县明细"}],
        on_token=collect,
    )
    forwarded = "".join(tokens)
    assert forwarded == "结论如下。\n\n"
    assert "function_calls" not in forwarded
    # 原文仍完整保留：Agent 依赖它把调用还原出来继续执行。
    assert "<function_calls>" in message.content
    assert parse_leaked_calls(message.content)[0].name == "run_sql"

# ---------------------------------------------------------------- 正文内部链接清理


def test_scrub_internal_links_drops_download_section():
    """快报回复里模型补的“### 下载链接”整段要被清掉，其它内容保留。"""
    from geoagent.core.toolcall_guard import scrub_internal_links

    sample = (
        "本期快报已生成，统计期为 2026 年第二期。\n\n"
        "### 下载链接\n\n"
        "- [Word 版本](/api/files/reports/%E5%BF%AB%E6%8A%A5_d18aac29.docx)\n"
        "- [PDF 版本](/api/files/reports/%E5%BF%AB%E6%8A%A5_d18aac29.pdf)\n\n"
        "### 统计口径\n\n面积统一按亩展示。\n"
    )
    cleaned = scrub_internal_links(sample)
    assert "下载链接" not in cleaned
    assert "/api/" not in cleaned
    assert "本期快报已生成" in cleaned
    assert "统计口径" in cleaned


def test_scrub_internal_links_keeps_prose_without_links():
    from geoagent.core.toolcall_guard import scrub_internal_links

    plain = "### 结论\n\n本期耕地净减少 100 亩，无需下载链接。"
    assert scrub_internal_links(plain) == plain


def test_scrub_internal_links_unwraps_inline_link():
    """夹在正文里的内部链接只去掉链接本身，保留文字。"""
    from geoagent.core.toolcall_guard import scrub_internal_links

    cleaned = scrub_internal_links("快报已生成：[Word 版本](/api/files/a.docx) 供下载。")
    assert "/api/" not in cleaned
    assert "供下载" in cleaned
