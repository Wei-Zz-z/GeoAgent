"""启动两个真实stdio子进程并发现工具；不连接数据库或模型。"""

import asyncio
import os
from pathlib import Path
import sys

from mcp import Client, StdioServerParameters


async def main() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(root / "backend")
    environment["GEOAGENT_DATA_DIR"] = str(root / "work" / "county-runtime" / "data")
    environment["GEOAGENT_REPORTS_DIR"] = str(root / "work" / "county-runtime" / "reports")
    for module, expected in [("geoagent.mcp_server", 7), ("geoagent.county_mcp_server", 1)]:
        parameters = StdioServerParameters(command=sys.executable, args=["-m", module],
                                           cwd=root / "backend", env=environment)
        async with Client(parameters) as client:
            listing = await client.list_tools()
            assert len(listing.tools) == expected, module
            print(module, "STDIO_OK", len(listing.tools))


if __name__ == "__main__":
    asyncio.run(main())
