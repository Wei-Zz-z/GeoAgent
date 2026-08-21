"""Agent 内置工具：所有 Agent 默认携带，与业务工具解耦。

当前内置工具：
- todo_write：任务清单（参考 Claude Code TodoWrite 的规划机制）。
  整体替换式更新，校验上限 20 项、每项 content 非空、同一时间最多一个 in_progress；
  更新结果同时通过 `todo` 事件推给前端渲染，状态存于 ConversationContext.todos。
- task：子 Agent 委派（参考 s06），全新上下文运行嵌套 Agent，只返回最终文本。
- list_skills / load_skill：技能按需加载（参考 s07），目录注入 system prompt，
  完整 SKILL.md 按需读取；名称走注册表，不做文件路径拼接。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..core.events import Event
from ..skills import SkillNotFoundError
from .registry import get_tools, register_tool
from .result import Artifact, ToolResult

MAX_TODO_ITEMS = 20
MAX_SKILL_CHARS = 5000

STATUS_MARKS: dict[str, str] = {
    "pending": "[ ]",
    "in_progress": "[>]",
    "completed": "[x]",
}


class TodoItem(BaseModel):
    """任务清单中的一项。"""

    content: str = Field(description="Task description")
    status: Literal["pending", "in_progress", "completed"] = Field(
        default="pending",
        description="Task status",
    )


class TodoWriteParams(BaseModel):
    """todo_write 工具参数。"""

    todos: list[TodoItem] = Field(
        description="The full task list to replace the current one",
    )

    @model_validator(mode="after")
    def validate_todo_list(self) -> "TodoWriteParams":
        if len(self.todos) > MAX_TODO_ITEMS:
            raise ValueError(f"todo list exceeds {MAX_TODO_ITEMS} items")
        if sum(1 for t in self.todos if t.status == "in_progress") > 1:
            raise ValueError("at most one item can be in_progress")
        if any(not t.content.strip() for t in self.todos):
            raise ValueError("todo content must be non-empty")
        return self


class TaskParams(BaseModel):
    """task 工具参数。"""

    prompt: str = Field(
        description=(
            "The subtask to delegate, written as an instruction to a fresh agent"
        )
    )
    model: Optional[str] = Field(
        default=None,
        description="Model override for the subagent (optional)",
    )


class ListSkillsParams(BaseModel):
    """list_skills 工具参数（空）。"""


class LoadSkillParams(BaseModel):
    """load_skill 工具参数。"""

    name: str = Field(
        description="Skill name to load (use list_skills to discover available skills)",
    )


class CompactParams(BaseModel):
    """compact 工具参数（空）。"""


def render_todos(todos: list[dict[str, str]]) -> str:
    """把任务列表渲染成给 LLM / 前端的文本视图。"""
    return "\n".join(
        f"{STATUS_MARKS.get(t.get('status', 'pending'), '[ ]')} {t['content']}"
        for t in todos
    )


@register_tool(
    "todo_write",
    (
        "Create and manage a task list. Use it for multi-step tasks: write the full "
        "list first, then update item statuses (pending / in_progress / completed) "
        "as work progresses."
    ),
    TodoWriteParams,
)
async def todo_write(ctx: Any, todos: list[dict[str, str]]) -> ToolResult:
    """更新任务清单（整体替换当前列表）。"""
    ctx.todos = list(todos)
    await ctx.emit(Event("todo", {"todos": list(ctx.todos)}))
    rendered = render_todos(ctx.todos)
    return ToolResult(
        tool_call_id="",
        name="todo_write",
        content=f"任务清单已更新（{len(ctx.todos)} 项）：\n{rendered}",
    )


def get_builtin_tools() -> list[Any]:
    """返回全部内置工具（当前：todo_write / task / list_skills / load_skill / compact）。"""
    return get_tools("todo_write", "task", "list_skills", "load_skill", "compact")


@register_tool(
    "task",
    (
        "Run a subagent with fresh conversation context and return its final text. "
        "Delegate large or self-contained subtasks to keep the main conversation focused."
    ),
    TaskParams,
)
async def task(ctx: Any, prompt: str, model: Optional[str] = None) -> ToolResult:
    """把子任务委托给一个全新上下文的子 Agent。"""
    from ..core.subagent import SubagentError, run_subagent

    try:
        text = await run_subagent(ctx, prompt, model=model)
    except SubagentError as exc:
        return ToolResult(
            tool_call_id="",
            name="task",
            content=str(exc),
            is_error=True,
        )
    return ToolResult(
        tool_call_id="",
        name="task",
        content=f"子任务完成：\n{text}",
    )


@register_tool(
    "list_skills",
    "List available skills (name + description).",
    ListSkillsParams,
)
def list_skills(ctx: Any) -> ToolResult:
    """列出当前可用的技能目录。"""
    loader = getattr(ctx, "skills", None)
    if loader is None:
        return ToolResult(
            tool_call_id="",
            name="list_skills",
            content="技能加载器未配置（缺少技能目录）",
            is_error=True,
        )
    catalog = loader.catalog()
    rows = [
        {"name": info["name"], "description": info["description"]}
        for info in catalog.values()
    ]
    content = "\n".join(f"- {r['name']}: {r['description']}" for r in rows) or "（无可用技能）"
    return ToolResult(
        tool_call_id="",
        name="list_skills",
        content=content,
        artifacts=[
            Artifact(
                kind="table",
                name="skills",
                data={"columns": ["name", "description"], "rows": rows},
            )
        ],
    )


@register_tool(
    "load_skill",
    (
        "Load a skill's full instructions (SKILL.md) by name. "
        "Call list_skills first to discover available skills."
    ),
    LoadSkillParams,
)
async def load_skill(ctx: Any, name: str) -> ToolResult:
    """按名称加载完整技能说明。"""
    loader = getattr(ctx, "skills", None)
    if loader is None:
        return ToolResult(
            tool_call_id="",
            name="load_skill",
            content="技能加载器未配置（缺少技能目录）",
            is_error=True,
        )
    try:
        content = loader.load(name)
    except SkillNotFoundError as exc:
        return ToolResult(
            tool_call_id="",
            name="load_skill",
            content=str(exc),
            is_error=True,
        )
    if len(content) > MAX_SKILL_CHARS:
        content = content[:MAX_SKILL_CHARS] + (
            f"\n\n[Skill content truncated from {len(content)} chars]"
        )
    return ToolResult(
        tool_call_id="",
        name="load_skill",
        content=f"技能 `{name}` 已加载：\n\n{content}",
    )


@register_tool(
    "compact",
    (
        "Summarize the earlier conversation to free context space. "
        "Call it after finishing a phase when the remaining work only needs a "
        "summary of what has been done."
    ),
    CompactParams,
)
async def compact(ctx: Any) -> ToolResult:
    """主动请求压缩较早的对话历史（在当前工具批次执行完后生效）。"""
    ctx.compact_requested = True
    return ToolResult(
        tool_call_id="",
        name="compact",
        content="压缩请求已记录，将在本轮工具执行完成后压缩较早对话。",
    )
