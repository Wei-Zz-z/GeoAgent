"""校验 / 同步 TBLX 映射资产与数据库字典。

用法：
    backend/.venv/Scripts/python.exe scripts/sync_tblx_mapping.py --check
    backend/.venv/Scripts/python.exe scripts/sync_tblx_mapping.py --write

`--write` 只同步编码/名称与版本元数据，不改变分类 category；分类边界仍需业务确认。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from geoagent.config import Settings, load_dotenv  # noqa: E402

ASSET = ROOT / "skills" / "land-report" / "assets" / "tblx_categories.json"


async def load_db(settings: Settings) -> dict[str, str]:
    conn = await asyncpg.connect(settings.pg_dsn)
    try:
        rows = await conn.fetch(
            'SELECT "TBLX", "TBLX_CODE" FROM knowledge_base.dict_tblx ORDER BY "TBLX"'
        )
    finally:
        await conn.close()
    return {str(row["TBLX"]): str(row["TBLX_CODE"]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="TBLX 映射与 dict_tblx 同步检查")
    parser.add_argument("--write", action="store_true", help="把数据库名称同步回映射资产")
    parser.add_argument("--check", action="store_true", help="只检查，不一致返回非 0")
    args = parser.parse_args()
    load_dotenv()
    settings = Settings()
    if not settings.pg_dsn:
        print("未配置 GEOAGENT_PG_DSN，无法检查 dict_tblx。")
        return 2

    data = json.loads(ASSET.read_text(encoding="utf-8"))
    entries = {str(item.get("code")): item for item in data.get("entries", [])}
    db = asyncio.run(load_db(settings))

    def canonical(code: str) -> str:
        """数据中 1-4 号地类存为 01-04，字典里可能是 1-4，视为同一编码。"""
        text = str(code)
        return f"0{text}" if len(text) == 1 and text.isdigit() else text

    db_by_code = {canonical(code): name for code, name in db.items()}
    entries_by_code = {canonical(code): item for code, item in entries.items()}
    missing = sorted(set(db_by_code) - set(entries_by_code))
    extra = sorted(set(entries_by_code) - set(db_by_code))
    mismatched = sorted(
        code for code, name in db_by_code.items()
        if code in entries_by_code and str(entries_by_code[code].get("name")) != name
    )
    print(f"数据库 {len(db)} 条，资产 {len(entries)} 条")
    print(f"缺少: {missing or '无'}")
    print(f"多余: {extra or '无'}")
    print(f"名称不一致: {mismatched or '无'}")
    if args.write:
        for code, name in db.items():
            if code in entries:
                entries[code]["name"] = name
        data["source"] = "knowledge_base.dict_tblx（同步校验）"
        data["reviewed_at"] = "2026-10-29"
        ASSET.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("已同步名称与元数据（category 未改动）")
    if args.check and (missing or extra or mismatched):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
