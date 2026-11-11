"""裁剪用户提供的PopSE到杭州边界范围，保持原值、原投影和NoData。"""
from pathlib import Path
import argparse
import json
import rasterio
from rasterio.mask import mask
from rasterio.warp import transform_geom

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--directory', type=Path, required=True)
args = parser.parse_args()
target = args.directory / 'population_2020.tif'
if target.exists():
    raise SystemExit('目标栅格已存在，请保留原文件并另选目录，或核实后手工处理。')
boundary = json.loads((args.directory / 'district_boundaries.geojson').read_text(encoding='utf-8'))
with rasterio.open(args.source) as src:
    shapes = [transform_geom(boundary['crs']['properties']['name'], src.crs, f['geometry']) for f in boundary['features']]
    data, affine = mask(src, shapes, crop=True, filled=True, all_touched=False)
    profile = src.profile.copy()
    profile.update(height=data.shape[1], width=data.shape[2], transform=affine,
                   compress='deflate', tiled=True, blockxsize=256, blockysize=256)
    with rasterio.open(target, 'w', **profile) as out:
        out.write(data)
        out.update_tags(source='PopSE_China2020_100m', year='2020', unit='persons_per_cell')
    meta = {'source': args.source.name, 'year': 2020, 'unit': 'persons_per_cell',
            'citation': 'https://doi.org/10.5194/essd-16-3705-2024', 'resolution_m': list(src.res),
            'source_crs': str(src.crs), 'datum_status': '源数据datum标记unknown，转换不确定性待核实',
            'scope': '按现有杭州13区县边界裁剪，非全国数据'}
(args.directory / 'population_2020.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
print('杭州人口栅格准备完成', data.shape, target.stat().st_size)
