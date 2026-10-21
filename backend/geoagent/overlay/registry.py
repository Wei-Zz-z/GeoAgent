"""把套合生成的新期次登记进 ``skills/qa-library/assets/datasets.json``。

登记是"一张套合表 = 一期数据"这条约定的落地点：登记之后前端"数据表"选择框、
问答链路与快报链路都会把这一期当成正常期次使用。写盘采用临时文件替换，避免
中途失败把资产写坏。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional


class RegistryError(Exception):
    """期次登记错误。"""


def _asset_path(path: Optional[Path]) -> Path:
    if path is not None:
        return Path(path)
    from geoagent.datasets import resolve_asset_path as resolve

    return resolve()


def load_asset(path: Optional[Path] = None) -> dict[str, Any]:
    """读取期次资产；文件不存在时返回可写骨架。"""
    target = _asset_path(path)
    if not target.is_file():
        return {"name": "land-change-datasets", "version": "1.0", "default_id": "", "datasets": []}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"期次资产不是合法 JSON：{exc}") from exc
    data.setdefault("datasets", [])
    return data


def _write_asset(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(payload, encoding="utf-8")
    os.replace(temp, path)


def register_dataset(
    entry: dict[str, Any],
    *,
    path: Optional[Path] = None,
    overwrite: bool = False,
    set_default: bool = False,
) -> dict[str, Any]:
    """追加/更新一条期次登记，返回最终写入的条目。"""
    dataset_id = str(entry.get("id", "")).strip()
    table = str(entry.get("table", "")).strip()
    if not dataset_id or not table:
        raise RegistryError("期次登记需要 id 与 table。")
    target = _asset_path(path)
    data = load_asset(target)
    entries: list[dict[str, Any]] = list(data.get("datasets") or [])
    for index, item in enumerate(entries):
        same_id = str(item.get("id", "")).strip() == dataset_id
        same_table = str(item.get("table", "")).strip() == table
        if not (same_id or same_table):
            continue
        if not (same_id and same_table) and not overwrite:
            other = item.get("table") if same_id else item.get("id")
            raise RegistryError(
                f"期次 id 或表名已被占用（{dataset_id} / {table} 与已有 {other} 冲突）；"
                "如需覆盖请勾选覆盖已存在的表。"
            )
        entries[index] = entry
        break
    else:
        entries.append(entry)
    data["datasets"] = entries
    if set_default or not str(data.get("default_id", "")).strip():
        data["default_id"] = dataset_id
    _write_asset(target, data)
    return entry


def remove_dataset(
    dataset_id: str, *, path: Optional[Path] = None
) -> bool:
    """按 id 摘掉一条期次登记（用于试跑回滚）；返回是否发生删除。"""
    target = _asset_path(path)
    data = load_asset(target)
    entries = [item for item in data.get("datasets") or []]
    kept = [item for item in entries if str(item.get("id", "")).strip() != str(dataset_id).strip()]
    if len(kept) == len(entries):
        return False
    data["datasets"] = kept
    if str(data.get("default_id", "")).strip() == str(dataset_id).strip():
        data["default_id"] = str(kept[0].get("id", "")) if kept else ""
    _write_asset(target, data)
    return True