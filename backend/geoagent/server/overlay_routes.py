"""套合（前后时相切分入库）的上传与任务接口。

- ``GET  /api/overlay/options``    图层清单、年份/期次候选、默认表名；
- ``POST /api/overlay/build``      上传图层并启动套合任务，返回任务号；
- ``GET  /api/overlay/jobs/{id}``  查询进度与结果；
- ``GET  /api/overlay/datasets``   已登记期次（便于确认新期次是否生效）。

套合是重任务（全量数据分钟级），因此接口只负责起任务，进度由前端轮询查询。
写库使用独立写入句柄，问答链路的只读网关不受影响。
"""

from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from ..overlay.db import OverlayDatabase, OverlayDbError
from ..overlay.layers import inspect_layer_files
from ..overlay.manage import delete_overlay_dataset, list_overlay_tables
from ..overlay.runner import OverlayInputs, run_overlay
from ..overlay.spec import (
    LAYERS,
    describe_layers,
    suggest_table_name,
    dataset_id,
    make_options,
    period_label,
)

router = APIRouter(prefix="/api/overlay", tags=["overlay"])

# 上传限制：全量四个图层合计约 117MB，这里单文件 400MB、单次合计 700MB。
MAX_FILE_BYTES = 400 * 1024 * 1024
MAX_TOTAL_BYTES = 700 * 1024 * 1024
MAX_FILES = 30


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jsonable(job: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in job.items() if k != "task"}


def _refresh_datasets(app: Any, removed: Sequence[str] = ()) -> None:
    """套合登记新期次后刷新运行时的期次注册表与只读白名单（免重启）。"""
    settings = getattr(app.state, "settings", None)
    if settings is None:
        return
    try:
        from ..datasets import load_registry

        load_registry.cache_clear()
        settings.datasets = settings._load_datasets()
        app.state.datasets = settings.datasets
    except Exception:
        return
    gateway = getattr(app.state, "pg", None)
    if gateway is None:
        return
    for dropped in removed:
        while dropped in gateway.whitelist:
            gateway.whitelist.remove(dropped)
    for table in settings.datasets.tables:
        if table not in gateway.whitelist:
            gateway.whitelist.append(table)


