import json
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, Point
from geoagent.analysis.population_overlay import PopulationOverlay


def prepare(directory, missing=False, zero=False):
    values = np.full((4, 4), 0 if zero else 10, dtype='int16')
    if missing:
        values[0, 0] = 32767
    with rasterio.open(directory / 'population_2020.tif', 'w', driver='GTiff', height=4, width=4,
                       count=1, dtype='int16', crs='EPSG:3857', transform=from_origin(0, 400, 100, 100), nodata=32767) as dst:
        dst.write(values, 1)
    (directory / 'population_2020.json').write_text(json.dumps({'year': 2020, 'unit': 'persons_per_cell'}))


def test_same_raster_denominator_partition_and_no_area_conversion(tmp_path):
    prepare(tmp_path)
    pop = PopulationOverlay(tmp_path, box(0, 0, 400, 400), 'EPSG:3857')
    result = pop.summarize(box(-100, 0, 200, 400))
    assert pop.metadata['study_population'] == 160
    assert result == {'covered_population_2020': 80, 'model_uncovered_population_2020': 80, 'population_coverage_percent': 50}
    assert pop.summarize(Point())['covered_population_2020'] == 0


def test_nodata_not_population_or_zero_coverage(tmp_path):
    prepare(tmp_path, missing=True)
    pop = PopulationOverlay(tmp_path, box(0, 0, 400, 400), 'EPSG:3857')
    assert pop.metadata['study_population'] == 150
    assert pop.metadata['missing_cells'] == 1
    assert pop.summarize(box(0, 0, 400, 400))['population_coverage_percent'] is None


def test_missing_data_outside_extent_and_zero_denominator(tmp_path):
    assert not PopulationOverlay(tmp_path, box(0, 0, 400, 400), 'EPSG:3857').metadata['available']
    prepare(tmp_path, zero=True)
    assert not PopulationOverlay(tmp_path, box(-100, 0, 400, 400), 'EPSG:3857').metadata['available']
    pop = PopulationOverlay(tmp_path, box(0, 0, 400, 400), 'EPSG:3857')
    assert pop.summarize(box(0, 0, 400, 400))['population_coverage_percent'] is None
