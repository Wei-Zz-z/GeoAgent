"""按杭州区县准备两种模式的数据及目录；新目录输出，保留原试点可回退。"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('facilities', 'boundary', 'roads', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit('输出目录已存在，请使用新目录。')
    args.output.mkdir(parents=True)
    records = [r for r in json.loads(args.facilities.read_text(encoding='utf-8')) if str(r.get('code', '')).startswith('3301')]
    source = args.output / 'source-hangzhou.json'
    source.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')
    boundaries = json.loads(args.boundary.read_text(encoding='utf-8-sig'))
    districts = sorted([{'id': str(f['properties']['gb'])[-6:], 'name': f['properties']['name']}
                        for f in boundaries['features'] if str(f['properties'].get('gb', '')).startswith('1563301')], key=lambda d: d['id'])
    catalog: dict = {'metadata': {'region': '杭州市', 'source_count': len(records)}, 'districts': districts, 'facilities': []}
    assigned = set()
    for district in districts:
        for mode in ('walking', 'driving'):
            destination = args.output / district['id'] / mode
            subprocess.run([sys.executable, str(Path(__file__).with_name('prepare_elder_access_pilot.py')),
                            '--facilities', str(source), '--boundary', str(args.boundary), '--roads', str(args.roads),
                            '--county-code', district['id'], '--road-year', '2025', '--travel-mode', mode,
                            '--output', str(destination)], check=True, stdout=subprocess.DEVNULL)
        bundle = json.loads((args.output / district['id'] / 'walking' / 'pilot.json').read_text(encoding='utf-8'))
        for facility in bundle['facilities']:
            facility.update(district_id=district['id'], available=True,
                            status='可试算；坐标及道路接入待核实')
            catalog['facilities'].append(facility)
            assigned.add(facility['source_row'])
        print(district['id'], district['name'], len(bundle['facilities']), flush=True)
    # 不根据模糊地址猜测坐标；未能空间归属的记录单列，保留缺失信息。
    for record in records:
        if record['source_row'] in assigned:
            continue
        matches = [d for d in districts if d['name'] in (record.get('address') or '')]
        district = matches[0] if len(matches) == 1 else {'id': 'unassigned', 'name': '区县待核实'}
        catalog['facilities'].append({'id': f"unverified-{record['source_row']}", 'name': record['name'],
            'address': record.get('address') or '', 'region': district['name'], 'district_id': district['id'],
            'available': False, 'status': '坐标缺失、异常或与杭州边界不符，暂不能分析；区县仅按地址提示'})
    if any(f['district_id'] == 'unassigned' for f in catalog['facilities']):
        districts.append({'id': 'unassigned', 'name': '区县待核实'})
    (args.output / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False), encoding='utf-8')
    print('CATALOG', len(catalog['facilities']), 'READY', sum(f['available'] for f in catalog['facilities']), flush=True)


if __name__ == '__main__':
    main()