@router.get("/options")
async def overlay_options(year: Optional[int] = None, round: Optional[int] = None) -> dict[str, Any]:
    """表单选项：图层清单 + 年份/期次候选 + 默认表名。"""
    current_year = datetime.now().year
    year_value = int(year or current_year)
    round_value = int(round or 1)
    try:
        table = suggest_table_name(year_value, round_value)
        label = period_label(year_value, round_value)
        dataset = dataset_id(year_value, round_value)
    except ValueError:
        year_value, round_value = current_year, 1
        table = suggest_table_name(year_value, round_value)
        label = period_label(year_value, round_value)
        dataset = dataset_id(year_value, round_value)
    return {
        "layers": describe_layers(),
        "years": list(range(current_year - 2, current_year + 3)),
        "rounds": [1, 2, 3, 4],
        "defaults": {"year": year_value, "round": round_value, "table": table,
                     "label": label, "dataset_id": dataset},
        "limits": {"file_mb": MAX_FILE_BYTES // (1024 * 1024),
                   "total_mb": MAX_TOTAL_BYTES // (1024 * 1024)},
    }


@router.get("/datasets")
async def overlay_datasets(request: Request) -> dict[str, Any]:
    """当前已登记的期次（新套合完成后应立刻出现在这里）。"""
    settings = request.app.state.settings
    return {"default_id": settings.datasets.default_id,
            "datasets": [p.to_dict() for p in settings.datasets.periods]}


async def _save_upload(upload: UploadFile, directory: Path, limit: int) -> Path:
    """把上传文件落到磁盘，逐块读取并限制体积。"""
    name = Path(str(upload.filename or "upload")).name or "upload"
    if not name or name in (".", ".."):
        raise ValueError("上传文件名无效。")
    target = directory / name
    size = 0
    with target.open("wb") as handle:
        while True:
            chunk = await upload.read(4 * 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                raise ValueError(f"{name} 超过 {limit // (1024 * 1024)}MB。")
            handle.write(chunk)
    if size == 0:
        raise ValueError(f"{name} 是空文件。")
    return target


@router.post("/build")
async def build_overlay(request: Request) -> dict[str, Any]:
    """接收四个图层的文件并启动套合任务。"""
    app = request.app
    jobs: dict[str, dict[str, Any]] = app.state.overlay_jobs
    if any(job.get("status") == "running" for job in jobs.values()):
        raise HTTPException(409, "已有套合任务在运行，请等待完成后再提交。")

    content_length = int(request.headers.get("content-length") or 0)
    if content_length > MAX_TOTAL_BYTES + 65536:
        raise HTTPException(413, f"上传总量不得超过 {MAX_TOTAL_BYTES // (1024 * 1024)}MB。")

    try:
        form = await request.form(max_files=MAX_FILES, max_fields=20)
    except Exception as exc:
        raise HTTPException(400, f"表单解析失败：{type(exc).__name__}") from exc

    job_id = uuid.uuid4().hex[:12]
    directory = Path(app.state.settings.data_dir) / "overlay" / job_id
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    saved: dict[str, list[Path]] = {role: [] for role in LAYERS}
    try:
        for role in LAYERS:
            uploads = form.getlist(role)
            for upload in uploads:
                if not isinstance(upload, UploadFile):
                    raise ValueError(f"{role} 只接收文件。")
                saved[role].append(
                    await _save_upload(upload, directory, MAX_FILE_BYTES)
                )
        # 落地后先做一次整套检查：漏选 .dbf/.prj 这类问题立刻返回，不占用后台任务
        problems: list[str] = []
        for role, spec in LAYERS.items():
            files = saved[role]
            if not files:
                if spec["required"]:
                    problems.append(f"{spec['label']}：没有收到文件")
                continue
            issues = inspect_layer_files(files, role)
            if issues:
                problems.append(f"{spec['label']}：{'；'.join(issues)}")
        if problems:
            raise ValueError(
                "上传文件不完整——" + "；".join(problems)
                + "。Shapefile 请把 .shp/.shx/.dbf/.prj/.cpg 整套一起选中，或打包成 ZIP 上传。"
            )
        raw = {key: str(form.get(key) or "").strip() for key in
               ("table_name", "year", "round", "label", "dataset_id", "note")}
        options = make_options(
            table_name=raw["table_name"],
            year=int(raw["year"] or datetime.now().year),
            round_no=int(raw["round"] or 1),
            label=raw["label"],
            dataset_id_value=raw["dataset_id"],
            note=raw["note"],
            overwrite=str(form.get("overwrite") or "").lower() in ("1", "true", "on", "yes"),
            register=str(form.get("register") or "1").lower() not in ("0", "false", "off", "no"),
        )
    except (ValueError, TypeError) as exc:
        shutil.rmtree(directory, ignore_errors=True)
        raise HTTPException(400, str(exc)) from exc

    job: dict[str, Any] = {
        "id": job_id,
        "status": "running",
        "stage": "排队",
        "progress": 0,
        "detail": "",
        "error": None,
        "result": None,
        "table": options.table_ref,
        "label": options.period_label,
        "created_at": _stamp(),
        "finished_at": None,
        "uploads": str(directory),
    }
    jobs[job_id] = job

    async def progress(stage: str, percent: int, detail: str = "") -> None:
        job.update({"stage": stage, "progress": int(percent), "detail": detail})

    async def worker() -> None:
        db = OverlayDatabase(app.state.settings.pg_dsn)
        try:
            inputs = OverlayInputs(
                hsx=saved["hsx"], qsx=saved["qsx"], yjn=saved["yjn"], hx=saved["hx"]
            )
            result = await run_overlay(inputs, options, db, progress=progress)
            if options.register:
                _refresh_datasets(app)
            job.update({"status": "done", "result": result, "stage": "完成", "progress": 100})
            shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            if isinstance(exc, OverlayDbError):
                message = str(exc)
            job.update({"status": "failed", "error": message, "finished_at": _stamp()})
        finally:
            await db.close()
            job.setdefault("finished_at", _stamp())

    task = asyncio.create_task(worker())
    app.state.overlay_tasks.add(task)
    task.add_done_callback(app.state.overlay_tasks.discard)
    return {"job_id": job_id, "table": job["table"], "label": job["label"]}


@router.get("/jobs/{job_id}")
async def overlay_job(job_id: str, request: Request) -> dict[str, Any]:
    """查询套合任务进度与结果。"""
    job = request.app.state.overlay_jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "没有这个套合任务。")
    return _jsonable(job)


@router.get("/jobs")
async def overlay_jobs(request: Request) -> dict[str, Any]:
    """最近的任务列表（倒序）。"""
    items = [_jsonable(job) for job in request.app.state.overlay_jobs.values()]
    items.sort(key=lambda item: item.get("created_at") or "", reverse=True)
    return {"jobs": items[:20], "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
class OverlayDeleteRequest(BaseModel):
    """删除一期套合数据：必须带上期次 id（或表名）+ 表名确认。"""

    dataset_id: str = ""
    table_name: str = ""
    confirm: str = ""
    backup: bool = True


def _pg_dump_available() -> bool:
    """是否具备删除前备份能力（找不到 pg_dump 时前端会提示）。"""
    from ..overlay.manage import _pg_dump_path

    return _pg_dump_path() is not None

@router.get("/tables")
async def overlay_tables(request: Request) -> dict[str, Any]:
    """列出 data schema 下的套合表（含行数、面积、几何与期次登记情况）。"""
    app = request.app
    settings = app.state.settings
    db = OverlayDatabase(settings.pg_dsn)
    try:
        items = await list_overlay_tables(db, settings.datasets)
    except OverlayDbError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await db.close()
    return {"tables": items, "backup_available": _pg_dump_available()}


@router.post("/delete")
async def delete_overlay(payload: OverlayDeleteRequest, request: Request) -> dict[str, Any]:
    """删除一期套合数据：默认先备份，再删表并摘掉期次登记。"""
    app = request.app
    jobs: dict[str, dict[str, Any]] = app.state.overlay_jobs
    if any(job.get("status") == "running" for job in jobs.values()):
        raise HTTPException(409, "已有套合任务在运行，请等待完成后再删除。")
    settings = app.state.settings
    db = OverlayDatabase(settings.pg_dsn)
    try:
        result = await delete_overlay_dataset(
            db,
            dataset_id=payload.dataset_id,
            table_name=payload.table_name,
            confirm=payload.confirm,
            backup=bool(payload.backup),
            backups_dir=Path(settings.data_dir) / "overlay_backups",
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OverlayDbError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await db.close()
    _refresh_datasets(app, removed=[result["table_ref"], result["table_name"]])
    return result