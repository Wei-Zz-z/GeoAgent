"""向量知识库（问题模板 + schema 元数据 + 字典知识）构建与检索。"""

from .kb import KnowledgeBase
from .store import (
    FileVectorStore,
    PgVectorStore,
    VectorRecord,
    VectorStore,
    VectorStoreError,
    create_store,
)
from .sources import (
    build_question_records,
    build_schema_records,
    collect_dict_records,
    collect_schema_meta,
)

__all__ = [
    "KnowledgeBase",
    "VectorRecord",
    "VectorStore",
    "VectorStoreError",
    "FileVectorStore",
    "PgVectorStore",
    "create_store",
    "build_question_records",
    "build_schema_records",
    "collect_dict_records",
    "collect_schema_meta",
]
