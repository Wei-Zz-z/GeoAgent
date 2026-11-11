"""社区养老服务点代理统计；不是社区中心点或社区边界覆盖。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from shapely.geometry import Point
from shapely.ops import transform


@lru_cache(maxsize=2)
def _read(path: str, modified_ns: int) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def reference_points(directory: Path, project: Callable) -> tuple[dict[str, Any], list]:
    path = directory / 'community_reference_points.json'
    if not path.is_file():
        return {'available': False, 'notice': '社区服务参考点未配置，不能统计社区代理数量。'}, []
    data = _read(str(path), path.stat().st_mtime_ns)
    if not data.get('points'):
        return {'available': False, 'notice': '未取得可定位社区参考点，不能统计社区代理数量。'}, []
    points = [(p, transform(project, Point(p['coordinates']))) for p in data['points']]
    return {'available': True, **{k: v for k, v in data.items() if k != 'points'}}, points


def summarize_communities(coverage: Any, points: list) -> list[dict[str, str]]:
    communities = {p['community_code']: p['community_name'] for p, point in points if coverage.covers(point)}
    return [{'code': code, 'name': name} for code, name in sorted(communities.items())]
