"""套合功能测试：只用临时文件与内存数据，不连接真实数据库或模型。"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely.geometry import Polygon

from geoagent.overlay import ddl, layers, manage, spec
from geoagent.overlay.registry import (
    RegistryError,
    load_asset,
    register_dataset,
    remove_dataset,
)


def test_table_name_and_period_rules() -> None:
    assert spec.validate_table_name(" 2026_3_change_landuse ") == "2026_3_change_landuse"
    assert spec.quote_table("2026_3_change_landuse") == 'data."2026_3_change_landuse"'
    for bad in ("", "a b", 'x";drop', "a.b", "a/b"):
        with pytest.raises(ValueError):
            spec.validate_table_name(bad)
    with pytest.raises(ValueError):
        spec.validate_period(2026, 0)
    with pytest.raises(ValueError):
        spec.validate_period(1800, 1)
    assert spec.suggest_table_name(2026, 3) == "2026_3_change_landuse"
    assert spec.dataset_id(2026, 11) == "2026_11"
    assert spec.period_label(2026, 3) == "2026年第三期"


def test_dataset_entry_aliases() -> None:
    options = spec.make_options("2026_3_change_landuse", 2026, 3)
    entry = options.to_dataset_entry()
    assert entry["table"] == 'data."2026_3_change_landuse"'
    assert entry["id"] == "2026_3"
    assert "2026年第三期" in entry["aliases"] and "第3期" in entry["aliases"]
    assert entry["enabled"] is True


def test_target_ddl_matches_existing_structure() -> None:
    create = ddl.create_table_sql("2026_3_change_landuse")
    for column in ("XZQDM", "TBLX", "JCBH", "DLBM", "SFYN", "SFHX", "MJ"):
        assert f'"{column}"' in create
    assert "public.geometry(MultiPolygon,4490)" in create
    assert "GENERATED ALWAYS AS IDENTITY" in create
    statements = " ".join(ddl.post_create_sql("2026_3_change_landuse"))
    assert "USING gist (geom)" in statements
    assert "PRIMARY KEY" in statements and "fn_update_timestamp" in statements
    assert "COMMENT ON TABLE" in statements


def test_insert_sql_transforms_geometry() -> None:
    sql = ddl.insert_sql("2026_3_change_landuse", "overlay_tmp.o1_frag", 4528, 4490)
    assert 'INSERT INTO data."2026_3_change_landuse"' in sql
    assert "ST_Multi(ST_Transform(f.geom, 4490))" in sql
    assert sql.count("f.") >= len(ddl.INSERT_COLUMNS)


def frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "XZQDM": ["330101", "330101"],
            "JCBH": ["A1", "A2"],
            "TBLX": ["01", "DT"],
            "QSX": ["20251006", "20251006"],
            "HSX": ["20260501", "20260501"],
            "TZ": [None, "x"],
        },
        geometry=[
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        ],
        crs=4528,
    )


def test_text_width_guard() -> None:
    data = frame()
    assert layers.check_text_widths(data, ["XZQDM", "TBLX"]) == []
    data.loc[0, "TBLX"] = "0123456789"
    problems = layers.check_text_widths(data, ["TBLX", "TZ"])
    assert problems and "TBLX" in problems[0]


def test_frame_records_null_and_binary_wkb() -> None:
    records = layers.frame_records(frame(), ["XZQDM", "TZ"])
    assert len(records) == 2
    assert records[0][0] == "330101"
    assert records[0][1] is None  # NaN / 空值统一写 NULL
    assert isinstance(records[0][2], (bytes, bytearray))


def test_read_layer_detects_crs_and_fills_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "hsx.shp"
    frame().to_file(path, driver="ESRI Shapefile")
    data = layers.read_layer([path], "hsx")
    assert data.info.rows == 2
    assert data.info.epsg == 4528
    assert "XMC" in data.frame.columns  # 可选字段自动补齐
    assert data.info.encoding == "UTF-8"


def test_shapefile_without_shx_is_rebuilt(tmp_path: Path) -> None:
    path = tmp_path / "hsx.shp"
    frame().to_file(path, driver="ESRI Shapefile")
    path.with_suffix(".shx").unlink()
    data = layers.read_layer([path], "hsx")
    assert data.info.rows == 2
    assert any(".shx" in item for item in data.info.warnings)


def test_inspect_layer_files_reports_missing_sidecars(tmp_path: Path) -> None:
    path = tmp_path / "hsx.shp"
    frame().to_file(path, driver="ESRI Shapefile")
    assert layers.inspect_layer_files([path], "hsx") == []
    path.with_suffix(".dbf").unlink()
    problems = layers.inspect_layer_files([path], "hsx")
    assert problems and ".dbf" in problems[0]
    assert layers.inspect_layer_files([], "hsx") == ["没有收到文件"]

def test_read_layer_rejects_missing_fields(tmp_path: Path) -> None:
    source = frame().drop(columns=["TBLX"])
    path = tmp_path / "broken.shp"
    source.to_file(path, driver="ESRI Shapefile")
    with pytest.raises(ValueError, match="TBLX"):
        layers.read_layer([path], "hsx")


class _StubCrs:
    """模拟 .prj 无法识别 EPSG 的情形。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def to_epsg(self) -> None:
        return None


