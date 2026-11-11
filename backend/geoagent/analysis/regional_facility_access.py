"""市级目录按机构归属加载区县路网，不混用其他区县的分析数据。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .facility_access import _load


class RegionalFacilityAccess:
    def __init__(self, root: Path, mode: str) -> None:
        self.root = root
        self.mode = mode
        catalog = json.loads((root / 'catalog.json').read_text(encoding='utf-8'))
        self.metadata = catalog['metadata']
        self.facilities = catalog['facilities']
        self.districts = catalog['districts']

    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        query = query.strip()
        rows = [f for f in self.facilities if not query or any(query in str(f.get(key, ''))
                for key in ('id', 'name', 'address', 'region'))]
        return sorted(rows, key=lambda f: (f['name'] != query, f['name'], f['id']))[:limit]

    def analyze(self, facility_id: str, thresholds: list[float], speed_kmh: float, study_area: str = 'facility_district') -> dict[str, Any]:
        facility = next((f for f in self.facilities if f['id'] == facility_id), None)
        if facility is None:
            raise ValueError('设施编号不存在，请先检索。')
        if not facility.get('available'):
            raise ValueError(facility['status'])
        filename = 'pilot-driving.json' if self.mode == 'driving' else 'pilot.json'
        path = (self.root / facility['district_id'] / self.mode / filename).resolve()
        if not path.is_relative_to(self.root.resolve()) or not path.is_file():
            raise ValueError('所选区县的该模式路网尚未准备，暂不能分析。')
        data = _load(str(path), path.stat().st_mtime_ns)
        return data.analyze(facility_id, thresholds, speed_kmh) if study_area == 'facility_district' else data.analyze(facility_id, thresholds, speed_kmh, study_area)
