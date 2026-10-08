"""网页上传模拟矢量文件并生成区县图表底图。"""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import UploadFile

from ..analysis.county_charts import MAX_VECTOR_ZIP, build_county_chart_map

router = APIRouter(prefix="/api/county-charts", tags=["county-charts"])


@router.post("/build")
async def build_map(request: Request) -> dict[str, Any]:
    multipart = request.headers.get("content-type", "").startswith("multipart/form-data")
    limit = 2 * MAX_VECTOR_ZIP + 65536 if multipart else MAX_VECTOR_ZIP
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > limit:
            raise HTTPException(413, "每份ZIP不得超过100MB，上传总量不得超过200MB。")
    with TemporaryDirectory(prefix="geoagent_county_") as directory:
        path = Path(directory) / "input.zip"
        boundary_path = None
        try:
            if multipart:
                async def receive() -> dict[str, Any]:
                    return {"type": "http.request", "body": bytes(content), "more_body": False}
                bounded_request = Request(request.scope, receive)
                async with bounded_request.form(max_files=2, max_fields=0) as form:
                    if not isinstance(form.get("vector"), UploadFile):
                        raise ValueError("请选择变化图斑ZIP。")
                    for key, target in (("vector", path), ("boundary", Path(directory) / "boundary.zip")):
                        upload = form.get(key)
                        if upload is None:
                            continue
                        if not isinstance(upload, UploadFile):
                            raise ValueError("请上传ZIP文件。")
                        data = await upload.read(MAX_VECTOR_ZIP + 1)
                        if len(data) > MAX_VECTOR_ZIP:
                            raise ValueError("每份ZIP不得超过100MB。")
                        await asyncio.to_thread(target.write_bytes, data)
                        if key == "boundary":
                            boundary_path = target
            else:
                await asyncio.to_thread(path.write_bytes, content)
            return await asyncio.to_thread(build_county_chart_map, path, boundary_path)
        except Exception as exc:
            if isinstance(exc, ValueError):
                raise HTTPException(400, str(exc)) from exc
            raise HTTPException(400, f"读取矢量失败：{type(exc).__name__}；请核对ZIP与依赖。") from exc