class _StubFrame:
    def __init__(self, name: str) -> None:
        self.crs = _StubCrs(name)


def test_epsg_fallback_for_cgcs2000_zone40() -> None:
    assert layers._detect_epsg(_StubFrame("CGCS2000_3.000000_Degree_GK_Zone_40"), "hsx") == 4528
    with pytest.raises(ValueError, match="坐标系"):
        layers._detect_epsg(_StubFrame("WGS 84 / UTM zone 51N"), "hsx")


def test_encoding_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []

    def fake_read(source: str, encoding: Any) -> Any:
        calls.append(encoding)
        if encoding == "UTF-8":
            raise layers._EncodingFailure("bad")
        return "frame"

    monkeypatch.setattr(layers, "_read_frame", fake_read)
    frame_value, encoding = layers._read_with_encoding_fallback("x.shp", True)
    assert frame_value == "frame" and encoding == "GBK"
    assert calls == ["UTF-8", "GBK"]


def test_registry_append_conflict_and_remove(tmp_path: Path) -> None:
    asset = tmp_path / "datasets.json"
    entry = spec.make_options("2026_3_change_landuse", 2026, 3).to_dataset_entry()
    register_dataset(entry, path=asset)
    assert load_asset(asset)["default_id"] == "2026_3"
    register_dataset(entry, path=asset)  # 同 id 同表可重复登记
    assert len(load_asset(asset)["datasets"]) == 1
    with pytest.raises(RegistryError):
        register_dataset(spec.make_options("2026_9_other", 2026, 3).to_dataset_entry(), path=asset)
    assert remove_dataset("2026_3", path=asset) is True
    assert load_asset(asset)["datasets"] == []


def _fake_zip() -> bytes:
    """最小可用的 Shapefile ZIP：只需成员名成套，套合本身被替换为假实现。"""
    import io
    import zipfile

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("a.shp", b"x")
        handle.writestr("a.dbf", b"x")
        handle.writestr("a.prj", b"x")
    return buffer.getvalue()

class _RecordingDb:
    """记录执行的 SQL，用于验证删除流程；不连接真实数据库。"""

    def __init__(self, exists: bool = True) -> None:
        self.exists = exists
        self.statements: list[str] = []

    async def table_exists(self, schema: str, table: str) -> bool:
        return self.exists and schema == "data" and table == "2026_3_change_landuse"

    async def execute(self, sql: str, *args: Any) -> str:
        self.statements.append(sql)
        return "DROP TABLE"


def _asset_with_period(tmp_path: Path) -> Path:
    asset = tmp_path / "datasets.json"
    register_dataset(spec.make_options("2026_3_change_landuse", 2026, 3).to_dataset_entry(), path=asset)
    return asset


def test_table_name_of_normalizes_forms() -> None:
    assert manage.table_name_of('data."2026_3_change_landuse"') == "2026_3_change_landuse"
    assert manage.table_name_of("data.2026_3_change_landuse") == "2026_3_change_landuse"
    assert manage.table_name_of("2026_3_change_landuse") == "2026_3_change_landuse"
    assert manage.table_name_of("") == ""


def test_delete_requires_exact_table_name(tmp_path: Path) -> None:
    asset = _asset_with_period(tmp_path)
    db = _RecordingDb()
    with pytest.raises(ValueError, match="确认失败"):
        asyncio.run(
            manage.delete_overlay_dataset(
                db, dataset_id="2026_3", confirm="随便", backup=False, asset_path=asset
            )
        )
    assert db.statements == []
    assert len(load_asset(asset)["datasets"]) == 1


def test_delete_drops_table_and_unregisters(tmp_path: Path) -> None:
    asset = _asset_with_period(tmp_path)
    db = _RecordingDb()
    result = asyncio.run(
        manage.delete_overlay_dataset(
            db, dataset_id="2026_3", confirm="2026_3_change_landuse", backup=False, asset_path=asset
        )
    )
    assert result["dropped"] is True and result["unregistered"] is True
    assert any("DROP TABLE" in sql for sql in db.statements)
    assert load_asset(asset)["datasets"] == []


def test_delete_missing_table_still_unregisters(tmp_path: Path) -> None:
    asset = _asset_with_period(tmp_path)
    db = _RecordingDb(exists=False)
    result = asyncio.run(
        manage.delete_overlay_dataset(
            db, dataset_id="2026_3", confirm="2026_3_change_landuse", backup=False, asset_path=asset
        )
    )
    assert result["dropped"] is False and result["unregistered"] is True
    assert db.statements == []
    assert load_asset(asset)["datasets"] == []

class _Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.pg_dsn = "postgresql://example"
        self.datasets = self._load_datasets()

    def _load_datasets(self) -> Any:
        from geoagent.datasets import DatasetRegistry

        return DatasetRegistry(periods=(), default_id="")


