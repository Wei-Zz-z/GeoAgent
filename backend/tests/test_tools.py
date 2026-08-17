from __future__ import annotations

import pytest

from geoagent.core.llm import ToolCall
from geoagent.tools import ToolExecutor, get_tools


@pytest.mark.asyncio
async def test_buffer_point_valid():
    executor = ToolExecutor(get_tools("buffer_point"))
    result = await executor.execute(
        ToolCall(id="c1", name="buffer_point", arguments={"lon": 116.4, "lat": 39.9, "radius_km": 5}),
        ctx=None,
    )
    assert not result.is_error
    assert result.artifacts
    assert result.artifacts[0].kind == "geojson"
    assert result.artifacts[0].data["geometry"]["type"] == "Polygon"


@pytest.mark.asyncio
async def test_buffer_point_invalid_params():
    executor = ToolExecutor(get_tools("buffer_point"))
    result = await executor.execute(
        ToolCall(id="c2", name="buffer_point", arguments={"lon": 116.4, "lat": 39.9, "radius_km": -5}),
        ctx=None,
    )
    assert result.is_error
    assert "参数校验失败" in result.content


@pytest.mark.asyncio
async def test_unknown_tool():
    executor = ToolExecutor(get_tools("buffer_point"))
    result = await executor.execute(ToolCall(id="c3", name="nope", arguments={}), ctx=None)
    assert result.is_error
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_load_dataset_artifact():
    executor = ToolExecutor(get_tools("load_dataset"))
    result = await executor.execute(
        ToolCall(id="c4", name="load_dataset", arguments={"dataset_id": "beijing_pois"}),
        ctx=None,
    )
    assert not result.is_error
    geojson = result.artifacts[0].data
    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 6


def test_tool_schema_generated_from_pydantic():
    tool = get_tools("buffer_point")[0]
    schema = tool.to_llm_format()
    assert schema["function"]["parameters"]["properties"]["radius_km"]["type"] == "number"
    assert "required" in schema["function"]["parameters"]
