"""命令行套合：把前后时相（含可选永农/红线）套合入库并登记期次。

用法（在 backend 目录下执行，或先切到 backend 再运行）：

    uv run python ../scripts/overlay_cli.py \
        --hsx "C:\\data\\HSX_DLBHJCTB.shp" \
        --qsx "C:\\data\\QSX_BGDLTB.shp" \
        --yjn "C:\\data\\yjbntb_mock.shp" \
        --hx  "C:\\data\\stbhhx_mock.shp" \
        --table 2026_3_change_landuse --year 2026 --round 3

--hsx/--qsx/--yjn/--hx 都可以直接给目录（自动取目录下唯一的 .shp），
也可以把 .shp/.dbf/.prj/.cpg 整套文件依次列出。默认会登记期次，用
--no-register 只建表不登记，--overwrite 覆盖同名表。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from geoagent.config import Settings, load_dotenv  # noqa: E402
from geoagent.overlay.db import OverlayDatabase  # noqa: E402
from geoagent.overlay.runner import OverlayInputs, run_overlay  # noqa: E402
from geoagent.overlay.spec import make_options  # noqa: E402

_VECTOR_SUFFIXES = (".shp", ".dbf", ".shx", ".prj", ".cpg", ".qmd", ".zip", ".geojson", ".json")


def _collect(values: list[str] | None) -> list[Path]:
    """把命令行参数展开成文件清单：目录取其中的矢量文件，文件原样保留。"""
    files: list[Path] = []
    for raw in values or []:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in _VECTOR_SUFFIXES))
        elif path.is_file():
            files.append(path)
        else:
            raise SystemExit(f"找不到输入：{raw}")
    return files


async def _main(args: argparse.Namespace) -> int:
    load_dotenv()
    settings = Settings()
    options = make_options(
        table_name=args.table,
        year=args.year,
        round_no=args.round,
        label=args.label or "",
        note=args.note or "",
        overwrite=args.overwrite,
        register=not args.no_register,
    )
    inputs = OverlayInputs(
        hsx=_collect(args.hsx),
        qsx=_collect(args.qsx),
        yjn=_collect(args.yjn),
        hx=_collect(args.hx),
    )
    for role in ("hsx", "qsx"):
        if not getattr(inputs, role):
            raise SystemExit(f"缺少必需图层：--{role}")

    async def progress(stage: str, percent: int, detail: str = "") -> None:
        print(f"[{percent:>3}%] {stage} {detail}".rstrip(), flush=True)

    db = OverlayDatabase(settings.pg_dsn)
    try:
        result = await run_overlay(inputs, options, db, progress=progress)
    finally:
        await db.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GeoAgent 套合入库")
    for role, help_text in (
        ("hsx", "后时相（变化监测图斑），必需"),
        ("qsx", "前时相（变更地类图斑），必需"),
        ("yjn", "永久基本农田，可选"),
        ("hx", "生态保护红线，可选"),
    ):
        parser.add_argument(f"--{role}", nargs="+", help=help_text)
    parser.add_argument("--table", required=True, help="目标表名，例如 2026_3_change_landuse")
    parser.add_argument("--year", type=int, required=True, help="监测年份")
    parser.add_argument("--round", type=int, required=True, help="期次（1-12）")
    parser.add_argument("--label", default="", help="期次展示名，默认 20XX年第N期")
    parser.add_argument("--note", default="", help="期次备注")
    parser.add_argument("--overwrite", action="store_true", help="覆盖同名表")
    parser.add_argument("--no-register", action="store_true", help="只建表，不登记期次")
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())