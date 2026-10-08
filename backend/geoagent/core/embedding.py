"""OpenAI 兼容的 Embedding 服务门面（语义检索底座）。

复用现有 OpenAI 兼容端点（DashScope ``text-embedding-v3`` 等）为文本生成向量，
供标准问题库 / Schema 知识库做语义检索。与 ``core/llm.py`` 的 ``LLMService`` 同构：

- 复用同一个 AsyncOpenAI 客户端（``OPENAI_API_KEY`` / ``OPENAI_BASE_URL``）；
- 模型与 base_url 可用环境变量覆盖（``GEOAGENT_EMBEDDING_MODEL`` /
  ``GEOAGENT_EMBEDDING_BASE_URL``，base_url 为空时沿用 ``OPENAI_BASE_URL``）；
- 未配置 API key 时 ``available=False``，上层（``tools/qa_library``）自动回退关键词检索。

零新增第三方依赖：仅用标准库 + 已有的 ``openai`` 包。
"""

from __future__ import annotations

import math
import os
from typing import Any, Optional

from openai import AsyncOpenAI

from ..config import Settings


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """两个等长向量的余弦相似度（纯 Python，避免引入 numpy）。

    空向量 / 长度不一致 / 模长为 0 时返回 0.0。
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class EmbeddingService:
    """把文本向量化的门面；未配置 key 时 ``available=False``（调用方降级）。"""

    def __init__(self, settings: Settings, batch_size: int = 10) -> None:
        self.settings = settings
        self.model = settings.embedding_model
        self.base_url = (
            settings.embedding_base_url or os.getenv("OPENAI_BASE_URL") or None
        )
        self.batch_size = batch_size
        self._client: Optional[AsyncOpenAI] = None

    @property
    def available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY", ""))

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {"api_key": os.getenv("OPENAI_API_KEY", "")}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """把一组文本向量化，返回与输入等长的向量列表（自动分批）。"""
        if not texts:
            return []
        vectors: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            chunk = texts[i : i + self.batch_size]
            resp = await self._get_client().embeddings.create(
                model=self.model, input=chunk
            )
            # 按 index 排序，保证返回顺序与输入顺序一致。
            ordered = sorted(resp.data, key=lambda item: item.index)
            vectors.extend(list(item.embedding) for item in ordered)
        return vectors
