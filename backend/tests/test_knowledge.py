"""向量知识库（store / sources / kb）测试（不依赖真实库与网络）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from geoagent.knowledge.kb import KnowledgeBase
from geoagent.knowledge.sources import build_question_records, build_schema_records
from geoagent.knowledge.store import (
    FileVectorStore,
    VectorRecord,
    create_store,
)
from geoagent.tools.qa_library import load_entries, resolve_asset_path


def test_question_library_accepts_explicit_asset_path(tmp_path) -> None:
    asset = tmp_path / "question_library.json"
    asset.write_text('{"entries": [{"id": "q", "question": "测试问题", "intent": "测试"}]}', encoding="utf-8")
    assert resolve_asset_path(asset) == asset
    assert load_entries(asset)[0].id == "q"


class FakeEmbedder:
    """字符袋向量假 embedder：共享字符的文本有正相似度。"""

    available = True
    model = "fake-embed"

    def __init__(self, dims: str) -> None:
        self._dims = sorted(set(dims))

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0 if d in t else 0.0 for d in self._dims] for t in texts]


# ---------------------------------------------------------------- 存储后端


def test_file_store_upsert_and_search(tmp_path) -> None:
    store = FileVectorStore(tmp_path / "vectors.jsonl")
    records = [
        VectorRecord(id="a", kind="question", text="耕地面积", payload={"k": "a"}, vector=[1.0, 0.0]),
        VectorRecord(id="b", kind="question", text="建设用地", payload={"k": "b"}, vector=[0.0, 1.0]),
    ]
    import asyncio

    asyncio.run(store.upsert(records))
    hits = asyncio.run(store.search([1.0, 0.0], top_k=2, kind="question"))
    assert hits[0][0].id == "a"
    assert hits[0][1] == pytest.approx(1.0)
    assert asyncio.run(store.count("question")) == 2


def test_file_store_persists_across_instances(tmp_path) -> None:
    path = tmp_path / "vectors.jsonl"
    import asyncio

    asyncio.run(
        FileVectorStore(path).upsert(
            [VectorRecord(id="x", kind="schema", text="字段 MJ 面积", payload={"scope": "column"}, vector=[1.0])]
        )
    )
    loaded = FileVectorStore(path)
    assert asyncio.run(loaded.count("schema")) == 1


def test_create_store_backends(tmp_path) -> None:
    settings = SimpleNamespace(
        vector_store="file", knowledge_dir=tmp_path, pgvector_dsn=""
    )
    assert isinstance(create_store(settings), FileVectorStore)
    settings.vector_store = "auto"
    assert isinstance(create_store(settings), FileVectorStore)


# ---------------------------------------------------------------- 数据源


def test_build_question_records_from_default_library() -> None:
    entries = load_entries()
    records = build_question_records(entries)
    ids = {r.id for r in records}
    assert {"total_summary", "farmland_flow", "construction_ratio", "convert_area_units"} <= ids
    for r in records:
        assert r.kind == "question"
        assert r.text


def test_build_schema_records_includes_table_and_columns() -> None:
    meta = {
        "tables": [
            {
                "schema": "data",
                "table": "t",
                "description": "变化图斑主表",
                "columns": [
                    {
                        "column_name": "MJ",
                        "data_type": "double precision",
                        "udt_name": "float8",
                        "is_nullable": "YES",
                        "character_maximum_length": None,
                        "comment": "面积（平方米）",
                        "is_primary_key": False,
                    },
                    {
                        "column_name": "OBJECTID",
                        "data_type": "bigint",
                        "udt_name": "int8",
                        "is_nullable": "NO",
                        "character_maximum_length": None,
                        "comment": "主键",
                        "is_primary_key": True,
                    },
                ],
            }
        ]
    }
    records = build_schema_records(meta)
    assert any(r.id == "table:data.t" and r.payload["scope"] == "table" for r in records)
    col = next(r for r in records if r.id == "column:data.t.MJ")
    assert col.payload["comment"] == "面积（平方米）"
    pk = next(r for r in records if r.payload.get("is_primary_key"))
    assert pk.payload["column"] == "OBJECTID"


# ---------------------------------------------------------------- 编排器（构建 + 检索）


def _settings(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        vector_store="file", knowledge_dir=tmp_path, pgvector_dsn=""
    )


def _schema_meta() -> dict:
    return {
        "tables": [
            {
                "schema": "data",
                "table": "t",
                "description": "变化图斑主表",
                "columns": [
                    {
                        "column_name": "MJ",
                        "data_type": "double precision",
                        "udt_name": "float8",
                        "is_nullable": "YES",
                        "character_maximum_length": None,
                        "comment": "面积（平方米）",
                        "is_primary_key": False,
                    },
                    {
                        "column_name": "XMC",
                        "data_type": "character varying",
                        "udt_name": "varchar",
                        "is_nullable": "YES",
                        "character_maximum_length": 60,
                        "comment": "县级行政区名称",
                        "is_primary_key": False,
                    },
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_kb_build_and_search_questions_persistent_path(tmp_path) -> None:
    embedder = FakeEmbedder("耕地建设用地类型面积县图斑流出流入净变化转占百分比换算公顷亩")
    kb = KnowledgeBase(
        _settings(tmp_path), embedder, store=FileVectorStore(tmp_path / "v.jsonl")
    )
    report = await kb.build(question_entries=load_entries())
    assert report["stored"] > 0
    hits = await kb.search_questions("耕地流出流入净变化", top_k=3)
    assert hits and hits[0].id == "farmland_flow"


@pytest.mark.asyncio
async def test_kb_search_questions_falls_back_when_not_built(tmp_path) -> None:
    embedder = FakeEmbedder("耕地建设用地类型面积县图斑流出流入净变化转占百分比换算公顷亩")
    kb = KnowledgeBase(
        _settings(tmp_path), embedder, store=FileVectorStore(tmp_path / "v.jsonl")
    )
    # 未 build（空 store）→ 回退按需语义检索，仍应命中。
    hits = await kb.search_questions("耕地流出流入净变化", top_k=3)
    assert hits and hits[0].id == "farmland_flow"


@pytest.mark.asyncio
async def test_kb_search_schema_hits_area_column(tmp_path) -> None:
    embedder = FakeEmbedder("面积字段县级行政区名称图斑类型编码MJ平方米")
    kb = KnowledgeBase(
        _settings(tmp_path), embedder, store=FileVectorStore(tmp_path / "v.jsonl")
    )
    await kb.build(schema_meta=_schema_meta())
    hits = await kb.search_schema("面积字段", top_k=5)
    assert hits, "应命中面积相关字段"
    assert hits[0]["column"] == "MJ"
    assert hits[0]["_similarity"] > 0.4


@pytest.mark.asyncio
async def test_kb_search_schema_empty_when_not_built(tmp_path) -> None:
    embedder = FakeEmbedder("面积字段县级行政区名称")
    kb = KnowledgeBase(
        _settings(tmp_path), embedder, store=FileVectorStore(tmp_path / "v.jsonl")
    )
    assert await kb.search_schema("面积字段", top_k=5) == []
