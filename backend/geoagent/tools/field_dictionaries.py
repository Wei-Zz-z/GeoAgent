"""TZ / QSX / CZCSXM 字段业务字典加载器。"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

DEFAULT_ASSET = (
    Path(__file__).resolve().parents[3]
    / "skills"
    / "qa-library"
    / "assets"
    / "field_dictionaries.json"
)


def resolve_asset(skills_dir: Optional[str | Path] = None) -> Path:
    if skills_dir:
        candidate = Path(skills_dir) / "qa-library" / "assets" / "field_dictionaries.json"
        if candidate.is_file():
            return candidate
    return DEFAULT_ASSET


@lru_cache(maxsize=8)
def load_dictionaries(path: Optional[Path] = None) -> dict[str, Any]:
    asset = Path(path) if path else DEFAULT_ASSET
    if not asset.is_file():
        return {"version": "", "fields": {}}
    return json.loads(asset.read_text(encoding="utf-8"))


def label_for(column: str, value: Any) -> Optional[str]:
    """返回字段值的中文标签；未收录/待确认值返回 None，避免编造。"""
    field = (load_dictionaries().get("fields") or {}).get(str(column).upper())
    if not field or value is None:
        return None
    raw = str(value)
    if field.get("type") == "date" and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    values = field.get("values") or {}
    if raw in values:
        return str(values[raw])
    return None


def describe(column: str) -> str:
    field = (load_dictionaries().get("fields") or {}).get(str(column).upper()) or {}
    return str(field.get("label") or "")


def prompt_summary() -> str:
    """给 SQLAgent 的紧凑字段字典说明；未确认的值不写成确定含义。"""
    fields = load_dictionaries().get("fields") or {}
    parts: list[str] = []
    for name in ("TZ", "QSX", "CZCSXM"):
        field = fields.get(name) or {}
        if not field:
            continue
        if field.get("type") == "date":
            parts.append(f"{name}={field.get('label')}（{field.get('format')}）")
        else:
            values = field.get("values") or {}
            preview = "、".join(f"{key}={value}" for key, value in list(values.items())[:6])
            parts.append(f"{name}={field.get('label')}（{preview}；未收录/待确认值按原码保留）")
    return "；".join(parts)
