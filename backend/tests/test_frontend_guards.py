"""前端源码守卫：挡住会破坏运行时的那类写法。

本机曾出现「新建会话后仍显示上一段对话」的故障：``ArtifactView.vue`` 把 OpenLayers
的 ``Map`` 以 ``Map`` 名字导入，遮蔽了 JS 内置 ``Map``，于是 ``featureIndex`` 实际是
一个 OpenLayers 地图（没有 ``clear()``），组件卸载时 ``onBeforeUnmount`` 抛错，
Vue 的卸载流程中断，旧消息 DOM 残留。

这类问题在浏览器里才暴露，但根因是源码层面的命名遮蔽，因此这里用静态检查守住；
真正的会话切换竞态在 ``frontend/scripts/check-chat-store.mjs`` 里做行为回归。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"
# 会被前端代码误用的 JS 内置对象：一旦被 import 同名绑定遮蔽，就会在运行时以奇怪的方式失败
SHADOWED_BUILTINS = ("Map", "Set", "Date", "Array", "Object", "Promise", "Error", "Number", "String")
IMPORT_RE = re.compile(
    r"^\s*import\s+(" + "|".join(SHADOWED_BUILTINS) + r")\s+from\b", re.M
)


def _sources() -> list[Path]:
    files: list[Path] = []
    for pattern in ("*.vue", "*.js", "*.mjs"):
        files.extend(sorted(FRONTEND_SRC.rglob(pattern)))
    return [path for path in files if "node_modules" not in path.parts]


@pytest.mark.skipif(not FRONTEND_SRC.is_dir(), reason="前端源码不在本机")
def test_frontend_does_not_shadow_js_builtins() -> None:
    offenders: list[str] = []
    for path in _sources():
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in IMPORT_RE.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(FRONTEND_SRC.parent)}:{line} 导入了 {match.group(1)}")
    assert not offenders, (
        "以下位置用 JS 内置对象名做了 import 绑定，会遮蔽内置对象（改用别名，例如 OlMap）：\n"
        + "\n".join(offenders)
    )