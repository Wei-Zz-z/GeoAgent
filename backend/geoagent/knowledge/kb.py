"""知识库编排器：构建（embedding + 入库）与检索（语义召回）的门面。

``KnowledgeBase`` 挂载在 ``app.state.knowledge`` 并通过 ``ConversationContext`` 注入工具，
是"问题模板 + schema 元数据 + 字典知识"三合一向量知识库的运行时入口：

- ``build(...)``：由构建脚本调用，把数据源记录向量化后写入后端；
- ``search_questions(...)``：问题库混合检索（关键词 + 持久向量），未构建时回退到
  ``qa_library`` 的按需语义检索，保证可用性；
- ``search_schema(...)``：schema 元数据语义检索（供 ``search_schema_knowledge`` 工具）。
"""

from __future__ import annotations

from typing import Any, Optional

from ..tools.qa_library import (
    _KEYWORD_SCORE_CAP,
    _KEYWORD_WEIGHT,
    _SEMANTIC_THRESHOLD,
    _SEMANTIC_WEIGHT,
    QuestionEntry,
    _score,
    load_entries,
    resolve_asset_path,
    search,
    search_semantic,
)
from .sources import build_question_records, build_schema_records
from .store import VectorRecord, VectorStore, create_store

# schema 检索的余弦阈值：低于该值视为不相关（避免把不相关字段召回给模型）。
_SCHEMA_THRESHOLD = 0.40


class KnowledgeBase:
    """向量知识库：构建 + 检索。"""

    def __init__(
        self,
        settings: Any,
        embedder: Any,
        store: Optional[VectorStore] = None,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self._store = store

    @property
    def store(self) -> VectorStore:
        if self._store is None:
            self._store = create_store(self.settings)
        return self._store

    @property
    def embedder_available(self) -> bool:
        return self.embedder is not None and bool(
            getattr(self.embedder, "available", False)
        )

    async def build(
        self,
        *,
        question_entries: Optional[list[Any]] = None,
        schema_meta: Optional[dict[str, Any]] = None,
        dict_records: Optional[list[VectorRecord]] = None,
    ) -> dict[str, Any]:
        """构建 / 重建向量库（记录 → embedding → 入库），返回构建统计。

        各类记录均为可选；未传的类别不会被清空。默认按 (kind, id) 幂等覆盖写入。
        """
        records: list[VectorRecord] = []
        if question_entries is not None:
            records.extend(build_question_records(question_entries))
        if schema_meta is not None:
            records.extend(build_schema_records(schema_meta))
        if dict_records is not None:
            records.extend(dict_records)
        if not records:
            return {"stored": 0, "backend": type(self.store).__name__}
        if not self.embedder_available:
            raise RuntimeError(
                "Embedding 未配置（缺少 OPENAI_API_KEY），无法构建向量知识库。"
            )
        texts = [r.text for r in records]
        vectors = await self.embedder.embed(texts)
        if len(vectors) != len(records):
            raise RuntimeError("Embedding 返回向量数量与输入不一致")
        for record, vector in zip(records, vectors):
            record.vector = vector
        await self.store.upsert(records)
        return {"stored": len(records), "backend": type(self.store).__name__}

    async def search_questions(
        self,
        query: str,
        top_k: int = 3,
        skills_dir: Optional[str | Any] = None,
    ) -> list[QuestionEntry]:
        """问题库混合检索：已构建用持久向量，未构建回退按需语义检索。"""
        path = resolve_asset_path(skills_dir)
        entries = load_entries(path)
        if not entries:
            return []
        store = self.store
        try:
            has_vectors = await store.count("question") > 0
        except Exception:
            has_vectors = False
        if has_vectors and self.embedder_available:
            try:
                qvec = (await self.embedder.embed([query]))[0]
                hits = await store.search(qvec, top_k=len(entries), kind="question")
                sim_by_id = {r.id: sim for r, sim in hits}
            except Exception:
                return search(query, top_k=top_k, skills_dir=skills_dir)
            scored: list[tuple[QuestionEntry, float]] = []
            for entry in entries:
                kw = _score(query, entry)
                sim = max(0.0, sim_by_id.get(entry.id, 0.0))
                kw_norm = min(1.0, kw / _KEYWORD_SCORE_CAP)
                combined = _KEYWORD_WEIGHT * kw_norm + _SEMANTIC_WEIGHT * sim
                if kw > 0 or sim >= _SEMANTIC_THRESHOLD:
                    scored.append((entry, combined))
            scored.sort(key=lambda pair: pair[1], reverse=True)
            return [entry for entry, _ in scored[:top_k]]
        # 未构建（或 embedding 不可用）→ 回退既有检索。
        if self.embedder_available:
            return await search_semantic(
                query, top_k=top_k, skills_dir=skills_dir, embedder=self.embedder
            )
        return search(query, top_k=top_k, skills_dir=skills_dir)

    async def search_schema(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = _SCHEMA_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """schema 元数据语义检索，返回命中 payload（相似度附加在 _similarity）。"""
        if not self.embedder_available:
            return []
        store = self.store
        try:
            if await store.count("schema") == 0:
                return []
            qvec = (await self.embedder.embed([query]))[0]
            hits = await store.search(qvec, top_k=top_k * 2, kind="schema")
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for record, sim in hits:
            if sim < threshold:
                continue
            item = dict(record.payload)
            item["_similarity"] = round(float(sim), 4)
            out.append(item)
        return out[:top_k]

    async def close(self) -> None:
        if self._store is not None:
            await self._store.close()