class _FakeDb:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    async def close(self) -> None:
        return None


def test_overlay_delete_route(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from geoagent.server import overlay_routes

    async def fake_list(db: Any, registry: Any) -> list[dict[str, Any]]:
        return [{"table_name": "2026_3_change_landuse", "table_ref": 'data."2026_3_change_landuse"',
                 "rows": 5, "area_mu": 1.0, "with_geom": 5, "registered": True, "label": "2026年第三期",
                 "dataset_id": "2026_3", "is_default": False}]

    calls: dict[str, Any] = {}

    async def fake_delete(db: Any, **kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        return {"table_name": "2026_3_change_landuse", "table_ref": 'data."2026_3_change_landuse"',
                "dropped": True, "unregistered": True, "dataset_id": "2026_3", "label": "2026年第三期",
                "backup": None}

    monkeypatch.setattr(overlay_routes, "list_overlay_tables", fake_list)
    monkeypatch.setattr(overlay_routes, "delete_overlay_dataset", fake_delete)
    monkeypatch.setattr(overlay_routes, "OverlayDatabase", _FakeDb)

    app = FastAPI()
    app.include_router(overlay_routes.router)
    app.state.settings = _Settings(tmp_path)
    app.state.overlay_jobs = {}

    with TestClient(app) as client:
        tables = client.get("/api/overlay/tables").json()
        assert tables["tables"][0]["table_name"] == "2026_3_change_landuse"
        assert isinstance(tables["backup_available"], bool)

        response = client.post(
            "/api/overlay/delete",
            json={"dataset_id": "2026_3", "confirm": "2026_3_change_landuse", "backup": False},
        )
        assert response.status_code == 200, response.text
        assert response.json()["unregistered"] is True
        assert calls["dataset_id"] == "2026_3" and calls["backup"] is False

        app.state.overlay_jobs["busy"] = {"status": "running"}
        busy = client.post("/api/overlay/delete", json={"dataset_id": "2026_3", "confirm": "x"})
        assert busy.status_code == 409
        app.state.overlay_jobs.clear()

        # 参数校验在 manage.delete_overlay_dataset 的单元测试里覆盖（这里替换成了假实现）

def test_overlay_route_runs_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from geoagent.server import overlay_routes

    async def fake_run(inputs: Any, options: spec.OverlayOptions, db: Any, *, progress: Any = None, asset_path: Any = None) -> dict[str, Any]:
        assert options.table_name == "2026_4_change_landuse"
        assert inputs.hsx and inputs.qsx and not inputs.yjn
        if progress is not None:
            await progress("切分套合", 50, "测试")
        return {
            "table": options.table_ref, "table_name": options.table_name,
            "dataset_id": options.period_id, "label": options.period_label,
            "rows": 3, "counties": 1, "area_m2": 1000.0, "area_mu": 1.5,
            "sfyn_yes": 1, "sfhx_yes": 0, "layers": {}, "warnings": [],
            "registered": False, "dataset": None, "elapsed_s": 0.1, "job_id": "test",
        }

    monkeypatch.setattr(overlay_routes, "run_overlay", fake_run)
    monkeypatch.setattr(overlay_routes, "OverlayDatabase", _FakeDb)

    app = FastAPI()
    app.include_router(overlay_routes.router)
    app.state.settings = _Settings(tmp_path)
    app.state.overlay_jobs = {}
    app.state.overlay_tasks = set()

    with TestClient(app) as client:
        options = client.get("/api/overlay/options").json()
        assert [item["key"] for item in options["layers"]] == ["hsx", "qsx", "yjn", "hx"]
        assert client.get("/api/overlay/jobs/missing").status_code == 404
        assert client.post("/api/overlay/build", data={"year": "2026", "round": "4"}).status_code == 400

        app.state.overlay_jobs["busy"] = {"status": "running"}
        blocked = client.post("/api/overlay/build", data={"table_name": "x", "year": "2026", "round": "4"})
        assert blocked.status_code == 409
        app.state.overlay_jobs.clear()

        incomplete = client.post(
            "/api/overlay/build",
            data={"table_name": "2026_4_change_landuse", "year": "2026", "round": "4", "register": "0"},
            files={"hsx": ("a.shp", b"binary"), "qsx": ("b.shp", b"binary")},
        )
        assert incomplete.status_code == 400
        assert "上传文件不完整" in incomplete.json()["detail"]
        response = client.post(
            "/api/overlay/build",
            data={"table_name": "2026_4_change_landuse", "year": "2026", "round": "4", "register": "0"},
            files={"hsx": ("a.zip", _fake_zip()), "qsx": ("b.zip", _fake_zip())},
        )
        assert response.status_code == 200, response.text
        job_id = response.json()["job_id"]
        job: dict[str, Any] = {}
        for _ in range(300):
            job = client.get(f"/api/overlay/jobs/{job_id}").json()
            if job["status"] != "running":
                break
            time.sleep(0.01)
        assert job["status"] == "done", job
        assert job["result"]["rows"] == 3
        assert client.get("/api/overlay/jobs").json()["jobs"]