"""预警分析工具测试：不依赖真实数据库与模型。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.core.llm import ToolCall
from geoagent.datasets import DatasetPeriod
from geoagent.tools import ToolExecutor
from geoagent.tools.warning import get_warning_tools

TABLE = 'data."2026_2_change_landuse"'

FEATURE_COLLECTION = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "MultiPolygon", "coordinates": []},
            "properties": {
                "objectid": 6031,
                "county": "缙云县",
                "tblx": "DT",
                "dlmc": "水田",
                "area_mu": 399.27,
            },
        }
    ],
}


class FakeGateway:
    """按 SQL 特征返回预置结果；记录执行过的 SQL。"""

    def __init__(self, *, geo: bool = True, empty_geo: bool = False) -> None:
        self.sql: list[str] = []
        self.geo = geo
        self.empty_geo = empty_geo

    async def run_sql(self, sql, conversation_id="", tool_call_id=""):
        self.sql.append(sql)
        if "json_build_object" in sql:
            if not self.geo:
                raise RuntimeError('column "geom" does not exist')
            payload = (
                {"type": "FeatureCollection", "features": []}
                if self.empty_geo
                else FEATURE_COLLECTION
            )
            return {
                "columns": ["shown_n", "with_geom", "fc"],
                "rows": [{"shown_n": 0 if self.empty_geo else 360,
                          "with_geom": 0 if self.empty_geo else 360, "fc": payload}],
                "row_count": 1,
                "truncated": False,
            }
        if "LIMIT 20" in sql:
            return {
                "columns": ["objectid", "county", "tblx", "dlmc", "area_m2", "sfyn", "sfhx"],
                "rows": [{
                    "objectid": 6031, "county": "缙云县", "tblx": "DT", "dlmc": "水田",
                    "area_m2": 266180.0, "sfyn": "是", "sfhx": "否",
                }],
                "row_count": 1,
                "truncated": False,
            }
        return {
            "columns": ["n", "area_m2"],
            "rows": [{"n": 360, "area_m2": 13570111.45}],
            "row_count": 1,
            "truncated": False,
        }


def _ctx(gateway: FakeGateway) -> SimpleNamespace:
    return SimpleNamespace(
        pg=gateway,
        dataset=DatasetPeriod(id="2026_2", label="2026年第二期", table=TABLE),
        datasets=None,
        conversation_id="conv-1",
    )


@pytest.mark.asyncio
async def test_warning_tool_returns_geojson_and_table():
    gateway = FakeGateway()
    executor = ToolExecutor(get_warning_tools())
    result = await executor.execute(
        ToolCall(id="c1", name="warning_yongnong_polygons", arguments={}),
        ctx=_ctx(gateway),
    )

    assert result.is_error is False
    kinds = [artifact.kind for artifact in result.artifacts]
    assert kinds == ["geojson", "table"]

    geo = result.artifacts[0]
    # 预警图层带红色样式提示（前端按它渲染）
    assert geo.data["features"] == FEATURE_COLLECTION["features"]
    assert geo.data["style"]["stroke"] == "#d93025"
    assert "rgba(217, 48, 37" in geo.data["style"]["fill"]
    assert "永久基本农田" in geo.name and "2026年第二期" in geo.name

    detail = result.artifacts[1].data
    assert detail["columns"] == ["主键", "行政区", "图斑类型", "原地类", "面积(亩)", "涉及永农", "涉及红线"]
    # TBLX 编码转中文，面积由平方米换算为亩
    assert detail["rows"][0]["图斑类型"] == "动土"
    assert detail["rows"][0]["面积(亩)"] == 399.27

    # 统计口径与期次正确，且全部走当期表
    assert all(TABLE in sql for sql in gateway.sql)
    assert "360" in result.content and "亩" in result.content
    assert "疑似" in result.content


@pytest.mark.asyncio
async def test_warning_tool_without_geometry_returns_table_only():
    gateway = FakeGateway(geo=False)
    executor = ToolExecutor(get_warning_tools())
    result = await executor.execute(
        ToolCall(id="c2", name="warning_yongnong_polygons", arguments={}),
        ctx=_ctx(gateway),
    )

    assert result.is_error is False
    assert [artifact.kind for artifact in result.artifacts] == ["table"]
    assert "无法在地图上展示" in result.content


@pytest.mark.asyncio
async def test_warning_tool_with_empty_geometry_collection():
    gateway = FakeGateway(empty_geo=True)
    executor = ToolExecutor(get_warning_tools())
    result = await executor.execute(
        ToolCall(id="c3", name="warning_redline_polygons", arguments={}),
        ctx=_ctx(gateway),
    )

    assert result.is_error is False
    assert [artifact.kind for artifact in result.artifacts] == ["table"]
    assert "无法在地图上展示" in result.content


@pytest.mark.asyncio
async def test_warning_tool_reports_zero_without_map():
    class EmptyGateway(FakeGateway):
        async def run_sql(self, sql, conversation_id="", tool_call_id=""):
            self.sql.append(sql)
            if "json_build_object" in sql:
                return {"columns": [], "rows": [], "row_count": 0, "truncated": False}
            return {
                "columns": ["n", "area_m2"],
                "rows": [{"n": 0, "area_m2": None}],
                "row_count": 1,
                "truncated": False,
            }

    result = await ToolExecutor(get_warning_tools()).execute(
        ToolCall(id="c4", name="warning_yongnong_polygons", arguments={}),
        ctx=_ctx(EmptyGateway()),
    )
    assert result.artifacts == []
    assert "没有匹配到" in result.content


def test_warning_tools_are_registered_with_defaults():
    tools = get_warning_tools()
    assert [tool.name for tool in tools] == [
        "warning_yongnong_polygons",
        "warning_redline_polygons",
    ]
    for tool in tools:
        # prematch 用空参数调用，必须全部有默认值
        assert tool.parameters["properties"]["max_features"]["default"] == 500

@pytest.mark.asyncio
async def test_warning_tool_filters_by_county():
    """传 county 时三条 SQL 都带上行政区条件，标题也标出区县。"""
    gateway = FakeGateway()
    result = await ToolExecutor(get_warning_tools()).execute(
        ToolCall(
            id="c5",
            name="warning_yongnong_polygons",
            arguments={"county": "缙云县"},
        ),
        ctx=_ctx(gateway),
    )

    assert result.is_error is False
    assert gateway.sql
    assert all('"XMC" = \'缙云县\'' in sql for sql in gateway.sql)
    assert "缙云县" in result.artifacts[0].name
    assert "缙云县" in result.content


@pytest.mark.asyncio
async def test_warning_tool_escapes_county_quotes():
    """地方名进 SQL 前必须转义单引号。"""
    gateway = FakeGateway()
    await ToolExecutor(get_warning_tools()).execute(
        ToolCall(
            id="c6",
            name="warning_yongnong_polygons",
            arguments={"county": "缙'云县"},
        ),
        ctx=_ctx(gateway),
    )
    assert any("缙''云县" in sql for sql in gateway.sql)
