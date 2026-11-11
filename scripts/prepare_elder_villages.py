"""筛选杭州历史村级边界；冲突代码隔离，不改变几何精度和原始名称。"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
import sys

import geopandas as gpd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from geoagent.analysis.village_overlay import boundary_kind


def prepare(source: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f'输出已存在，请另选路径：{output}')
    frame = gpd.read_file(source)
    frame = frame.loc[frame['市级'] == '杭州市'].copy()
    if frame.crs is None:
        raise ValueError('源数据未声明坐标系，不能猜测。')
    conflicts = frame.loc[frame['村代码'].duplicated(keep=False)]
    # 同码不同名或重复图形均先隔离，避免重复计数和静默合并。
    frame = frame.loc[~frame['村代码'].duplicated(keep=False)]
    features = json.loads(frame.to_json())['features']
    for feature in features:
        props = feature['properties']
        name, code = str(props['村级']), str(props['村代码'])
        feature['properties'] = dict(name=name, code=code, county=props['区县级'],
                                     township=props['乡镇级'], kind=boundary_kind(name, code))
    meta = dict(type='FeatureCollection', source='用户提供：浙江村界.rar / 3浙江村界-备注省市县乡',
                source_crs=frame.crs.to_string(), version_status='实际测绘及区划年份未说明；包含江干区、下城区等历史名称，不能视为现行完整社区边界。',
                excluded_conflicts=conflicts[['区县级', '乡镇级', '村级', '村代码']].to_dict('records'),
                notice='仅统计概略覆盖面与历史边界的正面积相交，接触边线不计。数据混有村、社区、街道和其他单元；名称含社区不等于现行社区。相交不代表全域可达，不计算社区覆盖率。冲突代码已隔离，未与现行名录强行关联。')
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, 'wt', encoding='utf-8') as stream:
        json.dump(dict(meta, features=features), stream, ensure_ascii=False, separators=(',', ':'))
    return dict(meta, boundary_count=len(features), community_named_count=sum(f['properties']['kind'] == '名称含社区' for f in features))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.output), ensure_ascii=False, indent=2))
