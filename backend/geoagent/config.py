from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .auth.ticket import DEFAULT_VALIDATE_URL as DEFAULT_TICKET_VALIDATE_URL
from .datasets import DatasetRegistry, load_registry
from .datasets import resolve_asset_path as resolve_dataset_asset


# 土地变化检测库默认白名单：图斑主表 + 地类字典/元数据表。
# 提示词只描述表结构与口径，编码/名称/大类等"数据知识"由模型按需查询，
# 避免提示词背字典造成漂移（可通过 GEOAGENT_PG_WHITELIST 覆盖）。
DEFAULT_PG_WHITELIST: list[str] = [
    'data."2026_1_change_landuse"',
    'data."2026_2_change_landuse"',
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


def _parse_mcp_tokens(raw: str) -> dict[str, str]:
    """解析 GEOAGENT_MCP_TOKENS：支持「令牌」或「令牌:调用方名」，逗号分隔。"""
    tokens: dict[str, str] = {}
    for item in str(raw or "").replace("\n", ",").split(","):
        value = item.strip()
        if not value:
            continue
        token, _, label = value.partition(":")
        token = token.strip()
        if token:
            tokens[token] = (label.strip() or f"token-{token[-6:]}")
    return tokens


def elder_care_enabled() -> bool:
    """养老可达性评估场景是否启用（GEOAGENT_ELDER_CARE，默认启用）。

    部署机上设为 0 时，启动阶段不再导入 analysis 下的重依赖
    （geopandas / pandas / networkx / shapely），可显著降低常驻内存；
    前端智能体清单与路由枚举也会同步隐藏该场景。
    """
    value = os.getenv("GEOAGENT_ELDER_CARE", "1").strip().lower()
    return value not in {"0", "off", "false", "no"}


class Settings:
    """应用配置。数据目录与默认模型可通过环境变量覆盖。"""

    def __init__(self) -> None:
        default_data_dir = Path(__file__).resolve().parent.parent / "data"
        configured_data_dir = os.getenv("GEOAGENT_DATA_DIR", "").strip()
        self.data_dir = Path(configured_data_dir) if configured_data_dir else default_data_dir
        self.default_model = os.getenv("GEOAGENT_DEFAULT_MODEL", "qwen3.8-27b")
        self.router_model = os.getenv("GEOAGENT_ROUTER_MODEL", "")
        # 场景开关：养老可达性评估是否启用（部署机可关，避免启动即加载整套分析栈）
        self.elder_care_enabled = elder_care_enabled()
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
        # 会话存储：默认写 PostgreSQL（GEOAGENT_CHAT_DSN，未配置则沿用业务库 DSN）；
        # memory 仅用于单元测试或未配库的本地联调（进程重启即丢失）。
        self.chat_store = os.getenv("GEOAGENT_CHAT_STORE", "postgres").strip().lower() or "postgres"
        self.chat_dsn = os.getenv("GEOAGENT_CHAT_DSN", "").strip() or self.pg_dsn
        self.chat_schema = os.getenv("GEOAGENT_CHAT_SCHEMA", "geoagent").strip() or "geoagent"
        # MCP（Streamable HTTP）对外接口：下载链接有效期与对外基地址
        # （MCP 令牌由 MCP 层自己校验，见 mcp_http._configured_tokens）
        self.mcp_download_ttl_s = int(os.getenv("GEOAGENT_MCP_DOWNLOAD_TTL_S", "3600"))
        self.public_base_url = os.getenv("GEOAGENT_PUBLIC_BASE_URL", "").strip().rstrip("/")
        # 期次数据集：一张套合表 = 一期数据。datasets.json 是期次的唯一来源，
        # 因此无论白名单是否显式配置，都把已登记期次表并进白名单——否则新登记一期后
        # 忘了改 .env 就会出现“期次选得到、查询被护栏拒绝”。换一期只改 datasets.json。
        self.datasets = self._load_datasets()
        for table in self.datasets.tables:
                if table not in self.pg_whitelist:
                    self.pg_whitelist.append(table)
        # 问答链路：先标准问题匹配（execute=命中即确定性执行 / hint=只作为提示 /
        # off=关闭），未命中再走自由 SQL 兜底。
        self.qa_prematch = os.getenv("GEOAGENT_QA_PREMATCH", "execute").strip().lower() or "execute"
        self.qa_prematch_confidence = float(
            os.getenv("GEOAGENT_QA_PREMATCH_CONFIDENCE", "0.42")
        )
        # 预匹配是否启用 embedding 向量召回 + RRF；未配置 API key 时自动降级为纯规则。
        self.qa_vector = os.getenv("GEOAGENT_QA_VECTOR", "1").strip().lower() not in {
            "0", "off", "false", "no",
        }
        # 灰度区是否调用 LLM judge 复核候选（只在 candidates/clarify 时调用）。
        self.qa_judge = os.getenv("GEOAGENT_QA_JUDGE", "1").strip().lower() not in {
            "0", "off", "false", "no",
        }
        self.pgvector_dsn = os.getenv("GEOAGENT_PGVECTOR_DSN", "").strip() or self.pg_dsn
        # 入口验证与用户体系：省域平台 ticket 换本系统登录态。
        # dev=不校验（改造前的开发模式，行为不变）；ticket=校验后按用户隔离会话。
        self.auth_mode = os.getenv("GEOAGENT_AUTH_MODE", "dev").strip().lower() or "dev"
        self.auth_ticket_validate_url = (
            os.getenv("GEOAGENT_TICKET_VALIDATE_URL", "").strip()
            or DEFAULT_TICKET_VALIDATE_URL
        )
        self.auth_session_ttl_s = int(os.getenv("GEOAGENT_AUTH_SESSION_TTL_S", "28800"))
        self.auth_cookie_name = (
            os.getenv("GEOAGENT_AUTH_COOKIE", "geoagent_session").strip()
            or "geoagent_session"
        )
        self.auth_cookie_secure = os.getenv("GEOAGENT_AUTH_COOKIE_SECURE", "").strip() == "1"
        self.auth_secret = os.getenv("GEOAGENT_AUTH_SECRET", "").strip()
        self.auth_ticket_timeout_s = float(os.getenv("GEOAGENT_TICKET_TIMEOUT_S", "5"))
        self.auth_audit_path = self.data_dir / "auth_audit.jsonl"
        # 快报等生成文件输出到仓库外的用户数据目录（默认 %LOCALAPPDATA%/GeoAgent/reports）。
        # 配置项留空视为未配置：否则 Path("") 会解析成当前工作目录，快报会被写进仓库。
        base_data_home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        self.reports_dir = Path(
            os.getenv("GEOAGENT_REPORTS_DIR", "").strip()
            or str(base_data_home / "GeoAgent" / "reports")
        )

    def _load_datasets(self) -> DatasetRegistry:
        """加载期次注册表；资产缺失或损坏时回退空注册表（工具仍有兜底表）。"""
        try:
            return load_registry(resolve_dataset_asset(self.skills_dir))
        except Exception:
            return DatasetRegistry(periods=(), default_id="")

    def profile(self, model_id: str) -> ModelProfile:
        try:
            return self.model_registry[model_id]
        except KeyError:
            known = ", ".join(sorted(self.model_registry))
            raise KeyError(f"Unknown model '{model_id}'. Available: {known}") from None

    @property
    def auth_enabled(self) -> bool:
        """是否开启入口验证（GEOAGENT_AUTH_MODE=ticket）。"""
        return self.auth_mode == "ticket"

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
