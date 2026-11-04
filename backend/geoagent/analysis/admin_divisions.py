"""区划历史字典与机构地址文字关联；不推断空间归属，不改变路网选择。"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=2)
def load_divisions(path: str, modified_ns: int) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))


def match_addresses(facilities: list[dict[str, Any]], divisions: dict[str, Any]) -> dict[str, Any]:
    """仅采用本区县内唯一的完整街道名；重名、多候选、缺失均保持待核实。"""
    assignments = {}
    for facility in facilities:
        address = facility.get('address') or ''
        streets = [s for s in divisions['streets']
                   if s['areaCode'] == facility.get('district_id') and s['name'] in address]
        item = {'street_code': '', 'village_code': '', 'status': '地址未唯一匹配，归属待核实'}
        if len(streets) == 1:
            street = streets[0]
            item.update(street_code=street['code'], status='依据地址文字匹配2023区划，归属待核实')
            villages = [v for v in divisions['villages']
                        if v['streetCode'] == street['code'] and v['name'] in address]
            if len(villages) == 1:
                item['village_code'] = villages[0]['code']
        assignments[facility['id']] = item
    return assignments
