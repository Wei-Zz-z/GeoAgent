"""同一人口栅格、同一研究区的像元中心法人口估算。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.errors import RasterioError
from rasterio.features import geometry_mask, geometry_window
from shapely.geometry import mapping, shape, box
from shapely.ops import transform


class PopulationOverlay:
    def __init__(self, directory: Path, boundary: Any, metric_crs: str) -> None:
        self.metadata: dict[str, Any] = {'available': False, 'notice': '人口栅格或研究区未配置，未计算人口。'}
        self.values = None
        path = directory / 'population_2020.tif'
        info = directory / 'population_2020.json'
        if boundary is None or not path.is_file() or not info.is_file():
            return
        try:
            meta = json.loads(info.read_text(encoding='utf-8'))
            if meta.get('unit') != 'persons_per_cell' or meta.get('year') != 2020:
                self.metadata['notice'] = '人口数据年份或单位未核验，未计算人口。'
                return
            with rasterio.open(path) as src:
                self.project = Transformer.from_crs(metric_crs, src.crs, always_xy=True).transform
                study = mapping(transform(self.project, boundary))
                if not box(*src.bounds).covers(shape(study)):
                    raise ValueError('研究区超出人口栅格范围')
                window = geometry_window(src, [study])
                self.affine = src.window_transform(window)
                data = src.read(1, window=window, masked=True)
                inside = geometry_mask([study], data.shape, self.affine, invert=True, all_touched=False)
                valid = ~np.ma.getmaskarray(data) & np.isfinite(data.data) & (data.data >= 0)
                self.study_mask = inside & valid
                self.values = data.data
                self.total = int(np.sum(self.values[self.study_mask], dtype=np.float64))
                missing = int(np.count_nonzero(inside & ~valid))
                self.metadata = {**meta, 'available': True, 'study_population': self.total,
                                 'valid_cells': int(np.count_nonzero(self.study_mask)), 'missing_cells': missing,
                                 'method': '像元中心落入法；同一栅格、同一研究区，不按边缘像元面积分摊',
                                 'notice': '2020年总人口栅格估算，非老年人口、非现状人口、非实际已服务人数；覆盖面和坐标转换存在不确定性。'
                                           + ('研究区存在无数据网格，人口占比不计算。' if missing else '')}
        except (RasterioError, ValueError, KeyError, OSError) as exc:
            self.values = None
            self.metadata = {'available': False, 'notice': f'人口数据无法参与本次研究区计算（{type(exc).__name__}），未以零代替。'}

    def summarize(self, coverage: Any) -> dict[str, Any]:
        empty = {'covered_population_2020': None, 'model_uncovered_population_2020': None,
                 'population_coverage_percent': None}
        if self.values is None:
            return empty
        if coverage.is_empty:
            count = 0
        else:
            mask = geometry_mask([mapping(transform(self.project, coverage))], self.values.shape,
                                 self.affine, invert=True, all_touched=False)
            count = int(np.sum(self.values[mask & self.study_mask], dtype=np.float64))
        return {'covered_population_2020': count, 'model_uncovered_population_2020': self.total - count,
                'population_coverage_percent': round(count / self.total * 100, 4)
                if self.total > 0 and not self.metadata['missing_cells'] else None}
