from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 土地变化检测库默认白名单：图斑主表 + 地类字典/元数据表。
# 提示词只描述表结构与口径，编码/名称/大类等"数据知识"由模型按需查询，
# 避免提示词背字典造成漂移（可通过 GEOAGENT_PG_WHITELIST 覆盖）。
DEFAULT_PG_WHITELIST: list[str] = [
    'data."2026_1_change_landuse"',
    "knowledge_base.dict_tblx",
    "knowledge_base.dict_land_classification_summary",
]


@dataclass(frozen=True)
class ModelProfile:
    """模型注册表中的一条模型（或兼容端点）配置。"""

    id: str
    provider: str = "openai"
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    temperature: float | None = None
    max_tokens: int | None = None
    description: str = ""

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "available": self.available,
            "description": self.description,
        }


def default_model_registry() -> dict[str, ModelProfile]:
    """内置模型注册表。新增模型时在此追加一个 ModelProfile。"""
    return {
        "qwen3.8-27b": ModelProfile(
            id="qwen3.8-27b",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.8-27B (DashScope)",
        ),
        "qwen3.7-flash": ModelProfile(
            id="qwen3.7-flash",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.7-Flash (DashScope)",
        ),
        "qwen3.7-plus": ModelProfile(
            id="qwen3.7-plus",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.7-Plus (DashScope)",
        ),
        "qwen3.7-max-2026-06-08": ModelProfile(
            id="qwen3.7-max-2026-06-08",
            provider="openai_compatible",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key_env="OPENAI_API_KEY",
            description="Alibaba Qwen3.7-Max (DashScope)",
        ),
    }


class Settings:
    """应用配置。数据目录与默认模型可通过环境变量覆盖。"""

    def __init__(self) -> None:
        default_data_dir = Path(__file__).resolve().parent.parent / "data"
        configured_data_dir = os.getenv("GEOAGENT_DATA_DIR", "").strip()
        self.data_dir = Path(configured_data_dir) if configured_data_dir else default_data_dir
        self.default_model = os.getenv("GEOAGENT_DEFAULT_MODEL", "qwen3.8-27b")
        self.router_model = os.getenv("GEOAGENT_ROUTER_MODEL", "")
        default_skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"
        configured_skills_dir = os.getenv("GEOAGENT_SKILLS_DIR", "").strip()
        self.skills_dir = Path(configured_skills_dir) if configured_skills_dir else default_skills_dir
        self.model_registry = default_model_registry()
        # 向量知识库与 Embedding；默认文件存储，避免要求业务库写权限。
        self.embedding_model = os.getenv("GEOAGENT_EMBEDDING_MODEL", "text-embedding-v3").strip() or "text-embedding-v3"
        self.embedding_base_url = os.getenv("GEOAGENT_EMBEDDING_BASE_URL", "").strip()
        self.vector_store = os.getenv("GEOAGENT_VECTOR_STORE", "file").strip() or "file"
        self.template_vector_match = os.getenv("GEOAGENT_TEMPLATE_VECTOR_MATCH", "").strip() == "1"
        self.knowledge_dir = self.data_dir / "knowledge"
        # PostgreSQL/PostGIS 土地变化检测库（受控只读访问，连接串来自环境变量）。
        self.pg_dsn = os.getenv("GEOAGENT_PG_DSN", "").strip()
        raw_whitelist = os.getenv("GEOAGENT_PG_WHITELIST", "").strip()
        self.pg_whitelist = (
            [item.strip() for item in raw_whitelist.split(",") if item.strip()]
            if raw_whitelist
            else list(DEFAULT_PG_WHITELIST)
        )
        self.pg_max_rows = int(os.getenv("GEOAGENT_PG_MAX_ROWS", "200"))
        self.pg_timeout_s = float(os.getenv("GEOAGENT_PG_TIMEOUT_S", "10"))
        self.pg_audit_path = self.data_dir / "pg_audit.jsonl"
        self.pgvector_dsn = os.getenv("GEOAGENT_PGVECTOR_DSN", "").strip() or self.pg_dsn
        # 快报等生成文件输出到仓库外的用户数据目录（默认 %LOCALAPPDATA%/GeoAgent/reports）。
        base_data_home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        self.reports_dir = Path(
            os.getenv("GEOAGENT_REPORTS_DIR", str(base_data_home / "GeoAgent" / "reports"))
        )

    def profile(self, model_id: str) -> ModelProfile:
        try:
            return self.model_registry[model_id]
        except KeyError:
            known = ", ".join(sorted(self.model_registry))
            raise KeyError(f"Unknown model '{model_id}'. Available: {known}") from None

    def list_profiles(self) -> list[dict[str, Any]]:
        return [p.to_dict() for p in self.model_registry.values()]


def load_dotenv(path: Path | None = None) -> None:
    """供手动离线脚本读取 backend/.env，不覆盖已设置的环境变量。"""
    source = path or Path(__file__).resolve().parent.parent / ".env"
    if not source.is_file():
        return
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                value = value[1:-1]
            os.environ[key] = value
