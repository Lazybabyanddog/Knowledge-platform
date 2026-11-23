"""应用配置：全部来自环境变量，集中在此处声明，避免散落读取 os.environ。"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- 应用 ----
    app_name: str = "ai-platform"
    app_env: Literal["local", "test", "prod"] = "local"
    debug: bool = True
    api_prefix: str = "/api/v1"
    # 列表型配置统一用 NoDecode 关掉 pydantic-settings 的 JSON 强解，交给 _split_csv 处理
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # ---- PostgreSQL ----
    database_url: str = "postgresql+psycopg://ai_platform:ai_platform@localhost:5434/ai_platform"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ---- Neo4j ----
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "ai_platform_neo4j"
    neo4j_database: str = "neo4j"
    neo4j_max_connection_lifetime: int = 3600

    # ---- MinIO ----
    minio_endpoint: str = "localhost:9100"
    minio_access_key: str = "ai_platform"
    minio_secret_key: str = "ai_platform_minio"
    minio_bucket: str = "ai-platform"
    minio_secure: bool = False
    minio_presign_expire_seconds: int = 900

    # ---- Redis / Celery ----
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ---- 认证 ----
    # HS256 建议密钥不短于 32 字节，否则 PyJWT 会告警
    jwt_secret: str = "dev-only-secret-change-me-in-production-01"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 7200
    refresh_token_ttl_seconds: int = 604800
    login_max_failures: int = 5
    login_lock_minutes: int = 10

    # ---- 上传 ----
    upload_max_size_mb: int = 200
    upload_allowed_extensions: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            ".pdf",
            ".doc",
            ".docx",
            ".ppt",
            ".pptx",
            ".xls",
            ".xlsx",
            ".txt",
            ".md",
            ".html",
            ".csv",
            ".png",
            ".jpg",
            ".jpeg",
            ".tiff",
        ]
    )

    # ---- 解析 ----
    parse_quality_threshold: int = 60
    parse_block_page_size_max: int = 200
    parse_block_retention_days: int = 90

    # ---- 抽取 ----
    extract_max_repair_attempts: int = 2
    extract_batch_size: int = 8
    extract_max_blocks: int = 2000
    extract_max_attempts: int = 3
    graph_subgraph_max_nodes: int = 200
    merge_undo_window_days: int = 7
    # 跨文档对齐后同一身份节点会被多个任务并发写入，"读既有 → 合并 → 写入"
    # 这段临界区需要串行化；拿不到锁时最多等这么久，超时降级写入。
    graph_persist_lock_timeout_seconds: float = 5.0

    # ---- 任务重试与恢复 ----
    # attempt 是执行序号（只增），下面三个预算按来源分开计，互不侵占。
    # 自动重试置 0 即回到"失败后只能人工重试"的旧行为。
    task_auto_retry_max_attempts: int = 3
    task_auto_retry_backoff_seconds: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [30, 120, 300]
    )
    task_downgrade_max_attempts: int = 1
    task_downgrade_batch_ratio: float = 0.5
    task_downgrade_batch_min: int = 2
    task_manual_revive_max_attempts: int = 2
    task_skip_failed_batches_enabled: bool = True
    # 看护：心跳过期即判死，回 PENDING 续跑或落 FAILED，二者由预算决定
    task_heartbeat_ttl_seconds: int = 120
    task_stale_threshold_seconds: int = 300
    task_watchdog_enabled: bool = True
    task_watchdog_interval_seconds: int = 60

    # ---- LLM ----
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_temperature: float = 0.0
    llm_timeout_seconds: int = 120
    # 进程内重试：只消抖动，不替代任务级重试
    llm_max_attempts: int = 3
    llm_retry_base_delay_seconds: float = 1.0
    llm_retry_max_delay_seconds: float = 8.0
    llm_retry_jitter_ratio: float = 0.25

    # ---- 执行上限与锁 ----
    # 四个值必须联动：hard > soft，visibility 与锁 TTL 都要大于 hard，否则会出现
    # "锁失效后双执行"或"消息被 broker 提前重投"
    parse_soft_time_limit_seconds: int = 900
    parse_hard_time_limit_seconds: int = 960
    extract_soft_time_limit_seconds: int = 3600
    extract_hard_time_limit_seconds: int = 3900
    parse_run_lock_ttl_seconds: int = 5400
    extract_run_lock_ttl_seconds: int = 5400
    celery_visibility_timeout_seconds: int = 5400

    # ---- Embedding ----
    # 与 LLM 分开配置：换向量模型不应牵动对话模型
    embedding_provider: str = "openai"
    embedding_model: str = "qwen3.7-text-embedding"
    embedding_api_key: str = ""
    embedding_base_url: str = ""
    # 必须与 document_chunk.embedding 的列维度一致，改动需要迁移并全量重建
    embedding_dim: int = 1024
    embedding_batch_size: int = 10
    embedding_timeout_seconds: int = 60

    # ---- 检索 ----
    retrieval_top_k: int = 6
    retrieval_oversample: int = 4
    retrieval_hnsw_ef_search: int = 100
    # 非管理员可见的最高密级；管理员不受此限制
    retrieval_max_sensitivity: Literal["public", "internal", "confidential", "secret"] = "internal"

    # ---- 种子数据 ----
    seed_workspace_name: str = "默认空间"
    seed_workspace_slug: str = "default"
    seed_admin_username: str = "admin"
    seed_admin_password: str = "Admin@12345"
    seed_admin_display_name: str = "系统管理员"

    @field_validator("cors_origins", "upload_allowed_extensions", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """允许用逗号分隔或 JSON 数组覆盖列表型配置。"""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in text.split(",") if item.strip()]
        return value

    @field_validator("task_auto_retry_backoff_seconds", mode="before")
    @classmethod
    def _split_int_csv(cls, value: object) -> object:
        """退避序列写成 "30,120,300" 或 [30,120,300] 都可以。"""
        if isinstance(value, str):
            text = value.strip()
            if text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass
            return [int(item) for item in text.split(",") if item.strip()]
        return value

    @property
    def upload_max_size_bytes(self) -> int:
        return self.upload_max_size_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
