"""手动真实矢量验收：通过MCP读取ZIP并保存地图数据，不写业务数据库。"""

import argparse
import asyncio
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from mcp import Client
from geoagent.county_mcp_server import mcp


async def run(source: Path, output: Path, boundary: Path | None = None) -> None:
    if output.exists():
        raise FileExistsError("输出已存在，请使用新的验收文件名。")
    async with Client(mcp) as client:
        arguments = {"vector_zip_path": str(source.resolve())}
        if boundary:
            arguments["boundary_zip_path"] = str(boundary.resolve())
        result = await client.call_tool("create_county_chart_map", arguments)
    if result.is_error:
        raise RuntimeError(str(result.content))
    artifact = json.loads(Path(result.structured_content["artifact_path"]).read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(artifact["data"]["metadata"], ensure_ascii=True))
    print("JSON_BYTES", output.stat().st_size)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--boundary", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.source, args.output, args.boundary))
