from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from geoagent.core.llm import ToolCall
from geoagent.skills import SkillLoader, SkillNotFoundError
from geoagent.tools import ToolExecutor
from geoagent.tools.builtin import get_builtin_tools

REPORT_SKILL = """---
name: land-report
description: Generate land flow change briefing reports
---
# 土地流向变化快报

按模板输出快报。
"""

CODE_REVIEW_SKILL = """# Code Review

Review code for style issues.
"""


@pytest.fixture()
def skills_dir(tmp_path: Path) -> Path:
    for name, content in (
        ("land-report", REPORT_SKILL),
        ("code-review", CODE_REVIEW_SKILL),
    ):
        skill_root = tmp_path / name
        skill_root.mkdir()
        (skill_root / "SKILL.md").write_text(content, encoding="utf-8")
    return tmp_path


def test_scan_parses_frontmatter_and_fallback(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    assert set(loader.skills) == {"land-report", "code-review"}
    assert (
        loader.skills["land-report"]["description"]
        == "Generate land flow change briefing reports"
    )
    assert "Code Review" in loader.skills["code-review"]["description"]
    assert "按模板输出快报" in loader.skills["land-report"]["content"]


def test_load_unknown_skill_raises(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    with pytest.raises(SkillNotFoundError):
        loader.load("nope")


def test_catalog_prompt_includes_skills(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    prompt = loader.catalog_prompt()
    assert "land-report" in prompt
    assert "load_skill" in prompt


@pytest.mark.asyncio
async def test_load_skill_tool_returns_content(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    ctx = SimpleNamespace(skills=loader)
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(id="s1", name="load_skill", arguments={"name": "land-report"}),
        ctx=ctx,
    )
    assert not result.is_error
    assert "技能 `land-report` 已加载" in result.content
    assert "土地流向变化快报" in result.content


@pytest.mark.asyncio
async def test_load_skill_tool_rejects_unknown_and_traversal(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    ctx = SimpleNamespace(skills=loader)
    executor = ToolExecutor(get_builtin_tools())
    for name in ("nope", "../evil", "a/b"):
        result = await executor.execute(
            ToolCall(id="s2", name="load_skill", arguments={"name": name}),
            ctx=ctx,
        )
        assert result.is_error
        assert "Unknown skill" in result.content


@pytest.mark.asyncio
async def test_list_skills_tool_returns_table(skills_dir: Path) -> None:
    loader = SkillLoader(skills_dir)
    loader.scan()
    ctx = SimpleNamespace(skills=loader)
    executor = ToolExecutor(get_builtin_tools())
    result = await executor.execute(
        ToolCall(id="s3", name="list_skills", arguments={}),
        ctx=ctx,
    )
    assert not result.is_error
    assert result.artifacts[0].kind == "table"
    assert len(result.artifacts[0].data["rows"]) == 2
