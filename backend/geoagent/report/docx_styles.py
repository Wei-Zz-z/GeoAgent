"""写回 Word 时的样式兜底。

回填目标可能是**用户上传的模板**，它不一定带 Word 内置样式（实测某快报模板就没有
``List Bullet``）。直接 ``add_paragraph(style="List Bullet")`` 会抛
``KeyError: no style with name 'List Bullet'``，所以统一走这里的兜底函数：
样式缺失时退化为普通段落，保证回填不因排版差异失败。
"""

from __future__ import annotations

from typing import Any


def add_heading_safe(document: Any, text: str, level: int = 1) -> Any:
    """加标题；模板缺少内置 Heading 样式时退化为加粗普通段落。"""
    try:
        return document.add_heading(text, level=level)
    except KeyError:
        paragraph = document.add_paragraph(text)
        if paragraph.runs:
            paragraph.runs[0].bold = True
        return paragraph


def add_bullet_safe(document: Any, text: str) -> Any:
    """加项目符号段落；模板缺少 List Bullet 样式时用普通段落加符号前缀。"""
    try:
        return document.add_paragraph(text, style="List Bullet")
    except KeyError:
        return document.add_paragraph(f"· {text}")