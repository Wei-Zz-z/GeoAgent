"""区县汇总与MCP测试，使用内存矢量，不连接数据库或模型。"""

from pathlib import Path

import geopandas as gpd
import pytest
from mcp import Client
from shapely.geometry import Point, Polygon

from geoagent.analysis.county_charts import aggregate_counties
from geoagent import county_mcp_server, mcp_server


def sample() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"XZQDM": ["330101", "330101", "330102"],
        "XMC": ["甲县", "甲县", "乙县"], "TBLX": ["01", "03", "01"],
        "MJ": [1000, 2000, 500], "MU": [1.5, 3, 0.75]},
        geometry=[Polygon([(120,29),(120.01,29),(120.01,29.01),(120,29.01)])] * 3, crs=4326)


def test_county_totals_units_and_internal_anchor() -> None:
    from shapely.geometry import shape
    data = aggregate_counties(sample())
    assert data["metadata"]["county_count"] == 2
    assert data["metadata"]["feature_count"] == 3
    assert data["metadata"]["area_mu"] == 5.25
    assert data["features"][0]["properties"]["values"] == {"耕地": 1.5, "林地": 3}
    for feature in data["features"]:
        assert shape(feature["geometry"]).covers(Point(feature["properties"]["anchor"]))


def test_units_and_county_id_must_not_be_guessed() -> None:
    frame = sample()
    frame.loc[0, "MU"] = 1000
    with pytest.raises(ValueError, match="换算"):
        aggregate_counties(frame)
    frame = sample()
    frame.loc[0, "XZQDM"] = ""
    with pytest.raises(ValueError, match="代码"):
        aggregate_counties(frame)


def boundaries() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({"XZQDM": ["330101", "330102"], "XZQMC": ["甲县", "乙县"]},
        geometry=[Polygon([(119,28),(121,28),(121,30),(119,30)])] * 2, crs=4326)


def test_boundary_replaces_geometry_not_statistics() -> None:
    from shapely.geometry import shape
    frame = sample()
    original = aggregate_counties(frame)
    result = aggregate_counties(frame, boundaries())
    assert result["metadata"]["area_mu"] == original["metadata"]["area_mu"]
    assert result["metadata"]["uses_administrative_boundaries"]
    for feature in result["features"]:
        assert shape(feature["geometry"]).area > 1
        assert shape(feature["geometry"]).covers(Point(feature["properties"]["anchor"]))
    frame["geometry"] = None
    assert aggregate_counties(frame, boundaries())["metadata"]["area_mu"] == 5.25


def test_boundary_mismatch_and_duplicates_are_rejected() -> None:
    with pytest.raises(ValueError, match="未匹配"):
        aggregate_counties(sample(), boundaries().iloc[:1])
    wrong = boundaries()
    wrong.loc[0, "XZQMC"] = "另一县"
    with pytest.raises(ValueError, match="名称不一致"):
        aggregate_counties(sample(), wrong)
    wrong = boundaries()
    wrong.loc[1, "XZQDM"] = "330101"
    with pytest.raises(ValueError, match="重复"):
        aggregate_counties(sample(), wrong)


def test_boundary_without_statistics_is_not_zero() -> None:
    result = aggregate_counties(sample().iloc[:2], boundaries())
    extra = next(f for f in result["features"] if f["properties"]["XZQDM"] == "330102")
    assert extra["properties"]["area_mu"] is None
    assert not extra["properties"]["has_statistics"]
    assert result["metadata"]["warnings"]


def test_invalid_boundary_repair_is_visible() -> None:
    source = boundaries()
    source.loc[0, "geometry"] = Polygon([(119,28),(121,30),(121,28),(119,30)])
    assert aggregate_counties(sample(), source)["metadata"]["warnings"]


def test_map_route_accepts_two_zips_and_old_raw_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from geoagent.server import county_routes
    def fake_build(vector: Path, boundary: Path | None = None) -> dict:
        assert vector.read_bytes() == b"vector"
        assert boundary is None or boundary.read_bytes() == b"boundary"
        return {"has_boundary": boundary is not None}
    monkeypatch.setattr(county_routes, "build_county_chart_map", fake_build)
    app = FastAPI()
    app.include_router(county_routes.router)
    with TestClient(app) as client:
        response = client.post("/api/county-charts/build", files={"vector": ("a.zip", b"vector"), "boundary": ("b.zip", b"boundary")})
        assert response.status_code == 200
        assert response.json()["has_boundary"]
        assert not client.post("/api/county-charts/build", content=b"vector").json()["has_boundary"]
        assert client.post("/api/county-charts/build", files={"boundary": ("b.zip", b"boundary")}).status_code == 400
@pytest.mark.asyncio
async def test_map_mcp_discovery_and_real_tool_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEOAGENT_REPORTS_DIR", str(tmp_path))
    monkeypatch.setattr(county_mcp_server, "build_county_chart_map", lambda path: {
        "kind": "map_chart", "data": aggregate_counties(sample())})
    async with Client(county_mcp_server.mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {"create_county_chart_map"}
        result = await client.call_tool("create_county_chart_map", {"vector_zip_path": "mock.zip"})
        assert not result.is_error
        assert result.structured_content["metadata"]["county_count"] == 2
        assert Path(result.structured_content["artifact_path"]).is_file()
        assert len(result.structured_content["counties"]) == 2


@pytest.mark.asyncio
async def test_report_mcp_reuses_dynamic_query_and_refill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from docx import Document
    monkeypatch.setenv("GEOAGENT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GEOAGENT_REPORTS_DIR", str(tmp_path / "reports"))
    source = tmp_path / "template.docx"
    doc = Document()
    doc.add_paragraph("本期全库变化图斑***个，总面积***亩。")
    doc.save(source)
    async with Client(mcp_server.mcp) as client:
        parsed = await client.call_tool("start_template_workflow", {"template_path": str(source)})
        assert not parsed.is_error
        workflow_id = parsed.structured_content["workflow_id"]
        # 未查询仍能输出核验文件，但必须保留待核实项，不伪造答案。
        result = await client.call_tool("generate_template_report", {"workflow_id": workflow_id})
        assert not result.is_error
        assert result.structured_content["summary"]["pending_items"]
        assert Path(result.structured_content["word"]["path"]).is_file()
        assert result.structured_content["pdf"] is None
